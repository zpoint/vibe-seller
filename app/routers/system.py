"""System-level info endpoints (runtime metadata about the server).

Exists so the frontend has a single, stable source of truth for
things like host OS and build version instead of having to infer
them from error payloads or stale env vars.
"""

import subprocess
import time

from fastapi import APIRouter, Depends, HTTPException, Query
import httpx

from app import auto_update, pip_update, telemetry
from app.ai.claude_backend_manager import agent_manager
from app.auth import get_current_user, require_admin
from app.browser.ziniao_utils import get_platform
from app.config import BASE_DIR
from app.release_channel import channel_of
from app.release_notes import merged_notes
from app.telemetry import APP_VERSION
from app.update_check import (
    NIGHTLY_PAGE_URL,
    RELEASES_PAGE_URL,
    fetch_latest_pypi_version,
    fetch_nightly_upgrade,
    fetch_release_notes,
    is_newer,
)

router = APIRouter(prefix='/api/system', tags=['system'])

# The frontend calls /update-check on every page load, but PyPI/GitHub
# don't need to be hit that often — GitHub's unauthenticated rate limit
# is 60 req/hr per IP, and a slow/blocked network (see
# update_check._FETCH_TIMEOUT_SECONDS) shouldn't cost every single page
# load its full timeout. One in-process slot is enough: APP_VERSION is
# fixed for the process lifetime, so there's only ever one possible
# "current version" to cache a result for.
_UPDATE_CHECK_CACHE_TTL_SECONDS = 3600
_update_check_cache: dict | None = None
_update_check_cache_at: float = 0.0


def _git_commit_short() -> str | None:
    """Return the short commit SHA of the source tree, or None.

    Best-effort: when the deploy is a git checkout (the mac2 / dev
    case), this gives the user something unambiguous to point at when
    a behavior changes between deploys. When the deploy is a wheel
    install with no .git dir, returns None and the frontend just
    shows APP_VERSION on its own.
    """
    try:
        result = subprocess.run(
            ['git', '-C', str(BASE_DIR), 'rev-parse', '--short', 'HEAD'],
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


# Resolved once at import. Restart the server to pick up a new commit
# — same lifecycle as APP_VERSION, and avoids paying a subprocess on
# every /info request.
_GIT_COMMIT_SHORT: str | None = _git_commit_short()


@router.get('/info')
async def system_info() -> dict:
    """Return runtime info about the server.

    Fields:
      - ``platform``: 'mac' | 'windows' | 'wsl' | 'linux' — host OS.
      - ``version``: package version from ``importlib.metadata``.
        For a PyPI install this is the clean release version
        (e.g. ``0.1.5``); for a dev checkout setuptools_scm formats
        it as ``<base>.devN+g<short_sha>`` so the SHA is already
        embedded.
      - ``release_channel``: 'stable' | 'nightly' | 'dev' derived
        from the version string (app/release_channel.py).
      - ``commit``: short git SHA if the source tree is a git
        checkout, else None. Useful when ``version`` is the
        ``fallback_version`` ('0.0.0+dev') because setuptools_scm
        couldn't read git history (shallow clone, etc.).
      - ``install_id``: this installation's id, stable across upgrades.
        Shown under the version so a person can quote it to support;
        the ads service files the installation under the same id.
    """
    return {
        'platform': get_platform(),
        'version': APP_VERSION,
        'release_channel': channel_of(APP_VERSION),
        'commit': _GIT_COMMIT_SHORT,
        'install_id': telemetry.stable_install_id(),
    }


async def _compute_update_check() -> dict:
    current = APP_VERSION
    channel = channel_of(current)
    if channel == 'dev':
        return {'dev': True}

    async with httpx.AsyncClient() as client:
        # Stable keeps PyPI's info.version, which never names a
        # pre-release — so a stable install can't be offered a nightly.
        if channel == 'stable':
            latest = await fetch_latest_pypi_version(client)
        else:
            latest = await fetch_nightly_upgrade(client, current)
        if not latest or not is_newer(latest, current):
            return {
                'dev': False,
                'update_available': False,
                'current_version': current,
                'channel': channel,
            }
        releases = await fetch_release_notes(client, current)
        # Reconcile the two sources: `latest` is PyPI (what `vibe-seller
        # upgrade` / pip actually installs), but the notes come from
        # GitHub Releases. If GitHub has tagged a release PyPI hasn't
        # published yet, drop its notes — otherwise the popup advertises a
        # version the user's upgrade path can't reach. Never show notes
        # newer than the installable `latest`.
        releases = [r for r in releases if not is_newer(r['version'], latest)]

    platform = get_platform()
    is_windows = platform == 'windows'
    # A nightly lives on the rolling prerelease, not on releases/latest.
    page = (
        NIGHTLY_PAGE_URL
        if channel_of(latest) == 'nightly'
        else RELEASES_PAGE_URL
    )
    merged = merged_notes(releases)
    return {
        'dev': False,
        'update_available': True,
        'current_version': current,
        'latest_version': latest,
        'channel': channel,
        'platform': platform,
        'upgrade_command': None if is_windows else 'vibe-seller upgrade',
        'download_url': page if is_windows else None,
        'releases_page_url': page,
        # Whether the UI should show the in-app flow (background
        # download + "restart and update") or today's manual popup.
        'in_app_update': auto_update.in_app_update(),
        'releases': releases,
        # Release notes for every version between the installed one
        # and latest, merged BY SECTION (newest version first) —
        # app/release_notes.py.
        'highlights': merged['highlights'],
        'notes': merged['notes'],
    }


@router.get('/update-check')
async def update_check() -> dict:
    """Check PyPI for a release newer than the one running here.

    Dev/local checkouts (see ``update_check.is_dev_version``) always
    get ``{'dev': True}`` — there's no PyPI release that corresponds
    to an arbitrary in-between commit, so any comparison would be
    misleading.

    Otherwise returns ``update_available`` plus, when true, the
    platform-appropriate upgrade instructions (``vibe-seller
    upgrade`` on macOS/Linux/WSL, a releases-page link on native
    Windows — matching the two install paths in the README) and
    GitHub release notes for every version newer than the installed
    one.

    Cached in-process for ``_UPDATE_CHECK_CACHE_TTL_SECONDS`` (success
    *or* failure) — see the module docstring comment above the cache
    globals for why.
    """
    global _update_check_cache, _update_check_cache_at
    now = time.monotonic()
    if (
        _update_check_cache is not None
        and now - _update_check_cache_at < _UPDATE_CHECK_CACHE_TTL_SECONDS
    ):
        return _update_check_cache

    result = await _compute_update_check()
    _update_check_cache = result
    _update_check_cache_at = now
    return result


@router.get('/update-status')
async def update_status(user=Depends(get_current_user)) -> dict:
    """In-app auto-update state — cheap, no network I/O; the frontend
    polls this while an update downloads, is waiting to be applied, or
    is restarting.

    ``running_tasks`` is the live-agent count a restart right now would
    interrupt (the modal warns about it before offering the button).

    ``path`` says what ``ready`` means: ``pip`` (a few MB into the venv,
    applied by itself the moment the server is idle — so ``auto_apply``
    is true and ``popup_due`` stays false for a day) or ``installer``
    (the bundled runtime changed; nothing happens until an admin
    clicks). ``last_update`` is what the previous apply did — including
    one that failed and rolled back, which the machine is now serving.
    """
    snap = auto_update.snapshot()
    return {
        'supported': auto_update.in_app_update(),
        'state': snap['state'],
        'version': snap['version'],
        'current_version': APP_VERSION,
        'downloaded_bytes': snap['downloaded_bytes'],
        'total_bytes': snap['total_bytes'],
        'error': snap['error'],
        'running_tasks': agent_manager.live_agent_count(),
        'platform': get_platform(),
        'path': snap['path'],
        'auto_apply': snap['auto_apply'],
        'popup_due': snap['popup_due'],
        'ready_since': snap['ready_since'],
        'last_update': snap['last_update'],
    }


@router.get('/release-notes')
async def release_notes(
    since: str = Query(..., min_length=1, max_length=64),
    until: str | None = Query(None, max_length=64),
    user=Depends(get_current_user),
) -> dict:
    """Merged release notes for ``since`` → ``until`` (the latter
    defaults to the version running here).

    Exists because ``/update-check`` can only ever describe what is NEWER
    than this server: right after an update, that is nothing, so the
    "已更新到 v{x}" panel has to ask for the range it just crossed.
    Best-effort like the check itself — an unreachable GitHub yields
    empty lists rather than an error.
    """
    target = until or APP_VERSION
    async with httpx.AsyncClient() as client:
        releases = await fetch_release_notes(client, since)
        releases = [r for r in releases if not is_newer(r['version'], target)]
    merged = merged_notes(releases)
    return {
        'since': since,
        'until': target,
        'releases': [r['version'] for r in releases],
        'highlights': merged['highlights'],
        'notes': merged['notes'],
    }


@router.post('/update/restart')
async def update_restart(user=Depends(require_admin)) -> dict:
    """Apply the ready update: restart onto it now (admin only).

    Running task agents are interrupted — the modal warned about that
    before the button was clicked, so there is no drain. 409 unless the
    background download actually reached ``ready``.
    """
    if auto_update.snapshot()['state'] != auto_update.READY:
        raise HTTPException(
            status_code=409,
            detail={'message': 'no downloaded update is ready to restart onto'},
        )
    try:
        return await auto_update.apply_restart(
            interrupted_tasks=agent_manager.live_agent_count(),
            trigger=auto_update.manual_trigger(),
        )
    except pip_update.ApplyInProgress as exc:
        # A second click while an update is already applying, or another
        # server on the same data dir got there first. Nothing failed —
        # there is one already running — so this is 409, not a 500 that
        # the UI would read as "the updater is broken".
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
