"""Unit test: Ziniao stale-launch recovery is PER-STORE, never a global
client kill.

Regression guard for the multi-store outage (docs/ziniao-concurrency.md):
a stale launch for one store used to call ``kill_and_relaunch_ziniao``
(``pkill -9`` the whole client), destroying every other store's live
browser and cascading. The fix recovers per store — ``stopBrowser`` this
env + retry ``startBrowser`` — and never touches the shared client.
"""

from unittest import mock

import pytest

from app.browser import ziniao as zmod
from app.browser.ziniao import ZiniaoBackend

pytestmark = pytest.mark.unit


@pytest.fixture
def cfg():
    return {
        'company': 'co',
        'username': 'u',
        'password': 'p',
        'socket_port': 16851,
        'client_path': 'ziniao',
        'browser_oauth': 'OAUTH==',
        'proxy_port': 9222,
        'store_slug': 'store-x',
    }


async def test_stale_launch_recovers_per_store_no_global_kill(cfg, tmp_path):
    """First two startBrowser calls stale-launch; recovery must be a
    per-store stopBrowser + retry — no kill_and_relaunch / force_kill."""
    calls: list[str] = []

    async def fake_try_connect(port, data, timeout):
        calls.append(data['action'])
        # startBrowser always "succeeds" at the API level; stopBrowser too.
        return (
            {'statusCode': '0', 'debuggingPort': 5000 + len(calls)},
            '127.0.0.1',
        )

    # Unreachable for the first 2 probes (stale), reachable on the 3rd.
    reachable = iter([False, False, True])

    async def fake_reachable(host, port):
        return next(reachable)

    with (
        mock.patch.object(zmod, 'ensure_ziniao_running', new=mock.AsyncMock()),
        mock.patch.object(zmod, 'update_ziniao_core', new=mock.AsyncMock()),
        mock.patch.object(
            zmod,
            'try_connect_ziniao',
            new=mock.AsyncMock(side_effect=fake_try_connect),
        ),
        mock.patch.object(
            ZiniaoBackend,
            '_cdp_port_reachable',
            new=mock.AsyncMock(side_effect=fake_reachable),
        ),
        mock.patch.object(zmod, 'CDPMuxProxy') as MockProxy,
        mock.patch('app.browser.ziniao.DOWNLOADS_DIR', tmp_path),
        # These MUST NOT be called — assert after.
        mock.patch('app.browser.ziniao_utils.force_kill_ziniao') as fk,
        mock.patch(
            'app.browser.ziniao_utils.kill_and_relaunch_ziniao',
            new=mock.AsyncMock(),
        ) as kar,
    ):
        MockProxy.return_value.start = mock.AsyncMock()
        await ZiniaoBackend().start(cfg)

    # Recovered without ever killing the shared client.
    fk.assert_not_called()
    kar.assert_not_called()
    # Between the two stale attempts we issued per-store stopBrowser calls.
    assert calls.count('stopBrowser') >= 2, (
        f'expected per-store stopBrowser recovery, got calls={calls}'
    )
    assert 'startBrowser' in calls


def test_clear_singleton_locks_removes_stale_files(tmp_path):
    """Stale Chrome Singleton* files (left by a crash/SIGKILL) are removed;
    a missing dir / None is a safe no-op."""
    for name in ('SingletonLock', 'SingletonSocket', 'SingletonCookie'):
        (tmp_path / name).write_text('stale')
    (tmp_path / 'Cookies').write_text('keep')  # unrelated file must survive

    zmod._clear_singleton_locks(str(tmp_path))

    assert not (tmp_path / 'SingletonLock').exists()
    assert not (tmp_path / 'SingletonSocket').exists()
    assert not (tmp_path / 'SingletonCookie').exists()
    assert (tmp_path / 'Cookies').exists()  # untouched
    # None + nonexistent dir must not raise.
    zmod._clear_singleton_locks(None)
    zmod._clear_singleton_locks(str(tmp_path / 'does-not-exist'))


async def test_all_attempts_stale_fails_only_this_store(cfg, tmp_path):
    """If every attempt stale-launches, start() raises for THIS store
    (isolated failure) — still no global kill."""

    async def fake_try_connect(port, data, timeout):
        return ({'statusCode': '0', 'debuggingPort': 6000}, '127.0.0.1')

    with (
        mock.patch.object(zmod, 'ensure_ziniao_running', new=mock.AsyncMock()),
        mock.patch.object(zmod, 'update_ziniao_core', new=mock.AsyncMock()),
        mock.patch.object(
            zmod,
            'try_connect_ziniao',
            new=mock.AsyncMock(side_effect=fake_try_connect),
        ),
        mock.patch.object(
            ZiniaoBackend,
            '_cdp_port_reachable',
            new=mock.AsyncMock(return_value=False),
        ),
        mock.patch('app.browser.ziniao.DOWNLOADS_DIR', tmp_path),
        mock.patch('app.browser.ziniao_utils.force_kill_ziniao') as fk,
        mock.patch(
            'app.browser.ziniao_utils.kill_and_relaunch_ziniao',
            new=mock.AsyncMock(),
        ) as kar,
    ):
        with pytest.raises(RuntimeError, match='failed to launch'):
            await ZiniaoBackend().start(cfg)

    fk.assert_not_called()
    kar.assert_not_called()
