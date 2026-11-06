"""A stand-in GitHub for every sync test.

Answers exactly what the sync tier asks of GitHub and nothing else:

* the commits probe (``GET /repos/.../commits?path=...``) — with real
  ``ETag`` / ``If-None-Match`` / ``304`` semantics, because the whole
  point of the stored validator is that an unchanged upstream costs a
  304 rather than a body;
* raw file bodies (``MANIFEST.txt`` and the files it lists).

Usable two ways, from one dispatch:

* as an ``httpx.MockTransport`` (unit / integration tests, no sockets);
* as a real HTTP server (e2e / upgrade tests, where a separately
  running server is under test and must be pointed here via
  ``VIBE_GITHUB_API_BASE`` / ``SKILLS_REPO_URL`` / ``KNOWLEDGE_REPO_URL``).

Every request is counted per endpoint, so a test can assert the thing
this fixture exists for: that a probe which finds nothing new costs ONE
request and no downloads.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
import http.server
import json
import re
import threading

import httpx

#: The repo the real probes name (app/sync/github.py).
REPO_PATH = '/repos/zpoint/vibe-seller/commits'

# /raw/app/<tree>/<rel path> → the file map key <tree>/<rel path>
_RAW_RE = re.compile(r'^/raw/(?P<key>.+)$')


def _query_key(target: str) -> str:
    """What this request is about: the ``path=`` of a commits probe.

    Each probed repo path gets its own validator, like real GitHub —
    a single shared one would let a skills 304 stand in for a
    knowledge change and hide exactly the class of bug these tests
    exist for.
    """
    _, _, query = target.partition('?')
    for pair in query.split('&'):
        name, _, value = pair.partition('=')
        if name == 'path':
            return value
    return ''


class _Reply:
    """One answer, shaped for both transports."""

    def __init__(
        self,
        status: int,
        body: bytes = b'',
        headers: dict | None = None,
    ) -> None:
        self.status = status
        self.body = body
        self.headers = headers or {}


class FakeGitHub:
    """In-memory GitHub: a commit, a tree, and request counters."""

    def __init__(
        self,
        *,
        commit: str = 'a' * 40,
        etag: str = 'W/"v1"',
        changed: bool = True,
    ) -> None:
        #: Path (relative to the repo root, e.g. ``app/knowledge/x.md``)
        #: → body. The trees the syncs read are ``app/skills_v2`` and
        #: ``app/knowledge``.
        self.files: dict[str, bytes] = {}
        self.commit = commit
        self.etag = etag
        #: False → the commits probe answers 304 to a conditional
        #: request (upstream hasn't moved).
        self.changed = changed
        #: Set to force a 304 even on an unconditional request, to pin
        #: "an unsolicited 304 is a failure, not a no-op".
        self.force_unsolicited_304 = False
        self.counts: Counter = Counter()
        #: Substrings of request paths that answer 500 (e2e failure
        #: injection).
        self.failing: set[str] = set()
        self.log: list[str] = []
        #: Headers of every request, in order — so a test can assert
        #: that a conditional request actually carried If-None-Match.
        self.seen_headers: list[dict[str, str]] = []
        #: Validator per repo path: each URL gets its own ETag, exactly
        #: like GitHub (a shared one would let a skills 304 stand in for
        #: a knowledge change).
        self.path_etags: dict[str, str] = {}
        self.default_etag = etag

    # ── seeding ───────────────────────────────────────────────

    def add_tree(self, prefix: str, files: dict[str, str | bytes]) -> None:
        """Add ``{rel: content}`` under ``prefix`` (a repo-root path)."""
        for rel, content in files.items():
            self.files[f'{prefix}/{rel}'.lstrip('/')] = (
                content.encode() if isinstance(content, str) else content
            )

    def set_manifest(self, prefix: str, paths: list[str]) -> None:
        """Serve a MANIFEST.txt listing ``paths`` (relative to prefix)."""
        self.files[f'{prefix}/MANIFEST.txt'] = '\n'.join(paths).encode()

    def upstream_changed(self, commit: str | None = None) -> str:
        """Move upstream: a new commit, and a new validator for it."""
        self.commit = commit or ('b' * 40)
        self.etag = f'W/"{self.commit[:8]}"'
        self.path_etags.clear()
        self.changed = True
        return self.commit

    # ── dispatch (shared by both transports) ──────────────────

    def dispatch(
        self, method: str, path: str, headers: dict[str, str]
    ) -> _Reply:
        self.log.append(path)
        if any(part in path for part in self.failing) and not path.startswith(
            '/__'
        ):
            self.counts['failed_files'] += 1
            return _Reply(500, b'boom')
        key = _query_key(path)
        # ``BaseHTTPRequestHandler`` hands over the request target
        # INCLUDING its query string; httpx's ``url.path`` does not.
        path = path.partition('?')[0]
        lower = {k.lower(): v for k, v in headers.items()}
        self.seen_headers.append(lower)
        if path == '/__control':
            # Out-of-process control plane for the e2e tests: they drive
            # the fake the server is pointed at over HTTP. httpx
            # canonicalises header names (X-Control), so look it up
            # case-insensitively.
            body = json.loads(lower.get('x-control') or '{}')
            action = body.get('action')
            if action == 'upstream_changed':
                self.upstream_changed(body.get('commit'))
            elif action == 'set_manifest':
                self.set_manifest('app/skills_v2', body.get('files', []))
                self.add_tree('app/skills_v2', body.get('content', {}))
            elif action == 'set_knowledge':
                self.set_manifest('app/knowledge', body.get('files', []))
                self.add_tree('app/knowledge', body.get('content', {}))
            elif action == 'fail_path':
                self.failing.add(body.get('path', ''))
            elif action == 'clear_failures':
                self.failing.clear()
            return _Reply(200, b'{"ok": true}')

        if method != 'GET':
            self.counts['other'] += 1
            return _Reply(405, b'method not allowed')

        if path == '/__counts':
            return _Reply(
                200,
                json.dumps({
                    'counts': dict(self.counts),
                    'commit': self.commit,
                    'etag': self.etag,
                    'path_etags': self.path_etags,
                    'conditional': sum(
                        1 for h in self.seen_headers if 'if-none-match' in h
                    ),
                    'seen_headers': self.seen_headers[-12:],
                }).encode(),
            )
        if path.endswith(REPO_PATH):
            return self._commits(lower, key)
        match = _RAW_RE.match(path)
        if match:
            return self._raw(match.group('key'))
        self.counts['other'] += 1
        return _Reply(404, b'not found')

    def _commits(self, headers: dict[str, str], path_key: str) -> _Reply:
        self.counts['commits'] += 1
        # Pin a validator per probed path (and report them in
        # /__counts): each URL gets its own ETag, like real GitHub.
        current = self.path_etags.setdefault(path_key, self.etag)
        if self.force_unsolicited_304:
            self.counts['not_modified'] += 1
            return _Reply(304, headers={'etag': self.etag})
        # 304 whenever the client's validator still describes what is
        # being served — exactly like GitHub, which needs no "upstream
        # moved" flag: ``upstream_changed()`` bumps the etag, and that
        # mismatch is what forces a 200.
        conditional = headers.get('if-none-match')
        if conditional and conditional == current:
            self.counts['not_modified'] += 1
            return _Reply(304, headers={'etag': current})
        body = json.dumps([{'sha': self.commit}]).encode()
        return _Reply(200, body, headers={'etag': current})

    def _raw(self, key: str) -> _Reply:
        self.counts['files'] += 1
        content = self.files.get(key)
        if content is None:
            return _Reply(404, b'not found')
        return _Reply(200, content)

    # ── httpx.MockTransport side ──────────────────────────────

    def handler(self, request: httpx.Request) -> httpx.Response:
        reply = self.dispatch(
            request.method,
            request.url.path,
            dict(request.headers),
        )
        return httpx.Response(
            reply.status, content=reply.body, headers=reply.headers
        )

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def client(self) -> httpx.AsyncClient:
        """A client against this fake (use as ``async with``)."""
        return httpx.AsyncClient(transport=self.transport())

    # ── real-server side (e2e / upgrade tests) ────────────────

    def start(self, host: str = '127.0.0.1', port: int = 0) -> str:
        """Serve on a background thread; returns the base URL."""
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _handle(self):
                reply = fake.dispatch(
                    self.command, self.path, dict(self.headers)
                )
                self.send_response(reply.status)
                for key, value in reply.headers.items():
                    self.send_header(key, value)
                self.send_header(
                    'Content-Length',
                    str(len(reply.body)),
                )
                self.end_headers()
                self.wfile.write(reply.body)

            def do_POST(self):  # noqa: N802 — stdlib naming
                self._handle()

            def do_GET(self):  # noqa: N802 — stdlib naming
                reply = fake.dispatch('GET', self.path, dict(self.headers))
                self.send_response(reply.status)
                for key, value in reply.headers.items():
                    self.send_header(key, value)
                self.send_header(
                    'Content-Length',
                    str(len(reply.body)),
                )
                self.end_headers()
                self.wfile.write(reply.body)

            def log_message(self, *args) -> None:
                pass

        server = http.server.ThreadingHTTPServer((host, port), Handler)
        server.allow_reuse_address = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self._server = server
        self._thread = thread
        host_name, bound_port = server.server_address[:2]
        self.base_url = f'http://{host_name}:{bound_port}'
        return self.base_url

    def stop(self) -> None:
        server = getattr(self, '_server', None)
        if server is not None:
            server.shutdown()
            server.server_close()
            self._server = None

    def urls(self) -> dict[str, str]:
        """The three env values the server under test needs."""
        base = self.base_url
        return {
            'VIBE_GITHUB_API_BASE': f'{base}/api',
            'SKILLS_REPO_URL': f'{base}/raw/app/skills_v2',
            'KNOWLEDGE_REPO_URL': f'{base}/raw/app/knowledge',
        }


def client_for(base_url: str) -> FakeGitHubClient:
    """Handle on a fake GitHub served from another process."""
    return FakeGitHubClient(base_url)


class FakeGitHubClient:
    """A handle on a fake GitHub running in ANOTHER process.

    The e2e server fetches from the fake over HTTP, so the fake cannot
    live in the test process. What a test needs back is the request
    counters — served over ``/__counts``.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip('/')

    def counts(self) -> dict:
        resp = httpx.get(f'{self.base_url}/__counts', timeout=10)
        resp.raise_for_status()
        return resp.json()['counts']


@contextmanager
def fake_github_server(**kwargs):
    """Run a :class:`FakeGitHub` as a real server (e2e / upgrade)."""
    fake = FakeGitHub(**kwargs)
    fake.start()
    try:
        yield fake
    finally:
        fake.stop()
