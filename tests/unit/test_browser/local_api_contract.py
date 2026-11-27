"""The LocalApiClient contract, proven on fixture vendors.

Every LocalApiClient subclass must satisfy the same contract against a
fake local HTTP API serving its documented responses. The suite runs
over TWO fixture sources, treated identically:

- the in-suite doc-derived fixtures below (test-only vendors from the
  published API docs; no user-facing vendor is registered in this PR);
- every real vendor fixture in
  ``tests/unit/test_browser/local_api_vendors/<vendor_key>.py`` — a
  vendor adds ONE file exporting ``CONTRACT = ContractFixture(...)``
  and gets every parametrized contract test for free. NEVER edit this
  suite to add a vendor: the convention check below fails until the
  fixture file exists, and fails again if a fixture stops matching a
  discovered vendor.

- ``list_stores`` parses the vendor's documented list payload into
  StoreRefs, and a logged-out response is ``LoginRequired`` (NEVER an
  empty list standing in for "logged out");
- ``open_store`` returns the CDP endpoint from the vendor's documented
  open payload (a ws URL parses to host+port; a port-only answer is
  passed through — the engine verifies the endpoint itself);
- an unknown ref maps to ``StoreNotFound`` through the vendor's
  ``map_error``;
- the paid gate ("API not enabled" — the #1 support case) maps to
  ``ApiNotEnabled``, not a generic failure;
- a client that is not answering is ``ClientNotRunning``;
- ``close_store`` never raises;
- calls are spaced at least ``min_interval_s`` apart (rate limits);
- whatever auth the vendor declares, the credential value the manager
  injects (``<vendor>_<field>``) rides the call.

Fixtures are TEST-ONLY vendors derived from the vendors' published API
docs (the research catalogue); no user-facing vendor is registered in
this PR. Each behaviour pairs with a twist: the same payload family
proves the opposite shape (logged-out ≠ empty list, paid gate ≠ generic
failure, unknown ref ≠ raw 404).
"""

import asyncio
from dataclasses import asdict
import gc
import importlib
import json
import logging
import pkgutil
import socket
import time

from aiohttp import web
import pytest

import app.browser.adapters as adapters_pkg
from app.browser.inject.client import OpenedStore, StoreRef
from app.browser.inject.errors import (
    ApiNotEnabled,
    ClientNotRunning,
    InjectError,
    LoginRequired,
    StoreNotFound,
)
from app.browser.local_api_client import (
    LocalApiClient,
    PaginationOverflow,
    opened_from_ws,
)
from tests.unit.test_browser.local_api_fixture import (
    NO_PAID_GATE,
    ContractFixture,
)
import tests.unit.test_browser.local_api_vendors as vendors_pkg

pytestmark = pytest.mark.unit


# ── the fake local API (fixture responses served over HTTP) ─────────


class _FixtureServer:
    """Serves a vendor's documented responses on a random local port.

    ``routes`` maps ``(method, path)`` to either a static
    ``(status, payload)`` (a dict payload is served as JSON, anything
    else as text) or an ``async def handler(request) -> (status,
    payload)`` for request-dependent shapes (paginated lists, ref-keyed
    opens). Every request — method, path, headers and parsed JSON body —
    is recorded for the request-shape assertions.
    """

    def __init__(self, routes: dict):
        self.routes = routes
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
                'path': request.path_qs,
                'headers': dict(request.headers),
                'json': body,
            })
            hit = routes.get((request.method, request.path_qs))
            if hit is None:
                return web.Response(status=404, text='no such route')
            if callable(hit):
                status, payload = await hit(request)
            else:
                status, payload = hit
            if isinstance(payload, dict | list):
                return web.json_response(payload, status=status)
            return web.Response(status=status, text=str(payload))

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


_servers: list[_FixtureServer] = []
_clients: list[LocalApiClient] = []


async def _serve(routes: dict) -> _FixtureServer:
    server = _FixtureServer(routes)
    await server.start()
    _servers.append(server)
    return server


def _client(vendor_cls, port: int, **cfg) -> LocalApiClient:
    client = vendor_cls({'api_port': port, **cfg})
    _clients.append(client)
    return client


@pytest.fixture(autouse=True)
async def _teardown_servers_and_clients():
    yield
    for client in _clients:
        await client.aclose()
    _clients.clear()
    for server in _servers:
        await server.stop()
    _servers.clear()


# ── doc-derived fixture vendors (test-only) ──────────────────────────
#
# Shapes from the vendors' published local-API docs:
#   bitbrowser  POST :54345/browser/open {id} → {ws, http};
#               POST /browser/list → data.list[{id, name}]
#   adspower    GET :50325/api/v1/browser/start?user_id= →
#               {code, data: {ws: {puppeteer}, debug_port}};
#               GET /api/v1/user/list → {code, data: {list}}
#   morelogin   POST :40000/api/env/start {envId} → {code, data:
#               {debugPort}}; POST /api/env/list → {code, data:
#               {pageData: {list}}}
# One fixture per open-payload shape (ws URL / nested ws + port /
# port-only) and one per auth mode (none / header / bearer).


class BitBrowserFixture(LocalApiClient):
    """The verified live shape (127.0.0.1:54345): every call is POST +
    JSON; the list is paginated 0-based with a totalNum; rows carry
    each profile's site credentials (sentinel placeholders here)."""

    vendor_key = 'bitfixture'
    api_port = 54345
    list_method = 'POST'
    list_path = '/browser/list'
    open_method = 'POST'
    open_path = '/browser/open'
    close_method = 'POST'
    close_path = '/browser/close'
    page_size = 2
    min_interval_s = 0.05

    def list_request(self, page):
        return self.list_path, {
            'page': page,
            'pageSize': self.page_size,
        }

    def open_request(self, ref):
        return self.open_path, {'id': ref}

    def close_request(self, ref):
        return self.close_path, {'id': ref}

    def next_page(self, payload, page):
        data = payload.get('data') or {}
        total = int(data.get('totalNum') or 0)
        if (page + 1) * self.page_size < total:
            return page + 1
        return None

    def parse_stores(self, payload):
        rows = (payload.get('data') or {}).get('list') or []
        # The ref the open call needs is the row id; on this StoreRef
        # (name/platform/remote_env) `name` IS the ref the whole stack
        # flows to open_store, so the display name rides remote_env.
        return [
            StoreRef(name=r['id'], remote_env=r.get('name', '')) for r in rows
        ]

    def parse_opened(self, payload):
        return opened_from_ws(payload['ws'])

    def is_logged_out(self, status, payload):
        return isinstance(payload, dict) and bool(payload.get('notLoggedIn'))

    def map_error(self, status, payload):
        if not isinstance(payload, dict):
            # A 404 from a ref-keyed route: the env does not exist.
            return StoreNotFound('unknown env') if status == 404 else None
        if payload.get('apiNotEnabled'):
            return ApiNotEnabled(payload.get('msg') or '')
        if payload.get('code') == 'no_env':
            return StoreNotFound(payload.get('msg') or 'unknown env')
        return None


class AdsPowerFixture(LocalApiClient):
    vendor_key = 'adsfixture'
    api_port = 50325
    auth = 'header'
    auth_field = 'api_key'
    auth_header = 'x-api-key'
    list_method = 'GET'
    list_path = '/api/v1/user/list'
    open_method = 'GET'
    open_path = '/api/v1/browser/start?user_id={ref}'
    close_method = 'GET'
    close_path = '/api/v1/browser/stop?user_id={ref}'
    min_interval_s = 0.05

    def parse_stores(self, payload):
        rows = (payload.get('data') or {}).get('list') or []
        return [
            StoreRef(name=r.get('name') or r.get('user_id', '')) for r in rows
        ]

    def parse_opened(self, payload):
        data = payload.get('data') or {}
        ws = (data.get('ws') or {}).get('puppeteer') or ''
        if ws:
            return opened_from_ws(ws)
        return OpenedStore('127.0.0.1', int(data['debug_port']))

    def is_logged_out(self, status, payload):
        return (
            isinstance(payload, dict) and payload.get('code') == 'auth_expired'
        )

    def map_error(self, status, payload):
        if not isinstance(payload, dict):
            return None
        code = payload.get('code')
        if code == 'api_not_enabled':
            return ApiNotEnabled(payload.get('msg') or '')
        if code == 'user_not_found':
            return StoreNotFound(payload.get('msg') or 'unknown profile')
        return None


class MoreLoginFixture(LocalApiClient):
    vendor_key = 'morefixture'
    api_port = 40000
    auth = 'bearer'
    auth_field = 'token'
    list_method = 'POST'
    list_path = '/api/env/list'
    open_method = 'POST'
    open_path = '/api/env/start'
    close_method = 'POST'
    close_path = '/api/env/close'
    min_interval_s = 0.05

    def parse_stores(self, payload):
        rows = ((payload.get('data') or {}).get('pageData') or {}).get(
            'list'
        ) or []
        return [
            StoreRef(name=r.get('envName') or r.get('envId', '')) for r in rows
        ]

    def parse_opened(self, payload):
        # Port-only: the engine verifies the endpoint itself.
        return OpenedStore('127.0.0.1', int(payload['data']['debugPort']))

    def is_logged_out(self, status, payload):
        return status == 401

    def map_error(self, status, payload):
        if not isinstance(payload, dict):
            return None
        if payload.get('code') == 40301:
            return ApiNotEnabled(payload.get('msg') or 'plan required')
        if payload.get('code') == 40404:
            return StoreNotFound(payload.get('msg') or 'unknown env')
        return None


# The routes each fixture's fake API serves; a ref-keyed open path uses
# the parsed store name. BitBrowser's rows carry each profile's site
# credentials — sentinel PLACEHOLDERS here, proving they never leave
# the client (logs, StoreRef output).
BIT_ROWS = [
    {
        'id': '100000000001',
        'name': 'demo-store-one',
        'userName': 'sentinel-secret-user-1',
        'password': 'sentinel-secret-pass-1',
        'cookie': 'sentinel-secret-cookie-1',
        'proxy': {
            'host': '203.0.113.10',
            'password': 'sentinel-secret-proxy-1',
        },
    },
    {
        'id': '100000000002',
        'name': 'demo-store-two',
        'userName': 'sentinel-secret-user-2',
        'password': 'sentinel-secret-pass-2',
        'cookie': 'sentinel-secret-cookie-2',
        'proxy': {
            'host': '203.0.113.10',
            'password': 'sentinel-secret-proxy-2',
        },
    },
    {
        'id': '100000000003',
        'name': 'demo-store-three',
        'userName': 'sentinel-secret-user-3',
        'password': 'sentinel-secret-pass-3',
        'cookie': 'sentinel-secret-cookie-3',
        'proxy': {
            'host': '203.0.113.10',
            'password': 'sentinel-secret-proxy-3',
        },
    },
]
# Every SECRET sentinel value the leak assertions hunt for (the row id
# and display name are not secrets — they are the StoreRef's own
# fields).
BIT_SENTINELS = [
    v
    for row in BIT_ROWS
    for k, v in row.items()
    if k in ('userName', 'password', 'cookie')
] + [row['proxy']['password'] for row in BIT_ROWS]


async def _bit_list(request: web.Request):
    """The paginated list: 0-based page, pageSize, totalNum — and page 1
    repeats a row, so de-duplication is exercised."""
    body = await request.json()
    page = int(body['page'])
    size = int(body['pageSize'])
    rows = BIT_ROWS[page * size : (page + 1) * size]
    if page == 1:
        rows = [rows[0], BIT_ROWS[0]]  # a repeated row
    return 200, {'data': {'list': rows, 'totalNum': len(BIT_ROWS)}}


async def _bit_open(request: web.Request):
    body = await request.json()
    if body.get('id') not in {row['id'] for row in BIT_ROWS}:
        return 200, {'code': 'no_env', 'msg': 'unknown env'}
    return 200, {'ws': 'ws://127.0.0.1:54321/devtools/browser/abc'}


BIT_ROUTES = {
    ('POST', '/browser/list'): _bit_list,
    ('POST', '/browser/open'): _bit_open,
    ('POST', '/browser/close'): (200, {'ok': True}),
}

ADS_ROUTES = {
    ('GET', '/api/v1/user/list'): (
        200,
        {
            'code': 0,
            'data': {
                'list': [
                    {'user_id': 'u1', 'name': 'acme-us'},
                    {'user_id': 'u2', 'name': 'acme-uk'},
                ]
            },
        },
    ),
    ('GET', '/api/v1/browser/start?user_id=acme-us'): (
        200,
        {
            'code': 0,
            'data': {
                'ws': {'puppeteer': 'ws://127.0.0.1:54322/devtools/browser/d'},
                'debug_port': 54322,
            },
        },
    ),
    ('GET', '/api/v1/browser/stop?user_id=acme-us'): (200, {'code': 0}),
}

MORE_ROUTES = {
    ('POST', '/api/env/list'): (
        200,
        {
            'code': 0,
            'data': {
                'pageData': {
                    'list': [
                        {'envId': 'e1', 'envName': 'main-store'},
                    ]
                }
            },
        },
    ),
    ('POST', '/api/env/start'): (
        200,
        {'code': 0, 'data': {'debugPort': 54323}},
    ),
    ('POST', '/api/env/close'): (200, {'code': 0}),
}


# The in-suite doc-derived fixtures, in the ONE fixture shape the suite
# (and a real vendor's local_api_vendors/<key>.py file) uses.
IN_SUITE_FIXTURES = [
    ContractFixture(
        client_cls=BitBrowserFixture,
        routes=BIT_ROUTES,
        expected_refs=[row['id'] for row in BIT_ROWS],
        expected_cdp_port=54321,
        logged_out_routes={
            ('POST', '/browser/list'): (200, {'notLoggedIn': True})
        },
        paid_gate_routes={
            ('POST', '/browser/list'): (
                200,
                {'apiNotEnabled': True, 'msg': 'buy a plan'},
            )
        },
        unknown_ref_routes={
            ('POST', '/browser/open'): (
                200,
                {'code': 'no_env', 'msg': 'gone'},
            )
        },
    ),
    ContractFixture(
        client_cls=AdsPowerFixture,
        routes=ADS_ROUTES,
        expected_refs=['acme-us', 'acme-uk'],
        expected_cdp_port=54322,
        auth_env={'adsfixture_api_key': 'sekrit'},
        logged_out_routes={
            ('GET', '/api/v1/user/list'): (200, {'code': 'auth_expired'})
        },
    ),
    ContractFixture(
        client_cls=MoreLoginFixture,
        routes=MORE_ROUTES,
        expected_refs=['main-store'],
        expected_cdp_port=54323,
        auth_env={'morefixture_token': 'sekrit'},
        logged_out_routes={('POST', '/api/env/list'): (401, 'no token')},
    ),
]


def _registered_vendor_fixtures() -> list:
    """Every real vendor's CONTRACT from local_api_vendors/<key>.py.

    Collected by convention (pkgutil, like the adapter discovery), so a
    vendor PR adds its fixture file and nothing in this suite changes.
    """
    fixtures = []
    for mod_info in pkgutil.iter_modules(vendors_pkg.__path__):
        mod = importlib.import_module(
            f'tests.unit.test_browser.local_api_vendors.{mod_info.name}'
        )
        contract = getattr(mod, 'CONTRACT', None)
        if contract is not None:
            assert isinstance(contract, ContractFixture), (
                f'{mod_info.name}.py: CONTRACT must be a '
                'ContractFixture (tests/unit/test_browser/'
                'local_api_fixture.py)'
            )
            fixtures.append(contract)
    return fixtures


# Every fixture the contract tests run over: in-suite + real vendors.
VENDOR_FIXTURES = IN_SUITE_FIXTURES + _registered_vendor_fixtures()


# ── the contract suite (parametrized over every vendor) ──────────────


@pytest.mark.parametrize(
    'fx',
    VENDOR_FIXTURES,
    ids=[fx.client_cls.vendor_key for fx in VENDOR_FIXTURES],
)
async def test_contract_list_stores(fx):
    server = await _serve(fx.routes)
    client = _client(fx.client_cls, server.port)
    refs = await client.list_stores()
    assert [r.name for r in refs] == fx.expected_refs
    assert all(r.name for r in refs)


@pytest.mark.parametrize(
    'fx',
    VENDOR_FIXTURES,
    ids=[fx.client_cls.vendor_key for fx in VENDOR_FIXTURES],
)
async def test_contract_open_store(fx):
    server = await _serve(fx.routes)
    client = _client(fx.client_cls, server.port)
    refs = await client.list_stores()
    opened = await client.open_store(refs[0].name)
    assert opened.cdp_host == '127.0.0.1'
    assert opened.cdp_port == fx.expected_cdp_port


@pytest.mark.parametrize(
    'fx',
    VENDOR_FIXTURES,
    ids=[fx.client_cls.vendor_key for fx in VENDOR_FIXTURES],
)
async def test_contract_ensure_running_ok(fx):
    server = await _serve(fx.routes)
    client = _client(fx.client_cls, server.port)
    await client.ensure_running()  # the API answers


@pytest.mark.parametrize(
    'fx',
    VENDOR_FIXTURES,
    ids=[fx.client_cls.vendor_key for fx in VENDOR_FIXTURES],
)
async def test_contract_close_never_raises(fx):
    server = await _serve({})  # even with every route 404ing
    client = _client(fx.client_cls, server.port)
    await client.close_store('whatever')


@pytest.mark.parametrize(
    'fx',
    VENDOR_FIXTURES,
    ids=[fx.client_cls.vendor_key for fx in VENDOR_FIXTURES],
)
async def test_contract_not_running(fx):
    # Port 1: nothing listens (and on WSL an unbound loopback connect
    # HANGS rather than refusing — the connect timeout must bound it).
    # → ClientNotRunning, never a hang or a generic error.
    client = _client(fx.client_cls, 1)
    client.connect_timeout_s = 0.3
    with pytest.raises(ClientNotRunning):
        await client.ensure_running()


@pytest.mark.parametrize(
    'fx',
    VENDOR_FIXTURES,
    ids=[fx.client_cls.vendor_key for fx in VENDOR_FIXTURES],
)
async def test_contract_rate_limit_spaces_calls(fx):
    server = await _serve(fx.routes)
    client = _client(fx.client_cls, server.port)
    client.min_interval_s = 0.12
    start = time.monotonic()
    await client.list_stores()
    await client.list_stores()
    await client.list_stores()
    assert time.monotonic() - start >= 2 * 0.12


@pytest.mark.parametrize(
    'fx',
    VENDOR_FIXTURES,
    ids=[fx.client_cls.vendor_key for fx in VENDOR_FIXTURES],
)
async def test_contract_auth_material_rides_the_call(fx):
    cls = fx.client_cls
    if cls.auth == 'none' or not fx.auth_env:
        pytest.skip('fixture vendor without auth')
    server = await _serve(fx.routes)
    client = _client(cls, server.port, **fx.auth_env)
    await client.list_stores()
    assert server.requests, 'no call reached the fake API'
    headers = server.requests[-1]['headers']
    if cls.auth == 'bearer':
        expected = fx.auth_env.get(
            f'{cls.vendor_key}_{cls.auth_field}', 'sekrit'
        )
        assert headers.get('Authorization') == f'Bearer {expected}'
    else:
        expected = fx.auth_env.get(
            f'{cls.vendor_key}_{cls.auth_field}', 'sekrit'
        )
        assert headers.get(cls.auth_header) == expected


# ── logged-out / paid-gate / unknown-ref shapes (per fixture) ────────
# Parametrized over every fixture that declares the corresponding
# documented shape — a real vendor's fixture file joins these for free.


def _with(variant: str) -> list:
    paid = variant == 'paid_gate_routes'
    out = []
    for fx in VENDOR_FIXTURES:
        value = getattr(fx, variant)
        # A NoPaidGate sentinel is a declaration, not routes to serve.
        if paid and not isinstance(value, dict):
            continue
        if value:
            out.append(fx)
    return out


@pytest.mark.parametrize(
    'fx',
    _with('logged_out_routes'),
    ids=[fx.client_cls.vendor_key for fx in _with('logged_out_routes')],
)
async def test_contract_logged_out_is_login_required_not_empty(fx):
    server = await _serve(fx.logged_out_routes)
    client = _client(fx.client_cls, server.port)
    with pytest.raises(LoginRequired):
        await client.list_stores()


@pytest.mark.parametrize(
    'fx',
    _with('paid_gate_routes'),
    ids=[fx.client_cls.vendor_key for fx in _with('paid_gate_routes')],
)
async def test_contract_paid_gate_maps_to_api_not_enabled(fx):
    server = await _serve(fx.paid_gate_routes)
    client = _client(fx.client_cls, server.port)
    with pytest.raises(ApiNotEnabled):
        await client.list_stores()


@pytest.mark.parametrize(
    'fx',
    _with('unknown_ref_routes'),
    ids=[fx.client_cls.vendor_key for fx in _with('unknown_ref_routes')],
)
async def test_contract_unknown_ref_is_store_not_found(fx):
    server = await _serve(fx.unknown_ref_routes)
    client = _client(fx.client_cls, server.port)
    with pytest.raises(StoreNotFound):
        await client.open_store('nope')


async def test_bit_ref_keyed_404_is_store_not_found():
    # An unmatched ref on a keyed route (the fake API 404s with text).
    server = await _serve({
        ('POST', '/browser/list'): BIT_ROUTES[('POST', '/browser/list')]
    })
    client = _client(BitBrowserFixture, server.port)
    with pytest.raises(StoreNotFound):
        await client.open_store('nope')


async def test_ads_port_only_open():
    server = await _serve({
        ('GET', '/api/v1/browser/start?user_id=u9'): (
            200,
            {'code': 0, 'data': {'debug_port': 54399}},
        )
    })
    client = _client(AdsPowerFixture, server.port)
    opened = await client.open_store('u9')
    assert (opened.cdp_host, opened.cdp_port) == ('127.0.0.1', 54399)


# ── POST-JSON requests + pagination (the addendum's shapes) ──────────


async def test_bit_requests_are_post_json_with_the_documented_bodies():
    server = await _serve(BIT_ROUTES)
    client = _client(BitBrowserFixture, server.port)
    refs = await client.list_stores()
    opened = await client.open_store(refs[0].name)
    await client.close_store(refs[0].name)
    lists = [r for r in server.requests if r['path'] == '/browser/list']
    opens = [r for r in server.requests if r['path'] == '/browser/open']
    closes = [r for r in server.requests if r['path'] == '/browser/close']
    assert [r['json'] for r in lists] == [
        {'page': 0, 'pageSize': 2},
        {'page': 1, 'pageSize': 2},
    ]
    assert opens[0]['json'] == {'id': refs[0].name}
    assert closes[0]['json'] == {'id': refs[0].name}
    assert opened.cdp_port == 54321


async def test_bit_pagination_collects_every_page_deduplicated():
    """3 rows at pageSize 2 → two pages; page 1 repeats a row, so the
    result is the 3 distinct refs (never the repeat)."""
    server = await _serve(BIT_ROUTES)
    client = _client(BitBrowserFixture, server.port)
    refs = await client.list_stores()
    assert [r.name for r in refs] == [row['id'] for row in BIT_ROWS]
    assert len(server.requests) == 2  # exactly the two pages


async def test_bit_twist_without_pagination_only_page_one():
    """Negative control: the SAME fixture with next_page disabled
    returns only page 0's rows — the 3-row result above is pagination,
    not luck."""
    server = await _serve(BIT_ROUTES)
    client = _client(BitBrowserFixture, server.port)
    client.next_page = lambda payload, page: None
    refs = await client.list_stores()
    assert [r.name for r in refs] == [
        BIT_ROWS[0]['id'],
        BIT_ROWS[1]['id'],
    ]


async def test_bit_pagination_overflow_raises_not_truncates():
    """A vendor whose pagination never says stop fails at the named
    limit — never a silent truncation or an endless loop."""
    server = await _serve(BIT_ROUTES)
    client = _client(BitBrowserFixture, server.port)
    client.max_list_pages = 3
    client.next_page = lambda payload, page: page + 1
    with pytest.raises(PaginationOverflow) as exc:
        await client.list_stores()
    assert '3 pages' in str(exc.value)


async def test_one_based_pagination_translates_the_counter():
    """The 1-based vendor shape (string pageNum/pageSize, total in
    response.total): the counter translation lives in the vendor's
    list_request/next_page, not the base."""

    class OneBasedFixture(LocalApiClient):
        vendor_key = 'onebased'
        api_port = 45678
        list_method = 'POST'
        list_path = '/queryEnvV2'
        open_method = 'POST'
        open_path = '/openEnv'
        close_method = 'POST'
        close_path = '/closeEnv'

        def list_request(self, page):
            # 0-based counter → the vendor's 1-based string pageNum.
            return self.list_path, {
                'method': 'queryEnvV2',
                'pageNum': str(page + 1),
                'pageSize': '2',
            }

        def next_page(self, payload, page):
            total = int((payload.get('response') or {}).get('total') or 0)
            if (page + 1) * 2 < total:
                return page + 1
            return None

        def parse_stores(self, payload):
            rows = (payload.get('response') or {}).get('list') or []
            return [StoreRef(name=r['id']) for r in rows]

        def parse_opened(self, payload):
            return OpenedStore('127.0.0.1', int(payload['debugPort']))

    async def list_route(request: web.Request):
        body = await request.json()
        page = int(body['pageNum']) - 1
        rows = [
            {'id': 'env-a'},
            {'id': 'env-b'},
            {'id': 'env-c'},
        ][page * 2 : (page + 1) * 2]
        return 200, {'response': {'list': rows, 'total': 3}}

    server = await _serve({
        ('POST', '/queryEnvV2'): list_route,
        ('POST', '/openEnv'): (200, {'debugPort': 54324}),
        ('POST', '/closeEnv'): (200, {'ok': True}),
    })
    client = _client(OneBasedFixture, server.port)
    refs = await client.list_stores()
    assert [r.name for r in refs] == ['env-a', 'env-b', 'env-c']
    lists = [r for r in server.requests if r['path'] == '/queryEnvV2']
    assert [r['json']['pageNum'] for r in lists] == ['1', '2']


# ── secrets never leave the client ───────────────────────────────────


async def test_bit_secrets_never_reach_the_logs(caplog):
    """List rows carry each profile's site credentials (sentinels
    here); no captured log line may contain one — the base logs
    methods, URLs and error text, never payloads or bodies."""
    server = await _serve(BIT_ROUTES)
    client = _client(BitBrowserFixture, server.port)
    with caplog.at_level(logging.DEBUG, logger='app.browser'):
        refs = await client.list_stores()
        await client.open_store(refs[0].name)
        await client.close_store(refs[0].name)
    for sentinel in BIT_SENTINELS:
        assert sentinel not in caplog.text, (
            f'secret leaked into logs: {sentinel}'
        )


async def test_bit_twist_the_log_check_catches_a_leak(caplog):
    """Negative control for the check above: a log line that DOES
    carry a sentinel is visible to the same capture — the clean result
    means the base is silent, not that the assertion is blind."""
    with caplog.at_level(logging.DEBUG, logger='app.browser'):
        logging.getLogger('app.browser').info(
            'planted leak %s', BIT_SENTINELS[0]
        )
    assert BIT_SENTINELS[0] in caplog.text


async def test_bit_store_refs_carry_only_store_ref_fields():
    """Whatever the rows carry, the listing the UI (and
    /api/browser-vendors/{key}/stores) sees is bare StoreRef rows —
    no userName/password/cookie/proxy fields ride along."""
    server = await _serve(BIT_ROUTES)
    client = _client(BitBrowserFixture, server.port)
    refs = await client.list_stores()
    rows = [asdict(ref) for ref in refs]
    for row in rows:
        assert set(row) <= {
            'name',
            'platform',
            'remote_env',
            'ref',
            'available',
            'unavailable_reason',
            'unavailable_detail',
        }
    for sentinel in BIT_SENTINELS:
        assert sentinel not in str(rows)


# ── the shared response path: extra endpoints, logged-out on every
# call, retries, file-sourced port + auth (addendum 2 + 3) ────────────


class _FixtureRateLimited(InjectError):
    """What the rate-limit fixture's map_error raises (test-only)."""

    code = 'FIXTURE_RATE_LIMITED'
    hint = 'slow down'


class _RateLimitFixture(LocalApiClient):
    """Answers 429 (or a 200 rate-limit body) then success; maps the
    rate-limit payload through map_error like a real vendor would."""

    vendor_key = 'ratelimit'
    api_port = 45690
    list_method = 'GET'
    list_path = '/list'
    open_method = 'GET'
    open_path = '/open'
    close_method = 'GET'
    close_path = '/close'

    def parse_stores(self, payload):
        return [StoreRef(name=r) for r in payload.get('list', [])]

    def parse_opened(self, payload):
        return OpenedStore('127.0.0.1', int(payload['debugPort']))

    def map_error(self, status, payload):
        if isinstance(payload, dict) and payload.get('code') == (
            'rate_limited'
        ):
            return _FixtureRateLimited('slow down')
        return None


def _counting_routes(hits: int, body_kind: str):
    """A route that answers the rate-limit shape ``hits`` times, then
    the success payload; every request is counted."""
    state = {'calls': 0}

    async def route(request: web.Request) -> tuple:
        state['calls'] += 1
        if state['calls'] <= hits:
            if body_kind == '429':
                return 429, {'code': 'rate_limited'}
            return 200, {
                'code': 'rate_limited',
                'msg': 'Too many request per second',
            }
        return 200, {'list': ['s1']}

    return route, state


async def test_rate_limited_429_retried_once_then_succeeds():
    route, state = _counting_routes(hits=1, body_kind='429')
    server = await _serve({('GET', '/list'): route})
    client = _client(_RateLimitFixture, server.port)
    refs = await client.list_stores()
    assert [r.name for r in refs] == ['s1']
    assert state['calls'] == 2  # exactly one retry


async def test_rate_limited_twice_surfaces_map_error_after_two_calls():
    route, state = _counting_routes(hits=2, body_kind='429')
    server = await _serve({('GET', '/list'): route})
    client = _client(_RateLimitFixture, server.port)
    with pytest.raises(_FixtureRateLimited):
        await client.list_stores()
    assert state['calls'] == 2  # one retry, then map_error sees it


async def test_rate_limit_override_recognises_a_200_body():
    """The AdsPower shape: a 200 whose BODY says 'too fast' — the
    override catches it, the retry succeeds."""

    class TooFastFixture(_RateLimitFixture):
        vendor_key = 'toofast'

        def is_rate_limited(self, status, payload):
            return 'Too many request' in str(payload or '')

    route, state = _counting_routes(hits=1, body_kind='200')
    server = await _serve({('GET', '/list'): route})
    client = _client(TooFastFixture, server.port)
    refs = await client.list_stores()
    assert [r.name for r in refs] == ['s1']
    assert state['calls'] == 2


async def test_twist_without_the_override_a_200_body_is_not_retried():
    """Negative control: the default is_rate_limited (status == 429)
    does not catch the 200-body shape — the response goes straight to
    map_error, which is exactly what the override exists to fix."""
    route, state = _counting_routes(hits=1, body_kind='200')
    server = await _serve({('GET', '/list'): route})
    client = _client(_RateLimitFixture, server.port)  # no override
    with pytest.raises(_FixtureRateLimited):
        await client.list_stores()
    assert state['calls'] == 1  # never retried


async def test_logged_out_open_is_login_required_not_a_store_error():
    """Addendum 3 item 1: a vendor that answers a logged-out OPEN with
    an HTTP-200 error body surfaces LOGIN_REQUIRED — the check lives in
    the shared response path, before map_error."""
    server = await _serve({
        ('POST', '/browser/list'): BIT_ROUTES[('POST', '/browser/list')],
        ('POST', '/browser/open'): (200, {'notLoggedIn': True}),
    })
    client = _client(BitBrowserFixture, server.port)
    with pytest.raises(LoginRequired):
        await client.open_store('100000000001')


async def test_twist_a_real_open_error_still_maps():
    """Negative control: the logged-out check does not swallow the
    vendor's own open errors — a no_env body maps to StoreNotFound."""
    server = await _serve({
        ('POST', '/browser/list'): BIT_ROUTES[('POST', '/browser/list')],
        ('POST', '/browser/open'): (
            200,
            {'code': 'no_env', 'msg': 'gone'},
        ),
    })
    client = _client(BitBrowserFixture, server.port)
    with pytest.raises(StoreNotFound):
        await client.open_store('100000000001')


async def test_api_call_serves_a_vendor_extra_endpoint():
    """Addendum 3 item 2: subclasses call api_call for their extra
    endpoints — same rate limit, auth headers and logged-out check."""

    class DetailFixture(BitBrowserFixture):
        vendor_key = 'detailfixture'

        async def kernel_check(self, ref: str) -> bool:
            status, payload = await self.api_call(
                'POST', '/browser/detail', json_body={'id': ref}
            )
            return bool(payload.get('chrome'))

    async def detail_route(request: web.Request) -> tuple:
        body = await request.json()
        if body.get('id') == 'broken':
            return 200, {'chrome': False}
        return 200, {'chrome': True}

    server = await _serve({
        ('POST', '/browser/list'): BIT_ROUTES[('POST', '/browser/list')],
        ('POST', '/browser/detail'): detail_route,
    })
    client = _client(DetailFixture, server.port)
    assert await client.kernel_check('100000000001') is True
    assert await client.kernel_check('broken') is False
    # The extra endpoint went through the shared path: its JSON body
    # was sent and counted like any built-in call.
    details = [r for r in server.requests if r['path'] == '/browser/detail']
    assert [r['json'] for r in details] == [
        {'id': '100000000001'},
        {'id': 'broken'},
    ]


async def test_api_call_extra_endpoint_is_logged_out_checked():
    """The shared path applies to extra endpoints too: a logged-out
    /browser/detail raises LoginRequired, not a payload parse error."""
    server = await _serve({
        ('POST', '/browser/list'): BIT_ROUTES[('POST', '/browser/list')],
        ('POST', '/browser/detail'): (200, {'notLoggedIn': True}),
    })
    client = _client(BitBrowserFixture, server.port)
    with pytest.raises(LoginRequired):
        await client.api_call('POST', '/browser/detail', {'id': 'x'})


async def test_discover_port_override_reads_a_client_config_file(tmp_path):
    """Addendum 2 item 2: a vendor reads the port from the client's own
    config file (the read goes through asyncio.to_thread)."""
    config = tmp_path / 'config.json'
    config.write_text(
        json.dumps({'localServerAddress': '127.0.0.1:45691'}),
        encoding='utf-8',
    )

    class ConfigPortFixture(_RateLimitFixture):
        vendor_key = 'configport'
        api_port = 0  # discovery only
        config_path = str(config)

        async def discover_port(self) -> int | None:
            def _read() -> str:
                with open(self.config_path, encoding='utf-8') as fh:
                    return fh.read()

            text = await asyncio.to_thread(_read)
            return int(json.loads(text)['localServerAddress'].rsplit(':', 1)[1])

    server = await _serve({
        ('GET', '/list'): (200, {'list': ['s1']}),
    })
    # Point the fixture's config at the live server's port.
    port = server.port
    config.write_text(
        json.dumps({'localServerAddress': f'127.0.0.1:{port}'}),
        encoding='utf-8',
    )
    client = _client(ConfigPortFixture, 0)  # no api_port in the config
    # The engine's flow: ensure_running resolves the port first (the
    # discovery override), then the list runs against it.
    await client.ensure_running()
    refs = await client.list_stores()
    assert [r.name for r in refs] == ['s1']


async def test_auth_headers_override_reads_a_token_file(tmp_path, caplog):
    """Addendum 2 item 3: a vendor's token comes from a client file;
    the header rides the request, and its value never reaches logs."""
    token_file = tmp_path / 'token.txt'
    token_file.write_text('sentinel-secret-token', encoding='utf-8')

    class FileTokenFixture(_RateLimitFixture):
        vendor_key = 'filetoken'
        auth = 'header'
        auth_header = 'x-api-key'

        async def auth_headers(self) -> dict:
            def _read() -> str:
                with open(token_file, encoding='utf-8') as fh:
                    return fh.read().strip()

            token = await asyncio.to_thread(_read)
            return {'x-api-key': token}

    server = await _serve({
        ('GET', '/list'): (200, {'list': ['s1']}),
    })
    client = _client(FileTokenFixture, server.port)
    with caplog.at_level(logging.DEBUG, logger='app.browser'):
        await client.list_stores()
    headers = [r['headers'] for r in server.requests if r['path'] == '/list'][
        -1
    ]
    assert headers.get('x-api-key') == 'sentinel-secret-token'
    assert 'sentinel-secret-token' not in caplog.text


# ── review fixes: ref de-dup, single-probe login, shared limiter,
# hung-port scan, closed sessions (#332 B1–B6) ────────────────────────


async def test_same_name_different_refs_are_both_listed():
    """B1: two profiles may share a display name — they are distinct
    refs and both must be listed. (On the pre-fix code the second was
    silently dropped.)"""
    rows = [
        {'id': 'r1', 'name': 'US'},
        {'id': 'r2', 'name': 'US'},
        {'id': 'r2', 'name': 'US'},  # repeated across pages
    ]

    async def list_route(request):
        return 200, {'data': {'list': rows, 'totalNum': len(rows)}}

    server = await _serve({('POST', '/browser/list'): list_route})
    client = _client(BitBrowserFixture, server.port)
    refs = await client.list_stores()
    assert [r.name for r in refs] == ['r1', 'r2']  # both ids, repeat once


async def test_twist_a_genuinely_repeated_ref_lists_once():
    """Negative control for B1: the same open ref repeated across pages
    (the de-duplication the addendum asked for) still yields once —
    including the name-is-ref vendors with no id."""
    rows_page1 = [{'id': 'only', 'name': 'US'}]
    rows_page2 = [{'id': 'only', 'name': 'US'}]
    calls = {'n': 0}

    async def list_route(request):
        calls['n'] += 1
        rows = rows_page1 if calls['n'] == 1 else rows_page2
        return 200, {'data': {'list': rows, 'totalNum': 2}}

    server = await _serve({('POST', '/browser/list'): list_route})
    client = _client(BitBrowserFixture, server.port)
    refs = await client.list_stores()
    assert [r.name for r in refs] == ['only']


async def test_is_logged_in_is_a_single_call():
    """B3: answering 'logged in?' costs ONE list request, never the
    full pagination (a 1,000-profile account paid every page twice per
    task start before)."""
    calls = {'n': 0}

    async def list_route(request):
        calls['n'] += 1
        body = await request.json()
        page = int(body['page'])
        # Claim many more pages, so a paginating probe would show it.
        return 200, {
            'data': {
                'list': [{'id': f'r{page}'}, {'id': f'r{page}b'}],
                'totalNum': 1000,
            }
        }

    server = await _serve({('POST', '/browser/list'): list_route})
    client = _client(BitBrowserFixture, server.port)
    assert await client.is_logged_in() is True
    assert calls['n'] == 1


async def test_twist_logged_out_probe_is_still_one_call():
    calls = {'n': 0}

    async def list_route(request):
        calls['n'] += 1
        return 200, {'notLoggedIn': True}

    server = await _serve({('POST', '/browser/list'): list_route})
    client = _client(BitBrowserFixture, server.port)
    assert await client.is_logged_in() is False
    assert calls['n'] == 1


async def test_the_rate_limit_is_shared_per_vendor_class():
    """B4: two instances of ONE vendor (concurrent tasks on different
    stores) are spaced by min_interval_s against each other."""
    interval = 0.15

    class SharedFixture(_RateLimitFixture):
        vendor_key = 'sharedlimit'
        min_interval_s = interval

    server = await _serve({
        ('GET', '/list'): (200, {'list': ['s1']}),
    })
    a = _client(SharedFixture, server.port)
    b = _client(SharedFixture, server.port)
    start = time.monotonic()
    await asyncio.gather(a.list_stores(), b.list_stores())
    # Two calls through one shared limiter: at least one interval apart.
    assert time.monotonic() - start >= interval


async def test_twist_different_vendors_do_not_throttle_each_other():
    """Negative control for B4: two DIFFERENT vendors each make one
    call; neither waits on the other's limiter (both complete inside a
    fraction of the interval)."""
    interval = 1.0

    class SlowA(_RateLimitFixture):
        vendor_key = 'slowa'
        min_interval_s = interval

    class SlowB(_RateLimitFixture):
        vendor_key = 'slowb'
        min_interval_s = interval

    server = await _serve({('GET', '/list'): (200, {'list': ['s1']})})
    a = _client(SlowA, server.port)
    b = _client(SlowB, server.port)
    start = time.monotonic()
    await asyncio.gather(a.list_stores(), b.list_stores())
    assert time.monotonic() - start < interval


async def test_a_hung_candidate_port_does_not_kill_the_scan():
    """B5: a candidate port that accepts but never answers (the WSL
    unbound-loopback hang) is skipped, and the scan finds the next
    port. On the pre-fix code the timeout raised out of the scan."""

    hung = socket.socket()
    hung.bind(('127.0.0.1', 0))
    hung.listen(1)  # accepts connections, never responds
    try:
        hung_port = hung.getsockname()[1]

        class ScanningFixture(_RateLimitFixture):
            vendor_key = 'scanning'
            discovery = 'scan'
            api_port = hung_port  # probed first: hangs
            scan_ports = ()  # filled below

        server = await _serve({('GET', '/list'): (200, {'list': ['s1']})})
        ScanningFixture.scan_ports = (server.port,)
        client = _client(ScanningFixture, hung_port)
        client._port = None
        await client.ensure_running()
        refs = await client.list_stores()
        assert [r.name for r in refs] == ['s1']
    finally:
        hung.close()


async def test_no_session_leaks_around_the_calls(recwarn):
    """B6: sessions are per call and always closed — no ResourceWarning
    ('Unclosed client session') after a full list/open/close cycle."""
    server = await _serve(BIT_ROUTES)
    client = _client(BitBrowserFixture, server.port)
    refs = await client.list_stores()
    await client.open_store(refs[0].name)
    await client.close_store(refs[0].name)

    gc.collect()
    leaked = [
        w
        for w in recwarn.list
        if 'Unclosed' in str(w.message) or 'session' in str(w.message).lower()
    ]
    assert not leaked, [str(w.message) for w in leaked]


# ── discovery: real vendor modules join the suite automatically ─────


def _is_contract_vendor(cls) -> bool:
    return (
        isinstance(cls, type)
        and issubclass(cls, LocalApiClient)
        and cls is not LocalApiClient
        and getattr(cls, '__module__', '').startswith('app.browser.adapters')
    )


def _discovered_contract_vendors() -> list:
    """Every LocalApiClient subclass under app/browser/adapters."""
    found = []
    for mod_info in pkgutil.iter_modules(adapters_pkg.__path__):
        mod = importlib.import_module(f'app.browser.adapters.{mod_info.name}')
        found.extend(
            attr for attr in vars(mod).values() if _is_contract_vendor(attr)
        )
    return found


def _fixture_coverage_problems(discovered: list, registered: list) -> list:
    """Convention failures between adapters and fixture files.

    (a) a discovered LocalApiClient vendor with no fixture file under
    local_api_vendors/ (or whose fixture's client_cls is a different
    class);
    (b) a fixture file whose client_cls is not a discovered vendor
    (a stale fixture, or one that never pointed at a real adapter).
    """
    problems = []
    registered_classes = {fx.client_cls for fx in registered}
    for cls in discovered:
        if cls not in registered_classes:
            problems.append(
                f'{cls.__module__}.{cls.__name__}: a LocalApiClient '
                'vendor with no contract fixture — add '
                'tests/unit/test_browser/local_api_vendors/'
                f'<vendor_key>.py exporting CONTRACT = ContractFixture('
                'client_cls=<the real class>, ...)'
            )
    for fx in registered:
        name = f'{fx.client_cls.__module__}.{fx.client_cls.__name__}'
        if fx.client_cls not in discovered:
            problems.append(
                f'{name}: a fixture file whose '
                'client_cls is not a discovered LocalApiClient vendor '
                'under app/browser/adapters (stale fixture, or it never '
                'pointed at a real adapter)'
            )
            continue
        # A REGISTERED (real) vendor may not skip the safety shapes by
        # omission: logged-out and unknown-ref are the point of this
        # suite, and the paid gate needs routes or the explicit
        # NO_PAID_GATE sentinel. (In-suite doc fixtures are
        # illustrations and stay partial — they never reach here, not
        # being discovered adapter vendors.)
        if fx.logged_out_routes is None:
            problems.append(
                f'{name}: the contract fixture omits logged_out_routes '
                '— the "logged-out is never an empty list" guarantee '
                "may not be skipped; document the vendor's logged-out "
                f'shape in local_api_vendors/'
                f'{fx.client_cls.vendor_key}.py'
            )
        if fx.unknown_ref_routes is None:
            problems.append(
                f'{name}: the contract fixture omits unknown_ref_routes '
                '— the "unknown ref maps to StoreNotFound" guarantee may '
                "not be skipped; document the vendor's unknown-ref shape "
                f'in local_api_vendors/'
                f'{fx.client_cls.vendor_key}.py'
            )
        if fx.paid_gate_routes is None:
            problems.append(
                f'{name}: the contract fixture omits paid_gate_routes — '
                "either document the vendor's paid-gate shape, or "
                'declare paid_gate_routes=NO_PAID_GATE (with a reason) '
                f'in local_api_vendors/'
                f'{fx.client_cls.vendor_key}.py'
            )
    return problems


def test_every_contract_vendor_has_a_fixture_and_vice_versa():
    """The convention: a LocalApiClient vendor under app/browser/adapters
    and a fixture file under local_api_vendors/ come in pairs — the
    contract suite runs every vendor's behaviour with ZERO edits to the
    suite itself. (No real vendor exists in this PR, so the sets are
    empty today; the check below is what makes #334/#333-style vendor
    PRs add one fixture file instead of editing this suite.)"""
    discovered = _discovered_contract_vendors()
    registered = _registered_vendor_fixtures()
    problems = _fixture_coverage_problems(discovered, registered)
    assert not problems, '\n'.join(problems)


def test_the_convention_check_catches_a_missing_fixture():
    """Negative control (a): a discovered vendor with no fixture file is
    reported, naming the file to add."""

    class PlantedVendor(LocalApiClient):
        __module__ = 'app.browser.adapters.planted'

    problems = _fixture_coverage_problems([PlantedVendor], [])
    assert len(problems) == 1
    assert 'local_api_vendors' in problems[0]
    assert 'planted' in problems[0]


def test_the_convention_check_catches_an_orphan_fixture():
    """Negative control (b): a fixture whose client_cls is not a
    discovered vendor is reported as orphaned."""

    class OrphanClient(LocalApiClient):
        __module__ = 'tests.somewhere.not_adapters'

    orphan = ContractFixture(
        client_cls=OrphanClient,
        routes={},
        expected_refs=[],
        expected_cdp_port=1,
    )
    problems = _fixture_coverage_problems([], [orphan])
    assert len(problems) == 1
    assert 'not a discovered' in problems[0]


def test_refused_a_real_vendor_omitting_logged_out_routes():
    """Negative control: a registered vendor's fixture without
    logged_out_routes is refused, naming the field and the file."""

    class RealVendor(LocalApiClient):
        __module__ = 'app.browser.adapters.real_no_logged_out'

    fixture = ContractFixture(
        client_cls=RealVendor,
        routes={},
        expected_refs=[],
        expected_cdp_port=1,
        unknown_ref_routes={('GET', '/open'): (404, 'gone')},
        paid_gate_routes=NO_PAID_GATE,
    )
    problems = _fixture_coverage_problems([RealVendor], [fixture])
    assert len(problems) == 1
    assert 'logged_out_routes' in problems[0]
    assert 'real_no_logged_out' in problems[0]


def test_refused_a_real_vendor_omitting_unknown_ref_routes():
    """Negative control: a registered vendor's fixture without
    unknown_ref_routes is refused, naming the field and the file."""

    class RealVendor(LocalApiClient):
        __module__ = 'app.browser.adapters.real_no_unknown_ref'

    fixture = ContractFixture(
        client_cls=RealVendor,
        routes={},
        expected_refs=[],
        expected_cdp_port=1,
        logged_out_routes={('GET', '/list'): (401, 'out')},
        paid_gate_routes=NO_PAID_GATE,
    )
    problems = _fixture_coverage_problems([RealVendor], [fixture])
    assert len(problems) == 1
    assert 'unknown_ref_routes' in problems[0]


def test_refused_a_real_vendor_omitting_the_paid_gate():
    """Negative control: a registered vendor's fixture with neither
    paid-gate routes nor the NO_PAID_GATE sentinel is refused, naming
    the field, the sentinel and the file."""

    class RealVendor(LocalApiClient):
        __module__ = 'app.browser.adapters.real_no_paid_gate'

    fixture = ContractFixture(
        client_cls=RealVendor,
        routes={},
        expected_refs=[],
        expected_cdp_port=1,
        logged_out_routes={('GET', '/list'): (401, 'out')},
        unknown_ref_routes={('GET', '/open'): (404, 'gone')},
    )
    problems = _fixture_coverage_problems([RealVendor], [fixture])
    assert len(problems) == 1
    assert 'paid_gate_routes' in problems[0]
    assert 'NO_PAID_GATE' in problems[0]


def test_the_convention_check_passes_a_matched_pair():
    """Twist: a discovered vendor WITH its matching fixture reports
    nothing."""

    class MatchedVendor(LocalApiClient):
        __module__ = 'app.browser.adapters.matched'

    matched = ContractFixture(
        client_cls=MatchedVendor,
        routes={},
        expected_refs=[],
        expected_cdp_port=1,
        logged_out_routes={('GET', '/list'): (401, 'out')},
        unknown_ref_routes={('GET', '/open'): (404, 'gone')},
        paid_gate_routes=NO_PAID_GATE,
    )
    assert _fixture_coverage_problems([MatchedVendor], [matched]) == []


def test_the_discovery_predicate_matches_a_planted_vendor():
    """Negative control: the empty discovery above means 'none exist',
    not 'broken' — a planted adapters-module subclass matches, and the
    base class and the test fixtures (defined here, not under adapters/)
    do not."""
    planted = type(
        'Planted',
        (LocalApiClient,),
        {
            '__module__': 'app.browser.adapters.planted',
        },
    )
    assert _is_contract_vendor(planted)
    assert not _is_contract_vendor(LocalApiClient)
    assert not _is_contract_vendor(BitBrowserFixture)
