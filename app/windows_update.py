"""In-place Windows updater.

Checks GitHub Releases for a newer ``VibeSeller-Setup.exe``, downloads
it, and runs it — Inno Setup upgrades in place (same ``AppId``). Kept
importable and headless so the tray menu AND CI exercise the same code;
the release source is overridable via ``VIBE_SELLER_RELEASES_URL`` (a
``file://`` or ``http://`` JSON in the GitHub "latest release" shape) so
the upgrade flow can be tested with a local mock — no real release.

What an upgrade replaces: the **whole bundle**. Each installer is
self-contained, so running a newer one overwrites the install dir's
pinned Python, Git for Windows, claude, uv, and dependency wheels, then rebuilds
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
import sys
import tempfile
import threading
import time
import urllib.request

from packaging.version import InvalidVersion, Version

from app.version import get_version

logger = logging.getLogger(__name__)

REPO = 'zpoint/vibe-seller'
ASSET_NAME = 'VibeSeller-Setup.exe'
MANUAL_URL = f'https://github.com/{REPO}/releases/latest'
_DEFAULT_API = f'https://api.github.com/repos/{REPO}/releases/latest'

# One upgrade at a time. The tray spawns a daemon thread per "Check for
# updates" click; without this, a double-click launches TWO installers
# that race on the install dir. Non-blocking acquire → the second call
# reports 'in-progress' instead of starting a rival installer.
_upgrade_lock = threading.Lock()


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


def _is_url(s: str) -> bool:
    return s.startswith(('http://', 'https://', 'file://'))


def _fetch_release() -> dict:
    """Read the latest-release JSON from the configured source.

    Accepts an http(s)/file:// URL or a plain local filesystem path
    (the latter is what tests/CI use for a mock release — avoids
    fragile file:// URI construction across shells)."""
    src = _releases_url()
    if not _is_url(src):
        return json.loads(Path(src).read_text(encoding='utf-8'))
    with _open(src, timeout=15) as resp:
        return json.loads(resp.read().decode())


def check_for_update() -> dict | None:
    """Return ``{'version', 'url'}`` for a newer release, else ``None``."""
    rel = _fetch_release()
    tag = (rel.get('tag_name') or '').lstrip('v')
    assets = rel.get('assets') or []
    if isinstance(assets, dict):  # PowerShell may unwrap a 1-item array
        assets = [assets]
    asset = next(
        (
            a
            for a in assets
            if isinstance(a, dict) and a.get('name') == ASSET_NAME
        ),
        None,
    )
    if not tag or not asset or not asset.get('browser_download_url'):
        return None
    if not is_newer(tag, get_version()):
        return None
    return {'version': tag, 'url': asset['browser_download_url']}


def download_installer(url: str, dest: Path) -> Path:
    # Plain local path (tests/CI) → copy; http(s)/file:// → fetch.
    if not _is_url(url):
        shutil.copyfile(url, dest)
        return dest
    with _open(url, timeout=600) as resp, open(dest, 'wb') as f:
        shutil.copyfileobj(resp, f)
    return dest


def run_installer(path: Path, *, silent: bool = False) -> None:
    """Launch the downloaded installer, fully detached from this process.

    This code runs INSIDE the tray's ``pythonw.exe``, which lives at
    ``{app}\\.venv\\Scripts\\pythonw.exe`` — the very file the installer
    deletes and rebuilds. So the launch must be truly detached
    (``DETACHED_PROCESS`` + a new process group) so the installer
    outlives the tray quitting itself right after (see the tray's
    ``_on_check_updates``); a plain ``Popen`` child could be torn down
    with its parent and would keep the install dir locked.

    Retry on ``WinError 32`` (PermissionError): a freshly-written exe can
    be momentarily locked by an AV scan or a racing download.
    """
    args = [str(path)]
    if silent:
        args += ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART']
    creationflags = 0
    if sys.platform == 'win32':
        creationflags = (
            subprocess.DETACHED_PROCESS  # no console; survives our exit
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    last_exc: OSError | None = None
    for attempt in range(3):
        try:
            subprocess.Popen(  # noqa: S603
                args, creationflags=creationflags, close_fds=True
            )
            return
        except PermissionError as exc:  # WinError 32 — briefly locked
            last_exc = exc
            logger.warning(
                'installer launch locked (attempt %d): %s', attempt + 1, exc
            )
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    if last_exc is not None:
        raise last_exc


def upgrade(*, silent: bool = False) -> dict:
    """Full check → download → launch flow.

    Returns one of:
    - ``{'status': 'up-to-date', 'version': <current>}``
    - ``{'status': 'updating', 'version': <new>}``
    - ``{'status': 'in-progress'}`` — another upgrade is already running
    - ``{'status': 'error', 'error': <msg>, 'manual_url': <url>}``
    """
    if not _upgrade_lock.acquire(blocking=False):
        return {'status': 'in-progress', 'version': get_version()}
    try:
        upd = check_for_update()
        if not upd:
            return {'status': 'up-to-date', 'version': get_version()}
        # Unique dir per run: two concurrent downloads writing the same
        # fixed %TEMP%\VibeSeller-Setup.exe collide and the launch then
        # fails with WinError 32 (observed in the field).
        dest = Path(tempfile.mkdtemp(prefix='vibe-seller-upd-')) / ASSET_NAME
        download_installer(upd['url'], dest)
        run_installer(dest, silent=silent)
        return {'status': 'updating', 'version': upd['version']}
    except Exception as exc:  # noqa: BLE001 — surfaced to the user, not raised
        logger.exception('Windows update failed')
        return {'status': 'error', 'error': str(exc), 'manual_url': MANUAL_URL}
    finally:
        _upgrade_lock.release()
