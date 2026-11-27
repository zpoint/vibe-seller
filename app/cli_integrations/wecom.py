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

import asyncio
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
    Permission,
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


#: One READ-ONLY call per capability WeCom grants the bot on its own.
#: WeCom grants each for 7 days (developer.work.weixin.qq.com/document/
#: path/101468), and ``auth show`` keeps saying "authorized" after they
#: expire — only a real call tells. Never a call that writes or sends.
PERMISSION_PROBES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ('doc', ('doc', 'search', '--keywords', 'vibe', '--limit', '1')),
    ('contact', ('contact', 'users', 'search', '--keywords', 'vibe')),
    ('message', ('message', 'aibot', 'sessions', 'list')),
    ('mail', ('mail', 'search', '--limit', '1')),
    ('disk', ('disk', 'files', 'list', '--limit', '1')),
    ('calendar', ('calendar', 'schedules', 'list')),
    ('meeting', ('meeting', 'list', '--limit', '1')),
    ('todo', ('todo', 'list', '--limit', '1')),
)
#: "authorization expired": the capability's grant ran out.
EXPIRED_ERRCODE = 850003
_RENEW_URL = re.compile(
    r'https://work\.weixin\.qq\.com/ai/aiHelper/authorizationList\?[^\s)"\\]+'
)


def _first_json(text: str) -> dict | None:
    """The response object the CLI printed (it may print other lines)."""
    start = text.find('{')
    if start < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None


def parse_permission(key: str, rc: int, out: str, err: str) -> Permission:
    """One probe's answer. The reply's data is never kept — only
    whether the call was allowed."""
    body = _first_json(out) or _first_json(err)
    code = body.get('errcode') if body else None
    if code == EXPIRED_ERRCODE:
        m = _RENEW_URL.search(str(body.get('help_message') or ''))
        return Permission(key, 'expired', m.group(0) if m else None)
    # Allowed: an answer with no error. Unreadable output says nothing.
    if rc == 0 and body is not None and code in (None, 0):
        return Permission(key, 'ok')
    return Permission(key, 'unknown')


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

    per_permission = True
    expired_hint = (
        ' (WeCom grants each permission for 7 days; an expired one '
        'answers 「…」使用权限已过期 / "authorization expired", errcode '
        f'{EXPIRED_ERRCODE})'
    )

    async def permissions(self, run) -> list[Permission]:
        async def probe(key: str, args: tuple[str, ...]) -> Permission:
            rc, out, err = await run(list(args))
            return parse_permission(key, rc, out, err)

        return list(
            await asyncio.gather(*(probe(k, a) for k, a in PERMISSION_PROBES))
        )

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
