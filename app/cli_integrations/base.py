"""What one workplace CLI integration declares.

The framework (``manager``, ``skills``, ``login``) is the same for every
platform; a platform module only answers these questions:

- which version is current, and where its binary for this OS/arch is
  (candidate URLs + how to verify the bytes) — ``latest_version`` /
  ``binary_asset``;
- where the official agent skills of exactly that version are —
  ``skills_archive_urls`` + ``layout``;
- whether the installed CLI is usable and as whom —
  ``auth_status`` (it runs the managed binary through *run*);
- how a login is driven without a terminal — ``login_steps``;
- whether that login first needs something only the person can make
  (Google's sign-in key) — ``setup`` / ``needs_setup`` /
  ``apply_setup``;
- what of the inherited environment would point it at someone else's
  identity (``env_unset``), and which hosts its own runs must reach
  (``network_targets``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
import dataclasses
from pathlib import Path
import platform as _platform
import sys

from app.cli_integrations import download

# ``run(args)`` → (returncode, stdout, stderr) of the MANAGED binary.
Runner = Callable[[list[str]], Awaitable[tuple[int, str, str]]]


def host_platform() -> str:
    """``linux-x64`` / ``darwin-arm64`` / ``win32-x64`` / ..."""
    machine = _platform.machine().lower()
    arch = {
        'x86_64': 'x64',
        'amd64': 'x64',
        'aarch64': 'arm64',
        'arm64': 'arm64',
    }.get(machine, machine)
    osname = 'win32' if sys.platform == 'win32' else sys.platform
    if osname.startswith('linux'):
        osname = 'linux'
    return f'{osname}-{arch}'


@dataclasses.dataclass(frozen=True)
class BinaryAsset:
    """Where the binary for one version + platform comes from."""

    urls: tuple[str, ...]
    # Raises download.IntegrityError when the downloaded bytes are wrong.
    verify: Callable[[bytes], None] = dataclasses.field(compare=False)
    # Picks the executable out of the archive by its path inside it.
    member: Callable[[str], bool] = dataclasses.field(compare=False)


@dataclasses.dataclass(frozen=True)
class SkillLayout:
    """Where the skills sit in the source archive and how they are named.

    ``root`` is the path of the skills dir inside the archive (after the
    archive's single top-level dir, if any, is dropped). Each child dir
    named ``<prefix><name>`` becomes ``<name>/`` under the umbrella;
    ``exclude`` lists source dir names left out.
    """

    root: str
    prefix: str
    exclude: frozenset[str] = frozenset()
    # Keeps only the sub-skills it accepts (by name, prefix dropped):
    # a platform whose sign-in covers a few of its services documents
    # only those. None keeps every one.
    keep: Callable[[str], bool] | None = dataclasses.field(
        default=None, compare=False
    )


@dataclasses.dataclass
class AuthStatus:
    """Whether the CLI can act, and as whom."""

    usable: bool
    # logged_in | not_logged_in | not_configured | expired | error
    reason: str
    identity: str | None = None
    detail: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class Permission:
    """One capability a platform grants, and expires, on its own.

    WeCom grants its bot each capability (docs, contacts, messages…)
    separately, for 7 days each; a sign-in that looks fine can still
    have most of them expired. *key* is the page's i18n key, *state*
    is ``ok`` | ``expired`` | ``unknown`` (the check itself failed), and
    *renew_url* is where the bot's creator renews an expired one.
    """

    key: str
    state: str
    renew_url: str | None = None


@dataclasses.dataclass
class Notice:
    """Something the person must know about how a sign-in ended.

    *key* is the page's i18n key (a scope the org admin withheld, a
    sign-in that cannot renew itself, org CLI access not enabled…);
    *detail* fills it (the scopes, the expiry, the admins' names) and
    *url* is where to act on it.
    """

    key: str
    detail: str | None = None
    url: str | None = None


@dataclasses.dataclass
class LoginView:
    """What the Settings page shows while a login runs."""

    # starting | waiting | succeeded | failed | cancelled
    state: str = 'starting'
    url: str | None = None
    user_code: str | None = None
    qr: bool = False
    message: str | None = None
    step: int = 0
    steps: int = 0
    # The running step's LoginStep.hint — the page's i18n key for what
    # the person has to do (scan in the app, approve in the browser…).
    hint: str | None = None
    # What the person must know about the outcome — one sign-in can end
    # with several (scopes withheld AND no automatic renewal). Parsers
    # fill these; raw CLI output never is the page's message.
    notices: list[Notice] = dataclasses.field(default_factory=list)
    # Why the runner itself ended it (``timeout``) or what failed after
    # the sign-in (``enable_failed``) — the page's i18n key, with what to
    # do next; ``message`` keeps the technical line.
    error: str | None = None
    # Signed in, and what follows the sign-in (enabling the platform) is
    # still running. The outcome is final only once this is False.
    finishing: bool = False
    # The running step's LoginStep.paste_back: the page offers a box
    # for the address the browser ended on, for when that browser is
    # not on this computer. ``callback`` is the local address the CLI
    # waits on (its parser reads it from the sign-in link).
    paste_back: bool = False
    callback: str | None = None

    def notice(self, key: str) -> Notice:
        """The notice *key*, added on first use (a CLI prints its parts
        — the warning, the admins, the console link — on separate
        lines)."""
        for n in self.notices:
            if n.key == key:
                return n
        n = Notice(key)
        self.notices.append(n)
        return n

    def public(self) -> dict:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class LoginStep:
    """One CLI invocation of a login, run without a terminal.

    The runner feeds every output line (stdout and stderr) to *parse*,
    which fills the view (``url`` / ``user_code`` / ``message``); the
    step ends when the process exits — 0 moves on, anything else fails
    the login. *qr_file* names a file (relative to the session dir) the
    CLI writes a QR PNG into; the runner shows it once it appears.
    ``argv`` may contain ``{session}`` — replaced with the session dir.
    """

    argv: tuple[str, ...]
    parse: Callable[[str, LoginView], None] = dataclasses.field(compare=False)
    hint: str = 'open_url'
    qr_file: str | None = None
    timeout_s: float = 600.0
    # The approval comes back to the CLI as a redirect to a local
    # address (``LoginView.callback``) — reached by itself when the
    # browser runs on this computer, relayed from a pasted address
    # (``LoginSession.paste_back``) when it does not.
    paste_back: bool = False


class SetupError(ValueError):
    """What the person provided for ``apply_setup`` is not usable.

    *code* is the page's sentence (``integrations.cli.error.<code>``)
    saying what to fix; the message is the technical detail.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class CliIntegration(ABC):
    """One platform. Subclasses set the class attributes."""

    key: str  # url/dir slug: 'wecom', 'feishu', 'dingtalk'
    binary: str  # executable name without .exe
    title: str  # shown in Settings and in the umbrella skill
    layout: SkillLayout
    # Frontmatter description of the umbrella skill (agent's skill index).
    description: str
    # Subcommands the AGENT must never run — login / setup / self-update /
    # skill installers (the umbrella's rules name them; the official
    # skills tell an agent to run several of these).
    forbidden_commands: tuple[str, ...] = ()
    homepage: str = ''
    # Inherited env vars that would make this CLI act as someone else
    # (a personal token, another sign-in key): removed from every run,
    # the server's and the agent's — an empty value is not enough for
    # every CLI.
    env_unset: tuple[str, ...] = ()
    # Hosts the server's own runs (status, sign-in) must reach. Set:
    # they go over the network path that reaches them
    # (``system_proxy.checked_download_env``), not whatever the env
    # happens to hold.
    network_targets: tuple[str, ...] = ()
    # A one-time setup the person does before the first sign-in, done
    # on the page as a form of this kind (its i18n key); None: none.
    setup: str | None = None

    def env(self, home: Path) -> dict[str, str]:
        """Extra env for every run of this CLI — the server's AND the
        agent's: where its config + login live (``paths.state_dir``,
        under *home*), and its own switches (e.g. no update check)."""
        return {}

    @abstractmethod
    def latest_version(self) -> str:
        """The current release version (blocking: network)."""

    @abstractmethod
    def binary_asset(self, version: str, plat: str) -> BinaryAsset:
        """Binary source for *version* on *plat* (blocking: may fetch
        the published checksums). Raises ``ValueError`` for a platform
        the CLI does not ship."""

    def skills_archive_urls(self, version: str) -> tuple[str, ...]:
        """Candidate URLs of an archive holding *version*'s skills."""
        return ()

    def fetch_skills(self, version: str) -> list[tuple[str, bytes]]:
        """``(path, bytes)`` of every file of *version*'s skill source
        (blocking: network). Default: the first archive of
        ``skills_archive_urls`` that downloads."""
        data, _ = download.fetch(self.skills_archive_urls(version))
        return list(download.iter_archive(data))

    def needs_setup(self, status: AuthStatus) -> bool:
        """The sign-in cannot start before ``apply_setup``."""
        return False

    def apply_setup(self, payload: str, home: Path) -> None:
        """Store what the person provided for ``setup`` (blocking).
        Raises ``SetupError`` when it is not usable."""
        raise SetupError('unsupported', f'{self.title} needs no setup')

    @abstractmethod
    async def auth_status(self, run: Runner) -> AuthStatus:
        """Whether the installed CLI can act (offline where possible)."""

    #: The platform grants (and expires) capabilities one by one, so the
    #: page asks ``permissions`` for each one's state.
    per_permission = False
    #: How the CLI says a permission expired, for the agent's rules
    #: (``skills.umbrella_md``) — e.g. `` (…: "authorization expired")``.
    expired_hint = ''

    async def permissions(self, run: Runner) -> list[Permission]:
        """Each capability's grant, checked online with one read-only
        call apiece. Only for ``per_permission`` platforms."""
        return []

    @abstractmethod
    def login_steps(self, status: AuthStatus) -> list[LoginStep]:
        """The steps of a sign-in that starts from *status*.

        Signed in already is no exception: "Sign in again" (another
        account, or a fresh sign-in) runs exactly these, and every one
        of the official CLIs signs in again when asked. Only what the
        status says is set up already (Feishu's app) is skipped.
        """
