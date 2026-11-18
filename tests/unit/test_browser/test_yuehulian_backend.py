"""Yuehulian backend logic + error classification (WF* scenarios).

Covered with the scenario-driven FakeYuehulianClient — no real client.
Each error path pairs with a twist (negative control): flip one spec
field and assert the opposite outcome, so a test that always passes can't
hide. Account-conflict (WF6), re-bind idempotency (WF7) and idle (WF9)
land with the manager / router / reaper wiring in later steps.
"""

from contextlib import contextmanager
from unittest import mock

import pytest

from app.browser.adapters.yuehulian import YuehulianBackend
from app.browser.base import BrowserSessionInfo
from app.browser.inject import engine as engine_mod
from app.browser.inject.client import StoreRef
from app.browser.inject.errors import (
    CdpUnreachable,
    ClientNotRunning,
    LoginRequired,
    StoreNotFound,
)
from tests.unit.test_browser.fake_yuehulian import (
    FakeScenario,
    FakeYuehulianClient,
)

pytestmark = pytest.mark.unit


def _backend(scenario: FakeScenario):
    fake = FakeYuehulianClient(scenario)
    return YuehulianBackend(client_factory=lambda cfg: fake), fake


def _cfg(proxy_port: int, ref: str = 'store-a') -> dict:
    return {
        'browser_ref': ref,
        'proxy_port': proxy_port,
        'store_slug': f'slug-{ref}',
    }


@contextmanager
def _mocked_proxy(tmp_path):
    with (
        mock.patch.object(engine_mod, 'CDPMuxProxy') as proxy,
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        proxy.return_value.start = mock.AsyncMock()
        proxy.return_value.stop = mock.AsyncMock()
        yield proxy


# ── WF1: dropdown enumerate ──────────────────────────────────────────
async def test_wf1_list_stores_returns_visible():
    _, fake = _backend(
        FakeScenario(stores=[StoreRef('store-a'), StoreRef('store-b')])
    )
    names = [s.name for s in await fake.list_stores()]
    assert names == ['store-a', 'store-b']


async def test_wf1_twist_logged_out_raises_not_empty_list():
    _, fake = _backend(
        FakeScenario(logged_in=False, stores=[StoreRef('store-a')])
    )
    with pytest.raises(LoginRequired):
        await fake.list_stores()


# ── WF2: open the matched store + build the proxy ────────────────────
async def test_wf2_start_opens_matching_store(cdp_version_port, tmp_path):
    be, fake = _backend(
        FakeScenario(
            stores=[StoreRef('store-a'), StoreRef('store-b')],
            cdp_port=cdp_version_port,
        )
    )
    with _mocked_proxy(tmp_path) as proxy:
        info = await be.start(_cfg(9299, 'store-b'))
    assert isinstance(info, BrowserSessionInfo)
    assert info.cdp_port == cdp_version_port
    assert fake.opened == ['store-b']
    proxy.assert_called_once()
    proxy.return_value.start.assert_awaited_once()


async def test_wf2_twist_unknown_store_is_store_not_found(
    cdp_version_port, tmp_path
):
    be, fake = _backend(
        FakeScenario(stores=[StoreRef('store-a')], cdp_port=cdp_version_port)
    )
    with _mocked_proxy(tmp_path) as proxy:
        with pytest.raises(StoreNotFound):
            await be.start(_cfg(9299, 'store-zzz'))
    assert fake.opened == []
    proxy.assert_not_called()


# ── WF3: not logged in blocks start ──────────────────────────────────
async def test_wf3_not_logged_in_blocks_start(cdp_version_port, tmp_path):
    be, fake = _backend(
        FakeScenario(
            logged_in=False,
            stores=[StoreRef('store-a')],
            cdp_port=cdp_version_port,
        )
    )
    with _mocked_proxy(tmp_path) as proxy:
        with pytest.raises(LoginRequired):
            await be.start(_cfg(9299, 'store-a'))
    proxy.assert_not_called()
    assert fake.opened == []


async def test_wf3_twist_logged_in_starts_ok(cdp_version_port, tmp_path):
    be, _ = _backend(
        FakeScenario(stores=[StoreRef('store-a')], cdp_port=cdp_version_port)
    )
    with _mocked_proxy(tmp_path):
        info = await be.start(_cfg(9299, 'store-a'))
    assert info.cdp_port == cdp_version_port


# ── WF4: login lost mid-session → LOGIN_REQUIRED, not store-not-found ─
async def test_wf4_login_lost_after_open_reports_login_required(
    cdp_version_port, tmp_path
):
    be, fake = _backend(
        FakeScenario(
            stores=[StoreRef('store-a')],
            cdp_port=cdp_version_port,
            drop_login_after_open=True,
        )
    )
    with _mocked_proxy(tmp_path):
        await be.start(_cfg(9299, 'store-a'))
    with pytest.raises(LoginRequired):
        await fake.open_store('store-a')


async def test_wf4_twist_login_kept_allows_reopen(cdp_version_port, tmp_path):
    be, fake = _backend(
        FakeScenario(stores=[StoreRef('store-a')], cdp_port=cdp_version_port)
    )
    with _mocked_proxy(tmp_path):
        await be.start(_cfg(9299, 'store-a'))
    again = await fake.open_store('store-a')
    assert again.cdp_port == cdp_version_port


# ── WF5: concurrent stores stay isolated ─────────────────────────────
async def test_wf5_concurrent_stores_isolated(cdp_version_port, tmp_path):
    be_a, fa = _backend(
        FakeScenario(stores=[StoreRef('store-a')], cdp_port=cdp_version_port)
    )
    be_b, fb = _backend(
        FakeScenario(stores=[StoreRef('store-b')], cdp_port=cdp_version_port)
    )
    with _mocked_proxy(tmp_path):
        await be_a.start(_cfg(9301, 'store-a'))
        await be_b.start(_cfg(9302, 'store-b'))
    assert fa.opened == ['store-a']
    assert fb.opened == ['store-b']
    oa = await fa.open_store('store-a')
    ob = await fb.open_store('store-b')
    assert oa.user_data_dir != ob.user_data_dir


# ── WF8: client not running → autostart; can't start → CLIENT_NOT_RUNNING
async def test_wf8_autostart_when_not_running(cdp_version_port, tmp_path):
    be, fake = _backend(
        FakeScenario(
            running=False,
            can_start=True,
            stores=[StoreRef('store-a')],
            cdp_port=cdp_version_port,
        )
    )
    with _mocked_proxy(tmp_path):
        info = await be.start(_cfg(9299, 'store-a'))
    assert fake.started is True
    assert info.cdp_port == cdp_version_port


async def test_wf8_twist_cannot_start_is_client_not_running(
    cdp_version_port, tmp_path
):
    be, _ = _backend(
        FakeScenario(
            running=False,
            can_start=False,
            stores=[StoreRef('store-a')],
            cdp_port=cdp_version_port,
        )
    )
    with _mocked_proxy(tmp_path) as proxy:
        with pytest.raises(ClientNotRunning):
            await be.start(_cfg(9299, 'store-a'))
    proxy.assert_not_called()


# ── CDP unreachable: client-side and engine-probe side ───────────────
async def test_cdp_unreachable_client_side(cdp_version_port, tmp_path):
    be, _ = _backend(
        FakeScenario(
            stores=[StoreRef('store-a')],
            cdp_port=cdp_version_port,
            open_unreachable=True,
        )
    )
    with _mocked_proxy(tmp_path) as proxy:
        with pytest.raises(CdpUnreachable):
            await be.start(_cfg(9299, 'store-a'))
    proxy.assert_not_called()


async def test_cdp_unreachable_engine_probe_when_port_dead(
    cdp_version_port, tmp_path
):
    be, _ = _backend(
        FakeScenario(stores=[StoreRef('store-a')], cdp_port=cdp_version_port)
    )
    with (
        _mocked_proxy(tmp_path) as proxy,
        mock.patch.object(
            YuehulianBackend,
            '_cdp_reachable',
            new=mock.AsyncMock(return_value=False),
        ),
    ):
        with pytest.raises(CdpUnreachable):
            await be.start(_cfg(9299, 'store-a'))
    proxy.assert_not_called()


# ── stop() tears down proxy and closes the env ───────────────────────
async def test_stop_closes_proxy_and_env(cdp_version_port, tmp_path):
    be, fake = _backend(
        FakeScenario(stores=[StoreRef('store-a')], cdp_port=cdp_version_port)
    )
    with _mocked_proxy(tmp_path) as proxy:
        info = await be.start(_cfg(9299, 'store-a'))
        await be.stop(info)
    proxy.return_value.stop.assert_awaited_once()
    assert fake.closed == ['store-a']
