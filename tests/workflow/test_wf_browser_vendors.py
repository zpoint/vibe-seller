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
from app.browser.inject.errors import (
    AccountMismatch,
    ApiNotEnabled,
    ClientNotRunning,
    LoginRequired,
    PlanRequired,
    StoreUnavailable,
    UnsupportedKernel,
)
from app.browser.vendor_rules import validate_spec
from app.browser.vendors import (
    CARDINALITY_SINGLE,
    BrowserVendorSpec,
    VendorCredentialField,
    registered_vendor_specs,
    vendor_spec,
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


# The credential-less shape (R8): 'single' (one account per machine —
# there is just nothing stored to refuse) with EMPTY credential_fields
# — the user signs in once in the client; the local API acts as that
# login.
_REFLESS_SPEC = BrowserVendorSpec(
    key='fakerefless',
    display_name_en='ReflessVendor',
    display_name_zh='免登录浏览器',
    account_cardinality=CARDINALITY_SINGLE,
    credential_fields=(),
    lists_profiles=True,
    i18n_prefix='vendors.fakerefless',
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
            ctx.register_browser_backend('fakerefless', _Backend, _REFLESS_SPEC)

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


def _client_behavior(
    *, logged_in=True, stores=None, raise_cnr=False, list_error=None
) -> None:
    _CLIENT.ensure_running = mock.AsyncMock(
        side_effect=ClientNotRunning() if raise_cnr else None
    )
    _CLIENT.ensure_logged_in = mock.AsyncMock(
        side_effect=None if logged_in else LoginRequired()
    )
    _CLIENT.list_stores = mock.AsyncMock(
        side_effect=list_error, return_value=stores or []
    )


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
        stores=[
            StoreRef('fv-a', 'MarketplaceX', 'US', ref='fv-ref-a'),
            StoreRef('fv-b'),
            StoreRef('fv-c', available=False, unavailable_reason='no device'),
        ]
    )
    r = await admin_client.get('/api/browser-vendors/fakevendor/stores')
    assert r.status_code == 200
    body = r.json()
    assert [s['name'] for s in body] == ['fv-a', 'fv-b', 'fv-c']
    assert body[0]['platform'] == 'MarketplaceX'
    # A1: the contract PR's StoreRef fields flow through the generic
    # asdict serialization untouched — the picker can bind by ref and
    # grey out an unavailable env.
    assert body[0]['ref'] == 'fv-ref-a'
    assert body[1]['ref'] == ''
    assert body[2]['available'] is False
    assert body[2]['unavailable_reason'] == 'no device'


async def test_fake_vendor_list_error_codes_are_structured(
    admin_client, fake_vendor
):
    """A2: every InjectError the client raises reaches the UI as a
    structured status (the shared taxonomy lower-cased) with its
    message — never a 500 and never collapsed into one generic status."""
    codes = [
        (AccountMismatch('signed in as other@example.com'), 502),
        (ApiNotEnabled('api off'), 502),
        (PlanRequired('plan needed'), 502),
        (StoreUnavailable('no device bound'), 502),
        (UnsupportedKernel('firefox kernel'), 502),
    ]
    for err, expected in codes:
        _client_behavior(list_error=err)
        r = await admin_client.get('/api/browser-vendors/fakevendor/stores')
        assert r.status_code == expected, (
            f'{type(err).__name__} → {r.status_code}: {r.text}'
        )
        detail = json.loads(r.json()['detail'])
        assert detail['status'] == type(err).code.lower()
        assert detail['message']


async def test_twist_a_non_taxonomy_error_is_not_laundered(
    admin_client, fake_vendor
):
    """Negative control for the mapping above: the route maps the shared
    taxonomy, not everything — a bug in a client (a plain RuntimeError)
    propagates as the server error it is, instead of being laundered
    into a structured status that would pretend to be actionable."""
    _client_behavior(list_error=RuntimeError('a client bug'))
    with pytest.raises(RuntimeError):
        await admin_client.get('/api/browser-vendors/fakevendor/stores')


async def test_only_store_ref_fields_cross_the_api(admin_client, fake_vendor):
    """A vendor whose list rows carry site credentials can never leak
    them through this route: only StoreRef fields cross — including the
    contract PR's ref/available/unavailable_reason."""
    _client_behavior(stores=[StoreRef('fv-a')])
    r = await admin_client.get('/api/browser-vendors/fakevendor/stores')
    assert r.status_code == 200
    for row in r.json():
        assert set(row) <= {
            'name',
            'platform',
            'remote_env',
            'ref',
            'available',
            'unavailable_reason',
            'unavailable_detail',
        }


async def test_fake_vendor_structured_statuses(admin_client, fake_vendor):
    _client_behavior(logged_in=False)
    r = await admin_client.get('/api/browser-vendors/fakevendor/stores')
    assert r.status_code == 502
    assert json.loads(r.json()['detail'])['status'] == 'login_required'

    _client_behavior(raise_cnr=True)
    r = await admin_client.get('/api/browser-vendors/fakevendor/stores')
    assert r.status_code == 502
    assert json.loads(r.json()['detail'])['status'] == ('client_not_running')


async def test_credential_less_vendor_lists_without_stored_login(
    admin_client, fake_vendor
):
    """R8 / addendum 2 item 1: a credential-less vendor ('single' with
    empty credential_fields — the user signs in once in the client, the
    local API acts as that login) is served by the generic stores
    route, and the credential routes keep refusing it."""
    _client_behavior(stores=[StoreRef('rf-a')])
    r = await admin_client.get('/api/browser-vendors/fakerefless/stores')
    assert r.status_code == 200
    assert [s['name'] for s in r.json()] == ['rf-a']
    r = await admin_client.get('/api/browser-vendors/fakerefless/credentials')
    assert r.status_code == 400
    r = await admin_client.post(
        '/api/browser-vendors/fakerefless/credentials', json={}
    )
    assert r.status_code == 400


def _single_vendors_with_credentials():
    """Every registered 'single' vendor that stores credential fields —
    the R8 contract (A/B) runs against each of them, so a new vendor
    joins automatically."""
    return sorted(
        spec.key
        for spec in registered_vendor_specs().values()
        if spec.account_cardinality == CARDINALITY_SINGLE
        and spec.credential_fields
    )


async def _save_credentials(admin_client, key, tag):
    """Save a vendor's machine login with placeholder values."""
    spec = vendor_spec(key)
    return await admin_client.post(
        f'/api/browser-vendors/{key}/credentials',
        json={f.key: f'{tag}-{f.key}' for f in spec.credential_fields},
    )


@pytest.mark.parametrize('key', _single_vendors_with_credentials())
async def test_r8_a_save_refused_while_stores_exist(admin_client, key):
    """(A) one account per machine: while any of the vendor's stores is
    bound, saving credentials is refused with `one_account` — a bound
    store belongs to the current account."""
    r = await _save_credentials(admin_client, key, 'first')
    assert r.status_code == 200
    async with tasks_mod.async_session() as db:
        db.add(Store(name=f'r8-{key}', browser_backend=key))
        await db.commit()
    r = await _save_credentials(admin_client, key, 'second')
    assert r.status_code == 400
    assert json.loads(r.json()['detail'])['status'] == 'one_account'


@pytest.mark.parametrize('key', _single_vendors_with_credentials())
async def test_r8_b_after_stores_deleted_save_replaces(admin_client, key):
    """(B) the same vendor after its stores are deleted: the save is
    accepted and REPLACES the stored account."""
    r = await _save_credentials(admin_client, key, 'first')
    assert r.status_code == 200
    async with tasks_mod.async_session() as db:
        store = Store(name=f'r8-{key}', browser_backend=key)
        db.add(store)
        await db.commit()
    r = await _save_credentials(admin_client, key, 'second')
    assert r.status_code == 400  # still bound → refused
    async with tasks_mod.async_session() as db:
        await db.delete(store)
        await db.commit()
    r = await _save_credentials(admin_client, key, 'second')
    assert r.status_code == 200
    status = (
        await admin_client.get(f'/api/browser-vendors/{key}/credentials')
    ).json()
    spec = vendor_spec(key)
    non_secret = [f.key for f in spec.credential_fields if not f.secret]
    assert all(
        status[f.key] == f'second-{f.key}'
        for f in spec.credential_fields
        if f.key in non_secret
    )


@pytest.mark.parametrize('key', _single_vendors_with_credentials())
async def test_r8_twist_a_without_stores_the_save_is_accepted(
    admin_client, key
):
    """Negative control for (A): the refusal is the bound STORE, not the
    vendor — with no store the same save is accepted."""
    r = await _save_credentials(admin_client, key, 'free')
    assert r.status_code == 200


@pytest.mark.parametrize('key', _single_vendors_with_credentials())
async def test_r8_twist_b_a_refused_save_does_not_replace(admin_client, key):
    """Negative control for (B): while the store exists, the refused
    save leaves the ORIGINAL account in place."""
    await _save_credentials(admin_client, key, 'first')
    async with tasks_mod.async_session() as db:
        db.add(Store(name=f'r8-{key}', browser_backend=key))
        await db.commit()
    await _save_credentials(admin_client, key, 'second')
    status = (
        await admin_client.get(f'/api/browser-vendors/{key}/credentials')
    ).json()
    spec = vendor_spec(key)
    assert all(
        status[f.key] == f'first-{f.key}'
        for f in spec.credential_fields
        if not f.secret
    )


async def test_r8_registration_refuses_wrong_account_rules(
    admin_client,
):
    """R8 spec validation: an anti-detect vendor cannot be 'none' (a
    credential-less vendor is 'single' + empty fields); 'multi' needs
    a MULTI_ACCOUNT_VENDORS entry; a plain browser must be 'none'.
    Each refusal happens at registration, before anything runs."""

    def spec(**kw):
        base = dict(
            key='rulesprobe',
            display_name_en='Probe',
            display_name_zh='探针',
        )
        base.update(kw)
        return BrowserVendorSpec(**base)

    # anti-detect + 'none' → refused (use 'single' + empty fields).
    with pytest.raises(ValueError, match='cannot be'):
        validate_spec(spec(account_cardinality='none'))
    # anti-detect + 'multi' without an allowlist entry → refused.
    with pytest.raises(ValueError, match='MULTI_ACCOUNT_VENDORS'):
        validate_spec(spec(account_cardinality='multi'))
    # plain + 'single' → refused (plain browsers are 'none').
    with pytest.raises(ValueError, match='plain browser'):
        validate_spec(spec(kind='plain'))
    # And the two legal shapes pass.
    validate_spec(spec(account_cardinality='single', credential_fields=()))
    validate_spec(spec(kind='plain', account_cardinality='none'))


async def test_twist_single_cardinality_keeps_its_credentials(
    admin_client, fake_vendor
):
    """Negative control: the 'none' refusal is cardinality-driven — the
    single-cardinality vendor's credential routes still work."""
    r = await admin_client.get('/api/browser-vendors/fakevendor/credentials')
    assert r.status_code == 200
    r = await admin_client.post(
        '/api/browser-vendors/fakevendor/credentials',
        json={'acct': 'a', 'password': 'p'},
    )
    assert r.status_code == 200


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
