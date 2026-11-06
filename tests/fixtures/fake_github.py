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
        self.log: list[str] = []
        #: Headers of every request, in order — so a test can assert
        #: that a conditional request actually carried If-None-Match.
        self.seen_headers: list[dict[str, str]] = []

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
        self.changed = True
        return self.commit

    # ── dispatch (shared by both transports) ──────────────────

    def dispatch(
        self, method: str, path: str, headers: dict[str, str]
    ) -> _Reply:
        self.log.append(path)
        lower = {k.lower(): v for k, v in headers.items()}
        self.seen_headers.append(lower)
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
                }).encode(),
            )
        if path.endswith(REPO_PATH):
            return self._commits(lower)
        match = _RAW_RE.match(path)
        if match:
            return self._raw(match.group('key'))
        self.counts['other'] += 1
        return _Reply(404, b'not found')

    def _commits(self, headers: dict[str, str]) -> _Reply:
        self.counts['commits'] += 1
        if self.force_unsolicited_304:
            return _Reply(304, headers={'etag': self.etag})
        conditional = headers.get('if-none-match')
        if not self.changed and conditional and conditional == self.etag:
            return _Reply(304, headers={'etag': self.etag})
        body = json.dumps([{'sha': self.commit}]).encode()
        return _Reply(200, body, headers={'etag': self.etag})

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


@contextmanager
def fake_github_server(**kwargs):
    """Run a :class:`FakeGitHub` as a real server (e2e / upgrade)."""
    fake = FakeGitHub(**kwargs)
    fake.start()
    try:
        yield fake
    finally:
        fake.stop()
