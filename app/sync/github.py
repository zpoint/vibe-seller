"""The GitHub commit probe shared by the skills and knowledge syncs.

"Has upstream changed since we last looked?" — one request, answered
as cheaply as GitHub allows: the validator from the last probe is sent
back as ``If-None-Match``, and a 304 answer does not count against the
unauthenticated rate limit (60 req/h per IP). That matters because a
deployment polls several paths; without the conditional request a
mis-set cooldown turns every task launch into a rate-limit hit.

Leaf module on purpose: only ``app.config`` and ``httpx``, so the
workspace sync managers can use it without importing the scheduler.
"""

import logging

import httpx

from app.config import GITHUB_API_BASE

logger = logging.getLogger(__name__)

#: The one repo the sync tiers read from (mirrors SKILLS_REPO_URL /
#: KNOWLEDGE_REPO_URL in app/config.py).
REPO = 'zpoint/vibe-seller'

_ACCEPT = 'application/vnd.github.v3+json'


def commits_url(repo_path: str) -> str:
    """The commits-API URL for the last commit touching ``repo_path``."""
    return (
        f'{GITHUB_API_BASE}/repos/{REPO}/commits'
        f'?path={repo_path}&per_page=1&sha=main'
    )


async def probe_commit(
    client: httpx.AsyncClient,
    repo_path: str,
    etag: str | None = None,
) -> dict:
    """Latest commit touching ``repo_path`` on main.

    Returns ``{'ok', 'not_modified', 'commit', 'etag', 'error'}``:

    * ``not_modified`` — a 304 to a conditional request we sent; the
      stored commit is still current and ``etag`` is the refreshed
      validator (may be absent).
    * ``commit`` — the sha, when upstream answered with a body.
    * ``etag`` — the validator to store for the next probe. ``''``
      means "there is none — clear any stored one" (a 200 without an
      ETag header must not leave a stale validator behind, or the next
      probe would keep asking "unchanged since something that no
      longer describes upstream").
    * ``ok=False`` — upstream unreachable, malformed, or answered 304
      to a request we did NOT make conditional: an unsolicited 304 has
      no body to trust and is a failure, not a "nothing changed".
    """
    headers = {'Accept': _ACCEPT}
    conditional = bool(etag)
    if conditional:
        headers['If-None-Match'] = etag
    url = commits_url(repo_path)
    try:
        resp = await client.get(url, headers=headers, timeout=15)
    except Exception as exc:
        logger.debug('Failed to probe %s: %s', repo_path, exc)
        return {
            'ok': False,
            'not_modified': False,
            'commit': None,
            'etag': None,
            'error': f'probe failed: {exc}',
        }

    if resp.status_code == 304:
        if not conditional:
            return {
                'ok': False,
                'not_modified': False,
                'commit': None,
                'etag': None,
                'error': 'HTTP 304 without If-None-Match',
            }
        return {
            'ok': True,
            'not_modified': True,
            'commit': None,
            'etag': resp.headers.get('etag') or etag,
            'error': None,
        }
    if resp.status_code != 200:
        return {
            'ok': False,
            'not_modified': False,
            'commit': None,
            'etag': None,
            'error': f'HTTP {resp.status_code}',
        }
    try:
        commits = resp.json()
        commit = commits[0]['sha'] if commits else None
    except Exception as exc:
        return {
            'ok': False,
            'not_modified': False,
            'commit': None,
            'etag': None,
            'error': f'unexpected commits payload: {exc}',
        }
    if not commit:
        return {
            'ok': False,
            'not_modified': False,
            'commit': None,
            'etag': None,
            'error': 'no commit in response',
        }
    return {
        'ok': True,
        'not_modified': False,
        'commit': commit,
        'etag': resp.headers.get('etag') or '',
        'error': None,
    }
