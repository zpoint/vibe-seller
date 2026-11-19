"""The yuehulian store-enumerate endpoint returns actionable statuses.

The UI dropdown must distinguish "log in", "launch the client" and
"unsupported platform" from a real (possibly empty) list — never collapse
them into one. Each status pairs with the logged-in happy path.
"""

import json
from unittest import mock

import pytest

from app.browser.inject.client import StoreRef
from app.browser.inject.errors import ClientNotRunning, LoginRequired
from app.models.app_settings import AppSettings
from app.models.store import Store
import app.routers.tasks as tasks_mod
from app.utils.crypto import decrypt_password

pytestmark = pytest.mark.workflow


async def _add_yuehulian_store(name='yhl-store'):
    async with tasks_mod.async_session() as db:
        db.add(Store(name=name, browser_backend='yuehulian'))
        await db.commit()


def _fake_client(*, logged_in=True, stores=None, raise_cnr=False):
    client = mock.MagicMock()
    client.ensure_running = mock.AsyncMock(
        side_effect=ClientNotRunning() if raise_cnr else None
    )
    client.is_logged_in = mock.AsyncMock(return_value=logged_in)
    # Enumerate auto-logs-in via ensure_logged_in; logged-out with no stored
    # creds raises LoginRequired (what the router maps to login_required).
    client.ensure_logged_in = mock.AsyncMock(
        side_effect=None if logged_in else LoginRequired()
    )
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


async def test_save_and_get_credentials_roundtrip(admin_client):
    r = await admin_client.post(
        '/api/yuehulian/credentials',
        json={
            'team_name': 'acme-team',
            'subaccount': 'sub-01',
            'password': 'secret',
        },
    )
    assert r.status_code == 200
    d = (await admin_client.get('/api/yuehulian/credentials')).json()
    assert d['configured'] is True
    assert d['team_name'] == 'acme-team' and d['subaccount'] == 'sub-01'
    assert 'password' not in d  # the secret is never returned


async def test_credentials_password_stored_encrypted(admin_client):
    await admin_client.post(
        '/api/yuehulian/credentials',
        json={
            'team_name': 't',
            'subaccount': 'a',
            'password': 'plaintext-secret',
        },
    )
    async with tasks_mod.async_session() as db:
        row = await db.get(AppSettings, 'yuehulian_password')
    assert row.value != 'plaintext-secret'  # encrypted at rest
    assert decrypt_password(row.value) == 'plaintext-secret'


async def test_save_credentials_requires_all_fields(admin_client):
    r = await admin_client.post(
        '/api/yuehulian/credentials',
        json={'team_name': 't', 'subaccount': '', 'password': 'p'},
    )
    assert r.status_code == 400


async def test_add_account_blocked_when_stores_exist(admin_client):
    """One account per machine: [+] (POST) is refused with the one_account
    status whenever a yuehulian store is bound — the store belongs to the
    current account, so the user must delete all yuehulian stores first."""
    await admin_client.post(
        '/api/yuehulian/credentials',
        json={'team_name': 'acme', 'subaccount': 'sub-01', 'password': 'pw'},
    )
    await _add_yuehulian_store()
    r = await admin_client.post(
        '/api/yuehulian/credentials',
        json={'team_name': 'other', 'subaccount': 'sub-02', 'password': 'pw'},
    )
    assert r.status_code == 400
    assert json.loads(r.json()['detail'])['status'] == 'one_account'
    # The stored login is unchanged — the overwrite did not take.
    d = (await admin_client.get('/api/yuehulian/credentials')).json()
    assert d['team_name'] == 'acme' and d['subaccount'] == 'sub-01'


async def test_resave_same_account_also_blocked_with_stores(admin_client):
    """No edit path: even re-saving the SAME account is blocked while stores
    exist (one design — stores exist => no [+]), not just a different one."""
    await admin_client.post(
        '/api/yuehulian/credentials',
        json={'team_name': 'acme', 'subaccount': 'sub-01', 'password': 'pw1'},
    )
    await _add_yuehulian_store()
    r = await admin_client.post(
        '/api/yuehulian/credentials',
        json={'team_name': 'acme', 'subaccount': 'sub-01', 'password': 'pw2'},
    )
    assert r.status_code == 400
    assert json.loads(r.json()['detail'])['status'] == 'one_account'
    async with tasks_mod.async_session() as db:
        row = await db.get(AppSettings, 'yuehulian_password')
    assert decrypt_password(row.value) == 'pw1'  # unchanged — save refused


async def test_add_account_overwrites_when_no_stores(admin_client):
    """Negative control: with no yuehulian stores bound, [+] overwrites the
    single account (the "delete all stores, then add" path)."""
    await admin_client.post(
        '/api/yuehulian/credentials',
        json={'team_name': 'acme', 'subaccount': 'sub-01', 'password': 'pw'},
    )
    r = await admin_client.post(
        '/api/yuehulian/credentials',
        json={'team_name': 'other', 'subaccount': 'sub-02', 'password': 'pw'},
    )
    assert r.status_code == 200
    d = (await admin_client.get('/api/yuehulian/credentials')).json()
    assert d['team_name'] == 'other' and d['subaccount'] == 'sub-02'


async def test_enumerate_passes_decrypted_creds_to_client(admin_client):
    await admin_client.post(
        '/api/yuehulian/credentials',
        json={'team_name': 'acme', 'subaccount': 'sub', 'password': 'pw'},
    )
    fake = _fake_client(stores=[StoreRef('store-a')])
    with (
        mock.patch(
            'app.routers.yuehulian.get_platform', return_value='windows'
        ),
        mock.patch(
            'app.routers.yuehulian.RealYuehulianClient', return_value=fake
        ) as cls,
    ):
        r = await admin_client.get('/api/yuehulian/stores')
    assert r.status_code == 200
    cfg = cls.call_args.args[0]
    assert cfg['yuehulian_team'] == 'acme'
    assert cfg['yuehulian_account'] == 'sub'
    assert cfg['yuehulian_password'] == 'pw'  # decrypted for the login eval
