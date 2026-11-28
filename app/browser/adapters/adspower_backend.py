"""The AdsPower backend class (the thin InjectCdpBackend adapter).

The client and the vendor facts live in :mod:`app.browser.adapters
.adspower`; this module is only the backend wrapper the registry
registers, split out to keep each file small (the repo's 800-line
limit).
"""

from app.browser.adapters.adspower import AdsPowerClient
from app.browser.adapters.adspower_common import DEFAULT_API_PORT
from app.browser.adapters.adspower_win import ADSPOWER_SPEC
from app.browser.base import BrowserSessionInfo
from app.browser.inject.client import InjectClient
from app.browser.inject.engine import ClientFactory, InjectCdpBackend
from app.browser.inject.errors import UnsupportedPlatform
from app.browser.vendors import (
    CARDINALITY_SINGLE,
    CLIENT_LOCAL_API,
    KIND_ANTIDETECT,
    PROFILES_LOGIN,
    BrowserVendorSpec,
    VendorCredentialField,
    VendorDocsLink,
)
from app.browser.ziniao_utils import get_platform


def make_client(browser_config: dict) -> InjectClient:
    """Build the real local-API client (patched to a fake in tests)."""
    return AdsPowerClient(browser_config)


class AdsPowerBackend(InjectCdpBackend):
    """Route-B backend for the AdsPower anti-detect browser (Windows)."""

    def __init__(self, client_factory: ClientFactory | None = None):
        # BrowserManager constructs backends with no args (the real
        # path). Tests pass a factory that returns a fake client.
        super().__init__(client_factory or (lambda cfg: make_client(cfg)))

    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        platform = get_platform()
        if platform not in ADSPOWER_VENDOR.platforms:
            raise UnsupportedPlatform(
                f'adspower is Windows-only; platform is {platform}'
            )
        return await super().start(browser_config)


# ── the vendor declaration (the whole AdsPower spec) ────────────────

#: Install descriptor, carried on the spec so the locator serves it
#: from the registry.
ADSPOWER_WIN_APP = ADSPOWER_SPEC

# The whole vendor declaration: one AdsPower login per machine (the
# API key of the signed-in account, stored encrypted by the generic
# machine-scope credential storage), profiles listed through the local
# API, and the spec-driven docs links for the UI's help links.
ADSPOWER_VENDOR = BrowserVendorSpec(
    key='adspower',
    display_name_en='AdsPower',
    display_name_zh='AdsPower',
    kind=KIND_ANTIDETECT,
    platforms=frozenset({'windows', 'mac', 'wsl'}),
    account_cardinality=CARDINALITY_SINGLE,
    credential_fields=(
        VendorCredentialField(
            'api_key', 'vendors.adspower.apiKey', secret=True
        ),
    ),
    lists_profiles=True,
    profiles_scope=PROFILES_LOGIN,
    client_kind=CLIENT_LOCAL_API,
    i18n_prefix='vendors.adspower.',
    # R20 / C3's task-agent guard (app/ai/vendor_guard.py): the
    # vendor's DISTINCTIVE dir names, as FACTS records them — the
    # data dir (local_api, the logs that carry the API key in plain
    # text, cwd_global with the kernels) appears as adspower_global
    # under %APPDATA%, ~/Library/Application Support and ~/.config,
    # so the bare NAME covers every OS; .ADSPOWER_GLOBAL is the
    # Windows profile cache (C:\.ADSPOWER_GLOBAL\cache — the cookie
    # DBs runaway 2 copied). No generic names.
    private_paths=(
        'adspower_global',
        '.ADSPOWER_GLOBAL',
    ),
    # The documented default is all a static tuple can say: the real
    # Local API port is RANDOM per launch (discovered from the
    # client's local_api file, only inside the adapter). The runtime
    # seam C3 offers (extra_vendor_ports) is fed from Ziniao account
    # rows today — wiring AdsPower's live port into it is a shared-
    # code change, routed to the coordinator; until then the dynamic
    # port is protected only as far as the guard's other rules go
    # (the CDP-range and connect-context rules).
    local_ports=(DEFAULT_API_PORT,),
    docs={
        'setup': VendorDocsLink(
            topic='adspowerSetup',
            zh='https://docs.vibe-sellers.com/adspower/',
            en='https://docs.vibe-sellers.com/en/adspower/',
        ),
        'errors': VendorDocsLink(
            topic='adspowerErrors',
            zh='https://docs.vibe-sellers.com/adspower/#常见提示--报错',
            en=(
                'https://docs.vibe-sellers.com/en/adspower/'
                '#common-messages--errors'
            ),
        ),
        'one_account': VendorDocsLink(
            topic='adspowerOneAccount',
            zh=(
                'https://docs.vibe-sellers.com/adspower/#一台机器只支持一个账号'
            ),
            en=(
                'https://docs.vibe-sellers.com/en/adspower/'
                '#one-adspower-login-per-machine'
            ),
        ),
    },
    client_factory=lambda cfg: AdsPowerClient(cfg),
    win_app=ADSPOWER_WIN_APP,
    client_path_setting='adspower_client_path',
)
