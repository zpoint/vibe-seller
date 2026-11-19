"""Feishu / Lark (飞书) — the official ``lark-cli`` (github.com/larksuite/cli).

Binary   release archive ``lark-cli-<ver>-<os>-<arch>.tar.gz|zip`` from
         GitHub releases, then the npmmirror binary mirror; sha256 from
         the release's ``checksums.txt`` (identical on both hosts).
Skills   the repo's ``skills/lark-*`` at tag ``v<ver>`` (exactly the
         installed version); when GitHub is unreachable, the archives
         Feishu hosts on open.feishu.cn (digest-verified, "current"
         rather than pinned).
Login    two steps: ``config init --new`` creates the CLI's Feishu app in
         the browser (blocks until done), then ``auth login --recommend
         --json`` authorises the user (device flow, NDJSON). Config,
         secrets (Linux) and logs live under ``tools/feishu/state``.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from app.cli_integrations import download, paths
from app.cli_integrations.base import (
    AuthStatus,
    BinaryAsset,
    CliIntegration,
    LoginStep,
    LoginView,
    SkillLayout,
)

GITHUB = 'https://github.com/larksuite/cli/releases/download'
MIRROR = 'https://registry.npmmirror.com/-/binary/lark-cli'
CODELOAD = 'https://codeload.github.com/larksuite/cli/tar.gz/refs/tags'
HOSTED_INDEX = (
    'https://open.feishu.cn/lark-cli/skills/regular/.well-known/'
    'agent-skills/index.json'
)
_OS = {'linux': 'linux', 'darwin': 'darwin', 'win32': 'windows'}
_ARCH = {'x64': 'amd64', 'arm64': 'arm64'}
_URL = re.compile(r'https?://\S+')
_USABLE = ('ready', 'needs_refresh')


def _parse_url(line: str, view: LoginView) -> None:
    """``config init --new``: a text QR, then the setup link (stderr)."""
    m = _URL.search(line)
    if m and view.url is None:
        view.url = m.group(0)
    _error(line, view)


def _parse_device(line: str, view: LoginView) -> None:
    """``auth login --json``: NDJSON events."""
    stripped = line.strip()
    if stripped.startswith('{'):
        try:
            event = json.loads(stripped)
        except ValueError:
            event = None
        if isinstance(event, dict):
            if event.get('event') == 'device_authorization':
                view.url = event.get('verification_uri_complete') or event.get(
                    'verification_uri'
                )
                view.user_code = event.get('user_code')
            elif event.get('event') == 'authorization_failed':
                view.message = event.get('message') or event.get('error')
            _error(stripped, view)
            return
    if view.url is None:
        m = _URL.search(stripped)
        if m:
            view.url = m.group(0)


def _error(line: str, view: LoginView) -> None:
    """The CLI's error envelope ``{"ok": false, "error": {...}}``."""
    stripped = line.strip()
    if not (stripped.startswith('{') and '"error"' in stripped):
        return
    try:
        err = json.loads(stripped)['error']
    except (ValueError, KeyError, TypeError):
        return
    if isinstance(err, dict):
        view.message = err.get('message') or str(err)
    elif isinstance(err, str):
        view.message = err


def _identity(data: dict) -> str | None:
    user = (data.get('identities') or {}).get('user') or {}
    for source in (user, data):
        for k in ('userName', 'user_name', 'name'):
            if isinstance(source.get(k), str) and source[k]:
                return source[k]
    return user.get('openId') or data.get('appId')


class Feishu(CliIntegration):
    key = 'feishu'
    binary = 'lark-cli'
    title = 'Feishu / Lark (飞书)'
    homepage = 'https://github.com/larksuite/cli'
    # skill-maker writes new skills into agent homes — Vibe Seller has
    # its own skill flow.
    layout = SkillLayout(
        root='skills', prefix='lark-', exclude=frozenset({'lark-skill-maker'})
    )
    description = (
        'Feishu / Lark (飞书) through the official lark-cli: IM messages '
        'and chats, docs, sheets, Base (多维表格), drive, wiki, calendar, '
        'meetings and minutes, tasks, mail, approvals, contacts and more. '
        'Use for any 飞书 task.'
    )
    forbidden_commands = (
        'config init',
        'auth login',
        'auth logout',
        'auth qrcode',
        'update',
        'config strict-mode',
        'config bind',
    )

    def env(self, home: Path) -> dict[str, str]:
        state = paths.state_dir(self.key, home)
        return {
            'LARKSUITE_CLI_CONFIG_DIR': str(state / 'config'),
            'LARKSUITE_CLI_DATA_DIR': str(state / 'data'),
            'LARKSUITE_CLI_LOG_DIR': str(state / 'logs'),
            # Vibe Seller installs + updates the CLI and its skills.
            'LARKSUITE_CLI_NO_UPDATE_NOTIFIER': '1',
            'LARKSUITE_CLI_NO_SKILLS_NOTIFIER': '1',
            'LARK_CLI_NO_PROXY_WARN': '1',
        }

    def latest_version(self) -> str:
        urls = [f'{r}/@larksuite/cli/latest' for r in download.npm_registries()]
        data, _ = download.fetch_json(urls)
        return str(data['version'])

    def _archive(self, version: str, plat: str) -> str:
        osname, _, arch = plat.partition('-')
        if osname not in _OS or arch not in _ARCH:
            raise ValueError(f'lark-cli has no build for {plat}')
        ext = 'zip' if osname == 'win32' else 'tar.gz'
        return f'lark-cli-{version}-{_OS[osname]}-{_ARCH[arch]}.{ext}'

    def binary_asset(self, version: str, plat: str) -> BinaryAsset:
        archive = self._archive(version, plat)
        hosts = (f'{GITHUB}/v{version}', f'{MIRROR}/v{version}')
        text, _ = download.fetch([f'{h}/checksums.txt' for h in hosts])
        sums = download.parse_checksums(text.decode('utf-8', 'replace'))
        if archive not in sums:
            raise download.DownloadError(f'{archive} not in checksums.txt')
        return BinaryAsset(
            urls=tuple(f'{h}/{archive}' for h in hosts),
            verify=download.sha256_verifier(sums[archive]),
            member=lambda name: name in ('lark-cli', 'lark-cli.exe'),
        )

    def skills_archive_urls(self, version: str) -> tuple[str, ...]:
        return (f'{CODELOAD}/v{version}',)

    def fetch_skills(self, version: str) -> list[tuple[str, bytes]]:
        try:
            return super().fetch_skills(version)
        except download.DownloadError:
            return self._hosted_skills()

    def _hosted_skills(self) -> list[tuple[str, bytes]]:
        """open.feishu.cn's Agent Skills index: one digest-verified
        archive per skill, files at the archive root."""
        index, _ = download.fetch_json([HOSTED_INDEX])
        base = HOSTED_INDEX.rsplit('/', 1)[0]
        files: list[tuple[str, bytes]] = []
        for entry in index.get('skills', []):
            name, url = entry.get('name'), entry.get('url', '')
            digest = str(entry.get('digest', ''))
            if not name or not digest.startswith('sha256:'):
                continue
            if url.startswith('./'):
                url = f'{base}/{url[2:]}'
            data, _ = download.fetch(
                [url], verify=download.sha256_verifier(digest[7:])
            )
            files += [
                (f'skills/{name}/{p}', b)
                for p, b in download.iter_archive(data)
            ]
        return files

    async def auth_status(self, run) -> AuthStatus:
        # Offline: reads the local config + token state.
        rc, out, err = await run(['auth', 'status'])
        if rc == 3 and 'not_configured' in err:
            return AuthStatus(False, 'not_configured')
        if rc != 0:
            return AuthStatus(False, 'error', detail={'error': err[-300:]})
        try:
            data = json.loads(out)
        except ValueError:
            return AuthStatus(False, 'error', detail={'error': out[-300:]})
        user = (data.get('identities') or {}).get('user') or {}
        bot = (data.get('identities') or {}).get('bot') or {}
        detail = {
            'app_id': data.get('appId'),
            'brand': data.get('brand'),
            'bot': bot.get('status'),
            'user': user.get('status'),
        }
        if user.get('status') in _USABLE:
            return AuthStatus(
                True, 'logged_in', identity=_identity(data), detail=detail
            )
        reason = (
            'expired'
            if user.get('status') == 'verify_failed'
            else ('not_logged_in')
        )
        return AuthStatus(False, reason, detail=detail)

    def login_steps(self, status: AuthStatus) -> list[LoginStep]:
        if status.usable:
            return []
        steps = []
        if status.reason == 'not_configured':
            steps.append(
                LoginStep(
                    ('config', 'init', '--new'),
                    _parse_url,
                    hint='feishu_app',
                    timeout_s=660.0,
                )
            )
        steps.append(
            LoginStep(
                ('auth', 'login', '--recommend', '--json'),
                _parse_device,
                hint='feishu_auth',
                timeout_s=660.0,
            )
        )
        return steps
