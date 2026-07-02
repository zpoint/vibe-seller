"""PyPI + GitHub release check for the web UI's update-available popup.

Unlike ``windows_update.py`` (which drives the native-Windows tray's
in-place installer swap), this module only answers "is there a newer
release, and what should the user run" — the frontend renders the
popup and links out; nothing here downloads or executes anything.

Dev/local checkouts (setuptools_scm ``.devN+g<sha>`` builds, the
``+dev`` fallback version, or anything else carrying a local version
segment) are never checked: there's no PyPI release that corresponds
to an arbitrary in-between commit, so "latest" could easily be
*behind* what's actually checked out.
"""

from __future__ import annotations

import httpx
from packaging.version import InvalidVersion, Version

REPO = 'zpoint/vibe-seller'
PYPI_JSON_URL = 'https://pypi.org/pypi/vibe-seller/json'
GITHUB_RELEASES_URL = f'https://api.github.com/repos/{REPO}/releases'
RELEASES_PAGE_URL = f'https://github.com/{REPO}/releases/latest'

# Cap on how many missed releases the "what's new" popup lists, so a
# user many versions behind doesn't get a wall of changelog text.
_MAX_RELEASES = 5

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

    Covers both the tagged-but-ahead shape (``0.1.0.dev3+g<sha>``,
    ``is_devrelease``) and the no-git-metadata fallback
    (``0.0.0+dev``, a local version segment). Unparseable strings are
    treated as dev too — conservative, since we can't safely compare
    them against a PyPI version either way.
    """
    try:
        parsed = Version(v)
    except InvalidVersion:
        return True
    return parsed.is_devrelease or parsed.local is not None


def is_newer(latest: str, current: str) -> bool:
    """True if *latest* is a strictly newer version than *current*."""
    try:
        return Version(latest) > Version(current)
    except InvalidVersion:
        return False


async def fetch_latest_pypi_version(
    client: httpx.AsyncClient,
) -> str | None:
    """Latest version per PyPI's JSON API, or None on any failure."""
    try:
        resp = await client.get(PYPI_JSON_URL, timeout=_FETCH_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()['info']['version']
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None


async def fetch_release_notes(
    client: httpx.AsyncClient, current: str
) -> list[dict]:
    """GitHub release notes newer than *current*, newest first.

    Best-effort: any fetch/parse failure returns an empty list rather
    than raising, since a missing changelog shouldn't block the
    update-available popup itself.
    """
    try:
        resp = await client.get(
            GITHUB_RELEASES_URL,
            params={'per_page': 10},
            headers={'User-Agent': 'vibe-seller'},
            timeout=_FETCH_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        releases = resp.json()
    except (httpx.HTTPError, TypeError, ValueError):
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
