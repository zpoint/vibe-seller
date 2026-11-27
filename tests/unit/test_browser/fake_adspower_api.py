"""A fake AdsPower local API + kernel CDP endpoint (unit-test server).

Serves the response shapes verified live (client v8.9.23) so the real
:class:`AdsPowerClient` can be exercised in CI without Windows:
paginated user/list, the exact error bodies (``Require api-key``,
``API Key mismatch``, ``Too many request per second``, ``invalid
disordered id``, ``User_id is not open``, the kernel-download
messages), the start shapes (CDP endpoint / empty-ws Firefox), stop,
active, local-active, and the kernel's ``/json/version``. State is
flipped through the attributes so one server can walk a scenario.

Placeholder data only (acme / demo-store / 203.0.113.10 / kaaaaaa1).
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
from pathlib import Path

from aiohttp import web

_FIXTURES = Path(__file__).resolve().parents[2] / 'fixtures' / 'adspower'


def _load(name: str) -> dict:
    return json.loads((_FIXTURES / f'{name}.json').read_text())


def _store(
    user_id: str,
    name: str,
    *,
    serial: str,
    group: str = 'acme',
    domain: str = 'https://www.amazon.com',
) -> dict:
    return {
        'serial_number': serial,
        'user_id': user_id,
        'name': name,
        'group_id': '110000000001',
        'group_name': group,
        'domain_name': domain,
        'username': '',
        'remark': '',
        'created_time': '1795745501',
        'ip': '203.0.113.10' if domain else '',
        'ip_country': 'cn' if domain else '',
        'password': '',
        'last_open_time': '0',
        'user_proxy_config': {'proxy_soft': 'no_proxy'},
        'ipchecker': 'ip2location',
        'fakey': '',
        'fbcc_proxy_acc_id': '',
        'sys_app_cate_id': '0',
        'fbcc_user_tag': [],
        'platform_account': [],
    }


class FakeAdsPowerApi:
    """The client's local API on 127.0.0.1:<random port>."""

    def __init__(self, stores: list[dict] | None = None):
        self.stores = (
            stores
            if stores is not None
            else [
                _store('kaaaaaa1', 'demo-store', serial='1'),
                _store('kaaaaaa2', 'demo-firefox', serial='2', domain=''),
            ]
        )
        #: The key the API accepts ('' = no security verification).
        self.api_key = 'test-key-123'
        #: start result overrides per user_id: 'firefox' | 'flower159'
        #: | 'flower100' | 'downloading' | 'not_ready' | None (normal
        #: CDP answer).
        self.start_results: dict[str, str] = {}
        #: Consecutive rate-limit answers before the real one (0 = off).
        self.rate_limit_burst = 0
        #: Delay before /status answers (a launching client).
        self.status_delay_s = 0.0
        #: Whether /status answers at all (False = nothing listens).
        self.up = True
        #: Every (path, params) the server saw, for param assertions.
        self.calls: list[tuple[str, dict]] = []
        #: Auth state of the last guarded call (for handlers).
        self._auth_error = ''
        self._runner: web.AppRunner | None = None
        self.port = 0
        #: The fake kernel CDP listener's port (0 = none).
        self.cdp_port = 0
        self._cdp_runner: web.AppRunner | None = None

    # ── lifecycle ───────────────────────────────────────────────────

    async def start(self) -> int:
        app = web.Application()
        app.router.add_get('/status', self._status)
        app.router.add_get('/api/v1/user/list', self._list)
        app.router.add_get('/api/v1/browser/start', self._start)
        app.router.add_get('/api/v1/browser/stop', self._stop)
        app.router.add_get('/api/v1/browser/active', self._active)
        app.router.add_get('/api/v1/browser/local-active', self._local_active)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, '127.0.0.1', 0)
        await site.start()
        self.port = self._runner.addresses[0][1]
        await self.start_cdp()
        return self.port

    async def start_cdp(self) -> int:
        """The kernel's CDP endpoint (answers /json/version)."""
        if self._cdp_runner is not None:
            return self.cdp_port
        app = web.Application()
        app.router.add_get('/json/version', self._json_version)
        self._cdp_runner = web.AppRunner(app)
        await self._cdp_runner.setup()
        site = web.TCPSite(self._cdp_runner, '127.0.0.1', 0)
        await site.start()
        self.cdp_port = self._cdp_runner.addresses[0][1]
        return self.cdp_port

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        if self._cdp_runner:
            await self._cdp_runner.cleanup()
            self._cdp_runner = None

    async def stop_cdp(self) -> None:
        """Simulate the kernel dying: /json/version stops answering."""
        if self._cdp_runner:
            await self._cdp_runner.cleanup()
            self._cdp_runner = None

    # ── auth + bookkeeping ──────────────────────────────────────────

    @contextmanager
    def _guard(self, request: web.Request):
        self.calls.append((request.path, dict(request.query)))
        header = request.headers.get('Authorization') or ''
        if self.api_key:
            # Security verification ON (the shape verified live): no
            # key at all → Require api-key; a key that is not the
            # signed-in account's → API Key mismatch.
            self.auth_ok = None
            if not header:
                self._auth_error = 'require'
            elif header != f'Bearer {self.api_key}':
                self._auth_error = 'mismatch'
            else:
                self._auth_error = ''
        else:
            self._auth_error = ''
            self.auth_ok = True
        try:
            yield
        finally:
            pass

    def _denied(self) -> web.Response:
        errors = _load('errors')
        if self._auth_error == 'require':
            return web.json_response(errors['require_api_key'])
        return web.json_response(errors['api_key_mismatch'])

    # ── handlers ────────────────────────────────────────────────────

    async def _status(self, _request: web.Request) -> web.Response:
        if self.status_delay_s:
            await asyncio.sleep(self.status_delay_s)
        if not self.up:
            await asyncio.sleep(30)
        return web.json_response(_load('errors')['status_ok'])

    async def _list(self, request: web.Request) -> web.Response:
        with self._guard(request):
            if self._auth_error:
                return self._denied()
            if self.rate_limit_burst > 0:
                self.rate_limit_burst -= 1
                return web.json_response(_load('errors')['rate_limited'])
            try:
                page = int(request.query.get('page', '1'))
                size = int(request.query.get('page_size', '1'))
            except (TypeError, ValueError):
                page, size = 1, 1
            size = max(1, min(size, 100))
            start = (page - 1) * size
            batch = self.stores[start : start + size]
            return web.json_response({
                'code': 0,
                'msg': 'Success',
                'data': {
                    'list': batch,
                    'page': page,
                    'page_size': size,
                },
            })

    async def _start(self, request: web.Request) -> web.Response:
        with self._guard(request):
            if self._auth_error:
                return self._denied()
            user_id = request.query.get('user_id', '')
            if user_id and user_id not in {s['user_id'] for s in self.stores}:
                return web.json_response(_load('errors')['invalid_id'])
            result = self.start_results.get(user_id)
            if result == 'downloading':
                return web.json_response(_load('errors')['kernel_downloading'])
            if result == 'not_ready':
                return web.json_response(_load('errors')['kernel_not_ready'])
            if result == 'firefox':
                return web.json_response(_load('start_firefox_empty'))
            if result == 'flower159':
                return web.json_response(_load('start_flower159'))
            if result == 'flower100':
                return web.json_response(_load('start_flower100'))
            body = _load('start_ok')
            body = json.loads(json.dumps(body))
            body['data']['ws'] = {
                'puppeteer': (
                    f'ws://127.0.0.1:{self.cdp_port}'
                    '/devtools/browser/aaaaaaaa-bbbb-cccc-dddd'
                    '-eeeeeeeeeeee'
                ),
                'selenium': f'127.0.0.1:{self.cdp_port}',
            }
            body['data']['debug_port'] = str(self.cdp_port)
            return web.json_response(body)

    async def _stop(self, request: web.Request) -> web.Response:
        with self._guard(request):
            if self._auth_error:
                return self._denied()
            user_id = request.query.get('user_id', '')
            if user_id and user_id not in {s['user_id'] for s in self.stores}:
                return web.json_response(_load('errors')['invalid_id'])
            # A profile the API does not track (any FlowerBrowser
            # kernel — the window is invisible to the running list,
            # FACTS §j):
            untracked = self.start_results.get(user_id) in {
                'firefox',
                'flower159',
                'flower100',
            }
            if untracked:
                return web.json_response(_load('errors')['not_open'])
            return web.json_response(_load('errors')['status_ok'])

    async def _active(self, request: web.Request) -> web.Response:
        with self._guard(request):
            if self._auth_error:
                return self._denied()
            user_id = request.query.get('user_id', '')
            result = self.start_results.get(user_id)
            if result == 'firefox' or result is None:
                return web.json_response({
                    'code': 0,
                    'msg': 'success',
                    'data': {'status': 'Inactive'},
                })
            return web.json_response({
                'code': 0,
                'msg': 'success',
                'data': {
                    'status': 'Active',
                    'ws': {
                        'puppeteer': (
                            f'ws://127.0.0.1:{self.cdp_port}'
                            '/devtools/browser/aaaaaaaa-bbbb-cccc-dddd'
                            '-eeeeeeeeeeee'
                        ),
                        'selenium': f'127.0.0.1:{self.cdp_port}',
                    },
                },
            })

    async def _local_active(self, request: web.Request) -> web.Response:
        with self._guard(request):
            if self._auth_error:
                return self._denied()
            return web.json_response(_load('errors')['local_active_empty'])

    async def _json_version(self, _request: web.Request) -> web.Response:
        return web.json_response({
            'Browser': 'Chrome/156.0.8078.36',
            'Protocol-Version': '1.3',
            'webSocketDebuggerUrl': (
                f'ws://127.0.0.1:{self.cdp_port}'
                '/devtools/browser/aaaaaaaa-bbbb-cccc-dddd'
                '-eeeeeeeeeeee'
            ),
        })
