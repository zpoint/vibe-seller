"""Google Workspace — the official ``gws`` (github.com/googleworkspace/cli).

Binary   ``google-workspace-cli-<target>.tar.gz|zip`` from the GitHub
         release ``v<ver>``; sha256 from the ``<asset>.sha256`` file the
         release publishes next to it. There is no mirror (the npm
         package is only an installer that downloads the same asset).
         Linux takes the static (musl) build, which runs on any distro.
Version  npm ``@googleworkspace/cli`` latest (npmjs, then npmmirror),
         published with each release.
Skills   the repo's ``skills/gws-*`` at tag ``v<ver>`` (exactly the
         installed version), limited to the services the sign-in asks
         for: ``shared`` + Drive, Gmail, Sheets, Docs, Calendar.
Setup    Google issues no shared sign-in key for this CLI: the person
         creates one ("OAuth client", type Desktop app) in their own
         Google Cloud project, once — the README's "Manual OAuth setup".
         The page takes the downloaded JSON (``apply_setup``) and stores
         it where gws reads it, ``<config>/client_secret.json``.
Login    ``auth login -s <services>``: gws prints a Google sign-in link
         and waits on ``http://localhost:<port>`` for the browser's
         redirect. A browser on another computer cannot reach that
         port; the page then relays the address it ended on
         (``LoginStep.paste_back``).

Everything lives under ``tools/gws/state``: the config dir by
``GOOGLE_WORKSPACE_CLI_CONFIG_DIR``, and the key that encrypts the
saved sign-in in a file there (``KEYRING_BACKEND=file``). Left to its
default, gws keeps that key in the OS keyring under one fixed entry per
OS user — the SAME entry a personal ``gws`` on this machine uses, so the
two would overwrite each other's key. The inherited vars that would
point gws at another identity are removed (``env_unset``), and the
``gcloud`` gws calls on its own gets a config of its own
(``CLOUDSDK_CONFIG``), not the person's gcloud sign-in.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from app.cli_integrations import download, paths
from app.cli_integrations.base import (
    AuthStatus,
    BinaryAsset,
    CliIntegration,
    LoginStep,
    LoginView,
    SetupError,
    SkillLayout,
)

RELEASES = 'https://github.com/googleworkspace/cli/releases/download'
CODELOAD = 'https://codeload.github.com/googleworkspace/cli/tar.gz/refs/tags'
ARCHIVE = 'https://github.com/googleworkspace/cli/archive/refs/tags'
NPM_PACKAGE = '@googleworkspace/cli'
_TARGETS = {
    'darwin-arm64': 'aarch64-apple-darwin',
    'darwin-x64': 'x86_64-apple-darwin',
    'linux-x64': 'x86_64-unknown-linux-musl',
    'linux-arm64': 'aarch64-unknown-linux-musl',
    'win32-x64': 'x86_64-pc-windows-msvc',
}
#: What the sign-in asks for, and so what the agent gets skills for.
#: Few enough scopes for a Google project that is still "testing".
SERVICES = ('drive', 'gmail', 'sheets', 'docs', 'calendar')
_REQUIRED_CLIENT_KEYS = (
    'client_id',
    'client_secret',
    'project_id',
    'auth_uri',
    'token_uri',
)
_GOOGLE_URL = re.compile(r'https://accounts\.google\.com/\S+')
_MAX_KEY_FILE = 64 * 1024


def _keep(name: str) -> bool:
    """``shared`` and the skills of the services signed in for."""
    return name == 'shared' or name.split('-', 1)[0] in SERVICES


def _parse_login(line: str, view: LoginView) -> None:
    """``auth login``: "Open this URL…", then the Google link."""
    m = _GOOGLE_URL.search(line)
    if m and view.url is None:
        view.url = m.group(0)
        redirect = parse_qs(urlsplit(view.url).query).get('redirect_uri')
        if redirect and redirect[0].startswith('http://'):
            view.callback = redirect[0]
        return
    stripped = line.strip()
    # gws reports a failure as ``error[<kind>]: <message>`` (stderr),
    # after the same text as a pretty-printed JSON document (stdout).
    if stripped.startswith('error[') and ']:' in stripped:
        view.message = stripped.split(']:', 1)[1].strip()


def _status_json(out: str) -> dict | None:
    """``auth status`` prints one JSON object (older builds put a
    ``Using keyring backend`` line before it)."""
    start = out.find('{')
    if start < 0:
        return None
    try:
        data = json.loads(out[start:])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class GoogleWorkspace(CliIntegration):
    key = 'gws'
    binary = 'gws'
    title = 'Google Workspace'
    homepage = 'https://github.com/googleworkspace/cli'
    setup = 'google_client'
    layout = SkillLayout(root='skills', prefix='gws-', keep=_keep)
    description = (
        'Google Workspace through the official gws CLI: Gmail (read, '
        'send, reply, triage), Drive files and uploads, Sheets, Docs and '
        'Calendar. Use for any Google Sheets / Drive / Gmail / Docs / '
        'Calendar task.'
    )
    forbidden_commands = (
        'auth login',
        'auth setup',
        'auth logout',
        'auth export',
        'generate-skills',
    )
    env_unset = (
        'GOOGLE_WORKSPACE_CLI_CLIENT_ID',
        'GOOGLE_WORKSPACE_CLI_CLIENT_SECRET',
        'GOOGLE_WORKSPACE_CLI_CREDENTIALS_FILE',
        'GOOGLE_WORKSPACE_PROJECT_ID',
    )
    network_targets = ('https://oauth2.googleapis.com/',)

    def env(self, home: Path) -> dict[str, str]:
        return {
            'GOOGLE_WORKSPACE_CLI_CONFIG_DIR': str(self._config(home)),
            'GOOGLE_WORKSPACE_CLI_KEYRING_BACKEND': 'file',
            # Set and empty: gws ignores it, and a ``.env`` file in the
            # working dir (gws loads one) cannot supply a token either.
            'GOOGLE_WORKSPACE_CLI_TOKEN': '',
            # ``auth status`` (and the sign-in's scope picker) run
            # ``gcloud services list`` with whatever gcloud is on PATH.
            # Its own empty config here: the person's gcloud sign-in is
            # never used, and nothing is written to their ``~/.config``.
            'CLOUDSDK_CONFIG': str(paths.state_dir(self.key, home) / 'gcloud'),
        }

    def _config(self, home: Path) -> Path:
        return paths.state_dir(self.key, home) / 'config'

    def latest_version(self) -> str:
        urls = [f'{r}/{NPM_PACKAGE}/latest' for r in download.npm_registries()]
        data, _ = download.fetch_json(urls)
        return str(data['version'])

    def _asset(self, plat: str) -> str:
        target = _TARGETS.get(plat)
        if target is None:
            raise ValueError(f'gws has no build for {plat}')
        ext = 'zip' if plat.startswith('win32') else 'tar.gz'
        return f'google-workspace-cli-{target}.{ext}'

    def binary_asset(self, version: str, plat: str) -> BinaryAsset:
        asset = self._asset(plat)
        base = f'{RELEASES}/v{version}'
        text, _ = download.fetch([f'{base}/{asset}.sha256'])
        sums = download.parse_checksums(text.decode('utf-8', 'replace'))
        if asset not in sums:
            raise download.DownloadError(f'{asset} not in {asset}.sha256')
        return BinaryAsset(
            urls=(f'{base}/{asset}',),
            verify=download.sha256_verifier(sums[asset]),
            member=lambda name: name.rsplit('/', 1)[-1] in ('gws', 'gws.exe'),
        )

    def skills_archive_urls(self, version: str) -> tuple[str, ...]:
        return (
            f'{CODELOAD}/v{version}',
            f'{ARCHIVE}/v{version}.tar.gz',
        )

    async def auth_status(self, run) -> AuthStatus:
        # Reads the local files, then (when signed in) asks Google
        # whether the sign-in still works and as whom.
        rc, out, err = await run(['auth', 'status'])
        data = _status_json(out) if rc == 0 else None
        if data is None:
            return AuthStatus(
                False, 'error', detail={'error': (err or out)[-300:]}
            )
        signed_in = bool(
            data.get('encrypted_credentials_exists')
            or data.get('plain_credentials_exists')
        )
        detail = {
            'project_id': data.get('project_id'),
            'client_configured': bool(data.get('client_config_exists')),
        }
        if signed_in and data.get('encryption_valid') is not False:
            if data.get('token_valid') is False:
                detail['error'] = data.get('token_error')
                return AuthStatus(False, 'expired', detail=detail)
            # ``token_valid`` absent: Google did not answer (offline).
            # The saved sign-in is there; whether it still works shows
            # on the first real call.
            detail['checked'] = data.get('token_valid') is True
            return AuthStatus(
                True, 'logged_in', identity=data.get('user'), detail=detail
            )
        if signed_in:
            # Saved with a key this machine no longer has.
            return AuthStatus(False, 'expired', detail=detail)
        if not data.get('client_config_exists'):
            return AuthStatus(False, 'not_configured', detail=detail)
        return AuthStatus(False, 'not_logged_in', detail=detail)

    def needs_setup(self, status: AuthStatus) -> bool:
        return status.reason == 'not_configured'

    def apply_setup(self, payload: str, home: Path) -> None:
        """Store the sign-in key file the person downloaded from Google
        Cloud, as ``client_secret.json`` (only its ``installed`` part —
        the format gws reads)."""
        if len(payload) > _MAX_KEY_FILE:
            raise SetupError('google_client_invalid', 'file too large')
        try:
            data = json.loads(payload)
        except ValueError as e:
            raise SetupError('google_client_invalid', f'not JSON: {e}') from e
        if not isinstance(data, dict):
            raise SetupError('google_client_invalid', 'not a JSON object')
        installed = data.get('installed')
        if not isinstance(installed, dict):
            if isinstance(data.get('web'), dict):
                # A "Web application" key: Google refuses its redirect
                # to this computer (redirect_uri_mismatch).
                raise SetupError(
                    'google_client_web', 'a Web application client'
                )
            raise SetupError(
                'google_client_invalid', 'no "installed" client in the file'
            )
        missing = [
            k
            for k in _REQUIRED_CLIENT_KEYS
            if not isinstance(installed.get(k), str) or not installed[k]
        ]
        if missing:
            raise SetupError(
                'google_client_invalid', f'missing {", ".join(missing)}'
            )
        config = self._config(home)
        config.mkdir(parents=True, exist_ok=True)
        target = config / 'client_secret.json'
        tmp = config / '.client_secret.json.tmp'
        tmp.write_text(json.dumps({'installed': installed}), encoding='utf-8')
        if os.name != 'nt':
            os.chmod(tmp, 0o600)
        os.replace(tmp, target)

    def login_steps(self, status: AuthStatus) -> list[LoginStep]:
        return [
            LoginStep(
                ('auth', 'login', '-s', ','.join(SERVICES)),
                _parse_login,
                hint='gws_auth',
                paste_back=True,
                timeout_s=900.0,
            )
        ]
