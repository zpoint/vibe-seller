"""In-place Windows updater.

Checks GitHub Releases for a newer ``VibeSeller-Setup.exe``, downloads
it, and runs it — Inno Setup upgrades in place (same ``AppId``). Kept
importable and headless so the tray menu AND CI exercise the same code;
the release source is overridable via ``VIBE_SELLER_RELEASES_URL`` (a
``file://`` or ``http://`` JSON in the GitHub "latest release" shape) so
the upgrade flow can be tested with a local mock — no real release.

What an upgrade replaces: the **whole bundle**. Each installer is
self-contained, so running a newer one overwrites the install dir's
pinned Python, MinGit, claude, uv, and dependency wheels, then rebuilds
the runtime venv from the new wheels. It is NOT a code-only patch — you
get exactly what that release pinned. User data under
``%LOCALAPPDATA%\\vibe-seller`` (DB, stores, logs) is left untouched.

Returns plain dicts (never raises to the caller) so the tray can show a
result and, on failure, a manual-download link.
"""

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

from packaging.version import InvalidVersion, Version

from app.version import get_version

logger = logging.getLogger(__name__)

REPO = 'zpoint/vibe-seller'
ASSET_NAME = 'VibeSeller-Setup.exe'
MANUAL_URL = f'https://github.com/{REPO}/releases/latest'
_DEFAULT_API = f'https://api.github.com/repos/{REPO}/releases/latest'


def _releases_url() -> str:
    """Latest-release source. Overridable for tests/CI (mock/local)."""
    return os.environ.get('VIBE_SELLER_RELEASES_URL') or _DEFAULT_API


def _open(url: str, timeout: float):
    # GitHub's API rejects requests without a User-Agent; file:// ignores it.
    req = urllib.request.Request(url, headers={'User-Agent': 'vibe-seller'})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310


def is_newer(latest: str, current: str) -> bool:
    """True if *latest* is a strictly newer version than *current*.

    PEP 440 aware (``packaging``), so a release (``0.1.0``) correctly
    beats a dev build of the same line (``0.1.0.dev3+g<sha>``). On an
    unparseable version, be conservative and report "not newer".
    """
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


def check_for_update() -> dict | None:
    """Return ``{'version', 'url'}`` for a newer release, else ``None``."""
    with _open(_releases_url(), timeout=15) as resp:
        rel = json.loads(resp.read().decode())
    tag = (rel.get('tag_name') or '').lstrip('v')
    asset = next(
        (a for a in rel.get('assets', []) if a.get('name') == ASSET_NAME),
        None,
    )
    if not tag or not asset or not asset.get('browser_download_url'):
        return None
    if not is_newer(tag, get_version()):
        return None
    return {'version': tag, 'url': asset['browser_download_url']}


def download_installer(url: str, dest: Path) -> Path:
    with _open(url, timeout=600) as resp, open(dest, 'wb') as f:
        shutil.copyfileobj(resp, f)
    return dest


def run_installer(path: Path, *, silent: bool = False) -> None:
    args = [str(path)]
    if silent:
        args += ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART']
    # Detached: the running server/tray is replaced by the upgrade.
    subprocess.Popen(args)  # noqa: S603


def upgrade(*, silent: bool = False) -> dict:
    """Full check → download → launch flow.

    Returns one of:
    - ``{'status': 'up-to-date', 'version': <current>}``
    - ``{'status': 'updating', 'version': <new>}``
    - ``{'status': 'error', 'error': <msg>, 'manual_url': <url>}``
    """
    try:
        upd = check_for_update()
        if not upd:
            return {'status': 'up-to-date', 'version': get_version()}
        dest = Path(tempfile.gettempdir()) / ASSET_NAME
        download_installer(upd['url'], dest)
        run_installer(dest, silent=silent)
        return {'status': 'updating', 'version': upd['version']}
    except Exception as exc:  # noqa: BLE001 — surfaced to the user, not raised
        logger.exception('Windows update failed')
        return {'status': 'error', 'error': str(exc), 'manual_url': MANUAL_URL}
