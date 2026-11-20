"""WeCom (企业微信) — the official ``wecom-cli`` (github.com/WecomTeam/wecom-cli).

Binary   npm per-platform package ``@wecom/cli-<plat>`` — a tarball with
         ``package/bin/wecom-cli[.exe]``, verified against the registry's
         ``dist.integrity`` (sha512); npmjs, then npmmirror. No GitHub
         releases exist.
Skills   the repo's ``skills/wecomcli-*`` on ``main``. The CLI's command
         set is served by WeCom at run time (not built into the binary)
         and the repo has no tags, so the current skills are the right
         ones for any recent binary (they require >= 1.2.1).
Login    ``auth init --noninteractive --no-browser --output-qrcode`` binds
         a WeCom smart bot by scanning: prints a scan page URL, writes a
         QR PNG, polls up to 5 min. Credentials land in
         ``WECOM_CLI_CONFIG_DIR`` (file key + AES-GCM file).
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

_PLATFORMS = (
    'linux-x64',
    'linux-arm64',
    'darwin-x64',
    'darwin-arm64',
    'win32-x64',
)
_URL = re.compile(r'https?://\S+')
SKILLS_MAIN = (
    'https://codeload.github.com/WecomTeam/wecom-cli/tar.gz/refs/heads/main'
)


def _parse_login(line: str, view: LoginView) -> None:
    """stdout of ``auth init``: the scan page link, then the QR saved."""
    stripped = line.strip()
    m = _URL.search(stripped)
    if m and 'work.weixin.qq.com' in m.group(0) and view.url is None:
        view.url = m.group(0)
    # The PNG is created empty and filled later: ready only once the CLI
    # says it saved it.
    if '二维码已保存到' in stripped or 'QR code saved' in stripped:
        view.qr = True
    if stripped.startswith('{') and '"error"' in stripped:
        try:
            view.message = json.loads(stripped)['error']['message']
        except (ValueError, KeyError, TypeError):
            view.message = stripped


class WeCom(CliIntegration):
    key = 'wecom'
    binary = 'wecom-cli'
    title = 'WeCom (企业微信)'
    homepage = 'https://github.com/WecomTeam/wecom-cli'
    layout = SkillLayout(root='skills', prefix='wecomcli-')
    description = (
        'WeCom / WeChat Work (企业微信) through the official wecom-cli: '
        'messages to chats the bot is in, mail, docs, sheets, smart '
        'sheets, smart pages, calendar, meetings, to-dos, drive files and '
        'contacts. Use for any 企业微信 task.'
    )
    forbidden_commands = ('auth init',)

    def env(self, home: Path) -> dict[str, str]:
        return {'WECOM_CLI_CONFIG_DIR': str(paths.state_dir(self.key, home))}

    def latest_version(self) -> str:
        data, _ = download.fetch_json([
            f'{r}/@wecom/cli/latest' for r in download.npm_registries()
        ])
        return str(data['version'])

    def binary_asset(self, version: str, plat: str) -> BinaryAsset:
        if plat not in _PLATFORMS:
            raise ValueError(f'wecom-cli has no build for {plat}')
        pkg = f'@wecom/cli-{plat}'
        regs = download.npm_registries()
        meta, _ = download.fetch_json([f'{r}/{pkg}/{version}' for r in regs])
        dist = meta['dist']
        urls = [dist['tarball']] + [
            f'{r}/{pkg}/-/cli-{plat}-{version}.tgz' for r in regs
        ]
        names = ('package/bin/wecom-cli', 'package/bin/wecom-cli.exe')
        return BinaryAsset(
            urls=tuple(dict.fromkeys(urls)),
            verify=download.sri_verifier(dist['integrity']),
            member=lambda name: name in names,
        )

    def skills_archive_urls(self, version: str) -> tuple[str, ...]:
        return (SKILLS_MAIN,)

    async def auth_status(self, run) -> AuthStatus:
        # Local check only: decrypts the stored credentials, no network.
        rc, out, err = await run(['auth', 'show'])
        if rc != 0:
            return AuthStatus(
                False, 'error', detail={'error': (err or out)[-300:]}
            )
        fields = {}
        for line in out.splitlines():
            k, sep, v = line.partition(':')
            if sep:
                fields[k.strip().lower()] = v.strip()
        state = fields.get('status', '')
        if state == 'authorized':
            bot = fields.get('bot id')
            return AuthStatus(
                True, 'logged_in', identity=f'Bot {bot}' if bot else None
            )
        if state == 'unauthorized':
            return AuthStatus(False, 'not_logged_in')
        return AuthStatus(False, 'error', detail={'error': out[-300:]})

    def login_steps(self, status: AuthStatus) -> list[LoginStep]:
        # The QR path must sit under the working dir (the CLI sandboxes
        # file paths) — the runner's cwd is the session dir.
        return [
            LoginStep(
                (
                    'auth',
                    'init',
                    '--noninteractive',
                    '--no-browser',
                    '--output-qrcode',
                    'qr.png',
                ),
                _parse_login,
                hint='scan_wecom',
                qr_file='qr.png',
                timeout_s=330.0,
            )
        ]
