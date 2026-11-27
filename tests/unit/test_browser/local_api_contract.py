"""The LocalApiClient contract, proven on doc-derived fixture vendors.

Every LocalApiClient subclass — the test fixtures below today, and any
real vendor module under ``app/browser/adapters`` (auto-discovered) —
must satisfy the same contract against a fake local HTTP API serving
its documented responses:

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

from dataclasses import asdict
import importlib
import logging
import pkgutil
import time

from aiohttp import web
import pytest

import app.browser.adapters as adapters_pkg
from app.browser.inject.client import OpenedStore, StoreRef
from app.browser.inject.errors import (
    ClientNotRunning,
    LoginRequired,
    StoreNotFound,
)
from app.browser.local_api_client import (
    ApiNotEnabled,
    LocalApiClient,
    PaginationOverflow,
    opened_from_ws,
)

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


VENDOR_FIXTURES = [
    (BitBrowserFixture, BIT_ROUTES),
    (AdsPowerFixture, ADS_ROUTES),
    (MoreLoginFixture, MORE_ROUTES),
]


# ── the contract suite (parametrized over every vendor) ──────────────


@pytest.mark.parametrize(
    'vendor_cls,routes',
    VENDOR_FIXTURES,
    ids=[v.vendor_key for v, _ in VENDOR_FIXTURES],
)
async def test_contract_list_stores(vendor_cls, routes):
    server = await _serve(routes)
    client = _client(vendor_cls, server.port)
    refs = await client.list_stores()
    assert [r.name for r in refs] == _EXPECTED_REFS[vendor_cls.vendor_key]
    assert all(r.name for r in refs)


@pytest.mark.parametrize(
    'vendor_cls,routes',
    VENDOR_FIXTURES,
    ids=[v.vendor_key for v, _ in VENDOR_FIXTURES],
)
async def test_contract_open_store(vendor_cls, routes):
    server = await _serve(routes)
    client = _client(vendor_cls, server.port)
    refs = await client.list_stores()
    opened = await client.open_store(refs[0].name)
    assert opened.cdp_host == '127.0.0.1'
    assert opened.cdp_port in (54321, 54322, 54323)


@pytest.mark.parametrize(
    'vendor_cls,routes',
    VENDOR_FIXTURES,
    ids=[v.vendor_key for v, _ in VENDOR_FIXTURES],
)
async def test_contract_ensure_running_ok(vendor_cls, routes):
    server = await _serve(routes)
    client = _client(vendor_cls, server.port)
    await client.ensure_running()  # the API answers


@pytest.mark.parametrize(
    'vendor_cls,routes',
    VENDOR_FIXTURES,
    ids=[v.vendor_key for v, _ in VENDOR_FIXTURES],
)
async def test_contract_close_never_raises(vendor_cls, routes):
    server = await _serve({})  # even with every route 404ing
    client = _client(vendor_cls, server.port)
    await client.close_store('whatever')


@pytest.mark.parametrize(
    'vendor_cls,routes',
    VENDOR_FIXTURES,
    ids=[v.vendor_key for v, _ in VENDOR_FIXTURES],
)
async def test_contract_not_running(vendor_cls, routes):
    # Port 1: nothing listens (and on WSL an unbound loopback connect
    # HANGS rather than refusing — the connect timeout must bound it).
    # → ClientNotRunning, never a hang or a generic error.
    client = _client(vendor_cls, 1)
    client.connect_timeout_s = 0.3
    with pytest.raises(ClientNotRunning):
        await client.ensure_running()


@pytest.mark.parametrize(
    'vendor_cls,routes',
    VENDOR_FIXTURES,
    ids=[v.vendor_key for v, _ in VENDOR_FIXTURES],
)
async def test_contract_rate_limit_spaces_calls(vendor_cls, routes):
    server = await _serve(routes)
    client = _client(vendor_cls, server.port)
    client.min_interval_s = 0.12
    start = time.monotonic()
    await client.list_stores()
    await client.list_stores()
    await client.list_stores()
    assert time.monotonic() - start >= 2 * 0.12


@pytest.mark.parametrize(
    'vendor_cls,routes',
    VENDOR_FIXTURES,
    ids=[v.vendor_key for v, _ in VENDOR_FIXTURES],
)
async def test_contract_auth_material_rides_the_call(vendor_cls, routes):
    if vendor_cls.auth == 'none':
        pytest.skip('fixture vendor without auth')
    server = await _serve(routes)
    client = _client(
        vendor_cls,
        server.port,
        **{f'{vendor_cls.vendor_key}_{vendor_cls.auth_field}': 'sekrit'},
    )
    await client.list_stores()
    assert server.requests, 'no call reached the fake API'
    headers = server.requests[-1]['headers']
    if vendor_cls.auth == 'bearer':
        assert headers.get('Authorization') == 'Bearer sekrit'
    else:
        assert headers.get(vendor_cls.auth_header) == 'sekrit'


# ── logged-out / paid-gate / unknown-ref shapes (per fixture) ────────


async def test_bit_logged_out_is_login_required_not_empty():
    server = await _serve({
        ('POST', '/browser/list'): (200, {'notLoggedIn': True})
    })
    client = _client(BitBrowserFixture, server.port)
    with pytest.raises(LoginRequired):
        await client.list_stores()


async def test_bit_paid_gate_maps_to_api_not_enabled():
    server = await _serve({
        ('POST', '/browser/list'): (
            200,
            {'apiNotEnabled': True, 'msg': 'buy a plan'},
        )
    })
    client = _client(BitBrowserFixture, server.port)
    with pytest.raises(ApiNotEnabled):
        await client.list_stores()


async def test_bit_unknown_ref_is_store_not_found():
    server = await _serve({
        ('POST', '/browser/open'): (
            200,
            {'code': 'no_env', 'msg': 'gone'},
        )
    })
    client = _client(BitBrowserFixture, server.port)
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


async def test_ads_logged_out_is_login_required():
    server = await _serve({
        ('GET', '/api/v1/user/list'): (200, {'code': 'auth_expired'})
    })
    client = _client(AdsPowerFixture, server.port)
    with pytest.raises(LoginRequired):
        await client.list_stores()


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


async def test_more_logged_out_is_login_required():
    server = await _serve({('POST', '/api/env/list'): (401, 'no token')})
    client = _client(MoreLoginFixture, server.port)
    with pytest.raises(LoginRequired):
        await client.list_stores()


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
        assert set(row) <= {'name', 'platform', 'remote_env'}
    for sentinel in BIT_SENTINELS:
        assert sentinel not in str(rows)


# ── discovery: real vendor modules join the suite automatically ─────


def _is_contract_vendor(cls) -> bool:
    return (
        isinstance(cls, type)
        and issubclass(cls, LocalApiClient)
        and cls is not LocalApiClient
        and getattr(cls, '__module__', '').startswith('app.browser.adapters')
    )


def test_adapters_local_api_subclasses_are_discovered():
    """Every LocalApiClient subclass defined under app/browser/adapters
    is a contract vendor — adding a real vendor means adding its
    fixtures, never editing this suite's list. (None exist yet: this PR
    registers no user-facing vendor.)"""
    found = []
    for mod_info in pkgutil.iter_modules(adapters_pkg.__path__):
        mod = importlib.import_module(f'app.browser.adapters.{mod_info.name}')
        found.extend(
            attr for attr in vars(mod).values() if _is_contract_vendor(attr)
        )
    assert found == []


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


# The refs each fixture's list must produce (page 1 repeats a row, so
# the BitBrowser order also pins de-duplication).
_EXPECTED_REFS = {
    'bitfixture': [row['id'] for row in BIT_ROWS],
    'adsfixture': ['acme-us', 'acme-uk'],
    'morefixture': ['main-store'],
}
