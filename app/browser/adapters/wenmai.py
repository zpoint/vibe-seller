"""Wenmai (稳卖) anti-detect browser backend.

稳卖 ships a local HTTP API (Express, port 3980) on every launch, so
unlike 悦互联 there is no GUI scraping: the client adapter talks HTTP
(a textbook :class:`~app.browser.local_api_client.LocalApiClient`),
returns the kernel's own debug endpoint from ``openEnv``, and clicks
the store-entry middle page over CDP (``after_open``). From the CDP
endpoint onward it is identical to the ziniao / chrome backends
(CDPMuxProxy → browser-use).

The engine (:class:`InjectCdpBackend`) holds the generic flow; this
file is the thin adapter plus the vendor spec (``WENMAI_VENDOR``) the
generic routes / UI / manager read. The real client lives in
``wenmai_client`` and is swapped for a fake in tests via the
``client_factory`` seam.
"""

from app.browser.adapters.wenmai_client import RealWenmaiClient
from app.browser.adapters.wenmai_win import WENMAI_WIN_APP
from app.browser.base import BrowserSessionInfo
from app.browser.inject.client import InjectClient
from app.browser.inject.engine import ClientFactory, InjectCdpBackend
from app.browser.inject.errors import UnsupportedPlatform
from app.browser.vendors import (
    CARDINALITY_SINGLE,
    CLIENT_LOCAL_API,
    KIND_ANTIDETECT,
    BrowserVendorSpec,
    VendorCredentialField,
    VendorDocsLink,
)
from app.browser.ziniao_utils import get_platform

# Needs the Windows 稳卖 client, ON Windows (native Windows is the
# platform; a WSL server on the same machine reaches the client's
# local API on 127.0.0.1 through mirrored networking). Pure Linux
# cannot; macOS is a separate client build (in verification — added
# only after its live run passes).
_SUPPORTED_PLATFORMS = frozenset({'windows', 'wsl'})


def make_client(browser_config: dict) -> InjectClient:
    """Build the real Windows-driven client (patched to a fake in tests)."""
    return RealWenmaiClient(browser_config)


# The whole vendor declaration: one enterprise login per machine
# (company / username / password, stored encrypted by the generic
# machine-scope credential storage and injected as wenmai_<field>),
# stores listed through the client's local API, and the spec-driven
# docs links for the UI's help links.
WENMAI_VENDOR = BrowserVendorSpec(
    key='wenmai',
    display_name_en='Wenmai',
    display_name_zh='稳卖',
    kind=KIND_ANTIDETECT,
    platforms=_SUPPORTED_PLATFORMS,
    account_cardinality=CARDINALITY_SINGLE,
    credential_fields=(
        VendorCredentialField('company', 'settings.wenmaiCompany'),
        VendorCredentialField('username', 'settings.wenmaiUsername'),
        VendorCredentialField(
            'password', 'settings.wenmaiPassword', secret=True
        ),
    ),
    lists_profiles=True,
    client_kind=CLIENT_LOCAL_API,
    i18n_prefix='settings.wenmai',
    docs={
        'errors': VendorDocsLink(
            topic='wenmaiErrors',
            zh='https://docs.vibe-sellers.com/wenmai/#常见提示--报错',
            en=(
                'https://docs.vibe-sellers.com/en/wenmai/'
                '#common-statuses--errors'
            ),
        ),
        'one_account': VendorDocsLink(
            topic='wenmaiOneAccount',
            zh=('https://docs.vibe-sellers.com/wenmai/#一台机器只支持一个账号'),
            en=(
                'https://docs.vibe-sellers.com/en/wenmai/'
                '#one-account-per-machine'
            ),
        ),
    },
    client_factory=make_client,
    win_app=WENMAI_WIN_APP,
    client_path_setting='wenmai_client_path',
)


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
