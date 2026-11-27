"""AdsPower's contract fixture: the live-verified shapes.

Every payload below was recorded from the real client (2026-11-27,
v8.9.23 — FACTS.md in ~/adspower-glm) with placeholders substituted:
ids like ``kaaaaaa1``, names like ``demo-store``, the documentation
IP. The contract suite serves them from a fake local HTTP API and
runs every behaviour against the real :class:`AdsPowerClient`.

AdsPower's "not authenticated" shape is ``Require api-key`` (no key
stored): it raises LOGIN_REQUIRED, with the generate-a-key advice in
the ``vendors.adspower.LoginRequired`` i18n override. It has no
session-expiry shape (verified live: signed out, a still-valid key
keeps working; a fully-signed-out client answers nothing at all —
decided at the process level in ``ensure_running``).

The paid gate: AdsPower's local API is paid-plans-only per its docs,
but the exact refusal body is UNVERIFIED live (no non-paid account
was available to probe), so no paid-gate route is asserted — declared
with a custom-reason NoPaidGate instead of an invented "recorded"
payload. Tracking note in the AdsPower lane's REPORT.md.
"""

from app.browser.adapters.adspower import AdsPowerClient
from tests.unit.test_browser.local_api_fixture import (
    ContractFixture,
    NoPaidGate,
)

_LIST_OK = {
    'code': 0,
    'msg': 'Success',
    'data': {
        'list': [
            {
                'serial_number': '1',
                'user_id': 'kaaaaaa1',
                'name': 'demo-store',
                'group_name': 'acme',
                'domain_name': 'https://www.example.com',
            },
            {
                'serial_number': '2',
                'user_id': 'kaaaaaa2',
                'name': 'demo-firefox',
                'group_name': 'acme',
                'domain_name': '',
            },
        ],
        'page': 1,
        'page_size': 100,
    },
}

_OPEN_OK = {
    'code': 0,
    'msg': 'success',
    'data': {
        'ws': {
            'puppeteer': (
                'ws://127.0.0.1:57094/devtools/browser/'
                'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
            ),
            'selenium': '127.0.0.1:57094',
        },
        'debug_port': '57094',
    },
}

CONTRACT = ContractFixture(
    client_cls=AdsPowerClient,
    routes={
        ('GET', '/api/v1/user/list?page=1&page_size=100'): (200, _LIST_OK),
        (
            'GET',
            '/api/v1/browser/start?user_id=kaaaaaa1&ip_tab=0&open_tabs=0',
        ): (200, _OPEN_OK),
        ('GET', '/api/v1/browser/stop?user_id=kaaaaaa1'): (
            200,
            {'code': 0, 'msg': 'success'},
        ),
        ('GET', '/status'): (200, {'code': 0, 'msg': 'success'}),
    },
    expected_refs=['kaaaaaa1', 'kaaaaaa2'],
    expected_cdp_port=57094,
    auth_env={'adspower_api_key': 'test-key-123'},
    logged_out_routes={
        ('GET', '/api/v1/user/list?page=1&page_size=100'): (
            200,
            {'code': -1, 'msg': 'Require api-key'},
        )
    },
    paid_gate_routes=NoPaidGate(
        reason=(
            "paid-plans-only per AdsPower's docs, but the refusal body "
            'is unverified live (no non-paid account available); the '
            'not-authenticated shape is documented as logged_out_routes'
        )
    ),
    unknown_ref_routes={
        (
            'GET',
            '/api/v1/browser/start?user_id=nope&ip_tab=0&open_tabs=0',
        ): (200, {'code': -1, 'msg': 'invalid disordered id'}),
    },
)
