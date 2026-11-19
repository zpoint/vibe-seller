"""A ``CliIntegration`` backed by ``fake_workplace_cli.py``.

Its "release" is a tar.gz on disk served over ``file://`` (``fetch``
goes through urllib, which reads file URLs), so install → login →
enable → disable runs end to end with no network. ``make_fake`` builds
one per test under *root*; several can coexist (distinct keys) to check
that only the enabled ones reach an agent.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

from app.cli_integrations import download
from app.cli_integrations.base import (
    AuthStatus,
    BinaryAsset,
    CliIntegration,
    LoginStep,
    LoginView,
    SkillLayout,
)

FAKE_CLI = Path(__file__).with_name('fake_workplace_cli.py')

SKILLS = {
    'fake-shared/SKILL.md': (
        '---\nname: fake-shared\ndescription: "Auth, flags | output."\n---\n'
        '# Shared\nRun `fakecli auth login` only if the user asks.\n'
    ),
    'fake-calendar/SKILL.md': (
        '---\nname: fake-calendar\ndescription: Calendar events.\n---\n'
        'Read [shared](../fake-shared/SKILL.md) first, and '
        '[ref](./references/agenda.md). The fake-shared skill is prose.\n'
    ),
    'fake-calendar/references/agenda.md': (
        'See ../fake-shared/SKILL.md and ../fake-calendar/SKILL.md\n'
    ),
    'fake-internal/SKILL.md': '---\nname: fake-internal\n---\nhidden\n',
    'other/SKILL.md': '---\nname: other\n---\nnot ours\n',
    'fake-nodoc/README.md': 'no SKILL.md here\n',
}


def _tar(files: dict[str, bytes], execs: set[str] = frozenset()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tf:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            info.mode = 0o755 if name in execs else 0o644
            tf.addfile(info, io.BytesIO(body))
    return buf.getvalue()


def _script() -> bytes:
    return f'#!{sys.executable}\n'.encode() + FAKE_CLI.read_bytes()


def _parse_qr(line: str, view: LoginView) -> None:
    if 'scan the QR' in line:
        view.qr = True


def _parse_device(line: str, view: LoginView) -> None:
    for word in line.split():
        if word.startswith('https://'):
            view.url = word
    if 'code ' in line:
        view.user_code = line.rsplit('code ', 1)[1].strip()


class FakeIntegration(CliIntegration):
    binary = 'fakecli'
    layout = SkillLayout(
        root='skills', prefix='fake-', exclude=frozenset({'fake-internal'})
    )
    forbidden_commands = ('config init', 'auth login')

    def __init__(self, key: str, root: Path, *, qr: bool = False) -> None:
        self.key = key
        self.title = f'Fake {key}'
        self.description = f'Fake platform {key} via fakecli.'
        self.state = root / f'{key}-state'
        self.qr = qr
        self.version = '1.2.3'
        self.release = root / f'{key}-release.tar.gz'
        self.skills = root / f'{key}-skills.tar.gz'
        self.bad_checksum = False
        # Exit code of a login that DID sign in (lark-cli: scopes withheld).
        self.login_exit = 0
        self.long_line = False
        self.publish()

    def publish(self, version: str | None = None) -> None:
        """Write the release + skills archives of *version*."""
        self.version = version or self.version
        top = f'fakecli-{self.version}'
        self.release.write_bytes(
            _tar({f'{top}/fakecli': _script()}, {f'{top}/fakecli'})
        )
        repo = f'fakerepo-{self.version}'
        files = {f'{repo}/skills/{k}': v.encode() for k, v in SKILLS.items()}
        files[f'{repo}/skills/fake-calendar/SKILL.md'] += (
            f'\nversion {self.version}\n'.encode()
        )
        files[f'{repo}/README.md'] = b'repo readme\n'
        self.skills.write_bytes(_tar(files))

    def env(self, home: Path) -> dict[str, str]:
        env = {
            'FAKE_CLI_STATE': str(self.state),
            'FAKE_CLI_SWITCH': self.key,
            'FAKE_CLI_LOGIN_EXIT': str(self.login_exit),
        }
        if self.long_line:
            env['FAKE_CLI_LONG_LINE'] = '1'
        return env

    def latest_version(self) -> str:
        return self.version

    def binary_asset(self, version: str, plat: str) -> BinaryAsset:
        digest = hashlib.sha256(self.release.read_bytes()).hexdigest()
        if self.bad_checksum:
            digest = '0' * 64
        return BinaryAsset(
            urls=(
                (self.release.parent / 'missing.tar.gz').as_uri(),
                self.release.as_uri(),
            ),
            verify=download.sha256_verifier(digest),
            member=lambda name: name.endswith('/fakecli'),
        )

    def skills_archive_urls(self, version: str) -> tuple[str, ...]:
        return (self.skills.as_uri(),)

    async def auth_status(self, run) -> AuthStatus:
        rc, out, err = await run(['auth', 'status'])
        if rc != 0:
            return AuthStatus(False, 'error', detail={'error': err})
        data = json.loads(out)
        if not data['configured']:
            return AuthStatus(False, 'not_configured')
        if not data['logged_in']:
            return AuthStatus(False, 'not_logged_in')
        return AuthStatus(True, 'logged_in', identity=data['user'])

    def login_steps(self, status: AuthStatus) -> list[LoginStep]:
        if status.usable:
            return []
        steps = []
        if status.reason == 'not_configured':
            steps.append(
                LoginStep(('config', 'init'), _parse_device, hint='fake_app')
            )
        if self.qr:
            steps.append(
                LoginStep(
                    ('auth', 'login', '--qr', '{session}/qr.png'),
                    _parse_qr,
                    hint='scan',
                    qr_file='qr.png',
                )
            )
        else:
            steps.append(LoginStep(('auth', 'login'), _parse_device))
        return steps

    # ── test helpers ───────────────────────────────────

    def approve(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / 'approve').write_text('y')

    def deny(self) -> None:
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / 'deny').write_text('y')

    def sign_in(self, user: str = 'Alice') -> None:
        """Skip the login: configured and holding a token."""
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / 'config').write_text('app')
        (self.state / 'token').write_text(user)
