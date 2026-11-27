"""Yuehulian (悦互联) anti-detect browser backend — Windows only.

Yuehulian ships no local automation API (unlike ziniao), so we drive its
Electron client through a Node inspector + renderer DOM and inject a CDP
debug port into the launched kernel. From the CDP endpoint onward it is
identical to the ziniao / chrome backends (CDPMuxProxy → browser-use).

The engine (:class:`InjectCdpBackend`) holds the generic flow; this file
is the thin adapter plus the vendor spec (``YUEHULIAN_VENDOR``) the
generic routes / UI / manager read. The real client lives in
``yuehulian_client`` and is swapped for a fake in tests via the
``client_factory`` seam.
"""

from app.browser.adapters.yuehulian_client import RealYuehulianClient
from app.browser.base import BrowserSessionInfo
from app.browser.inject.client import InjectClient
from app.browser.inject.engine import ClientFactory, InjectCdpBackend
from app.browser.inject.errors import UnsupportedPlatform
from app.browser.vendors import (
    CARDINALITY_SINGLE,
    KIND_ANTIDETECT,
    BrowserVendorSpec,
    VendorCredentialField,
    VendorDocsLink,
)
from app.browser.win_app_locator import WinAppSpec
from app.browser.ziniao_utils import get_platform

# Needs the Windows 悦互联 client. WSL-on-Windows is fine (we drive the
# Windows side over 127.0.0.1); pure Linux / macOS cannot reach a client.
_SUPPORTED_PLATFORMS = frozenset({'windows', 'wsl'})


def make_client(browser_config: dict) -> InjectClient:
    """Build the real Windows-driven client (patched to a fake in tests)."""
    return RealYuehulianClient(browser_config)


# Install descriptor, carried on the spec so the locator serves it from
# the registry (win_app_locator's YUEHULIAN_SPEC attribute resolves here).
YUEHULIAN_WIN_APP = WinAppSpec(
    key='yuehulian',
    display_name='悦互联 (YueHuLian)',
    exe_names=(),  # main exe has a Chinese name (e.g. 用户端.exe)
    match_tokens=('yuehulian', '悦互联'),
    dir_names=('YueHuLian', '悦互联'),
)

# The whole vendor declaration: one GUI login per machine (team name /
# sub-account / password, stored encrypted by the generic machine-scope
# credential storage), stores listed through the client's GUI, and the
# spec-driven docs links for the UI's help links.
#
# private_paths (agent guard): the kernel browser profiles live in a
# dir the client NAMES ``YueHuLianData`` wherever it installs (its own
# code matches that name, not a path), and the install dir is
# ``YueHuLian`` / 悦互联 — both are declared as distinctive directory
# names so every spelling (C:\, /mnt/c, quoted, globbed) matches. No
# local_ports: the client has no local API (it is driven through the
# Node inspector, and 9229 is denied for agents by the guard itself).
YUEHULIAN_VENDOR = BrowserVendorSpec(
    key='yuehulian',
    display_name_en='Yuehulian',
    display_name_zh='悦互联',
    kind=KIND_ANTIDETECT,
    platforms=_SUPPORTED_PLATFORMS,
    account_cardinality=CARDINALITY_SINGLE,
    private_paths=('YueHuLianData', 'YueHuLian', '悦互联'),
    credential_fields=(
        VendorCredentialField('team_name', 'settings.yuehulianTeamName'),
        VendorCredentialField('subaccount', 'settings.yuehulianSubaccount'),
        VendorCredentialField(
            'password', 'settings.yuehulianPassword', secret=True
        ),
    ),
    lists_profiles=True,
    i18n_prefix='settings.yuehulian',
    docs={
        'errors': VendorDocsLink(
            topic='yuehulianErrors',
            zh='https://docs.vibe-sellers.com/yuehulian/#常见提示--报错',
            en=(
                'https://docs.vibe-sellers.com/en/yuehulian/'
                '#common-statuses--errors'
            ),
        ),
        'one_account': VendorDocsLink(
            topic='yuehulianOneAccount',
            zh=(
                'https://docs.vibe-sellers.com/yuehulian/'
                '#一台机器只支持一个账号'
            ),
            en=(
                'https://docs.vibe-sellers.com/en/yuehulian/'
                '#one-account-per-machine'
            ),
        ),
    },
    client_factory=make_client,
    win_app=YUEHULIAN_WIN_APP,
    client_path_setting='yuehulian_client_path',
)


class YuehulianBackend(InjectCdpBackend):
    """Route-B backend for the 悦互联 e-commerce browser (Windows)."""

    def __init__(self, client_factory: ClientFactory | None = None):
        # BrowserManager constructs backends with no args (the real
        # path). Tests pass a factory that returns a FakeYuehulianClient.
        super().__init__(client_factory or (lambda cfg: make_client(cfg)))

    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        platform = get_platform()
        if platform not in _SUPPORTED_PLATFORMS:
            raise UnsupportedPlatform(
                f'yuehulian is Windows-only; platform is {platform}'
            )
        return await super().start(browser_config)
