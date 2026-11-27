"""Wenmai (稳卖) anti-detect browser backend — Windows only.

稳卖 ships a local HTTP API (Express, port 3980) on every launch, so
unlike 悦互联 there is no GUI scraping: the client adapter talks HTTP,
returns the kernel's own debug endpoint from ``openEnv``, and clicks
the store-entry middle page over CDP (``after_open``). From the CDP
endpoint onward it is identical to the ziniao / chrome backends
(CDPMuxProxy → browser-use).

The engine (:class:`InjectCdpBackend`) holds the generic flow; this
file is the thin adapter. The real client lives in ``wenmai_client``
and is swapped for a fake in tests via the ``client_factory`` seam.
"""

from app.browser.adapters.wenmai_client import RealWenmaiClient
from app.browser.base import BrowserSessionInfo
from app.browser.inject.client import InjectClient
from app.browser.inject.engine import ClientFactory, InjectCdpBackend
from app.browser.inject.errors import UnsupportedPlatform
from app.browser.ziniao_utils import get_platform

# Needs the Windows 稳卖 client. WSL-on-Windows is fine (the client's
# local API answers on 127.0.0.1 through mirrored networking); pure
# Linux / macOS cannot reach a client.
_SUPPORTED_PLATFORMS = frozenset({'windows', 'wsl'})

# The facts a future BrowserVendorSpec (ARCHITECTURE.md §4) will carry,
# declared here so the registry PR turns this dict into its spec
# registration without re-researching the vendor. Credential fields
# match the neutral schema documented in wenmai_client's docstring.
WENMAI_VENDOR_INFO = {
    'key': 'wenmai',
    'display_name_zh': '稳卖',
    'display_name_en': 'Wenmai (稳卖)',
    'kind': 'anti-detect',
    'platforms': ('windows', 'wsl'),
    'credential_schema': (
        # (key, label_zh, secret, required)
        ('company', '企业名称', False, True),
        ('username', '企业用户名', False, True),
        ('password', '企业密码', True, True),
    ),
    'ref_label': '环境ID',
    'lists_profiles': True,
    'machine_constraints': ('one account per machine',),
    'docs_anchor': 'wenmai',
}


def make_client(browser_config: dict) -> InjectClient:
    """Build the real Windows-driven client (patched to a fake in tests)."""
    return RealWenmaiClient(browser_config)


class WenmaiBackend(InjectCdpBackend):
    """Route-B backend for the 稳卖 e-commerce browser (Windows)."""

    def __init__(self, client_factory: ClientFactory | None = None):
        # BrowserManager constructs backends with no args (the real
        # path). Tests pass a factory that returns a FakeWenmaiClient.
        super().__init__(client_factory or (lambda cfg: make_client(cfg)))

    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        platform = get_platform()
        if platform not in _SUPPORTED_PLATFORMS:
            raise UnsupportedPlatform(
                f'wenmai is Windows-only; platform is {platform}'
            )
        return await super().start(browser_config)
