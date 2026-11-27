"""The invariant: a vendor registered through the plugin context LAUNCHES.

The registry test proves the spec reaches the helpers; this one proves
the runtime path — the manager resolves the backend from the registry
and the inject engine drives the vendor's spec-declared ``client_factory``
all the way to a live CDP endpoint — with ZERO shared-code edits (the
fake vendor exists only in this file).
"""

from contextlib import contextmanager
from unittest import mock

import pytest

from app.browser.inject import engine as engine_mod
from app.browser.inject.client import InjectClient, OpenedStore, StoreRef
from app.browser.manager import BrowserManager
from app.browser.vendors import (
    CARDINALITY_SINGLE,
    BrowserVendorSpec,
    VendorCredentialField,
)
import app.plugins as plugins_mod
from app.plugins import ExtensionContext, Plugin, reset_for_tests

pytestmark = pytest.mark.unit


class FakeVendorClient(InjectClient):
    """The vendor's client, as its module would write it."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.opened: list[str] = []

    async def ensure_running(self) -> None:
        return None

    async def is_logged_in(self) -> bool:
        return True

    async def list_stores(self) -> list[StoreRef]:
        return [StoreRef('fv-store-a'), StoreRef('fv-store-b')]

    async def open_store(self, ref: str) -> OpenedStore:
        self.opened.append(ref)
        return OpenedStore(cdp_host='127.0.0.1', cdp_port=self.cfg['cdp_port'])

    async def close_store(self, ref: str) -> None:
        return None


@pytest.fixture
def fake_vendor_backend(monkeypatch):
    """Register a fake vendor whose client factory reads the spec."""

    class _Plugin(Plugin):
        @property
        def name(self) -> str:
            return 'fakevendor'

        def install(self, ctx: ExtensionContext) -> None:
            ctx.register_browser_backend('fakevendor', _Backend, _SPEC)

    class _EntryPoint:
        name = 'fake:fakevendor'

        def load(self):
            return _Plugin

    def fake_entry_points(group=None):
        return [_EntryPoint()] if group == 'vibe_seller.plugins' else []

    monkeypatch.setattr(
        plugins_mod.importlib.metadata, 'entry_points', fake_entry_points
    )
    reset_for_tests()
    yield
    reset_for_tests()


_SPEC = BrowserVendorSpec(
    key='fakevendor',
    display_name_en='FakeVendor',
    display_name_zh='假浏览器',
    account_cardinality=CARDINALITY_SINGLE,
    credential_fields=(
        VendorCredentialField('acct', 'vendors.fakevendor.acct'),
    ),
    lists_profiles=True,
    i18n_prefix='vendors.fakevendor',
    client_factory=lambda cfg: FakeVendorClient(cfg),
)


class _Backend(engine_mod.InjectCdpBackend):
    """The vendor's backend class, as its module would write it (the
    spec's client factory is the engine's seam)."""

    def __init__(self):
        super().__init__(_SPEC.client_factory)


@contextmanager
def _mocked_proxy(tmp_path):
    with (
        mock.patch.object(engine_mod, 'CDPMuxProxy') as proxy,
        mock.patch('app.browser.downloads.DOWNLOADS_DIR', tmp_path),
    ):
        proxy.return_value.start = mock.AsyncMock()
        proxy.return_value.stop = mock.AsyncMock()
        yield proxy


async def test_registered_vendor_launches_through_the_engine(
    fake_vendor_backend, cdp_version_port, tmp_path
):
    # 1. The manager resolves the backend from the registry — no
    #    shared-code edit named this vendor.
    mgr = BrowserManager()
    backend = mgr._get_backend('fv-store', 'fakevendor')
    assert isinstance(backend, _Backend)

    # 2. The engine drives the spec-declared client factory to a live
    #    CDP endpoint (the fake /json/version server).
    with _mocked_proxy(tmp_path) as proxy:
        info = await backend.start({
            'browser_ref': 'fv-store-b',
            'proxy_port': 9299,
            'store_slug': 'fv-slug',
            'cdp_port': cdp_version_port,
        })
    assert info.cdp_port == cdp_version_port
    client = backend._client
    assert isinstance(client, FakeVendorClient)
    assert client.opened == ['fv-store-b']
    proxy.assert_called_once()


def test_twist_unregistered_vendor_never_launches(monkeypatch):
    # Negative control: without the registration, the manager refuses
    # the backend — the registry is the only way in.
    reset_for_tests()
    try:
        mgr = BrowserManager()
        with pytest.raises(ValueError):
            mgr._get_backend('fv-store', 'fakevendor')
    finally:
        reset_for_tests()
