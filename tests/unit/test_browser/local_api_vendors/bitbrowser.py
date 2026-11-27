"""BitBrowser's real-vendor contract fixture (recorded live shapes).

Every payload is a recorded live response, placeholdered
(``demo-store-*``, ``100000000001``, ``203.0.113.10``); the
logged-out shapes are BOTH bodies seen live — the session-expired
「token 失效…」 and the device-kick ``Login out！`` (one BitBrowser
account is signed in on one computer at a time). BitBrowser has NO
paid gate — the free plan includes the local API (the vendor's own
docs + the live probe: the sub-account's free 10-profile plan ran
every API call) — declared with the NO_PAID_GATE sentinel once B10
lands (see the TODO below). BitBrowser-only behaviour (the kernel
detail check, the workbench tab, config discovery, the locator)
stays in ``test_bitbrowser.py`` / ``test_bitbrowser_contract.py``.
"""

from app.browser.adapters.bitbrowser import BitBrowserClient
from tests.unit.test_browser.local_api_fixture import ContractFixture

_ROWS = [
    {
        'id': '100000000001',
        'seq': 1,
        'name': 'demo-store-one',
        'platform': 'https://www.amazon.com/',
        'coreProduct': 'chrome',
    },
    {
        'id': '100000000002',
        'seq': 2,
        'name': 'demo-store-two',
        'platform': 'https://www.facebook.com/',
        'coreProduct': 'chrome',
    },
]


async def _list(request):
    body = await request.json()
    page = int(body['page'])
    size = int(body['pageSize'])
    rows = _ROWS[page * size : (page + 1) * size]
    return 200, {
        'success': True,
        'data': {
            'list': rows,
            'totalNum': len(_ROWS),
            'page': page,
            'pageSize': size,
        },
    }


async def _not_found(request):
    return 200, {
        'success': False,
        'msg': '服务调用成功，但没有找到相应数据！',
    }


CONTRACT = ContractFixture(
    client_cls=BitBrowserClient,
    routes={
        ('POST', '/browser/list'): _list,
        ('POST', '/browser/detail'): (
            200,
            {
                'success': True,
                'data': {
                    'id': '100000000001',
                    'coreProduct': 'chrome',
                    'workbench': 'localserver',
                },
            },
        ),
        ('POST', '/browser/open'): (
            200,
            {
                'success': True,
                'data': {
                    'ws': 'ws://127.0.0.1:54399/devtools/browser/abc',
                    'http': '127.0.0.1:54399',
                },
            },
        ),
        ('POST', '/browser/close'): (
            200,
            {'success': True, 'data': '操作成功'},
        ),
        ('POST', '/health'): (200, {'success': True}),
    },
    expected_refs=['#1 demo-store-one', '#2 demo-store-two'],
    expected_cdp_port=54399,
    auth_env={},
    logged_out_routes={
        # The session-expired body (the client's hardcoded zh literal).
        ('POST', '/browser/list'): (
            200,
            {'success': False, 'msg': 'token 失效，请检查登录状态'},
        ),
        # The device-kick body (one account, one computer; EN,
        # FULLWIDTH !) — recorded verbatim on a live kick.
        ('POST', '/browser/open'): (
            200,
            {'success': False, 'msg': 'Login out！'},
        ),
    },
    # TODO(B10): BitBrowser has NO paid gate (the free plan includes
    # the local API) — declare it with the NO_PAID_GATE sentinel once
    # B10 lands (the addendum's follow-up); until then the field stays
    # unset so the suite's paid-gate parametrization does not try to
    # serve the sentinel as routes.
    unknown_ref_routes={
        # The real client answers the not-found message on BOTH the
        # detail pre-check and the open call for an unknown id.
        ('POST', '/browser/detail'): _not_found,
        ('POST', '/browser/open'): _not_found,
    },
)
