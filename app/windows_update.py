"""In-place Windows updater.

Checks GitHub Releases for a newer ``VibeSeller-Setup.exe``, downloads
it, and runs it — Inno Setup upgrades in place (same ``AppId``). Kept
importable and headless so the tray menu AND CI exercise the same code;
the release source is overridable via ``VIBE_SELLER_RELEASES_URL`` (a
``file://`` or ``http://`` JSON in the GitHub "latest release" shape) so
the upgrade flow can be tested with a local mock — no real release.

The download/staging primitives live here so the server's background
auto-download (``app/auto_update.py``) and the tray's "检查更新" share
one staged installer under ``<VIBE_HOME>/updates/<version>/`` — size +
sha256 verified, ``.part`` + HTTP ``Range`` resumable. The tray reuses
a staged installer for the offered version instead of downloading it
again.

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

import hashlib
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
    """``{'version', 'url', 'size', 'digest'}`` when *rel* carries the
    installer asset.

    ``size`` is the asset's byte count and ``digest`` GitHub's
    ``sha256:<hex>`` — the staged download verifies against them. A
    mock release may omit either; the verification then covers only
    what is known."""
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
    return {
        'version': version,
        'url': asset['browser_download_url'],
        'size': asset.get('size'),
        'digest': asset.get('digest'),
    }


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


# ── Staged installer (shared by the server's background download and
#    the tray) ────────────────────────────────────────────────────────
#
# ``<VIBE_HOME>/updates/<version>/`` holds ``VibeSeller-Setup.exe``
# (verified, ready to run), its ``.part`` (a download that can be
# resumed with an HTTP Range request), and ``install-meta.json`` (the
# size/digest the file was verified under, plus the source url). A
# verified stage for a version newer than the running one lets the
# server say ``ready`` on boot without touching the network, and lets
# the tray's "检查更新" reuse it instead of downloading again.

STAGED_NAME = ASSET_NAME
PART_NAME = ASSET_NAME + '.part'
META_NAME = 'install-meta.json'
_CHUNK = 1024 * 1024


class VerifyError(RuntimeError):
    """A staged/finished download failed its size or sha256 check."""


# win32 process-creation flags, as literals (subprocess only exposes
# some of them, and only on Windows; plain ints keep this module
# importable and testable everywhere).
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def updates_root() -> Path:
    # Late import: app.config creates the data dir at import time —
    # that side effect belongs to server boot, not to every tray
    # process that merely imports this module.
    from app.config import VIBE_SELLER_DIR  # noqa: PLC0415

    return VIBE_SELLER_DIR / 'updates'


def staged_paths(version: str) -> tuple[Path, Path, Path]:
    root = updates_root() / version
    return root / STAGED_NAME, root / PART_NAME, root / META_NAME


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _read_meta(version: str) -> dict | None:
    _final, _part, meta = staged_paths(version)
    try:
        data = json.loads(meta.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def staged_installer(
    version: str,
    *,
    verify: bool = True,
    expected_sha: str | None = None,
    expected_size: int | None = None,
) -> Path | None:
    """The verified staged installer for *version*, or None.

    With *verify*, the file is re-hashed against the meta's digest (a
    few seconds on a ~700 MB exe — run off the event loop). With
    ``verify=False``, only the recorded size is re-checked — enough for
    the apply path, where the same file was verified moments ago.

    ``expected_sha`` / ``expected_size`` (from the CURRENT release
    offer) additionally require the stage to be of those exact bytes:
    a re-uploaded release asset must not be served from a stale
    stage — the tag alone does not identify the bytes."""
    final, _part, _meta = staged_paths(version)
    meta = _read_meta(version)
    if not final.exists() or not meta or meta.get('version') != version:
        return None
    if expected_sha is not None and meta.get('sha256') != expected_sha:
        return None
    if expected_size is not None and meta.get('size') != expected_size:
        return None
    try:
        size = final.stat().st_size
    except OSError:
        return None
    if meta.get('size') is not None and size != meta['size']:
        return None
    if verify:
        if not meta.get('sha256'):
            # A mock stage with no recorded digest can only be checked
            # by size; the real GitHub asset always carries one.
            return final if size == (meta.get('size') or size) else None
        try:
            if _sha256_file(final) != meta['sha256']:
                return None
        except OSError:
            return None
    return final


def _prune_old_stages(keep: str) -> None:
    root = updates_root()
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name == keep or not entry.is_dir():
            continue
        shutil.rmtree(entry, ignore_errors=True)


def _download_stream(
    url: str, part: Path, progress, expected_size: int | None = None
) -> None:
    """Stream *url* into *part*, resuming what is already there.

    A 416 (Range start at/after the asset's end — a part left over
    from a changed asset, or already complete) or a part LONGER than
    the expected size means the resume is not trustworthy: discard it
    and start from zero, so one bad .part cannot wedge every future
    cycle."""
    have = part.stat().st_size if part.exists() else 0
    if expected_size is not None and have > expected_size:
        part.unlink(missing_ok=True)
        have = 0
    headers = {'User-Agent': 'vibe-seller'}
    if have:
        headers['Range'] = f'bytes={have}-'
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=600)  # noqa: S310
    except urllib.error.HTTPError as exc:
        if exc.code == 416 and have:
            logger.info(
                'stale .part (%d bytes) refused with 416; restarting '
                'the download',
                have,
            )
            part.unlink(missing_ok=True)
            have = 0
            req = urllib.request.Request(
                url, headers={'User-Agent': 'vibe-seller'}
            )
            resp = urllib.request.urlopen(req, timeout=600)  # noqa: S310
        else:
            raise
    with resp:
        status = getattr(resp, 'status', 200)
        total: int | None = None
        content_range = resp.headers.get('Content-Range') or ''
        content_len = resp.headers.get('Content-Length')
        if status == 206 and content_range.rpartition('/')[2].isdigit():
            total = int(content_range.rpartition('/')[2])
        elif content_len and content_len.isdigit():
            total = int(content_len) + (have if status == 206 else 0)
        if have and status != 206:
            # Server ignored the Range header — start over.
            have = 0
            total = int(content_len) if content_len else None
        mode = 'ab' if have else 'wb'
        done = have
        with open(part, mode) as f:
            while chunk := resp.read(_CHUNK):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)


def stage_installer(offer: dict, progress=None) -> Path:
    """Download → verify (size + sha256) → rename, and return the path.

    Resumable: bytes already in the ``.part`` are kept and the fetch
    continues with an HTTP ``Range`` request. On a size or sha256
    mismatch the ``.part`` is discarded and a ``RuntimeError`` raised —
    a corrupt stage must never be launched. *progress* is called as
    ``progress(downloaded_bytes, total_bytes_or_None)``. A plain local
    path source (tests/CI mock) is copied — resume does not apply."""
    version = offer['version']
    url = offer['url']
    size = offer.get('size')
    digest = offer.get('digest')
    expected_sha = (
        digest.split(':', 1)[1].lower()
        if isinstance(digest, str) and ':' in digest
        else None
    )
    final, part, meta = staged_paths(version)
    part.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        # A stage left by another flow without meta, or a stale one:
        # without verified meta it is not trustworthy — replace it.
        final.unlink(missing_ok=True)
    if progress:
        have = part.stat().st_size if part.exists() else 0
        progress(have, size)
    complete_part = (
        size is not None and part.exists() and (part.stat().st_size == size)
    )
    if _is_url(url):
        if not complete_part:
            # A part at exactly the expected size is complete — asking
            # for bytes=<size>- would only earn an HTTP 416.
            _download_stream(url, part, progress, size)
    else:
        src = Path(url)
        if not src.exists():
            raise RuntimeError(f'installer source not found: {url}')
        total = size or src.stat().st_size
        if not complete_part:
            # A part of exactly the expected size is a complete copy of
            # this same source (mock reruns) — keep it. Copy in chunks
            # so a file:// or plain-path source still reports progress
            # (the CI mock and the Windows demo stage this way).
            part.unlink(missing_ok=True)
            done = 0
            with open(src, 'rb') as fsrc, open(part, 'wb') as fdst:
                while chunk := fsrc.read(_CHUNK):
                    fdst.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        if progress:
            progress(part.stat().st_size, total)
    got = part.stat().st_size
    if size is not None and got != size:
        part.unlink(missing_ok=True)
        raise VerifyError(
            f'installer size mismatch: expected {size}, got {got}'
        )
    if expected_sha:
        actual = _sha256_file(part)
        if actual != expected_sha:
            part.unlink(missing_ok=True)
            raise VerifyError(
                f'installer sha256 mismatch: expected {expected_sha}, '
                f'got {actual}'
            )
    part.replace(final)
    meta.write_text(
        json.dumps({
            'version': version,
            'url': url,
            'size': got,
            'sha256': expected_sha,
            'staged_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        }),
        encoding='utf-8',
    )
    _prune_old_stages(keep=version)
    return final


#: Wizard modes. ``interactive`` shows the full wizard (the tray menu);
#: ``silent`` shows ONLY the progress window — /SILENT, so no wizard
#: questions, but the user sees the install happening (the in-app
#: "重启并更新"); ``very_silent`` shows nothing (CI).
INSTALLER_FLAGS = {
    'interactive': [],
    'silent': ['/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
    'very_silent': ['/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART'],
}


def run_installer(path: Path, *, mode: str = 'interactive') -> None:
    """Launch the installer, fully detached from this process.

    This code can run INSIDE ``{app}\\.venv``'s python(w).exe (the tray)
    or inside the server — in both cases the installer deletes and
    rebuilds the very files this process image sits on. So the launch
    must be truly detached (``DETACHED_PROCESS`` + a new process group)
    so the installer outlives its launcher; a plain ``Popen`` child
    could be torn down with the parent and would keep the install dir
    locked.

    ``CREATE_BREAKAWAY_FROM_JOB`` is tried first: GitHub Actions runners
    (and some supervisors) put the whole job tree in a job object whose
    kill-on-close would take the installer down with the server. A
    process that is not in such a job can't break away — the API just
    fails — so fall back to the plain detached flags.

    Retry on ``WinError 32`` (PermissionError): a freshly-written exe can
    be momentarily locked by an AV scan or a racing download.
    """
    args = [str(path), *INSTALLER_FLAGS[mode]]
    if mode != 'interactive':
        # An unattended install must leave an account of itself: the
        # debug log lands next to the staged exe (and CI asserts the
        # staged path from it).
        args.append(f'/LOG={path.parent / "install.log"}')
    if sys.platform == 'win32':
        base_flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        # Breakaway first, plain detached as the fallback.
        choices = (base_flags | CREATE_BREAKAWAY_FROM_JOB, base_flags)
    else:
        choices = (0,)
    last_exc: OSError | None = None
    for attempt in range(3):
        for i, creationflags in enumerate(choices):
            try:
                subprocess.Popen(  # noqa: S603
                    args, creationflags=creationflags, close_fds=True
                )
                return
            except PermissionError as exc:
                # ERROR_ACCESS_DENIED (winerror 5) on the BREAKAWAY
                # attempt is the job object refusing the flag — not a
                # locked file. Fall back to the plain detached flags
                # immediately (retrying the same flags would only burn
                # the loop).
                if (
                    i == 0
                    and len(choices) > 1
                    and getattr(exc, 'winerror', None) == 5
                ):
                    logger.info(
                        'installer breakaway refused by the job '
                        'object; launching plain-detached'
                    )
                    continue
                # WinError 32 (sharing violation) — briefly locked by
                # an AV scan or a racing handle. Other flags won't
                # unlock it: sleep and retry next round.
                last_exc = exc
                logger.warning(
                    'installer launch locked (attempt %d): %s',
                    attempt + 1,
                    exc,
                )
                break
            except OSError as exc:
                # Any other refusal on the breakaway attempt (e.g.
                # ERROR_NOT_SUPPORTED): try the plain flags next.
                if i == 0 and len(choices) > 1:
                    logger.debug(
                        'installer breakaway refused (%s); retrying plain',
                        exc,
                    )
                    continue
                raise
        if last_exc is not None and attempt < 2:
            time.sleep(1.0 * (attempt + 1))
    if last_exc is not None:
        raise last_exc


def upgrade(*, silent: bool = False) -> dict:
    """Full check → (reused stage OR download) → launch flow.

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
        # The server's background download stages the same version
        # under <VIBE_HOME>/updates/: reuse a verified stage instead of
        # downloading the installer again — but only the stage of the
        # bytes this release currently offers.
        exp_sha = (
            upd['digest'].split(':', 1)[1].lower()
            if isinstance(upd.get('digest'), str) and ':' in upd['digest']
            else None
        )
        staged = staged_installer(
            upd['version'],
            expected_sha=exp_sha,
            expected_size=upd.get('size'),
        )
        if staged is not None:
            dest = staged
        else:
            # Unique dir per run: two concurrent downloads writing the
            # same fixed %TEMP%\VibeSeller-Setup.exe collide and the
            # launch then fails with WinError 32 (observed in the
            # field).
            dest = (
                Path(tempfile.mkdtemp(prefix='vibe-seller-upd-')) / ASSET_NAME
            )
            download_installer(upd['url'], dest)
        run_installer(dest, mode='very_silent' if silent else 'interactive')
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
