"""R2-1: the attach signal is a REGISTRY CONTRACT, not prose.

Every inject vendor whose open ATTACHES to an already-open env (it
does NOT declare ``reopens_hand_opened`` — that declaration means the
vendor's open closes-and-reopens instead) MUST implement
``InjectClient.env_was_open``: without it, C4's teardown rule cannot
tell an env the user opened by hand from one the server spawned, and
a failed start would close the user's window (the default
unknown-⇒-ours teardown).

Vendor PRs that add an attaching vendor fail this test until they
implement the hook — intended. A vendor declared
``reopens_hand_opened`` never attaches on open, so it is exempt.
"""

import pytest

from app.browser.inject.client import InjectClient, OpenedStore, StoreRef
from app.browser.vendors import (
    CARDINALITY_SINGLE,
    BrowserVendorSpec,
    VendorCredentialField,
    registered_vendor_specs,
)
import app.plugins as plugins_mod
from app.plugins import ExtensionContext, Plugin, reset_for_tests

pytestmark = pytest.mark.unit


def attaching_problems() -> list[str]:
    """Every ATTACHING inject vendor that does not implement the hook."""
    problems = []
    for key, spec in registered_vendor_specs().items():
        if spec.client_factory is None:
            continue  # not an inject-engine vendor
        if spec.reopens_hand_opened:
            # Declared: its open closes-and-reopens an already-open env
            # instead of attaching — never attaches, exempt.
            continue
        client = spec.client_factory({})
        if type(client).env_was_open is InjectClient.env_was_open:
            problems.append(key)
    return problems


def test_every_attaching_vendor_implements_env_was_open():
    """The registry contract: no attaching vendor may ship without the
    attach signal. 悦互联 (the one inject vendor on main today) is
    exempt — it declares reopens_hand_opened (its open closes-and-
    reopens; its user docs say so)."""
    assert attaching_problems() == []


class _BaseClient(InjectClient):
    """A vendor client WITHOUT the hook (the base default)."""

    async def ensure_running(self) -> None:
        return None

    async def is_logged_in(self) -> bool:
        return True

    async def list_stores(self) -> list[StoreRef]:
        return [StoreRef('store-a')]

    async def open_store(self, ref: str) -> OpenedStore:
        return OpenedStore('127.0.0.1', 0)

    async def close_store(self, ref: str) -> None:
        return None


class _HookedClient(_BaseClient):
    """The same vendor, WITH the hook implemented."""

    async def env_was_open(self, ref: str) -> bool:
        return False


def _spec(key: str, client_cls) -> BrowserVendorSpec:
    return BrowserVendorSpec(
        key=key,
        display_name_en=key,
        display_name_zh=key,
        account_cardinality=CARDINALITY_SINGLE,
        credential_fields=(
            VendorCredentialField('acct', f'vendors.{key}.acct'),
        ),
        lists_profiles=True,
        i18n_prefix=f'vendors.{key}',
        client_factory=lambda cfg: client_cls(),
    )


def _install(monkeypatch, *specs: BrowserVendorSpec):
    class _Plugin(Plugin):
        @property
        def name(self) -> str:
            return 'fake-contract'

        def install(self, ctx: ExtensionContext) -> None:
            for spec in specs:
                ctx.register_browser_backend(spec.key, _BaseClient, spec)

    class _EntryPoint:
        name = 'fake:contract'
        value = 'fake:contract'

        def load(self):
            return _Plugin

    def fake_entry_points(group=None):
        return [_EntryPoint()] if group == 'vibe_seller.plugins' else []

    monkeypatch.setattr(
        plugins_mod.importlib.metadata, 'entry_points', fake_entry_points
    )
    reset_for_tests()


def test_contract_test_fails_an_attaching_vendor_without_the_hook(
    monkeypatch,
):
    """Negative control: an attaching vendor that does NOT implement
    env_was_open is flagged by the same check the registry test
    runs."""
    _install(monkeypatch, _spec('attachnohook', _BaseClient))
    assert attaching_problems() == ['attachnohook']


def test_contract_test_passes_an_attaching_vendor_with_the_hook(
    monkeypatch,
):
    """The same vendor WITH the hook: clean."""
    _install(monkeypatch, _spec('attachhook', _HookedClient))
    assert attaching_problems() == []


def test_a_reopens_vendor_is_exempt_even_without_the_hook(monkeypatch):
    """A vendor declared reopens_hand_opened never attaches on open —
    it may ship without the hook."""
    spec = BrowserVendorSpec(
        key='reopenvendor',
        display_name_en='reopenvendor',
        display_name_zh='reopenvendor',
        account_cardinality=CARDINALITY_SINGLE,
        credential_fields=(
            VendorCredentialField('acct', 'vendors.reopenvendor.acct'),
        ),
        lists_profiles=True,
        i18n_prefix='vendors.reopenvendor',
        client_factory=lambda cfg: _BaseClient(),
        reopens_hand_opened=True,
    )
    _install(monkeypatch, spec)
    assert attaching_problems() == []


# ── the reference pattern for vendor lanes ──────────────────────────


class _ListingClient(_BaseClient):
    """THE pattern a vendor's env_was_open follows: the vendor's own
    running-list (its listing or active-status API; no new
    vendor-storage reads). Recorded fixtures below pin the three
    outcomes: running → True, closed → False, API error → RAISE (the
    engine treats a raise/timeout as NOT ours — R2-2)."""

    def __init__(self, running: set[str] | None = None, error=None):
        self._running = running
        self._error = error

    async def _running_envs(self) -> set[str]:
        if self._error is not None:
            raise self._error
        return self._running

    async def env_was_open(self, ref: str) -> bool:
        return str(ref) in await self._running_envs()


async def test_reference_env_was_open_open_running_true():
    client = _ListingClient(running={'store-a', 'store-b'})
    assert await client.env_was_open('store-a') is True


async def test_reference_env_was_open_closed_false():
    client = _ListingClient(running=set())
    assert await client.env_was_open('store-a') is False


async def test_reference_env_was_open_api_error_raises():
    """Not None — RAISE: the engine's R2-2 rule treats a raise/timeout
    as NOT ours (never risk a hand-opened window). The negative
    control (the variant that answers None) is the BASE default,
    which only a non-attaching vendor may rely on."""
    client = _ListingClient(error=RuntimeError('status API down'))
    with pytest.raises(RuntimeError, match='status API down'):
        await client.env_was_open('store-a')


async def test_base_default_answers_none():
    """The base (unimplemented) hook answers None — unknown ⇒ ours in
    the engine; the registry contract keeps that to NON-attaching
    vendors only."""
    assert await _BaseClient().env_was_open('store-a') is None
