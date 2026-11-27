"""A fake 稳卖 client HTTP API + kernel CDP endpoint (unit-test server).

Serves the response shapes verified live (client v5.1.0715) so the real
``RealWenmaiClient`` can be exercised in CI without Windows: paginated
queryEnvV2, the two logged-out shapes, the device-unbound refusal, a
Firefox-kernel env, an openEnv that returns a CDP endpoint (the SAME
one on reopen), a slow relay, and the store browser's middle page over
a real WebSocket CDP target.

Placeholder data only (acme / demo-store / 203.0.113.10).
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json

from aiohttp import web

from app.browser.adapters.wenmai_dom import (
    CLICK_OPEN_STORE_JS,
    MIDDLE_READY_JS,
)


def _env(
    env_id: int,
    name: str,
    *,
    platform: str = '澳大利亚亚马逊',
    ip_id: int | None = 100200300,
) -> dict:
    return {
        'id': env_id,
        'storeId': env_id,
        'name': name,
        'storeName': name,
        'platform': platform,
        'actualPlatform': '亚马逊',
        'storeAccount': None,
        'ipId': ip_id,
        'ipAddress': '203.0.113.10',
        'equipmentType': 'self',
        'region': 'demo',
        'localTypeName': '静态网络',
        'ipPlatform': '自有',
    }


class FakeWenmaiApi:
    """The client's local API on 127.0.0.1:<random port>.

    State is flipped through the attributes (``logged_out``,
    ``slow_relay_s``, …) so one server can walk a scenario.
    """

    def __init__(
        self,
        stores: list[dict] | None = None,
        *,
        company: str = 'acme',
    ):
        self.stores = (
            stores
            if stores is not None
            else [
                _env(100000000001, 'demo-store'),
            ]
        )
        self.company = company
        #: Logged out → relay methods answer the top-level error shape
        #: and getUserInfo answers code 200 with token null.
        self.logged_out = False
        #: Delay before a RELAYED method answers (the slow-relay case:
        #: the main window still loading).
        self.slow_relay_s = 0.0
        #: Cold-boot race (W5-4): queryEnvV2 answers while getUserInfo
        #: is still loading — it refuses with the generic error shape
        #: until the account state finishes loading.
        self.user_info_loading = False
        #: openEnv result overrides per env id: 'unbound' | 'firefox' |
        #: 'unknown' | None (normal).
        self.open_results: dict[int, str] = {}
        #: The CDP endpoint a successful openEnv reports (and that
        #: reopen must report UNCHANGED).
        self.cdp_port = 0  # set to a listener for the happy path
        self.cdp_host = '127.0.0.1'
        self.call_log: list[str] = []
        #: Server-side concurrency watch (the rate-limit contract is
        # ONE request at a time): peak concurrent handlers.
        self.in_flight = 0
        self.peak = 0
        self.runner: web.AppRunner | None = None
        self.port = 0

    # ── lifecycle ───────────────────────────────────────────────────

    async def start(self) -> int:
        app = web.Application()
        app.router.add_post('/{method}', self._post)
        app.router.add_get('/getAllInfo', self._get_all_info)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.port = self.runner.addresses[0][1]
        return self.port

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    # ── handlers ────────────────────────────────────────────────────

    async def _get_all_info(self, _request: web.Request) -> web.Response:
        # Shell-answered: always responds, login or not.
        self.call_log.append('getAllInfo')
        with self._watch():
            return web.json_response({'code': 200, 'msg': 'success'})

    async def _post(self, request: web.Request) -> web.Response:
        method = request.match_info['method']
        self.call_log.append(method)
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if self.slow_relay_s and method != 'getUserInfo':
            await asyncio.sleep(self.slow_relay_s)
        handler = {
            'queryEnvV2': self._query_env_v2,
            'openEnv': self._open_env,
            'closeEnv': self._close_env,
            'getUserInfo': self._get_user_info,
        }.get(method)
        if handler is None:
            return web.json_response({
                'method': method,
                'status': 'error',
                'code': '404',
            })
        with self._watch():
            return await handler(method, body)

    @contextmanager
    def _watch(self):
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            yield
        finally:
            self.in_flight -= 1

    async def _relay_logged_out(self, method: str) -> web.Response:
        # The REAL logged-out shape of the relayed methods (the docs
        # claim 401; verified live 2026-11-27).
        return web.json_response({
            'method': method,
            'msg': '认证失败，无法访问系统资源',
            'status': 'error',
            'code': '400',
        })

    async def _query_env_v2(self, method, body) -> web.Response:
        if self.logged_out:
            return await self._relay_logged_out(method)
        try:
            page = int(body.get('pageNum', '1'))
            size = int(body.get('pageSize', '20'))
        except (TypeError, ValueError):
            page, size = 1, 20
        size = max(1, min(size, 100))
        total = len(self.stores)
        start = (page - 1) * size
        return web.json_response({
            'method': method,
            'response': {
                'code': 200,
                'msg': '查询成功',
                'total': total,
                'page': str(page),
                'pageSize': str(size),
                'pageTotal': (total + size - 1) // size,
                'envs': self.stores[start : start + size],
            },
        })

    async def _open_env(self, method, body) -> web.Response:
        if self.logged_out:
            return await self._relay_logged_out(method)
        try:
            env_id = int(body.get('id', ''))
        except (TypeError, ValueError):
            env_id = -1
        verdict = self.open_results.get(env_id)
        if verdict == 'unbound':
            return web.json_response({
                'status': 'error',
                'code': '400',
                'msg': '当前店铺未绑定可用设备，无法启动',
            })
        if verdict in ('firefox', 'unknown'):
            # No response wrapper — a refusal the caller must classify.
            return web.json_response({
                'status': 'error',
                'code': '400',
                'msg': (
                    '该环境使用Firefox内核'
                    if verdict == 'firefox'
                    else '环境不存在'
                ),
            })
        if not any(e['id'] == env_id for e in self.stores):
            return web.json_response({
                'status': 'error',
                'code': '400',
                'msg': '环境不存在',
            })
        return web.json_response({
            'response': {
                'code': 200,
                'msg': 'success',
                'pid': 4848,
                'selenium': f'http://{self.cdp_host}:{self.cdp_port}',
                'webdriverPath': (r'C:\demo\chromedriver.exe'),
            }
        })

    async def _close_env(self, method, body) -> web.Response:
        if self.logged_out:
            return await self._relay_logged_out(method)
        return web.json_response({'response': {'code': 200, 'msg': 'success'}})

    async def _get_user_info(self, method, _body) -> web.Response:
        if self.user_info_loading:
            # Cold-boot loading (C4's native run, 2026-11-27): the relay
            # answers queryEnvV2 while getUserInfo is not ready — the
            # client refuses it with the generic top-level error shape
            # (the vendor's own wording, seen live). The contract under
            # test: anything that is not (a token | the definitive
            # logged-out shape) is NOT an answer yet.
            return web.json_response({
                'method': method,
                'msg': '未知请求，请检查配置信息！',
                'status': 'error',
                'code': '400',
            })
        if self.logged_out:
            # code 200 STILL means logged out here; the tell is the
            # null token (verified live).
            return web.json_response({
                'response': {
                    'code': 200,
                    'msg': '认证失败，无法访问系统资源',
                    'token': None,
                }
            })
        return web.json_response({
            'response': {
                'code': 200,
                'msg': 'success',
                'companyId': 1,
                'companyName': self.company,
                'userId': 2,
                'token': 'demo-token-value',
            }
        })


class FakeMiddlePageCdp:
    """A kernel CDP endpoint whose only page is the middle page.

    Serves ``/json/list`` (a chrome-extension middlePage target until
    打开店铺 is clicked via Runtime.evaluate, then an http store page
    too) and a WebSocket that answers Runtime.evaluate. Matching is by
    the ``middlePage`` PATH — never a hardcoded extension id.
    """

    CLICK_JS = (
        '(function(){var b=document.querySelector('
        "'button.btn-primary');if(!b)return 'NOBTN';b.click();"
        "return 'OK';})()"
    )

    def __init__(self):
        self.runner: web.AppRunner | None = None
        self.port = 0
        self.store_page_open = False
        self.evals: list[str] = []
        #: True once the middle page has rendered its button; a click
        #: fired before this is silently dropped (the live race).
        self.ready = True

    async def start(self) -> int:
        app = web.Application()
        app.router.add_get('/json/list', self._list)
        app.router.add_get('/json/version', self._version)
        app.router.add_get('/devtools/page/middle', self._page_ws)
        app.router.add_get('/devtools/browser/x', self._browser_ws)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        self.port = self.runner.addresses[0][1]
        return self.port

    async def stop(self) -> None:
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    async def _version(self, _request: web.Request) -> web.Response:
        return web.json_response({
            # A real Chromium family string: the engine refuses a
            # non-Chromium kernel by this field.
            'Browser': 'Chrome/135.0.0.0',
            'webSocketDebuggerUrl': (
                f'ws://127.0.0.1:{self.port}/devtools/browser/x'
            ),
        })

    async def _list(self, _request: web.Request) -> web.Response:
        pages = [
            {
                'type': 'page',
                'url': 'chrome-extension://demo-ext-id/middlePage/index.html',
                'title': 'demo-store | 澳大利亚亚马逊',
                'webSocketDebuggerUrl': (
                    f'ws://127.0.0.1:{self.port}/devtools/page/middle'
                ),
            }
        ]
        if self.store_page_open:
            pages.append({
                'type': 'page',
                'url': 'https://sellercentral.example.com.au/',
                'title': 'demo store',
                'webSocketDebuggerUrl': (
                    f'ws://127.0.0.1:{self.port}/devtools/page/store'
                ),
            })
        return web.json_response(pages)

    async def _browser_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == 1:  # WSMsgType.TEXT
                data = json.loads(msg.data)
                if data.get('method') == 'Browser.close':
                    self.store_page_open = False
                await ws.send_json({'id': data.get('id'), 'result': {}})
        return ws

    async def _page_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)
        async for msg in ws:
            if msg.type == 1:
                data = json.loads(msg.data)
                if data.get('method') == 'Runtime.evaluate':
                    expr = data['params']['expression']
                    self.evals.append(expr)
                    if expr == MIDDLE_READY_JS:
                        value = self.ready
                    elif expr == CLICK_OPEN_STORE_JS and self.ready:
                        self.store_page_open = True
                        value = 'OK'
                    else:
                        # A click into a half-loaded page is dropped.
                        value = 'NOBTN' if expr == CLICK_OPEN_STORE_JS else None
                    await ws.send_json({
                        'id': data['id'],
                        'result': {'result': {'value': value}},
                    })
                else:
                    await ws.send_json({'id': data.get('id'), 'result': {}})
        return ws
