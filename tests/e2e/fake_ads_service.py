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
MARKETPLACE = 'SA'

#: The API store's 30-day spend. Deliberately unlike the console's 300.00.
API_SPEND = '123.45'
API_SALES = '617.25'

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


class AdsService(http.server.ThreadingHTTPServer):
    """The service, plus a log of every call it answered."""

    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls: list[tuple[str, str, dict]] = []
        self._lock = threading.Lock()

    def record(self, method: str, path: str, query: dict) -> None:
        with self._lock:
            self.calls.append((method, path, query))

    def calls_to(self, path: str) -> list[dict]:
        with self._lock:
            return [q for _, p, q in self.calls if p == path]


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
            return self._reply(200, {'version': 'e2e-1'})
        if url.path == '/skill/bundle':
            return self._reply(200, {'files': {'SKILL.md': SKILL_MD}})
        if url.path == '/facts/rollup':
            key = query.get('store_key')
            if not key:
                return self._reply(400, {'detail': 'store_key is required'})
            return self._reply(200, _rollup(key))
        return self._reply(404, {'detail': f'no such path: {url.path}'})

    def do_GET(self):  # noqa: N802 (stdlib interface)
        self._route('GET')

    def do_POST(self):  # noqa: N802 (stdlib interface)
        self._route('POST')

    def log_message(self, *_args):
        pass  # keep CI output readable


def serve() -> AdsService:
    """Start the service on its fixed port."""
    server = AdsService(('127.0.0.1', PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
