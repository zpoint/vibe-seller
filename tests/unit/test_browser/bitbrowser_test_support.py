"""Shared fixtures/fakes for the BitBrowser vendor tests.

Not a test module (no ``test_`` prefix): the vendor's unit tests and
its contract tests both build on the recorded live shapes served by
a fake local API (placeholders + sentinel site credentials) and the
fake CDP endpoint for after_open — one source, two modules.
"""

from dataclasses import asdict  # noqa: F401  (re-exported for tests)
import json
import logging  # noqa: F401  (re-exported for tests)

from aiohttp import web
import pytest  # noqa: F401  (re-exported for tests)

from app.browser.adapters.bitbrowser import BitBrowserClient
from app.browser.inject.client import OpenedStore
from app.browser.inject.errors import (  # noqa: F401
    ApiNotEnabled,
    ClientNotRunning,
    LoginRequired,
    StoreNotFound,
    StoreUnavailable,
    UnsupportedKernel,
)

# ── the fake local API (recorded shapes, placeholders only) ──────────


class FakeBitBrowserAPI:
    """Serves BitBrowser's documented+recorded response shapes."""

    def __init__(self, routes: dict | None = None):
        self.routes = routes or {}
        self.runner: web.AppRunner | None = None
        self.port = 0
        self.requests: list[dict] = []

    async def start(self) -> int:
        routes = self.routes
        seen = self.requests

        async def serve(request: web.Request) -> web.Response:
            body = None
            if request.can_read_body:
                try:
                    body = await request.json()
                except ValueError:
                    body = await request.text()
            seen.append({
                'method': request.method,
                'path': request.path,
                'json': body,
                'headers': dict(request.headers),
            })
            hit = routes.get((request.method, request.path))
            if hit is None:
                return web.json_response(
                    {'success': False, 'msg': '请传入 page'}, status=200
                )
            if callable(hit):
                status, payload = await hit(request)
            else:
                status, payload = hit
            return web.json_response(payload, status=status)

        app = web.Application()
        app.router.add_route('*', '/{tail:.*}', serve)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.port = self.runner.addresses[0][1]
        return self.port

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()


_servers: list[FakeBitBrowserAPI] = []
_clients: list[BitBrowserClient] = []


async def _serve(routes: dict | None = None) -> FakeBitBrowserAPI:
    server = FakeBitBrowserAPI(routes)
    await server.start()
    _servers.append(server)
    return server


def _client(port: int, **cfg) -> BitBrowserClient:
    client = BitBrowserClient({'api_port': port, **cfg})
    client.min_interval_s = 0.0  # tests are not rate-limit probes
    _clients.append(client)
    return client


async def cleanup() -> None:
    """The shared teardown: close clients, stop servers."""
    for client in _clients:
        await client.aclose()
    _clients.clear()
    for server in _servers:
        await server.stop()
    _servers.clear()


# The recorded list rows (placeholders + sentinel site credentials the
# leak assertions hunt for — the REAL rows carry userName/password/
# cookie/otherCookie/proxyPassword per profile).
ROWS = [
    {
        'id': '100000000001',
        'seq': 1,
        'name': 'demo-store-one',
        'platform': 'https://www.amazon.com/',
        'coreProduct': 'chrome',
        'coreVersion': '148',
        'userName': 'sentinel-secret-user-1',
        'password': 'sentinel-secret-pass-1',
        'cookie': 'sentinel-secret-cookie-1',
        'otherCookie': 'sentinel-secret-other-1',
        'proxyPassword': 'sentinel-secret-proxy-1',
    },
    {
        'id': '100000000002',
        'seq': 2,
        'name': 'demo-store-two',
        'platform': 'https://www.facebook.com/',
        'coreProduct': 'firefox',
        'coreVersion': '128',
        'userName': 'sentinel-secret-user-2',
        'password': 'sentinel-secret-pass-2',
        'cookie': 'sentinel-secret-cookie-2',
        'proxyPassword': 'sentinel-secret-proxy-2',
    },
]

SENTINELS = [
    v
    for row in ROWS
    for k, v in row.items()
    if k in ('userName', 'password', 'cookie', 'otherCookie', 'proxyPassword')
]


async def _list_route(request: web.Request):
    body = await request.json()
    page = int(body['page'])
    size = int(body['pageSize'])
    rows = ROWS[page * size : (page + 1) * size]
    return 200, {
        'success': True,
        'data': {
            'list': rows,
            'totalNum': len(ROWS),
            'page': page,
            'pageSize': size,
        },
    }


async def _detail_route(request: web.Request):
    body = await request.json()
    row = next((r for r in ROWS if r['id'] == body.get('id')), None)
    if row is None:
        return 200, {
            'success': False,
            'msg': '服务调用成功，但没有找到相应数据！',
        }
    return 200, {'success': True, 'data': dict(row)}


async def _open_route(request: web.Request):
    body = await request.json()
    if body.get('id') not in {r['id'] for r in ROWS}:
        return 200, {
            'success': False,
            'msg': '服务调用成功，但没有找到相应数据！',
        }
    return 200, {
        'success': True,
        'data': {
            'ws': 'ws://127.0.0.1:54321/devtools/browser/abc',
            'http': '127.0.0.1:54321',
            'coreVersion': '148',
            'driver': r'C:\chromedriver.exe',
            'seq': 1,
            'name': 'demo-store-one',
            'pid': 4242,
        },
    }


def _default_routes() -> dict:
    return {
        ('POST', '/browser/list'): _list_route,
        ('POST', '/browser/detail'): _detail_route,
        ('POST', '/browser/open'): _open_route,
        ('POST', '/browser/close'): (
            200,
            {'success': True, 'data': '操作成功'},
        ),
        ('POST', '/health'): (
            200,
            {'success': True, 'data': 'the server is running good.'},
        ),
    }


def _async_return(value):
    async def _inner():
        return value

    return _inner


# ── after_open: the workbench-tab hygiene (fake CDP endpoints) ───────


class FakeCDP:
    """The kernel's CDP surface after_open uses: /json/version, /json/list
    and the browser-level WebSocket (Target.closeTarget /
    Target.activateTarget), recording every WS command."""

    def __init__(
        self, pages: list[dict], listings: list[list[dict]] | None = None
    ):
        """``pages`` is what /json/list serves; ``listings`` (when set)
        serves each entry in turn and repeats the last — for races
        where a tab appears only on a later listing."""
        self.pages = pages
        self.listings = listings
        self._listing_count = 0
        self.listing_hits = 0
        self.commands: list[tuple[str, dict]] = []
        self.runner: web.AppRunner | None = None
        self.port = 0

    async def start(self) -> int:
        cdp = self

        async def version(request: web.Request) -> web.Response:
            return web.json_response({
                'Browser': 'FakeChrome/148',
                'webSocketDebuggerUrl': f'ws://127.0.0.1:{cdp.port}/devtools/browser/x',
            })

        async def targets(request: web.Request) -> web.Response:
            cdp.listing_hits += 1
            if cdp.listings is not None:
                idx = min(cdp._listing_count, len(cdp.listings) - 1)
                cdp._listing_count += 1
                return web.json_response(cdp.listings[idx])
            return web.json_response(cdp.pages)

        async def ws_handler(request: web.Request) -> web.WebSocketResponse:
            ws = web.WebSocketResponse(max_msg_size=0)
            await ws.prepare(request)
            async for msg in ws:
                cmd = json.loads(msg.data)
                cdp.commands.append((cmd['method'], cmd.get('params', {})))
                await ws.send_json({'id': cmd['id'], 'result': {}})
            return ws

        app = web.Application()
        app.router.add_get('/json/version', version)
        app.router.add_get('/json/list', targets)
        app.router.add_get('/devtools/browser/x', ws_handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.port = self.runner.addresses[0][1]
        return self.port

    async def stop(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    def target_id(self, url: str) -> str:
        return next(t['targetId'] for t in self.pages if t['url'] == url)


def _page(url: str, tid: str) -> dict:
    # The REAL /json/list shape: the target id is named `id` (no
    # `targetId` key) — the shape that hid the close-target bug.
    return {'type': 'page', 'url': url, 'id': tid}


async def _run_after_open(pages: list[dict]):
    cdp = FakeCDP(pages)
    await cdp.start()
    _servers.append(cdp)  # stop() shares the teardown loop

    client = _client(1)
    client.after_open_delay_s = 0.05
    client.after_open_deadline_s = 0.2
    await client.after_open(OpenedStore('127.0.0.1', cdp.port))
    return cdp
