"""Platform-neutral pieces shared by the AdsPower win/mac layers.

The Windows (:mod:`adspower_win`) and macOS (:mod:`adspower_mac`)
layers never import each other; what is true on BOTH platforms — the
kernel-process record, the user-data-dir attribution regex and the
``local_api`` URL→port parse — lives here (REVIEW-2 nit).
"""

from __future__ import annotations

import ntpath
import re

#: The Local API's default port (docs). Only used when free — the real
#: port is discovered from the ``local_api`` file on every connect.
DEFAULT_API_PORT = 50325

# ``...cache/<profile_id>_<inviteCode>[_flower]`` — the profile id is a
# path segment PREFIX, so ``<id>_`` matches both kernel spellings and
# never a longer id that merely starts with this one.
USER_DATA_DIR_RE = re.compile(
    r'--user-data-dir=(?P<dir>\S*[/\\]cache[/\\](?P<id>[^\s/\\]+))',
    re.IGNORECASE,
)


class KernelProcess:
    """One store-browser kernel process attributed to a profile."""

    def __init__(self, pid: int, name: str, user_data_dir: str):
        self.pid = pid
        #: Image name lowercased (platform-specific spelling).
        self.name = name.lower()
        #: The ``--user-data-dir`` value (carries the profile id).
        self.user_data_dir = user_data_dir

    @property
    def is_firefox(self) -> bool:
        """FlowerBrowser = the Firefox kernel (no CDP, marionette).

        The user-data-dir's ``_flower`` suffix is the cross-platform
        marker (vendor source: firefox appends it); the image name is a
        second signal for platforms where it is known.
        """
        return 'flower' in self.name or self.user_data_dir.rstrip(
            '/\\'
        ).endswith('_flower')

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f'KernelProcess({self.pid}, {self.name})'


def parse_local_api_port_text(text: str) -> int | None:
    """The port from a ``local_api`` file's URL text (``:NNNNN`` end).

    Only the port is taken — ``local.adspower.com`` is not depended on
    (it resolves through public DNS).
    """
    match = re.search(r':(\d+)\s*/?\s*$', (text or '').strip())
    return int(match.group(1)) if match else None


# ── R16: the not-authenticated wordings, ONE text per meaning ──────
# The exception message (what a failed task's banner shows) and the
# ``vendors.adspower.*`` i18n override (what the store form shows)
# must be the SAME text; the i18n test pins the JSON equal to these
# constants (the R14 two-texts lesson).

#: The sign-in-needed text (key stored and sent but not recognized,
#: or the headless recovery exhausted): LoginRequired, zh.
SIGN_IN_NEEDED_ZH = (
    'AdsPower 客户端需要登录：请打开 AdsPower，用和 API Key 同一个'
    '账号登录，按提示完成验证（如邮箱/短信验证码），然后点刷新或'
    '重试任务。'
)
#: The same, en.
SIGN_IN_NEEDED_EN = (
    'The AdsPower client needs you to sign in: open AdsPower, sign in '
    'with the account the API key belongs to, finish the verification '
    'it asks for (an email/SMS code), then refresh or retry the task.'
)
#: The no-key advice (keyless not-authenticated shape + the NoAccount
#: override), zh.
NO_KEY_ADVICE_ZH = (
    'AdsPower 还没有配置 API Key：在 AdsPower 左侧「API & MCP」点'
    '生成 Key，复制填到 Vibe Seller。'
)
#: The same, en.
NO_KEY_ADVICE_EN = (
    'AdsPower has no API key yet: open API & MCP in AdsPower, '
    'generate a key and paste it into Vibe Seller.'
)

#: The FlowerBrowser refusal text (zh / en in one message, like the
#: R16 constants — the i18n override carries the same zh wording).
UNSUPPORTED_KERNEL_TEXT = (
    '这个环境用的是 FlowerBrowser（火狐内核），Vibe Seller 只能操作'
    'SunBrowser（Chrome 内核）。请在 AdsPower 新建一个「浏览器」选'
    'SunBrowser 的环境，再把这家店铺改绑到新环境。/ This profile uses '
    'the FlowerBrowser (Firefox) kernel; only SunBrowser (Chrome-'
    'kernel) profiles can be driven. Create a new profile with 浏览器 '
    'set to SunBrowser in AdsPower, then re-bind this store to the new '
    'profile.'
)


def start_names_firefox(payload) -> bool:
    """Whether a code-0 start response names a FlowerBrowser kernel.

    The webdriver path carries the kernel (both shapes recorded
    live): ``…\\chrome_<N>\\chromedriver.exe`` is SunBrowser;
    ``…\\webdriver_flower_<N>\\driver.exe`` (flower_100) and
    ``…\\flower_<N>\\geckodriver.exe`` (flower_159, today's
    default) are FlowerBrowser — whose BiDi endpoint /json/version
    404s (a generic CdpUnreachable). Decided here so the refusal
    is typed, before the engine fronts the endpoint.
    """
    data = (payload or {}).get('data') or {}
    webdriver = str(data.get('webdriver') or '')
    if not webdriver:
        return False
    # Normalize to forward slashes so both \ and / paths work.
    parts = [
        p
        for p in ntpath.splitdrive(webdriver)[1].replace('\\', '/').split('/')
        if p
    ]
    if not parts:
        return False
    # A directory component naming a flower kernel (any version):
    # flower_100, webdriver_flower_100, flower_159, …
    for part in parts[:-1]:
        if re.fullmatch(r'(webdriver_)?flower_\d+', part, re.IGNORECASE):
            return True
    # Or the driver file itself is a geckodriver.
    return parts[-1].lower().startswith('geckodriver')


def kernels_for(kernels: list[KernelProcess], ref: str) -> list[KernelProcess]:
    """The kernel processes whose user-data-dir names ``ref``."""
    prefix = f'{ref}_'
    out = []
    for k in kernels:
        segment = k.user_data_dir.replace('/', '\\').rstrip('\\')
        if segment.rsplit('\\', 1)[-1].startswith(prefix):
            out.append(k)
    return out
