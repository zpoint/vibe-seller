"""The yuehulian store-enumerate endpoint returns actionable statuses.

The UI dropdown must distinguish "log in", "launch the client" and
"unsupported platform" from a real (possibly empty) list — never collapse
them into one. Each status pairs with the logged-in happy path.
"""

import json
from unittest import mock

import pytest

from app.browser.inject.client import StoreRef
from app.browser.inject.errors import ClientNotRunning

pytestmark = pytest.mark.workflow


def _fake_client(*, logged_in=True, stores=None, raise_cnr=False):
    client = mock.MagicMock()
    client.ensure_running = mock.AsyncMock(
        side_effect=ClientNotRunning() if raise_cnr else None
    )
    client.is_logged_in = mock.AsyncMock(return_value=logged_in)
    client.list_stores = mock.AsyncMock(return_value=stores or [])
    return client


def _patch(fake, platform='windows'):
    return (
        mock.patch('app.routers.yuehulian.get_platform', return_value=platform),
        mock.patch(
            'app.routers.yuehulian.RealYuehulianClient', return_value=fake
        ),
    )


async def test_lists_stores_when_logged_in(admin_client):
    fake = _fake_client(
        stores=[StoreRef('store-a', 'MarketplaceX', 'US'), StoreRef('store-b')]
    )
    p1, p2 = _patch(fake)
    with p1, p2:
        r = await admin_client.get('/api/yuehulian/stores')
    assert r.status_code == 200
    body = r.json()
    assert [s['name'] for s in body] == ['store-a', 'store-b']
    assert body[0]['platform'] == 'MarketplaceX'


async def test_login_required_is_its_own_status(admin_client):
    fake = _fake_client(logged_in=False)
    p1, p2 = _patch(fake)
    with p1, p2:
        r = await admin_client.get('/api/yuehulian/stores')
    assert r.status_code == 502
    assert json.loads(r.json()['detail'])['status'] == 'login_required'


async def test_client_not_running_is_its_own_status(admin_client):
    fake = _fake_client(raise_cnr=True)
    p1, p2 = _patch(fake)
    with p1, p2:
        r = await admin_client.get('/api/yuehulian/stores')
    assert r.status_code == 502
    assert json.loads(r.json()['detail'])['status'] == 'client_not_running'


async def test_unsupported_platform_is_rejected(admin_client):
    fake = _fake_client()
    p1, p2 = _patch(fake, platform='linux')
    with p1, p2:
        r = await admin_client.get('/api/yuehulian/stores')
    assert r.status_code == 400
    assert json.loads(r.json()['detail'])['status'] == 'unsupported_platform'
