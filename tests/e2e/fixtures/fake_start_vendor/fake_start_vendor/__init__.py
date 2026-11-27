"""E2E fixture: a browser vendor whose start fails, on demand.

Installs ONE browser backend (``fakestart``) through the real plugin
entry-point group, so a full e2e server can be driven into any
terminal browser-start failure state without a real vendor client —
the deterministic stand-in BRIEF-2 prescribes for the C2 tier-1 tests
and for the coordinator's banner screenshots.

Test-only: it registers itself only when ``FAKE_START_VENDOR`` is set
in the server's environment (the e2e harness / a local repro server),
so installing the package into a venv changes nothing for any other
test or server. Never shipped, never named in app/ code.

Behaviour — ``FakeStartVendorBackend.start`` raises the inject
taxonomy error named by ``FAKE_START_VENDOR_CODE`` (default
``UNSUPPORTED_KERNEL``); a code of ``OK`` starts nothing and raises
nothing (not used by the tier-1 tests; it exists so a local server can
prove the non-failure path is untouched). Optional
``FAKE_START_VENDOR_MESSAGE`` overrides the error message.
"""

import os

from app.browser.base import BrowserBackend, BrowserSessionInfo
from app.browser.inject.errors import (
    AccountMismatch,
    ApiNotEnabled,
    CdpUnreachable,
    ClientNotRunning,
    InjectError,
    LoginRequired,
    PlanRequired,
    StoreNotFound,
    StoreUnavailable,
    UnsupportedKernel,
)
from app.plugins import Plugin, PluginContext

#: The backend key stores are created with (``browser_backend``).
BACKEND_KEY = 'fakestart'

#: code -> exception class, every code the C2 contract names.
CODES: dict[str, type[InjectError]] = {
    'UNSUPPORTED_KERNEL': UnsupportedKernel,
    'STORE_UNAVAILABLE': StoreUnavailable,
    'STORE_NOT_FOUND': StoreNotFound,
    'PLAN_REQUIRED': PlanRequired,
    'API_NOT_ENABLED': ApiNotEnabled,
    'ACCOUNT_MISMATCH': AccountMismatch,
    'LOGIN_REQUIRED': LoginRequired,
    'CLIENT_NOT_RUNNING': ClientNotRunning,
    'CDP_UNREACHABLE': CdpUnreachable,
}


class FakeStartVendorBackend(BrowserBackend):
    """Fails ``start`` with the taxonomy code the environment names."""

    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        code = os.environ.get('FAKE_START_VENDOR_CODE', 'UNSUPPORTED_KERNEL')
        if code == 'OK':
            return BrowserSessionInfo(cdp_port=0)
        cls = CODES.get(code)
        if cls is None:
            raise ValueError(
                f'FAKE_START_VENDOR_CODE={code!r} is not one of: '
                + ', '.join(CODES)
            )
        message = os.environ.get('FAKE_START_VENDOR_MESSAGE', '')
        raise cls(message)

    async def stop(self, info: BrowserSessionInfo) -> None:
        return None


class FakeStartVendorPlugin(Plugin):
    """Registers the fake backend when the server opted in."""

    @property
    def name(self) -> str | None:
        return 'fake-start-vendor'

    @classmethod
    def should_load(cls, context: PluginContext) -> bool:
        # Opt-in: only servers started for the fixture load it, so the
        # package can sit installed in a shared venv invisibly. The
        # plugin CONTEXT still passes; the env var is the fixture's own
        # gate, not a plugin-loading seam.
        return bool(os.environ.get('FAKE_START_VENDOR'))

    def install(self, ctx) -> None:
        ctx.register_browser_backend(BACKEND_KEY, FakeStartVendorBackend)
