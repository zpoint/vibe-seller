"""稳卖 (Wenmai) — the contract fixture for the real vendor.

The verified live shapes (client v5.1.0715, 2026-11-27), placeholders
only. 稳卖 has NO paid gate on its local API (the API runs on every
launch) — no ``paid_gate_routes`` for that reason.

Placeholders only: acme / demo-store / 100000000001 / 203.0.113.10.
"""

from app.browser.adapters.wenmai_client import RealWenmaiClient
from tests.unit.test_browser.local_api_fixture import ContractFixture

_ROWS = [
    # ipId null = no device bound → the no_device unavailability token.
    {
        'id': 100000000001,
        'storeName': 'demo-store',
        'platform': '澳大利亚亚马逊',
        'ipId': 100200300,
    },
    {
        'id': 100000000002,
        'storeName': 'unbound-store',
        'platform': '澳大利亚亚马逊',
        'ipId': None,
    },
    {
        'id': 100000000003,
        'storeName': 'demo-store-3',
        'platform': '澳大利亚亚马逊',
        'ipId': 100200300,
    },
]


async def _list(request):
    """queryEnvV2, paginated: pageSize 2 over 3 rows → 2 pages."""
    body = await request.json()
    page = int(body['pageNum']) - 1
    size = int(body['pageSize'])
    return 200, {
        'method': 'queryEnvV2',
        'response': {
            'code': 200,
            'msg': '查询成功',
            'total': len(_ROWS),
            'envs': _ROWS[page * size : (page + 1) * size],
        },
    }


_OPEN = {
    'response': {
        'code': 200,
        'msg': 'success',
        'pid': 4848,
        # The kernel's own CDP endpoint; the engine verifies it.
        'selenium': 'http://127.0.0.1:54399',
        'webdriverPath': r'C:\demo\chromedriver.exe',
    }
}

# The verified live logged-out shape: a TOP-LEVEL error envelope (the
# docs claim 401; the client answers this — code 400, 认证失败).
_LOGGED_OUT = {
    'method': 'queryEnvV2',
    'msg': '认证失败，无法访问系统资源',
    'status': 'error',
    'code': '400',
}

# The verified live unknown-env shape (an id no login can see): an
# error envelope the client's map_error turns into CdpUnreachable,
# which open_store's knows_env disambiguation converts to
# StoreNotFound after confirming the ref is not in the list.
_UNKNOWN_REF = {
    'status': 'error',
    'code': '400',
    'msg': '环境不存在',
}

CONTRACT = ContractFixture(
    client_cls=RealWenmaiClient,
    routes={
        ('POST', '/queryEnvV2'): _list,
        ('POST', '/openEnv'): (200, _OPEN),
        ('POST', '/closeEnv'): (200, {'response': {'code': 200}}),
    },
    expected_refs=[r['storeName'] for r in _ROWS],
    expected_cdp_port=54399,
    logged_out_routes={
        ('POST', '/queryEnvV2'): (200, _LOGGED_OUT),
    },
    unknown_ref_routes={
        ('POST', '/queryEnvV2'): (
            200,
            {
                'response': {
                    'code': 200,
                    'msg': '查询成功',
                    'total': 0,
                    'envs': [],
                },
            },
        ),
        ('POST', '/openEnv'): (200, _UNKNOWN_REF),
    },
)
