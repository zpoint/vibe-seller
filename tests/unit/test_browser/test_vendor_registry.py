"""The browser-vendor registry: specs drive the shared helpers.

The invariant these pin: shared code (base helpers, credential storage,
injection) reads the registry, so registering a vendor through the plugin
context changes behaviour everywhere without a shared-code edit. Each
behaviour pairs with a twist: flip the spec (unregister / change a field)
and the opposite must hold.
"""

import logging
from unittest import mock

import pytest

from app.browser import vendor_creds, win_app_locator as wal
from app.browser.base import antidetect_display_name, is_antidetect
from app.browser.manager import inject_vendor_store_config
from app.browser.vendor_rules import (
    CARDINALITY_NONE,
    validate_spec,
)
from app.browser.vendors import (
    CARDINALITY_MULTI,
    CARDINALITY_SINGLE,
    KIND_ANTIDETECT,
    KIND_PLAIN,
    PICKER_CUSTOM,
    PICKER_GENERIC,
    BrowserVendorSpec,
    VendorCredentialField,
    VendorDocsLink,
    registered_vendor_specs,
    vendor_spec,
)
from app.browser.win_app_locator import WinAppSpec
from app.models.app_settings import AppSettings
from app.models.store import Store
import app.plugins as plugins_mod
from app.plugins import ExtensionContext, Plugin, reset_for_tests
import app.routers.stores as stores_route

pytestmark = pytest.mark.unit


class _FakeBackend:
    """A no-arg-constructible stand-in for a BrowserBackend."""


def _spec(
    key='fakevendor',
    *,
    kind=KIND_ANTIDETECT,
    cardinality=CARDINALITY_SINGLE,
    fields=(VendorCredentialField('acct', 'vendors.fakevendor.acct'),),
    docs=None,
    **extra,
):
    return BrowserVendorSpec(
        key=key,
        display_name_en='FakeVendor',
        display_name_zh='假浏览器',
        kind=kind,
        account_cardinality=cardinality,
        credential_fields=fields,
        lists_profiles=True,
        i18n_prefix=f'vendors.{key}',
        docs=docs or {},
        **extra,
    )


@pytest.fixture
def fake_vendor(monkeypatch):
    """Install a fake vendor through the plugin context (an entry-point
    plugin, the exact path a private plugin takes); reset on exit."""

    def _install(spec, backend_cls=_FakeBackend):
        class _Plugin(Plugin):
            @property
            def name(self) -> str:
                return spec.key

            def install(self, ctx: ExtensionContext) -> None:
                ctx.register_browser_backend(spec.key, backend_cls, spec)

        class _EntryPoint:
            name = f'fake:{spec.key}'
            value = f'fake:{spec.key}'

            def load(self):
                return _Plugin

        def fake_entry_points(group=None):
            return [_EntryPoint()] if group == 'vibe_seller.plugins' else []

        monkeypatch.setattr(
            plugins_mod.importlib.metadata, 'entry_points', fake_entry_points
        )
        reset_for_tests()
        return spec

    yield _install
    reset_for_tests()


# ── base helpers read the registry ──────────────────────────────────


def test_every_selectable_vendor_has_bilingual_setup_guide():
    """The create-store picker must offer docs for every backend option."""
    reset_for_tests()
    specs = registered_vendor_specs()
    expected = {
        'chrome',
        'ziniao',
        'yuehulian',
        'adspower',
        'wenmai',
        'bitbrowser',
    }
    assert expected <= specs.keys()
    for key in expected:
        link = specs[key].docs['setup']
        assert link.zh.startswith('https://docs.vibe-sellers.com/')
        assert link.en.startswith('https://docs.vibe-sellers.com/en/')
    reset_for_tests()


def test_is_antidetect_reads_the_registry(fake_vendor):
    fake_vendor(_spec())
    assert is_antidetect('fakevendor')
    assert not is_antidetect('chrome')


def test_twist_unregistering_removes_antidetect(fake_vendor, monkeypatch):
    # The negative control: the SAME key is not anti-detect once its spec
    # is gone (no hardcoded set anywhere).
    fake_vendor(_spec())
    assert is_antidetect('fakevendor')
    reset_for_tests()
    monkeypatch.undo()  # the fake entry point must not resurrect it
    assert not is_antidetect('fakevendor')


def test_display_name_composes_from_the_spec(fake_vendor):
    fake_vendor(_spec())
    assert antidetect_display_name('fakevendor') == 'FakeVendor (假浏览器)'


def test_plain_kind_is_not_antidetect(fake_vendor):
    fake_vendor(_spec(kind=KIND_PLAIN))
    assert not is_antidetect('fakevendor')


# ── spec registration through the plugin context ────────────────────


def test_r8_registration_validates_the_account_rule():
    """R8: an anti-detect spec cannot be 'none'; 'multi' needs an
    allowlist entry; a plain spec must be 'none' — refused at
    registration, not misbehaved at runtime."""
    ctx = ExtensionContext()
    with pytest.raises(ValueError, match='cannot be'):
        ctx.register_browser_backend(
            'badnone', _FakeBackend, _spec(key='badnone', cardinality='none')
        )
    with pytest.raises(ValueError, match='MULTI_ACCOUNT_VENDORS'):
        ctx.register_browser_backend(
            'badmulti', _FakeBackend, _spec(key='badmulti', cardinality='multi')
        )
    with pytest.raises(ValueError, match='plain browser'):
        ctx.register_browser_backend(
            'badplain',
            _FakeBackend,
            BrowserVendorSpec(
                key='badplain',
                display_name_en='Bad',
                display_name_zh='坏',
                kind='plain',
            ),
        )


def test_r8_registration_accepts_the_legal_shapes():
    """Twist: the R8-legal shapes register — 'single' with or without
    credential fields, plain 'none', and ziniao's allowlisted
    'multi'."""
    ctx = ExtensionContext()
    ctx.register_browser_backend('ok1', _FakeBackend, _spec(key='ok1'))
    ctx.register_browser_backend(
        'ok2',
        _FakeBackend,
        _spec(key='ok2', cardinality='single', fields=()),
    )
    ctx.register_browser_backend(
        'ok3',
        _FakeBackend,
        BrowserVendorSpec(
            key='ok3',
            display_name_en='Plain',
            display_name_zh='普通',
            kind='plain',
            account_cardinality='none',
        ),
    )
    assert 'ok1' in ctx.vendor_specs
    assert 'ok2' in ctx.vendor_specs
    assert 'ok3' in ctx.vendor_specs


def test_spec_key_must_match_the_registration_name():
    """A5: a spec registered under a different name would mean two keys
    for one vendor (routes key on one, the manager on the other) —
    refused at registration, not misbehaved at runtime."""
    ctx = ExtensionContext()
    with pytest.raises(ValueError, match='does not match'):
        ctx.register_browser_backend('othername', _FakeBackend, _spec())


def test_twist_a_matching_name_registers():
    ctx = ExtensionContext()
    ctx.register_browser_backend('fakevendor', _FakeBackend, _spec())
    assert 'fakevendor' in ctx.vendor_specs


def test_registered_spec_is_served(fake_vendor):
    fake_vendor(_spec())
    spec = vendor_spec('fakevendor')
    assert spec is not None
    assert spec.account_cardinality == CARDINALITY_SINGLE


def test_public_metadata_hides_wiring(fake_vendor):
    fake_vendor(
        _spec(docs={'errors': VendorDocsLink('fakeErrors', 'zh-u', 'en-u')})
    )
    meta = vendor_spec('fakevendor').public_metadata()
    assert meta['key'] == 'fakevendor'
    assert meta['credential_fields'][0]['key'] == 'acct'
    assert meta['credential_fields'][0]['secret'] is False
    assert meta['docs']['errors'] == {
        'topic': 'fakeErrors',
        'zh': 'zh-u',
        'en': 'en-u',
    }
    # Wiring callables never cross the API boundary.
    assert 'client_factory' not in meta
    assert 'win_app' not in meta


def test_ziniao_spec_declares_multi_account_shape():
    """The spec must already express ziniao's shape (user's constraint):
    an account list with per-account profiles and a launch-mode client,
    so a later migration needs no spec redesign."""
    spec = vendor_spec('ziniao')
    assert spec is not None
    assert spec.account_cardinality == CARDINALITY_MULTI
    assert spec.profiles_scope == 'account'
    assert spec.lists_profiles
    assert spec.picker_kind == PICKER_CUSTOM
    assert spec.win_app is not None  # the locator serves it from here


def test_yuehulian_spec_is_the_single_account_shape():
    spec = vendor_spec('yuehulian')
    assert spec is not None
    assert spec.account_cardinality == CARDINALITY_SINGLE
    assert spec.picker_kind == PICKER_GENERIC
    assert spec.client_factory is not None
    assert spec.win_app is not None
    assert spec.client_path_setting == 'yuehulian_client_path'


def test_yuehulian_spec_lists_mac_among_its_platforms():
    """The macOS client is driven natively, so the spec (and the vendor
    list route that serves ``public_metadata``) must offer 悦互联 on
    'mac' alongside windows/wsl — and still not on plain linux."""
    spec = vendor_spec('yuehulian')
    assert spec is not None
    assert spec.platforms == frozenset({'windows', 'wsl', 'mac'})
    assert 'mac' in spec.public_metadata()['platforms']


# ── machine-scope credential storage (vendor_creds) ─────────────────


async def test_credentials_roundtrip_and_injection(
    fake_vendor, async_db_session
):
    fake_vendor(
        _spec(
            fields=(
                VendorCredentialField('acct', 'vendors.fakevendor.acct'),
                VendorCredentialField(
                    'password', 'vendors.fakevendor.password', secret=True
                ),
            )
        )
    )
    spec = vendor_spec('fakevendor')
    await vendor_creds.save_credentials(
        async_db_session, spec, {'acct': 'acme', 'password': 'pw'}
    )
    # The app_settings keys are <vendor>_<field> — for yuehulian exactly
    # the keys the pre-registry code used, so saved creds survive.
    for key, encrypted in (
        ('fakevendor_acct', False),
        ('fakevendor_password', True),
    ):
        row = await async_db_session.get(AppSettings, key)
        assert row is not None
        if encrypted:
            assert row.value != 'pw'

    status = await vendor_creds.credentials_status(async_db_session, spec)
    assert status['configured'] is True
    assert status['acct'] == 'acme'
    assert 'password' not in status  # secrets never returned

    cfg: dict = {}
    await vendor_creds.inject_vendor_config(cfg, async_db_session, spec)
    assert cfg['fakevendor_acct'] == 'acme'
    assert cfg['fakevendor_password'] == 'pw'  # decrypted for the client


async def test_twist_missing_required_field_is_not_configured(
    async_db_session,
):
    spec = _spec()
    status = await vendor_creds.credentials_status(async_db_session, spec)
    assert status['configured'] is False


async def test_last_store_delete_rolls_back_store_and_credentials_together(
    fake_vendor, async_db_session, monkeypatch
):
    """Default (production-style) SQLite isolation: if credential cleanup
    fails after the store flush, rollback restores BOTH writes.

    Workflow's AUTOCOMMIT engine cannot pin this transaction boundary; the
    shared unit fixture deliberately uses SQLAlchemy's default isolation.
    """
    spec = fake_vendor(
        _spec(
            fields=(
                VendorCredentialField('acct', 'vendors.fakevendor.acct'),
                VendorCredentialField(
                    'password', 'vendors.fakevendor.password', secret=True
                ),
            )
        )
    )
    await vendor_creds.save_credentials(
        async_db_session, spec, {'acct': 'acme', 'password': 'pw'}
    )
    store = Store(name='atomic-delete', browser_backend='fakevendor')
    async_db_session.add(store)
    await async_db_session.commit()
    store_id = store.id
    encrypted_before = (
        await async_db_session.get(AppSettings, 'fakevendor_password')
    ).value
    monkeypatch.setattr(
        stores_route.browser_manager,
        'stop_session',
        mock.AsyncMock(return_value=None),
    )

    real_clear = vendor_creds.clear_credentials

    async def fail_clear(db, cleared_spec):
        # Exercise deletion of BOTH rows before the failure; merely raising
        # before cleanup would not test their restoration.
        await real_clear(db, cleared_spec)
        raise RuntimeError('injected credential cleanup failure')

    monkeypatch.setattr(
        stores_route.vendor_creds, 'clear_credentials', fail_clear
    )
    with pytest.raises(RuntimeError, match='injected credential cleanup'):
        await stores_route.delete_store(
            store_id,
            db=async_db_session,
            _user=mock.Mock(),
        )
    # FastAPI closes the request session on the exception, which rolls back
    # its open transaction. Do the same explicitly for this direct call.
    await async_db_session.rollback()
    assert await async_db_session.get(Store, store_id) is not None
    status = await vendor_creds.credentials_status(async_db_session, spec)
    assert status['configured'] is True
    assert status['acct'] == 'acme'
    acct_row = await async_db_session.get(AppSettings, 'fakevendor_acct')
    password_row = await async_db_session.get(
        AppSettings, 'fakevendor_password'
    )
    assert acct_row is not None
    assert acct_row.value == 'acme'
    assert password_row is not None
    assert password_row.value == encrypted_before


# ── a store binds by the ref the picker offered ─────────────────────


class _StoreRow:
    """The Store columns the injection reads."""

    def __init__(self, backend: str, browser_oauth: str = ''):
        self.browser_backend = backend
        self.browser_oauth = browser_oauth


async def test_store_bound_by_ref_reaches_browser_ref(
    fake_vendor, async_db_session
):
    """A1: whatever the picker bound (an env id when the vendor reports
    one, a name otherwise) rides browser_oauth into browser_ref — the
    value open_store receives."""
    fake_vendor(_spec())
    cfg: dict = {}
    await inject_vendor_store_config(
        cfg, _StoreRow('fakevendor', 'fv-ref-9'), async_db_session
    )
    assert cfg['browser_ref'] == 'fv-ref-9'


async def test_credential_less_vendor_gets_the_ref_but_no_creds(
    fake_vendor, async_db_session
):
    """R8 / addendum 2 item 1: a credential-less vendor ('single' with
    empty credential_fields; the local API acts as the login) still
    binds its profile ref — and injects nothing else."""
    fake_vendor(_spec(cardinality='single', fields=()))
    cfg: dict = {}
    await inject_vendor_store_config(
        cfg, _StoreRow('fakevendor', 'fv-ref-4'), async_db_session
    )
    assert cfg['browser_ref'] == 'fv-ref-4'
    assert not [k for k in cfg if k.startswith('fakevendor_')]


async def test_twist_unbindable_backends_get_no_ref(
    fake_vendor, async_db_session
):
    """Negative control: a plain backend (no machine login) and an
    unknown backend get no browser_ref — the injection is spec-gated,
    not name-gated."""
    fake_vendor(_spec())
    for backend in ('chrome', 'nosuchbackend'):
        cfg: dict = {}
        await inject_vendor_store_config(
            cfg, _StoreRow(backend, 'some-ref'), async_db_session
        )
        assert 'browser_ref' not in cfg


async def test_reopens_declarant_gets_the_flag_injected(
    fake_vendor, async_db_session
):
    """C4: a vendor whose stop path may close-and-reopen a hand-opened
    kernel (reopens_hand_opened) has the flag injected into its
    browser_config — it is how the engine's stale-stop guard learns
    the vendor declared the exception."""
    fake_vendor(_spec(reopens_hand_opened=True))
    cfg: dict = {}
    await inject_vendor_store_config(
        cfg, _StoreRow('fakevendor', 'fv-ref'), async_db_session
    )
    assert cfg['reopens_hand_opened'] is True


async def test_twist_default_vendor_gets_no_reopens_flag(
    fake_vendor, async_db_session
):
    """Negative control: the spec default is ATTACH — a vendor that
    declared nothing gets no flag, so the engine's stale-stop guard
    holds (no close_store on a kernel this server did not open)."""
    fake_vendor(_spec())
    cfg: dict = {}
    await inject_vendor_store_config(
        cfg, _StoreRow('fakevendor', 'fv-ref'), async_db_session
    )
    assert 'reopens_hand_opened' not in cfg


# ── the locator serves install descriptors from the registry ────────


def test_locator_serves_descriptors_from_the_registry(fake_vendor):
    win_app = WinAppSpec(
        key='fakevendor', display_name='FakeVendor', exe_names=('fv.exe',)
    )
    fake_vendor(
        BrowserVendorSpec(
            key='fakevendor',
            display_name_en='FakeVendor',
            display_name_zh='假浏览器',
            win_app=win_app,
        )
    )
    # <KEY>_SPEC attributes resolve through the registry — the compat
    # seam yuehulian_win (and the tests) still use.
    assert wal.FAKEVENDOR_SPEC is win_app
    assert vendor_spec('fakevendor').win_app is win_app
    # The real vendors' descriptors resolve the same way.
    assert wal.ZINIAO_SPEC.exe_names == ('ziniao.exe',)
    assert wal.YUEHULIAN_SPEC.match_tokens


def test_unknown_spec_attribute_still_raises():
    with pytest.raises(AttributeError):
        wal.NOSUCH_SPEC  # noqa: B018


class TestPrivateDataDeclaration:
    """D-C3-6: an anti-detect vendor that declares neither
    private_paths nor local_ports gets a registration warning (the
    next vendor PRs must declare theirs); plain browsers are exempt."""

    def test_undeclared_antidetect_vendor_warns(self, fake_vendor, caplog):
        with caplog.at_level(logging.WARNING):
            fake_vendor(
                _spec(
                    key='nodeclare',
                    private_paths=(),
                    local_ports=(),
                )
            )
            # Registration is lazy: touching the registry loads the
            # plugin (and runs validate_spec) inside this block.
            assert vendor_spec('nodeclare') is not None
        assert 'private_paths/local_ports' in caplog.text

    def test_declared_vendor_does_not_warn(self, fake_vendor, caplog):
        with caplog.at_level(logging.WARNING):
            fake_vendor(_spec(key='declared', private_paths=('SomeData',)))
        assert 'declares no private_paths' not in caplog.text

    def test_plain_browser_is_exempt(self, caplog):
        with caplog.at_level(logging.WARNING):
            validate_spec(
                BrowserVendorSpec(
                    key='plainx',
                    display_name_en='P',
                    display_name_zh='p',
                    kind=KIND_PLAIN,
                    account_cardinality=CARDINALITY_NONE,
                )
            )
        assert 'private_paths' not in caplog.text
