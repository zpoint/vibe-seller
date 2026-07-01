"""System-level info endpoints (runtime metadata about the server).

Exists so the frontend has a single, stable source of truth for
things like host OS and build version instead of having to infer
them from error payloads or stale env vars.
"""

import subprocess

from fastapi import APIRouter
import httpx

from app.browser.ziniao_utils import get_platform
from app.config import BASE_DIR
from app.telemetry import APP_VERSION
from app.update_check import (
    RELEASES_PAGE_URL,
    fetch_latest_pypi_version,
    fetch_release_notes,
    is_dev_version,
    is_newer,
)

router = APIRouter(prefix='/api/system', tags=['system'])


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
      - ``commit``: short git SHA if the source tree is a git
        checkout, else None. Useful when ``version`` is the
        ``fallback_version`` ('0.0.0+dev') because setuptools_scm
        couldn't read git history (shallow clone, etc.).
    """
    return {
        'platform': get_platform(),
        'version': APP_VERSION,
        'commit': _GIT_COMMIT_SHORT,
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
    """
    current = APP_VERSION
    if is_dev_version(current):
        return {'dev': True}

    async with httpx.AsyncClient() as client:
        latest = await fetch_latest_pypi_version(client)
        if not latest or not is_newer(latest, current):
            return {
                'dev': False,
                'update_available': False,
                'current_version': current,
            }
        releases = await fetch_release_notes(client, current)

    platform = get_platform()
    is_windows = platform == 'windows'
    return {
        'dev': False,
        'update_available': True,
        'current_version': current,
        'latest_version': latest,
        'platform': platform,
        'upgrade_command': None if is_windows else 'vibe-seller upgrade',
        'download_url': RELEASES_PAGE_URL if is_windows else None,
        'releases_page_url': RELEASES_PAGE_URL,
        'releases': releases,
    }
