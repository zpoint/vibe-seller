"""The generic browser-vendor routes serve a plugin-registered vendor.

The invariant (ARCHITECTURE §8): a fake vendor registered through the
plugin context appears in ``GET /api/browser-vendors``, saves and reads
its machine credentials through the generic credential routes (with the
one-account refusal), and lists its stores through the generic stores
route — with zero shared-code edits. The negative control is the same
calls without the registration: the vendor is simply absent (404).
"""

import json
from unittest import mock

import pytest

from app.browser.inject.client import InjectClient, StoreRef
from app.browser.inject.errors import ClientNotRunning, LoginRequired
from app.browser.vendors import (
    CARDINALITY_SINGLE,
    BrowserVendorSpec,
    VendorCredentialField,
)
from app.models.store import Store
import app.plugins as plugins_mod
from app.plugins import ExtensionContext, Plugin, reset_for_tests
import app.routers.tasks as tasks_mod

pytestmark = pytest.mark.workflow

# The fake client the spec's factory hands out; tests swap the behavior
# through this holder (the factory itself is frozen in the spec).
_CLIENT = mock.MagicMock(spec=InjectClient)


def _factory(cfg: dict) -> InjectClient:
    return _CLIENT


_SPEC = BrowserVendorSpec(
    key='fakevendor',
    display_name_en='FakeVendor',
    display_name_zh='假浏览器',
    account_cardinality=CARDINALITY_SINGLE,
    credential_fields=(
        VendorCredentialField('acct', 'vendors.fakevendor.acct'),
        VendorCredentialField(
            'password', 'vendors.fakevendor.password', secret=True
        ),
    ),
    lists_profiles=True,
    i18n_prefix='vendors.fakevendor',
    client_factory=_factory,
)


class _Backend:
    """A no-arg-constructible stand-in for a BrowserBackend."""


@pytest.fixture
def fake_vendor(monkeypatch):
    class _Plugin(Plugin):
        @property
        def name(self) -> str:
            return 'fakevendor'

        def install(self, ctx: ExtensionContext) -> None:
            ctx.register_browser_backend('fakevendor', _Backend, _SPEC)

    class _EntryPoint:
        name = 'fake:fakevendor'

        def load(self):
            return _Plugin

    def fake_entry_points(group=None):
        return [_EntryPoint()] if group == 'vibe_seller.plugins' else []

    monkeypatch.setattr(
        plugins_mod.importlib.metadata, 'entry_points', fake_entry_points
    )
    reset_for_tests()
    yield
    reset_for_tests()


def _client_behavior(*, logged_in=True, stores=None, raise_cnr=False) -> None:
    _CLIENT.ensure_running = mock.AsyncMock(
        side_effect=ClientNotRunning() if raise_cnr else None
    )
    _CLIENT.ensure_logged_in = mock.AsyncMock(
        side_effect=None if logged_in else LoginRequired()
    )
    _CLIENT.list_stores = mock.AsyncMock(return_value=stores or [])


async def test_fake_vendor_appears_in_the_generic_surface(
    admin_client, fake_vendor
):
    r = await admin_client.get('/api/browser-vendors')
    assert r.status_code == 200
    keys = [v['key'] for v in r.json()]
    assert 'fakevendor' in keys
    # Registration order is stable (builtin first, then entry points).
    assert keys.index('chrome') < keys.index('fakevendor')
    meta = next(v for v in r.json() if v['key'] == 'fakevendor')
    assert meta['account_cardinality'] == 'single'
    assert meta['credential_fields'][1]['secret'] is True


async def test_fake_vendor_credentials_roundtrip(admin_client, fake_vendor):
    r = await admin_client.post(
        '/api/browser-vendors/fakevendor/credentials',
        json={'acct': 'acme', 'password': 'pw'},
    )
    assert r.status_code == 200
    d = (
        await admin_client.get('/api/browser-vendors/fakevendor/credentials')
    ).json()
    assert d['configured'] is True
    assert d['acct'] == 'acme'
    assert 'password' not in d  # the secret is never returned


async def test_fake_vendor_missing_fields_refused(admin_client, fake_vendor):
    r = await admin_client.post(
        '/api/browser-vendors/fakevendor/credentials',
        json={'acct': 'acme', 'password': ''},
    )
    assert r.status_code == 400


async def test_fake_vendor_one_account_refusal(admin_client, fake_vendor):
    """The spec-driven constraint: a single-account vendor refuses the
    save while any of its stores is bound."""
    await admin_client.post(
        '/api/browser-vendors/fakevendor/credentials',
        json={'acct': 'acme', 'password': 'pw'},
    )
    async with tasks_mod.async_session() as db:
        db.add(Store(name='fv-store', browser_backend='fakevendor'))
        await db.commit()
    r = await admin_client.post(
        '/api/browser-vendors/fakevendor/credentials',
        json={'acct': 'other', 'password': 'pw'},
    )
    assert r.status_code == 400
    assert json.loads(r.json()['detail'])['status'] == 'one_account'


async def test_fake_vendor_lists_stores_through_generic_route(
    admin_client, fake_vendor
):
    _client_behavior(
        stores=[StoreRef('fv-a', 'MarketplaceX', 'US'), StoreRef('fv-b')]
    )
    r = await admin_client.get('/api/browser-vendors/fakevendor/stores')
    assert r.status_code == 200
    body = r.json()
    assert [s['name'] for s in body] == ['fv-a', 'fv-b']
    assert body[0]['platform'] == 'MarketplaceX'


async def test_fake_vendor_structured_statuses(admin_client, fake_vendor):
    _client_behavior(logged_in=False)
    r = await admin_client.get('/api/browser-vendors/fakevendor/stores')
    assert r.status_code == 502
    assert json.loads(r.json()['detail'])['status'] == 'login_required'

    _client_behavior(raise_cnr=True)
    r = await admin_client.get('/api/browser-vendors/fakevendor/stores')
    assert r.status_code == 502
    assert json.loads(r.json()['detail'])['status'] == ('client_not_running')


async def test_twist_without_registration_the_vendor_is_absent(
    admin_client,
):
    """Negative control: the same calls without the plugin registration
    find no vendor — nothing about 'fakevendor' exists in shared code."""
    r = await admin_client.get('/api/browser-vendors')
    assert 'fakevendor' not in [v['key'] for v in r.json()]
    r = await admin_client.get('/api/browser-vendors/fakevendor/stores')
    assert r.status_code == 404
    r = await admin_client.post(
        '/api/browser-vendors/fakevendor/credentials', json={}
    )
    assert r.status_code == 404
