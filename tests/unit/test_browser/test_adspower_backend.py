"""AdsPowerBackend flow with a fake client (platform gate, errors).

Mirrors test_yuehulian_backend / test_wenmai_backend: the engine is
exercised through ``AdsPowerBackend`` with a scenario fake swapped in
at the ``client_factory`` seam. AdsPower has NO after_open (the start
params avoid the vendor's start page) — pinned by a negative control.
"""

from unittest import mock

from aiohttp import web
import pytest

from app.browser.adapters.adspower_backend import (
    AdsPowerBackend,
    AdsPowerClient,
)
from app.browser.inject import engine as engine_mod
from app.browser.inject.client import (
    InjectClient,
    OpenedStore,
    StoreRef,
)
from app.browser.inject.errors import (
    AccountMismatch,
    ApiNotEnabled,
    ClientNotRunning,
    StoreNotFound,
    UnsupportedKernel,
    UnsupportedPlatform,
)

pytestmark = pytest.mark.unit


class FakeAdsPowerClient(InjectClient):
    """Scenario fake: one field per failure mode."""

    def __init__(
        self,
        *,
        stores=None,
        fail_open=None,
        cdp_port=0,
        running=True,
        can_start=True,
    ):
        self.stores = (
            stores
            if stores is not None
            else [StoreRef('#1 demo-store', ref='kaaaaaa1')]
        )
        self.fail_open = fail_open
        self.cdp_port = cdp_port
        self._running = running
        self._can_start = can_start
        self.opened: list[str] = []
        self.closed: list[str] = []
        self.started = False

    async def ensure_running(self) -> None:
        if not self._running:
            if not self._can_start:
                raise ClientNotRunning()
            self._running = True
            self.started = True

    async def is_logged_in(self) -> bool:
        return True

    async def list_stores(self) -> list[StoreRef]:
        return list(self.stores)

    async def open_store(self, ref: str) -> OpenedStore:
        if ref not in {s.open_ref for s in self.stores}:
            raise StoreNotFound(ref)
        if self.fail_open is not None:
            raise self.fail_open
        self.opened.append(ref)
        return OpenedStore('127.0.0.1', self.cdp_port)

    async def close_store(self, ref: str) -> None:
        self.closed.append(ref)


def _backend(fake: FakeAdsPowerClient) -> AdsPowerBackend:
    return AdsPowerBackend(client_factory=lambda cfg: fake)


def _cfg(ref: str = 'kaaaaaa1') -> dict:
    return {
        'browser_ref': ref,
        'proxy_port': 9299,
        'store_slug': 'demo-store',
    }


@pytest.fixture(autouse=True)
def _force_windows():
    with mock.patch(
        'app.browser.adapters.adspower_backend.get_platform',
        return_value='windows',
    ):
        yield


@pytest.fixture
def _mocked_proxy(tmp_path):
    with (
        mock.patch.object(engine_mod, 'CDPMuxProxy') as proxy,
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        proxy.return_value.start = mock.AsyncMock()
        proxy.return_value.stop = mock.AsyncMock()
        yield proxy


async def test_start_opens_by_profile_id(cdp_version_port, _mocked_proxy):
    fake = FakeAdsPowerClient(cdp_port=cdp_version_port)
    info = await _backend(fake).start(_cfg())
    assert info.cdp_port == cdp_version_port
    assert fake.opened == ['kaaaaaa1']
    _mocked_proxy.return_value.start.assert_awaited_once()


async def test_no_after_open_needed(cdp_version_port, _mocked_proxy):
    # The start params (ip_tab=0&open_tabs=0) land the browser on the
    # profile's platform URL — no vendor entry page to click, so the
    # real client must NOT override the engine's after_open no-op
    # (verified live, m4; wenmai is the contrast that does).
    assert AdsPowerClient.after_open is InjectClient.after_open
    fake = FakeAdsPowerClient(cdp_port=cdp_version_port)
    await _backend(fake).start(_cfg())


async def test_unknown_ref_is_store_not_found(_mocked_proxy):
    fake = FakeAdsPowerClient()
    with pytest.raises(StoreNotFound):
        await _backend(fake).start(_cfg('zzzz9999'))
    assert fake.opened == []
    _mocked_proxy.return_value.start.assert_not_called()


async def test_firefox_kernel_blocks_start(_mocked_proxy):
    fake = FakeAdsPowerClient(
        fail_open=UnsupportedKernel('FlowerBrowser kernel, no CDP')
    )
    with pytest.raises(UnsupportedKernel):
        await _backend(fake).start(_cfg())
    _mocked_proxy.return_value.start.assert_not_called()


async def test_key_mismatch_blocks_start(_mocked_proxy):
    # A key that does not belong to the signed-in account must fail
    # BEFORE any browser opens (the #1 AdsPower support case).
    fake = FakeAdsPowerClient(fail_open=AccountMismatch('key mismatch'))
    with pytest.raises(AccountMismatch):
        await _backend(fake).start(_cfg())
    assert fake.opened == []
    _mocked_proxy.return_value.start.assert_not_called()


async def test_api_not_enabled_blocks_start(_mocked_proxy):
    fake = FakeAdsPowerClient(fail_open=ApiNotEnabled('no key'))
    with pytest.raises(ApiNotEnabled):
        await _backend(fake).start(_cfg())
    _mocked_proxy.return_value.start.assert_not_called()


async def test_autostart_when_client_down(cdp_version_port, _mocked_proxy):
    fake = FakeAdsPowerClient(
        cdp_port=cdp_version_port, running=False, can_start=True
    )
    info = await _backend(fake).start(_cfg())
    assert info.cdp_port == cdp_version_port
    assert fake.started is True


async def test_cannot_start_maps_to_client_not_running(_mocked_proxy):
    fake = FakeAdsPowerClient(running=False, can_start=False)
    with pytest.raises(ClientNotRunning):
        await _backend(fake).start(_cfg())


async def test_platform_gating_windows_and_wsl_allowed(
    cdp_version_port, _mocked_proxy
):
    for platform in ('windows', 'wsl'):
        with mock.patch(
            'app.browser.adapters.adspower_backend.get_platform',
            return_value=platform,
        ):
            fake = FakeAdsPowerClient(cdp_port=cdp_version_port)
            info = await _backend(fake).start(_cfg())
            assert info.cdp_port == cdp_version_port


async def test_platform_gating_linux_refused(_mocked_proxy):
    with mock.patch(
        'app.browser.adapters.adspower_backend.get_platform',
        return_value='linux',
    ):
        fake = FakeAdsPowerClient(cdp_port=1)
        with pytest.raises(UnsupportedPlatform):
            await _backend(fake).start(_cfg())
    assert fake.opened == []


# ── P9: the shared ENGINE refuses AdsPower's Firefox shapes ───────


async def test_engine_refuses_firefox_partial_cdp(cdp_version_port):
    # Contract P9 owns the kernel check now (the vendor copy was
    # dropped with it): a Firefox kernel answering a real-looking
    # partial CDP (Firefox/100 Remote Agent — verified live, REVIEW-2)
    # must be refused by the ENGINE, from /json/version's Browser.
    async def firefox_version(_request):
        # The real Remote Agent answers the full CDP handshake shape
        # (seen live, REVIEW-2) — that is exactly the trap.
        return web.json_response({
            'Browser': 'Firefox/100.0.2',
            'Protocol-Version': '1.0',
            'webSocketDebuggerUrl': (
                f'ws://127.0.0.1:{fox_port}/devtools/browser/ffff'
            ),
        })

    app = web.Application()
    app.router.add_get('/json/version', firefox_version)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    fox_port = runner.addresses[0][1]
    try:
        fake = FakeAdsPowerClient(cdp_port=fox_port)
        with pytest.raises(UnsupportedKernel):
            await _backend(fake).start(_cfg())
        assert fake.opened == ['kaaaaaa1']
        assert fake.closed == ['kaaaaaa1']  # the engine closed the env
    finally:
        await runner.cleanup()


async def test_engine_accepts_chromium(cdp_version_port, _mocked_proxy):
    # The real kernel CDP (Chrome/156…): no refusal.
    fake = FakeAdsPowerClient(cdp_port=cdp_version_port)
    info = await _backend(fake).start(_cfg())
    assert info.cdp_port == cdp_version_port
