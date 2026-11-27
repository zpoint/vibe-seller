"""WenmaiBackend flow with a fake client (platform gate, errors, stop).

Mirrors test_yuehulian_backend: the engine is exercised through
``WenmaiBackend`` with a scenario fake swapped in at the
``client_factory`` seam; the wenmai-specific cases (device-unbound
error propagation, unsupported kernel) pair with twists.
"""

from unittest import mock

import pytest

from app.browser.adapters.wenmai import WenmaiBackend
from app.browser.inject import engine as engine_mod, errors as errors_mod
from app.browser.inject.client import (
    InjectClient,
    OpenedStore,
    StoreRef,
)
from app.browser.inject.engine import InjectCdpBackend
from app.browser.inject.errors import (
    CdpUnreachable,
    ClientNotRunning,
    LoginRequired,
    StoreNotFound,
    StoreUnavailable,
    UnsupportedKernel,
    UnsupportedPlatform,
)

pytestmark = pytest.mark.unit


def test_store_device_unbound_is_folded_into_store_unavailable():
    """The taxonomy has no vendor-specific StoreDeviceUnbound: the
    device-unbound case is StoreUnavailable carrying the vendor text."""
    assert not hasattr(errors_mod, 'StoreDeviceUnbound')
    assert errors_mod.StoreUnavailable.code == 'STORE_UNAVAILABLE'


class FakeWenmaiClient(InjectClient):
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
            else [StoreRef('demo-store', ref='100000000001')]
        )
        self.fail_open = fail_open
        self.cdp_port = cdp_port
        self._running = running
        self._can_start = can_start
        self.opened: list[str] = []
        self.closed: list[str] = []
        self.after_opens: list[int] = []
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

    async def after_open(self, opened: OpenedStore) -> None:
        self.after_opens.append(opened.cdp_port)

    async def close_store(self, ref: str) -> None:
        self.closed.append(ref)


def _backend(fake: FakeWenmaiClient) -> WenmaiBackend:
    return WenmaiBackend(client_factory=lambda cfg: fake)


def _cfg(ref: str = '100000000001') -> dict:
    return {
        'browser_ref': ref,
        'proxy_port': 9299,
        'store_slug': 'demo-store',
    }


@pytest.fixture(autouse=True)
def _force_windows():
    with mock.patch(
        'app.browser.adapters.wenmai.get_platform',
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


async def test_start_opens_by_env_ref_and_runs_after_open(
    cdp_version_port, _mocked_proxy
):
    fake = FakeWenmaiClient(cdp_port=cdp_version_port)
    info = await _backend(fake).start(_cfg())
    assert info.cdp_port == cdp_version_port
    assert fake.opened == ['100000000001']
    assert fake.after_opens == [cdp_version_port]  # the entry page ran
    _mocked_proxy.return_value.start.assert_awaited_once()


async def test_start_by_env_ref_not_by_name(cdp_version_port, _mocked_proxy):
    # The env id is the binding: a store RENAMED in the client still
    # opens (the ref is stable across renames).
    fake = FakeWenmaiClient(
        stores=[StoreRef('renamed-store', ref='100000000001')],
        cdp_port=cdp_version_port,
    )
    info = await _backend(fake).start(_cfg())
    assert info.cdp_port == cdp_version_port


async def test_unknown_ref_is_store_not_found(_mocked_proxy):
    fake = FakeWenmaiClient()
    with pytest.raises(StoreNotFound):
        await _backend(fake).start(_cfg('999999999999'))
    assert fake.opened == []
    _mocked_proxy.return_value.start.assert_not_called()


async def test_device_unbound_is_store_unavailable(_mocked_proxy):
    fake = FakeWenmaiClient(
        fail_open=StoreUnavailable('未绑定可用设备 — 在稳卖客户端绑定设备')
    )
    with pytest.raises(StoreUnavailable):
        await _backend(fake).start(_cfg())
    assert fake.after_opens == []  # the entry page never ran
    _mocked_proxy.return_value.start.assert_not_called()


async def test_unsupported_kernel_blocks_start(_mocked_proxy):
    fake = FakeWenmaiClient(
        fail_open=UnsupportedKernel('Firefox kernel, no CDP')
    )
    with pytest.raises(UnsupportedKernel):
        await _backend(fake).start(_cfg())
    _mocked_proxy.return_value.start.assert_not_called()


async def test_logged_out_blocks_start(cdp_version_port, _mocked_proxy):
    # The engine's login gate: a logged-out client never reaches open.
    fake = FakeWenmaiClient(cdp_port=cdp_version_port)

    async def logged_out():
        return False

    fake.is_logged_in = logged_out
    with pytest.raises(LoginRequired):
        await _backend(fake).start(_cfg())
    assert fake.opened == []
    _mocked_proxy.return_value.start.assert_not_called()


async def test_autostart_when_client_down(cdp_version_port, _mocked_proxy):
    fake = FakeWenmaiClient(
        running=False, can_start=True, cdp_port=cdp_version_port
    )
    info = await _backend(fake).start(_cfg())
    assert fake.started is True
    assert info.cdp_port == cdp_version_port


async def test_cannot_start_is_client_not_running(_mocked_proxy):
    fake = FakeWenmaiClient(running=False, can_start=False)
    with pytest.raises(ClientNotRunning):
        await _backend(fake).start(_cfg())
    _mocked_proxy.return_value.start.assert_not_called()


async def test_platform_gating_blocks_mac(_mocked_proxy):
    fake = FakeWenmaiClient(cdp_port=1)
    with (
        mock.patch(
            'app.browser.adapters.wenmai.get_platform',
            return_value='mac',
        ),
        pytest.raises(UnsupportedPlatform),
    ):
        await _backend(fake).start(_cfg())
    assert fake.opened == []


async def test_platform_gating_twist_wsl_allowed(
    cdp_version_port, _mocked_proxy
):
    fake = FakeWenmaiClient(cdp_port=cdp_version_port)
    with mock.patch(
        'app.browser.adapters.wenmai.get_platform', return_value='wsl'
    ):
        info = await _backend(fake).start(_cfg())
    assert info.cdp_port == cdp_version_port


async def test_stop_closes_browser_proxy_and_env(
    cdp_version_port, _mocked_proxy
):
    fake = FakeWenmaiClient(cdp_port=cdp_version_port)
    backend = _backend(fake)
    with mock.patch.object(
        WenmaiBackend, '_close_browser', new=mock.AsyncMock()
    ) as close_br:
        info = await backend.start(_cfg())
        await backend.stop(info)
    close_br.assert_awaited_once()
    _mocked_proxy.return_value.stop.assert_awaited_once()
    assert fake.closed == ['100000000001']


async def test_after_open_failure_fails_the_start_loudly(
    cdp_version_port, _mocked_proxy
):
    """Negative control: a middle page that never produces a store page
    must fail the launch (CdpUnreachable), not hand the agent a
    half-opened env."""

    class _Stuck(FakeWenmaiClient):
        async def after_open(self, opened):
            raise CdpUnreachable('no store page opened')

    fake = _Stuck(cdp_port=cdp_version_port)
    with pytest.raises(CdpUnreachable):
        await _backend(fake).start(_cfg())
    _mocked_proxy.return_value.start.assert_not_called()


async def test_start_recovers_a_wedged_kernel_then_retries(
    cdp_version_port, _mocked_proxy
):
    """The backend wires the client's wedged-kernel recovery into the
    engine flow: the first CDP verification fails, the recovery runs,
    the retry succeeds — a task recovers by itself instead of failing
    on a dead port."""
    probes = {'n': 0}
    real_version = InjectCdpBackend._cdp_version

    async def fail_once_then_ok(self, host, port):
        probes['n'] += 1
        if probes['n'] == 1:
            return None  # the engine's verification fails: dead port
        return await real_version(self, host, port)

    class _Recovering(FakeWenmaiClient):
        def __init__(self, cdp_port):
            super().__init__(cdp_port=cdp_port)
            self.recovered: list[str] = []

        async def recover_wedged_env(self, ref, opened):
            self.recovered.append(ref)
            return opened

    fake = _Recovering(cdp_port=cdp_version_port)
    backend = WenmaiBackend(client_factory=lambda cfg: fake)
    with mock.patch.object(
        InjectCdpBackend, '_cdp_version', new=fail_once_then_ok
    ):
        info = await backend.start(_cfg())
    assert info.cdp_port == cdp_version_port
    assert fake.recovered == ['100000000001']  # the recovery ran once
    assert probes['n'] == 2  # and the start was retried
