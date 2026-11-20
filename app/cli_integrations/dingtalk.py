"""DingTalk (钉钉) — the official ``dws`` (DingTalk-Real-AI/dingtalk-workspace-cli).

Binary   release asset ``dws-<os>-<arch>.tar.gz|zip`` from GitHub, then the
         Gitee mirror; sha256 from the release's ``checksums.txt``
         (byte-identical on both).
Skills   the release's ``dws-skills.zip`` (``multi/dingtalk-*``, the same
         files the binary embeds), verified against the same checksums.
Login    ``auth login --device --no-browser`` — a device code + link the
         person opens on any machine; blocks while polling (restarting
         with a NEW code if one expires). The organisation's admin must
         have enabled CLI access in the DingTalk developer console.

Every run carries the switches the CLI documents for a managing host:
its config + key store under ``tools/dingtalk/state`` (``DWS_CONFIG_DIR``
/ ``DWS_KEYCHAIN_DIR``), no update check, no usage telemetry, and
``DINGTALK_DWS_AGENTCODE`` so a missing permission comes back as an
error instead of a browser opening on the server.
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
from app.platform import IS_MAC

REPO = 'DingTalk-Real-AI/dingtalk-workspace-cli'
GITHUB = f'https://github.com/{REPO}/releases/download'
GITEE = 'https://gitee.com/dingtalk-real-ai/dingtalk-workspace-cli/releases/download'
GITHUB_LATEST = f'https://api.github.com/repos/{REPO}/releases/latest'
GITEE_LATEST = f'https://gitee.com/api/v5/repos/{REPO}/releases/latest'
_OS = {'linux': 'linux', 'darwin': 'darwin', 'win32': 'windows'}
_ARCH = {'x64': 'amd64', 'arm64': 'arm64'}
_URL_LINE = re.compile(r'^https?://\S+$')
_CODE = re.compile(r'(?:授权码|authorization code)\s*[:：]\s*(\S+)', re.I)
_STABLE = re.compile(r'^\d+\.\d+\.\d+$')


# The org has not enabled CLI data access (``cli_not_enabled`` /
# ``user_forbidden``): the CLI names the org's primary admins and the
# console page where they turn it on.
_NO_ACCESS = ('CLI 数据访问权限', 'CLI数据访问权限', 'CLI data access')
# The CLI speaks Chinese, or English when ``DWS_LANG`` / ``LANG`` start
# with ``en`` (a server's locale): its own two labels for each line.
_ADMINS = re.compile(
    r'(?:组织主管理员|Organization super admins?)\s*[:：]\s*(.+)$', re.I
)
_CONSOLE = re.compile(
    r'(?:管理员操作入口|Admin settings)\s*[:：]\s*(https?://\S+)', re.I
)
_MESSAGE = re.compile(r'"message"\s*:\s*"(.*)"\s*,?\s*$')
# Where the CLI itself sends the admin (``GetDeveloperSettingsURL``):
# 「更多」→「基本信息」→「CLI设置」, whose 「其他渠道」 switch is the
# one Vibe Seller needs.
ADMIN_CONSOLE = 'https://open-dev.dingtalk.com/fe/old#/developerSettings'
# The page's notice for an org whose CLI access is not enabled.
NO_ACCESS = 'dingtalk_cli_access'


def _parse_login(line: str, view: LoginView) -> None:
    """``auth login --device`` output: code, then the link with the code
    filled in on its own line, then a manual-entry link — or, when the
    org has not enabled CLI access, its admins and console link."""
    stripped = line.strip()
    m = _CODE.search(stripped)
    if m:
        # A new code (the CLI restarts an expired flow) voids the link.
        view.user_code = m.group(1)
        view.url = None
        return
    if _URL_LINE.match(stripped):
        if view.url is None:
            view.url = stripped
        return
    if m := _CONSOLE.search(stripped):
        view.notice(NO_ACCESS).url = m.group(1)
        return
    if m := _ADMINS.search(stripped):
        view.notice(NO_ACCESS).detail = m.group(1).strip()
        return
    if any(k in stripped for k in _NO_ACCESS) and stripped.startswith('⚠'):
        notice = view.notice(NO_ACCESS)
        notice.url = notice.url or ADMIN_CONSOLE
        return
    if stripped.startswith('{') and '"error"' in stripped:
        try:
            view.message = json.loads(stripped)['error']['message']
        except (ValueError, KeyError, TypeError):
            view.message = stripped
    elif m := _MESSAGE.search(stripped):
        # The error envelope may be pretty-printed over several lines.
        view.message = m.group(1)


class DingTalk(CliIntegration):
    key = 'dingtalk'
    binary = 'dws'
    title = 'DingTalk (钉钉)'
    homepage = f'https://github.com/{REPO}'
    layout = SkillLayout(root='multi', prefix='dingtalk-')
    description = (
        'DingTalk (钉钉) through the official dws CLI: chats and robot '
        'messages, AI tables (AI表格), docs, drive, wiki, calendar, to-dos, '
        'mail, minutes, contacts, DING and more. Use for any 钉钉 task.'
    )
    forbidden_commands = (
        'auth login',
        'auth logout',
        'auth exchange',
        'auth migrate-keychain',
        'skill setup',
        'upgrade',
        'pat browser-policy',
    )

    def env(self, home: Path) -> dict[str, str]:
        state = paths.state_dir(self.key, home)
        env = {
            'DWS_CONFIG_DIR': str(state / 'config'),
            'DWS_KEYCHAIN_DIR': str(state / 'keychain'),
            'DWS_NO_UPDATE_CHECK': '1',
            'DO_NOT_TRACK': '1',
            'NO_COLOR': '1',
            # Host-owned permission prompts: exit 4 + one JSON line, never
            # a browser on the server polling for 10 minutes.
            'DINGTALK_DWS_AGENTCODE': 'vibe-seller',
        }
        if IS_MAC:
            # A file key, not the login Keychain: the server may run as a
            # LaunchAgent, and login + agent must use the same mode.
            env['DWS_DISABLE_KEYCHAIN'] = '1'
        return env

    def latest_version(self) -> str:
        errors = []
        try:
            data, _ = download.fetch_json([
                f'{download.NPMJS}/dingtalk-workspace-cli/latest'
            ])
            if _STABLE.match(str(data.get('version', ''))):
                return str(data['version'])
        except download.DownloadError as e:
            errors.append(str(e))
        for url in (GITHUB_LATEST, GITEE_LATEST):
            try:
                data, _ = download.fetch_json([url])
            except download.DownloadError as e:
                errors.append(str(e))
                continue
            version = download.strip_v(str(data.get('tag_name', '')))
            if _STABLE.match(version):
                return version
        raise download.DownloadError('; '.join(errors) or 'no dws release')

    def _hosts(self, version: str) -> tuple[str, str]:
        return (f'{GITHUB}/v{version}', f'{GITEE}/v{version}')

    def _checksums(self, version: str) -> dict[str, str]:
        text, _ = download.fetch([
            f'{h}/checksums.txt' for h in self._hosts(version)
        ])
        return download.parse_checksums(text.decode('utf-8', 'replace'))

    def _asset(self, version: str, name: str) -> tuple[tuple[str, ...], str]:
        sums = self._checksums(version)
        if name not in sums:
            raise download.DownloadError(f'{name} not in checksums.txt')
        return tuple(f'{h}/{name}' for h in self._hosts(version)), sums[name]

    def binary_asset(self, version: str, plat: str) -> BinaryAsset:
        osname, _, arch = plat.partition('-')
        if osname not in _OS or arch not in _ARCH:
            raise ValueError(f'dws has no build for {plat}')
        ext = 'zip' if osname == 'win32' else 'tar.gz'
        urls, sha = self._asset(
            version, f'dws-{_OS[osname]}-{_ARCH[arch]}.{ext}'
        )
        return BinaryAsset(
            urls=urls,
            verify=download.sha256_verifier(sha),
            member=lambda name: name.rsplit('/', 1)[-1] in ('dws', 'dws.exe'),
        )

    def fetch_skills(self, version: str) -> list[tuple[str, bytes]]:
        urls, sha = self._asset(version, 'dws-skills.zip')
        data, _ = download.fetch(urls, verify=download.sha256_verifier(sha))
        return list(download.iter_archive(data))

    async def auth_status(self, run) -> AuthStatus:
        # --readonly: the local snapshot only — no lock, refresh or network.
        rc, out, err = await run([
            'auth',
            'status',
            '--format',
            'json',
            '--readonly',
        ])
        try:
            data = json.loads(out)
        except ValueError:
            return AuthStatus(
                False, 'error', detail={'error': (err or out)[-300:]}
            )
        if data.get('authenticated'):
            who = ' · '.join(
                x for x in (data.get('user_name'), data.get('corp_name')) if x
            )
            return AuthStatus(
                True,
                'logged_in',
                identity=who or None,
                detail={'corp_id': data.get('corp_id')},
            )
        reason = data.get('reason')
        if reason == 'token_refresh_failed':
            return AuthStatus(False, 'expired')
        if reason:  # the CLI cannot tell (key store problem), not "logged out"
            return AuthStatus(False, 'error', detail={'error': reason})
        return AuthStatus(False, 'not_logged_in')

    def login_steps(self, status: AuthStatus) -> list[LoginStep]:
        return [
            LoginStep(
                (
                    'auth',
                    'login',
                    '--device',
                    '--no-browser',
                    '--recommend',
                    '--format',
                    'json',
                ),
                _parse_login,
                hint='dingtalk_auth',
                timeout_s=1000.0,
            )
        ]
