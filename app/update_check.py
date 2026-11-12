"""PyPI + GitHub release check for the web UI's update-available popup.

Unlike ``windows_update.py`` (which drives the native-Windows tray's
in-place installer swap), this module only answers "is there a newer
release, and what should the user run" — the frontend renders the
popup and links out; nothing here downloads or executes anything.

Which releases an install is offered depends on its channel (see
``app/release_channel.py``): stable installs only ever see stable
releases (PyPI's ``info.version``, unchanged since before nightlies
existed); nightly installs see the highest newer nightly or stable
version on PyPI. Dev/local checkouts (setuptools_scm ``.devN+g<sha>``
builds, the ``+dev`` fallback version, or anything else carrying a
local version segment) are never checked: there's no PyPI release that
corresponds to an arbitrary in-between commit, so "latest" could easily
be *behind* what's actually checked out.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from app.env_options import Options
from app.release_channel import (
    NIGHTLY_TAG,
    best_upgrade,
    channel_of,
    is_newer as is_newer,
)

REPO = 'zpoint/vibe-seller'
PYPI_JSON_URL = 'https://pypi.org/pypi/vibe-seller/json'
GITHUB_RELEASES_URL = f'https://api.github.com/repos/{REPO}/releases'
RELEASES_PAGE_URL = f'https://github.com/{REPO}/releases/latest'
NIGHTLY_PAGE_URL = f'https://github.com/{REPO}/releases/tag/{NIGHTLY_TAG}'

# Cap on how many missed releases the "what's new" popup lists, so a
# user many versions behind doesn't get a wall of changelog text.
_MAX_RELEASES = 10

# GitHub (and sometimes PyPI) is frequently just *slow* rather than
# blocked from behind the Great Firewall — a short timeout would treat
# "would have succeeded in 8s" the same as "actually unreachable" and
# silently hide real updates from exactly the users least likely to
# find out about them another way. Favor patience: this call never
# blocks page render (the frontend fetches it independently) or any
# other request (asyncio runs it concurrently with everything else),
# and the caller (system.update_check) caches the outcome, so a slow
# network pays this cost once per cache TTL, not on every page load.
_FETCH_TIMEOUT_SECONDS = 15


def is_dev_version(v: str) -> bool:
    """True for dev builds / dirty or untagged checkouts.

    Covers both the tagged-but-ahead shape (``0.1.0.dev3+g<sha>``) and
    the no-git-metadata fallback (``0.0.0+dev``, a local version
    segment). Unparseable strings are treated as dev too —
    conservative, since we can't safely compare them against a PyPI
    version either way. A nightly (``0.2.1.dev<timestamp>``) is not dev:
    it is checked on its own channel.
    """
    return channel_of(v) == 'dev'


def pypi_json_source() -> str:
    """Where the PyPI JSON document is read from.

    ``VIBE_SELLER_PYPI_JSON_URL`` overrides it for tests/CI (same role
    as ``VIBE_SELLER_RELEASES_URL`` for the Windows installer source);
    the override may be a URL or a plain local path."""
    return Options.PYPI_JSON_URL.get().strip() or PYPI_JSON_URL


async def fetch_pypi_json(client: httpx.AsyncClient) -> dict:
    """The PyPI JSON document, from the URL or local path in
    ``pypi_json_source()``. Raises on any failure (callers swallow)."""
    src = pypi_json_source()
    if not src.startswith(('http://', 'https://', 'file://')):
        text = await asyncio.to_thread(Path(src).read_text, encoding='utf-8')
        return json.loads(text)
    resp = await client.get(src, timeout=_FETCH_TIMEOUT_SECONDS)
    resp.raise_for_status()
    return resp.json()


async def fetch_latest_pypi_version(
    client: httpx.AsyncClient,
) -> str | None:
    """Latest version per PyPI's JSON API, or None on any failure."""
    try:
        return (await fetch_pypi_json(client))['info']['version']
    except (httpx.HTTPError, OSError, KeyError, TypeError, ValueError):
        return None


async def fetch_nightly_upgrade(
    client: httpx.AsyncClient, current: str
) -> str | None:
    """Best version for a nightly install to move to, or None.

    Reads every release PyPI lists (``info.version`` skips pre-releases,
    so it can never name a nightly), drops fully-yanked ones, and picks
    the highest newer stable-or-nightly version. None on any failure.
    """
    try:
        releases = (await fetch_pypi_json(client))['releases']
        versions = [
            v
            for v, files in releases.items()
            if files and not all(f.get('yanked') for f in files)
        ]
    except (
        httpx.HTTPError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
        AttributeError,
    ):
        return None
    return best_upgrade(versions, current)


async def _fetch_releases_json(client: httpx.AsyncClient) -> list:
    """The GitHub releases list, from the URL or local path in
    ``VIBE_SELLER_GITHUB_RELEASES_URL`` (mock hook, like the PyPI
    source above)."""
    src = Options.GITHUB_RELEASES_URL.get().strip() or GITHUB_RELEASES_URL
    if not src.startswith(('http://', 'https://', 'file://')):
        text = await asyncio.to_thread(Path(src).read_text, encoding='utf-8')
        data = json.loads(text)
        return data if isinstance(data, list) else []
    resp = await client.get(
        src,
        params={'per_page': 10},
        headers={'User-Agent': 'vibe-seller'},
        timeout=_FETCH_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


async def fetch_release_notes(
    client: httpx.AsyncClient, current: str
) -> list[dict]:
    """GitHub release notes newer than *current*, newest first.

    Best-effort: any fetch/parse failure returns an empty list rather
    than raising, since a missing changelog shouldn't block the
    update-available popup itself.
    """
    try:
        releases = await _fetch_releases_json(client)
    except (httpx.HTTPError, TypeError, ValueError, OSError):
        return []

    notes = []
    for rel in releases:
        # Drafts/prereleases aren't what `vibe-seller upgrade` or the
        # Windows installer will actually fetch (PyPI's "latest" and
        # GitHub's installer asset both track full releases only), so
        # listing them as "what's new" would advertise a version the
        # user's upgrade path can't reach yet.
        if rel.get('draft') or rel.get('prerelease'):
            continue
        tag = (rel.get('tag_name') or '').lstrip('v')
        if not tag or not is_newer(tag, current):
            continue
        notes.append({
            'version': tag,
            'name': rel.get('name') or tag,
            'body': rel.get('body') or '',
            'url': rel.get('html_url') or RELEASES_PAGE_URL,
            'published_at': rel.get('published_at'),
        })
        if len(notes) >= _MAX_RELEASES:
            break
    return notes
