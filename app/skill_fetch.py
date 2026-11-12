"""Fetch a skill from a URL — server-side, bytes never through an LLM.

The URL half of the skill-import feature: the user (or their agent, or
the workspace assistant) names a URL, the SERVER pulls it and hands
the bytes to the ONE staged install (``app.skill_uploads``). Two URL
shapes resolve:

* a GitHub-style **tree/blob URL** — ``<host>/<owner>/<repo>/tree/<ref>[/<path>]``
  (or ``/blob/<ref>/<path>/<file>``, whose containing directory is the
  skill). Fetched through the GitHub git-trees/git-blobs API against
  ``api.github.com`` (github hosts) or the same path shape on the
  given origin (a GitHub-compatible API, e.g. a test fixture), so the
  fixture can serve the identical layout.
* anything else — fetched as a direct archive (``.tar.gz``/``.tar``/
  ``.zip``; a ``.skill`` file is a zip — the format is decided by
  MAGIC BYTES at staging, never by the extension).

Safety contracts held here:

* **http(s) only**, no userinfo, redirects followed MANUALLY with the
  same URL guard re-applied at every hop (cap :data:`MAX_REDIRECTS`).
* **No SSRF**: the host must resolve to a public address — literal
  IPs are checked with ``ipaddress``, names via ``getaddrinfo`` with
  EVERY answer checked; private, link-local (the cloud metadata
  address lives there), reserved and multicast targets are refused
  outright. The one exception is loopback, honoured only under the
  same test switch as the webhook stubs
  (``VIBE_ALLOW_LOOPBACK_WEBHOOKS``, see
  ``app.notifiers.base.loopback_test_enabled``) — production never
  fetches a machine-internal address. (A resolver TOCTOU window —
  the guard resolves, the socket resolves again — is inherent to
  name-based fetching and noted here rather than papered over.)
* **Bounded**: every body is counted while streaming against the
  ``MAX_SKILL_*`` caps (per file and total) and the archive cap
  (:data:`app.skill_uploads.MAX_ARCHIVE_UPLOAD_BYTES`), timeouts bound
  a stalled host.

The refusal is loud and pre-install: a tree over the file count, a
symlink or submodule entry, a non-200 or an over-cap body raises
:class:`FetchError` before anything is staged.
"""

import asyncio
import base64
from collections.abc import AsyncIterator
import ipaddress
import os
import re
import socket
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.notifiers.base import loopback_test_enabled
from app.skill_pack import pack_tar_gz
from app.skill_uploads import (
    MAX_ARCHIVE_UPLOAD_BYTES,
    entry_name,
)
from app.workspace.skills_manager import (
    MAX_SKILL_FILE_BYTES,
    MAX_SKILL_FILES,
    MAX_SKILL_TOTAL_BYTES,
)

# Bound every phase: a stalled host must not hold a server coroutine
# open indefinitely, and the whole fetch has a hard wall-clock budget.
FETCH_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)
MAX_REDIRECTS = 5
_GITHUB_HOSTS = frozenset({'github.com', 'www.github.com'})
# <owner>/<repo>/<tree|blob>/<ref>[/<path>…] — matched host-agnostically
# (see the module docstring) so a loopback fixture can serve the layout.
_TREE_RE = re.compile(
    r'^/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<kind>tree|blob)'
    r'/(?P<ref>[^/]+)(?:/(?P<path>.*))?$'
)


class FetchError(ValueError):
    """A refused URL source. Carries the status the HTTP route maps."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


# ── URL guards ────────────────────────────────────────────


def _check_literal_host(url: str) -> tuple[str, int, str, str]:
    """Static parts of an allowed URL: (host, port, scheme, rest)."""
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ('http', 'https'):
        raise FetchError(f'only http(s) URLs can be installed, got {scheme!r}')
    if parts.username or parts.password:
        raise FetchError('URLs with credentials in them are refused')
    host = (parts.hostname or '').lower()
    if not host:
        raise FetchError(f'URL has no host: {url!r}')
    port = parts.port or (443 if scheme == 'https' else 80)
    return host, port, scheme, parts.netloc


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_loopback:
        # The ONE exception, and only under the test switch — a
        # production fetch never goes to a machine-internal address.
        return loopback_test_enabled()
    return not (
        ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _refuse_address(host: str, ip_text: str) -> FetchError:
    return FetchError(
        f'the address {ip_text} for host {host!r} is not a public '
        'address — private, link-local and loopback targets are '
        'refused (loopback only under VIBE_ALLOW_LOOPBACK_WEBHOOKS)'
    )


async def guard_url(url: str) -> None:
    """Refuse anything this server must not connect to.

    Runs every resolved answer through the public-address rule; a
    name resolving to a mix of public and private addresses is
    refused WHOLE (an attacker's DNS can otherwise rotate the
    "checked" answer into the connected one).
    """
    host, port, _scheme, _netloc = _check_literal_host(url)
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if not _ip_is_public(literal):
            raise _refuse_address(host, str(literal))
        return
    infos = await asyncio.to_thread(socket.getaddrinfo, host, port)
    for info in infos:
        ip_text = str(info[4][0])
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError:
            raise _refuse_address(host, ip_text) from None
        if not _ip_is_public(ip):
            raise _refuse_address(host, ip_text)


# ── GitHub source resolution ─────────────────────────────


def parse_github_source(url: str) -> dict | None:
    """``{api_root, owner, repo, ref, subpath}`` for a GitHub-style
    tree/blob URL, or None when the URL is not that shape.

    A blob URL names a file: the skill is its CONTAINING directory
    (the root when the file sits at the repo root).
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in ('http', 'https'):
        return None
    host = (parts.hostname or '').lower()
    if not host:
        return None
    path = parts.path or '/'
    match = _TREE_RE.match(path)
    if not match:
        return None
    kind = match.group('kind')
    rel = match.group('path') or ''
    if kind == 'blob':
        if '/' in rel:
            rel = rel.rsplit('/', 1)[0]
        else:
            rel = ''
    try:
        port = parts.port
    except ValueError:
        return None
    host_only = parts.hostname.lower()
    netloc = host_only + (f':{port}' if port else '')
    # github.com → api.github.com ONLY for the canonical origin: real
    # GitHub is never on a custom port. A github.com URL with an
    # explicit non-default port IS a GitHub-COMPATIBLE origin (an
    # enterprise server, a proxy, a fixture) — keep the given origin
    # instead of silently jumping hosts/ports, which would fetch a
    # different machine than the user named.
    scheme = parts.scheme.lower()
    std_port = 443 if scheme == 'https' else 80
    nonstandard_port = port is not None and port != std_port
    if (
        host_only in _GITHUB_HOSTS
        and scheme == 'https'
        and not nonstandard_port
    ):
        api_root = 'https://api.github.com'
    else:
        api_root = f'{scheme}://{netloc}'
    return {
        'api_root': api_root,
        'owner': unquote(match.group('owner')),
        'repo': unquote(match.group('repo')),
        'ref': unquote(match.group('ref')),
        'subpath': '/'.join(unquote(seg) for seg in rel.split('/') if seg),
    }


def _repo_path(owner: str, repo: str, tail: str) -> str:
    return f'/repos/{quote(owner, safe="")}/{quote(repo, safe="")}/{tail}'


def _api_headers(api_root: str) -> dict:
    headers = {
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'vibe-seller-skill-import',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    # An operator token raises the rate ceiling — and is handed ONLY
    # to api.github.com, never to a user-named host.
    if api_root == 'https://api.github.com':
        token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
        if token:
            headers['Authorization'] = f'Bearer {token}'
    return headers


async def _get_json(client: httpx.AsyncClient, url: str) -> dict:
    await guard_url(url)
    try:
        resp = await client.get(url, headers=_api_headers(_api_root_of(url)))
    except httpx.HTTPError as exc:
        # connect refused / DNS / timeout: a CLEAN refusal, never a
        # 500 out of the route (observed: ConnectError escaping as
        # Internal Server Error left the agent with nothing to act on)
        raise FetchError(
            f'could not reach {urlsplit(url).netloc}: {exc}', 502
        ) from exc
    try:
        if resp.status_code != 200:
            raise FetchError(
                f'{urlsplit(url).path} returned HTTP {resp.status_code}'
            )
        try:
            data = resp.json()
        except ValueError as exc:
            raise FetchError(
                f'{urlsplit(url).path} is not JSON — not a '
                'GitHub-compatible API'
            ) from exc
    finally:
        await resp.aclose()
    if not isinstance(data, dict):
        raise FetchError(f'{urlsplit(url).path} returned an unexpected shape')
    return data


def _api_root_of(url: str) -> str:
    parts = urlsplit(url)
    return f'{parts.scheme}://{parts.netloc}'


async def _fetch_tree(
    client: httpx.AsyncClient, target: dict
) -> tuple[dict, set]:
    """The git-trees + git-blobs walk: files dict + exec-bit names."""
    ref = quote(target['ref'], safe='')
    sub = '/'.join(target['subpath'].split('/'))
    tail = f'git/trees/{ref}'
    if sub:
        tail = f'git/trees/{ref}:{quote(sub, safe="/")}'
    url = target['api_root'] + _repo_path(target['owner'], target['repo'], tail)
    body = await _get_json(client, url)
    if body.get('truncated'):
        raise FetchError(
            'the repository tree is too large to list — link the '
            'skill folder directly or upload an archive instead'
        )
    entries = body.get('tree')
    if not isinstance(entries, list):
        raise FetchError('the trees API returned no tree list')

    sub_prefix = target['subpath'] + '/' if target['subpath'] else ''
    files: dict[str, bytes] = {}
    execs: set[str] = set()
    blobs: list[tuple[str, str, int]] = []
    for entry in entries:
        raw = str(entry.get('path') or '')
        rel = (
            raw[len(sub_prefix) :]
            if sub_prefix and raw.startswith(sub_prefix)
            else raw
        )
        kind = entry.get('type')
        if kind == 'tree':
            continue  # recursive=1 lists the children as blobs
        if kind == 'commit':
            raise FetchError(
                f'{raw!r} is a git submodule — submodules cannot be '
                'fetched as part of a skill'
            )
        if kind == 'link':
            raise FetchError(
                f'{raw!r} is a symlink inside the repository — '
                'symlinks are refused'
            )
        if kind != 'blob':
            raise FetchError(f'{raw!r}: unsupported tree entry {kind!r}')
        try:
            name = entry_name(rel)
        except ValueError as exc:
            raise FetchError(str(exc)) from exc
        if name in files:
            raise FetchError(f'duplicate file in the repository tree: {name!r}')
        if len(files) + 1 > MAX_SKILL_FILES:
            raise FetchError(
                f'the skill folder holds more than {MAX_SKILL_FILES} files'
            )
        declared = entry.get('size')
        if isinstance(declared, int) and declared > MAX_SKILL_FILE_BYTES:
            raise FetchError(
                f'{name!r} is {declared} bytes — the per-file limit '
                f'is {MAX_SKILL_FILE_BYTES}'
            )
        files[name] = b''  # reserve the slot; content arrives below
        if str(entry.get('mode')) == '100755':
            execs.add(name)
        blobs.append((name, str(entry.get('sha'))))

    total = 0
    for name, sha in blobs:
        blob_tail = f'git/blobs/{quote(sha, safe="")}'
        blob_url = target['api_root'] + _repo_path(
            target['owner'], target['repo'], blob_tail
        )
        blob = await _get_json(client, blob_url)
        if blob.get('encoding') != 'base64':
            raise FetchError(f'{name!r}: unexpected blob encoding')
        try:
            data = base64.b64decode(blob.get('content') or '')
        except (ValueError, TypeError) as exc:
            raise FetchError(f'{name!r}: undecodable blob') from exc
        if len(data) > MAX_SKILL_FILE_BYTES:
            raise FetchError(
                f'{name!r} is {len(data)} bytes — the per-file limit '
                f'is {MAX_SKILL_FILE_BYTES}'
            )
        total += len(data)
        if total > MAX_SKILL_TOTAL_BYTES:
            raise FetchError(
                'this skill exceeds the total size limit of '
                f'{MAX_SKILL_TOTAL_BYTES} bytes'
            )
        files[name] = data
    execs &= set(files)
    return files, execs


# ── Direct archive fetch ─────────────────────────────────


async def _open_guarded(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET with redirects followed MANUALLY — guard_url re-runs on
    every hop, so a redirect cannot hand off to a private address the
    first URL did not have."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        await guard_url(current)
        request = client.build_request('GET', current)
        try:
            resp = await client.send(request, stream=True)
        except httpx.HTTPError as exc:
            raise FetchError(
                f'could not reach {urlsplit(current).netloc}: {exc}',
                502,
            ) from exc
        if resp.status_code in (301, 302, 303, 307, 308):
            location = resp.headers.get('location')
            await resp.aclose()
            if not location:
                raise FetchError(
                    f'HTTP {resp.status_code} redirect with no Location'
                )
            current = str(httpx.URL(current).join(location))
            continue
        return resp
    raise FetchError(f'too many redirects from {url!r}')


async def _iter_archive(client: httpx.AsyncClient, url: str):
    resp = await _open_guarded(client, url)
    try:
        if resp.status_code != 200:
            raise FetchError(
                f'{urlsplit(url).path} returned HTTP {resp.status_code}'
            )
        declared = resp.headers.get('content-length', '')
        if declared.isdigit() and int(declared) > MAX_ARCHIVE_UPLOAD_BYTES:
            raise FetchError(
                f'the remote file is {declared} bytes — the archive '
                f'limit is {MAX_ARCHIVE_UPLOAD_BYTES}',
                413,
            )
        total = 0
        try:
            async for chunk in resp.aiter_raw():
                total += len(chunk)
                if total > MAX_ARCHIVE_UPLOAD_BYTES:
                    raise FetchError(
                        'the archive exceeds the '
                        f'{MAX_ARCHIVE_UPLOAD_BYTES} byte upload limit',
                        413,
                    )
                yield chunk
        except httpx.HTTPError as exc:
            raise FetchError(
                f'the download failed part-way: {exc}', 502
            ) from exc
    finally:
        await resp.aclose()


# ── The public entry ─────────────────────────────────────


async def open_archive_stream(url: str) -> AsyncIterator[bytes]:
    """Stream *url*'s skill as archive bytes (tar.gz).

    A GitHub-style tree/blob URL is walked through the trees/blobs
    API and repacked; anything else is streamed as the archive it
    already is. The caller pipes the chunks into the same staging +
    install path as any uploaded archive (``stage_stream`` owns the
    final caps; this module refuses oversize bodies early as well).
    """
    # Fail on the SHAPE before any socket opens (scheme, userinfo,
    # host); the address guard runs per hop on the URLs actually
    # requested.
    _check_literal_host(url)
    client = httpx.AsyncClient(
        timeout=FETCH_TIMEOUT,
        follow_redirects=False,
        headers={'Accept-Encoding': 'identity'},
        trust_env=False,
    )
    try:
        target = parse_github_source(url)
        if target is None:
            async for chunk in _iter_archive(client, url):
                yield chunk
            return
        files, execs = await _fetch_tree(client, target)
        # One thread hop for the tar/gzip build — the loop never
        # compresses (same rule as every other disk-adjacent work).
        yield await asyncio.to_thread(pack_tar_gz, files, execs)
    finally:
        await client.aclose()
