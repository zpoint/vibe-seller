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
- how a login is driven without a terminal — ``login_steps``.
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


@dataclasses.dataclass
class AuthStatus:
    """Whether the CLI can act, and as whom."""

    usable: bool
    # logged_in | not_logged_in | not_configured | expired | error
    reason: str
    identity: str | None = None
    detail: dict = dataclasses.field(default_factory=dict)


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

    def env(self, home: Path) -> dict[str, str]:
        """Extra env for every run of this CLI — the server's AND the
        agent's: where its config + login live (``paths.state_dir``,
        under *home*), and its update / telemetry switches."""
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

    @abstractmethod
    async def auth_status(self, run: Runner) -> AuthStatus:
        """Whether the installed CLI can act (offline where possible)."""

    @abstractmethod
    def login_steps(self, status: AuthStatus) -> list[LoginStep]:
        """The steps that take *status* to a usable login."""
