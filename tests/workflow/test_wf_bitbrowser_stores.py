"""The BitBrowser store flows through the generic vendor surface.

A ``CARDINALITY_NONE`` vendor: the store binds with just the profile
ref (no machine credentials anywhere), and the generic stores route
lists the client's profiles with the structured statuses the UI
renders. The client's behaviour is faked at the class level (the
spec's frozen factory builds the real client; the methods are patched
so the test never touches a real BitBrowser install).
"""

import json
from unittest import mock

import pytest

from app.browser.adapters.bitbrowser import BitBrowserClient
from app.browser.inject.client import StoreRef
from app.browser.inject.errors import LoginRequired

pytestmark = pytest.mark.workflow


def _fake_client_behavior(*, stores=None, logged_out=False):
    BitBrowserClient.ensure_running = mock.AsyncMock(return_value=None)
    BitBrowserClient.ensure_logged_in = mock.AsyncMock(
        side_effect=LoginRequired() if logged_out else None
    )
    BitBrowserClient.list_stores = mock.AsyncMock(return_value=stores or [])


@pytest.fixture(autouse=True)
def _restore_client_methods():
    """The class-level patches must not leak into other tests."""
    saved = {
        name: getattr(BitBrowserClient, name)
        for name in ('ensure_running', 'ensure_logged_in', 'list_stores')
    }
    yield
    for name, attr in saved.items():
        setattr(BitBrowserClient, name, attr)


async def test_bitbrowser_lists_stores_without_any_credentials(
    admin_client,
):
    """The sign-in-once shape (a `single` vendor with no credential
    fields): nothing to configure, no stored credentials — the picker
    lists profiles straight from the (signed-in) client."""
    _fake_client_behavior(
        stores=[
            StoreRef(
                ref='100000000001',
                name='#1 demo-store-one',
                platform='amazon.com',
            ),
            StoreRef(
                ref='100000000002',
                name='#2 demo-store-two',
                platform='amazon.com',
                available=False,
                unavailable_reason='Firefox 内核，暂不支持',
            ),
        ]
    )
    r = await admin_client.get('/api/browser-vendors/bitbrowser/stores')
    assert r.status_code == 200
    body = r.json()
    assert [s['ref'] for s in body] == ['100000000001', '100000000002']
    assert body[0]['name'] == '#1 demo-store-one'
    assert body[1]['available'] is False  # greyed in the picker
    # 'single' with no fields (the sign-in-once shape): the generic
    # credential routes refuse a vendor with nothing to configure.
    r = await admin_client.get('/api/browser-vendors/bitbrowser/credentials')
    assert r.status_code == 400


async def test_bitbrowser_store_binds_by_profile_ref(admin_client):
    """Creating a store the way the UI does: backend 'bitbrowser' with
    the picked profile's ref in browser_oauth — nothing else."""
    r = await admin_client.post(
        '/api/stores',
        json={
            'name': 'bb-store',
            'browser_backend': 'bitbrowser',
            'browser_oauth': '100000000001',
        },
    )
    assert r.status_code == 200
    created = r.json()
    assert created['browser_backend'] == 'bitbrowser'
    assert created['browser_oauth'] == '100000000001'
    # It shows in the store list with the same binding.
    listing = (await admin_client.get('/api/stores')).json()
    mine = next(s for s in listing if s['name'] == 'bb-store')
    assert mine['browser_oauth'] == '100000000001'


async def test_bitbrowser_logged_out_is_the_structured_status(
    admin_client,
):
    _fake_client_behavior(logged_out=True)
    r = await admin_client.get('/api/browser-vendors/bitbrowser/stores')
    assert r.status_code == 502
    assert json.loads(r.json()['detail'])['status'] == 'login_required'


async def test_bitbrowser_twist_unbound_store_has_no_profile_ref(
    admin_client,
):
    """Negative control: the binding is the picked profile's ref — a
    store created without one (not via the picker) carries an empty
    browser_oauth, never a fabricated profile."""
    r = await admin_client.post(
        '/api/stores',
        json={'name': 'bb-store-unbound', 'browser_backend': 'bitbrowser'},
    )
    assert r.status_code == 200
    assert r.json()['browser_oauth'] in ('', None)
