"""A stand-in ads service a real vibe-seller can bind stores to in CI.

Just enough of the service for one question: when some stores are
authorized for the Ads API and others are not, does each store's agent
take its own path? So it answers registration, the store sync (which
decides who is authorized), the skill bundle, and one read — a campaign
rollup — whose figures exist nowhere else. A report carrying them can
only have come through the API; the browser stores' console carries
different ones (``fake_ads_console``).

The server reaches it through ``VIBE_ADS_SERVICE_URL``, read once at
start, so it listens on a fixed port rather than an ephemeral one.

All figures are round-ish and invented.
"""

from __future__ import annotations

import http.server
import json
import os
import threading
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get('E2E_ADS_SERVICE_PORT', '7791'))
URL = f'http://127.0.0.1:{PORT}'

#: A store whose name carries this is authorized; every other is not.
AUTHORIZED_MARK = '-api-'
#: The bundle version /skill/version reports.
VERSION = 'e2e-1'
MARKETPLACE = 'SA'

#: The API store's 30-day spend. Deliberately unlike the console's 300.00.
API_SPEND = '123.45'
API_SALES = '617.25'

#: The second version's own figure, on the second version's own path.
#: Neither appears in :data:`SKILL_MD`, so a report carrying it proves
#: the client read the newer document.
VERSION_V2 = 'e2e-2'
PULSE = '424.24'
PULSE_PATH = '/facts/e2e-pulse'

SKILL_MD = """---
name: amazon-ads-api
description: "ONLY for a store authorized with the ads service. Load this BEFORE any advertising work on such a store. Everything goes through the `vibe_seller_ads_call` tool — never a browser."
---

# Amazon Ads — over the API

This store is bound to an ads service. You do not open a browser for
advertising work here. One tool reaches everything:

    vibe_seller_ads_call(path, method?, params?, body?, store, marketplace?)

## Spend and sales per campaign

    vibe_seller_ads_call(path="/facts/rollup", params={"grain": "campaign"},
                         store="<store name>")

Answers the last 30 days per campaign, with totals.
"""

#: What the service ships after one change lands: the same document,
#: plus the path and the figure that did not exist in ``e2e-1``. A
#: client that refreshed reads it; a client that did not has no idea
#: this path is there, and its calls to it are guesses.
SKILL_MD_V2 = (
    SKILL_MD
    + """
## The pulse metric

    vibe_seller_ads_call(path="/facts/e2e-pulse", store="<store name>")

Answers this store's advertising pulse for the last 30 days. It exists
only here — the console has no such number — and is the figure to quote
when the question asks for the store's pulse.
"""
)


class AdsService(http.server.ThreadingHTTPServer):
    """The service, plus a log of every call it answered.

    **What it ships lives on the instance, not in module globals.** Two
    fakes can run in one process (xdist workers each start their own,
    and a test may want two services on two ports), and a version
    published through a module global would leak between them — one
    test's ``publish`` silently making another's client current.
    """

    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, str, dict]] = []
        self._lock = threading.Lock()
        self.bundle_version = VERSION
        self.bundle_files: dict[str, str] = {'SKILL.md': SKILL_MD}

    def record(self, method: str, path: str, query: dict) -> None:
        with self._lock:
            self.calls.append((method, path, query))

    def calls_to(self, path: str) -> list[dict]:
        with self._lock:
            return [q for _, p, q in self.calls if p == path]

    def publish(self, version: str, files: dict[str, str]) -> None:
        """Ship a new bundle, the way a deploy on the real service does."""
        with self._lock:
            self.bundle_version = version
            self.bundle_files = dict(files)

    def served_version(self) -> str:
        with self._lock:
            return self.bundle_version

    def served(self, name: str = 'SKILL.md') -> str:
        """The bytes a client pulling the bundle would get for *name*."""
        with self._lock:
            return self.bundle_files[name]

    def served_files(self) -> dict[str, str]:
        with self._lock:
            return dict(self.bundle_files)

    def log_index(self) -> int:
        """Where the call log stands now, for an order assertion."""
        with self._lock:
            return len(self.calls)

    def calls_since(self, index: int) -> list[tuple[str, str, dict]]:
        """Everything answered since :meth:`log_index` was taken."""
        with self._lock:
            return list(self.calls[index:])


def store_key(local_id: str) -> str:
    return f'sk_{local_id[:8]}'


def _rollup(key: str) -> dict:
    return {
        'store_key': key,
        'window': 'last 30 days',
        'currency': 'SAR',
        'rows': [
            {
                'campaign': 'api-widget auto',
                'spend': API_SPEND,
                'sales': API_SALES,
                'orders': 9,
            }
        ],
        'total': {'spend': API_SPEND, 'sales': API_SALES, 'orders': 9},
    }


class _Handler(http.server.BaseHTTPRequestHandler):
    def _reply(self, status: int, body) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        # What the real service puts on EVERY answer, the refused ones
        # included (vibe-ads-server PR #19), and the reason this whole
        # test module exists. One place, in the reply, so no route here
        # can miss it either.
        self.send_header('X-Skill-Version', self.server.served_version())
        self.end_headers()
        self.wfile.write(payload)

    def _route(self, method: str) -> None:
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.server.record(method, url.path, query)
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length) or b'{}') if length else {}

        if (method, url.path) == ('POST', '/installations'):
            return self._reply(200, {'api_key': 'vas_e2e'})
        if (method, url.path) == ('POST', '/me/stores'):
            rows = []
            for s in body.get('stores', []):
                ok = AUTHORIZED_MARK in s['name']
                rows.append({
                    'local_id': s['local_id'],
                    'name': s['name'],
                    'marketplace': MARKETPLACE if ok else None,
                    'store_key': store_key(s['local_id']) if ok else None,
                    'authorized': ok,
                })
            return self._reply(200, {'stores': rows})
        if url.path == '/skill/version':
            return self._reply(
                200,
                {
                    'version': self.server.served_version(),
                    'files': len(self.server.served_files()),
                },
            )
        if url.path == '/skill/bundle':
            return self._reply(
                200,
                {
                    'version': self.server.served_version(),
                    'files': self.server.served_files(),
                },
            )
        if url.path == '/facts/rollup':
            key = query.get('store_key')
            if not key:
                return self._reply(400, {'detail': 'store_key is required'})
            return self._reply(200, _rollup(key))
        if url.path == PULSE_PATH:
            # Answered since the first version, so the ONLY thing that
            # keeps a client from finding it is the skill that names it.
            key = query.get('store_key')
            if not key:
                return self._reply(400, {'detail': 'store_key is required'})
            return self._reply(
                200,
                {'store_key': key, 'window': 'last 30 days', 'pulse': PULSE},
            )
        return self._reply(404, {'detail': f'no such path: {url.path}'})

    def do_GET(self):  # noqa: N802 (stdlib interface)
        self._route('GET')

    def do_POST(self):  # noqa: N802 (stdlib interface)
        self._route('POST')

    def log_message(self, *_args):
        pass  # keep CI output readable


def serve(port: int = PORT) -> AdsService:
    """Start the service — on the fixed port by default.

    The fixed port belongs to the SHARED e2e server, which reads
    ``VIBE_ADS_SERVICE_URL`` once at boot, so only tests on that server
    use it. A suite that starts its own server passes ``port=0`` and
    points its server at :func:`url_of` instead: two suites on one fixed
    port collide whenever xdist runs them at the same time (observed: the
    second bind failed with "Address already in use").
    """
    server = AdsService(('127.0.0.1', port), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def url_of(server: AdsService) -> str:
    """The base URL a started service answers on."""
    return f'http://127.0.0.1:{server.server_address[1]}'


if __name__ == '__main__':  # standalone: the e2e runner starts this
    import time

    serve()
    print(f'fake ads service on {URL}', flush=True)
    while True:
        time.sleep(60)
