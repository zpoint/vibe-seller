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

Channels (``app/release_channel.py``): a stable install only reads
``releases/latest``, which GitHub never points at a prerelease, so it
can't be offered a nightly. A nightly install also reads the rolling
``nightly`` prerelease (its ``name`` is the exact nightly version) and
takes whichever of the two is newer.

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

from app.release_channel import (
    NIGHTLY_TAG,
    best_upgrade,
    channel_of,
    is_newer as is_newer,
)
from app.version import get_version

logger = logging.getLogger(__name__)

REPO = 'zpoint/vibe-seller'
ASSET_NAME = 'VibeSeller-Setup.exe'
MANUAL_URL = f'https://github.com/{REPO}/releases/latest'
# Human-facing "what's new" page listing every release (GitHub renders
# it cumulatively), shown in the tray's update dialog.
RELEASES_PAGE = f'https://github.com/{REPO}/releases'
_DEFAULT_API = f'https://api.github.com/repos/{REPO}/releases/latest'
_NIGHTLY_API = (
    f'https://api.github.com/repos/{REPO}/releases/tags/{NIGHTLY_TAG}'
)
NIGHTLY_PAGE = f'https://github.com/{REPO}/releases/tag/{NIGHTLY_TAG}'
_RELEASES_LIST_API = f'https://api.github.com/repos/{REPO}/releases'

# One upgrade at a time. The tray spawns a daemon thread per "Check for
# updates" click; without this, a double-click launches TWO installers
# that race on the install dir. Non-blocking acquire → the second call
# reports 'in-progress' instead of starting a rival installer.
_upgrade_lock = threading.Lock()


def _releases_url() -> str:
    """Latest-release source. Overridable for tests/CI (mock/local)."""
    return os.environ.get('VIBE_SELLER_RELEASES_URL') or _DEFAULT_API


def _nightly_url() -> str:
    """Rolling nightly-release source. Overridable like the stable one."""
    return os.environ.get('VIBE_SELLER_NIGHTLY_RELEASES_URL') or _NIGHTLY_API


def _open(url: str, timeout: float):
    # GitHub's API rejects requests without a User-Agent; file:// ignores it.
    req = urllib.request.Request(url, headers={'User-Agent': 'vibe-seller'})
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310


def _is_url(s: str) -> bool:
    return s.startswith(('http://', 'https://', 'file://'))


def _fetch_release(src: str | None = None) -> dict:
    """Read a release JSON from *src* (default: the latest-release source).

    Accepts an http(s)/file:// URL or a plain local filesystem path
    (the latter is what tests/CI use for a mock release — avoids
    fragile file:// URI construction across shells)."""
    src = src or _releases_url()
    if not _is_url(src):
        return json.loads(Path(src).read_text(encoding='utf-8'))
    with _open(src, timeout=15) as resp:
        return json.loads(resp.read().decode())


def _installer_offer(rel: dict, version: str) -> dict | None:
    """``{'version', 'url'}`` when *rel* carries the installer asset."""
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
    if not version or not asset or not asset.get('browser_download_url'):
        return None
    return {'version': version, 'url': asset['browser_download_url']}


def check_for_update() -> dict | None:
    """Return ``{'version', 'url'}`` for a newer release, else ``None``."""
    current = get_version()
    rel = _fetch_release()
    stable = _installer_offer(rel, (rel.get('tag_name') or '').lstrip('v'))
    if channel_of(current) != 'nightly':
        # Unchanged pre-nightly rule (stable, and dev/PR builds that CI
        # upgrades): take releases/latest when it is newer.
        if stable and is_newer(stable['version'], current):
            return stable
        return None
    offers = [stable]
    # Best effort: a missing/unreachable nightly still lets the stable
    # release through.
    try:
        nightly = _fetch_release(_nightly_url())
    except (OSError, ValueError):
        nightly = {}
    if nightly.get('prerelease'):
        offers.append(_installer_offer(nightly, nightly.get('name') or ''))
    by_version = {o['version']: o for o in offers if o}
    best = best_upgrade(list(by_version), current)
    return by_version[best] if best else None


def fetch_release_notes(current: str, *, limit: int = 5) -> list[dict]:
    """Releases strictly newer than *current*, newest first (best-effort).

    Powers the tray's "what's new" so a user jumping several versions
    (e.g. 0.0.8 → 0.0.10) sees every intermediate release, not just the
    target. Any fetch/parse failure returns ``[]`` — a missing changelog
    must never block the upgrade itself. Drafts/prereleases are skipped
    (the installer only ever fetches full releases).
    """
    try:
        with _open(_RELEASES_LIST_API, timeout=10) as resp:
            releases = json.loads(resp.read().decode())
    except (OSError, ValueError):
        return []
    if not isinstance(releases, list):
        return []
    notes = []
    for rel in releases:
        if (
            not isinstance(rel, dict)
            or rel.get('draft')
            or rel.get('prerelease')
        ):
            continue
        tag = (rel.get('tag_name') or '').lstrip('v')
        if not tag or not is_newer(tag, current):
            continue
        notes.append({
            'version': tag,
            'url': rel.get('html_url') or RELEASES_PAGE,
        })
        if len(notes) >= limit:
            break
    return notes


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
    - ``{'status': 'updating', 'version': <new>, 'notes_url': <url>}``
    - ``{'status': 'in-progress', 'version': <current>}`` — another
      upgrade is already running
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
        return {
            'status': 'updating',
            'version': upd['version'],
            'notes_url': RELEASES_PAGE,
        }
    except Exception as exc:  # noqa: BLE001 — surfaced to the user, not raised
        logger.exception('Windows update failed')
        manual = (
            NIGHTLY_PAGE
            if channel_of(get_version()) == 'nightly'
            else MANUAL_URL
        )
        return {'status': 'error', 'error': str(exc), 'manual_url': manual}
    finally:
        _upgrade_lock.release()
