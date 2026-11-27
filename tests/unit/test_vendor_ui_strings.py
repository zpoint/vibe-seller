"""Every string the generic vendor UI derives must resolve, per locale.

The generic VendorSection resolves each derived string through a
fallback chain: the vendor's own ``<i18n_prefix><Suffix>`` override,
else the shared ``browserVendor.<Suffix>`` key (interpolating
``{{vendor}}``). A key that resolves NOWHERE renders as the raw key in
the UI — this test fails the PR that would ship one, for every
registered generic-picker vendor × every derived key × every field
label × both locales (zh + en).

Keep ``DERIVED_SUFFIXES`` in sync with the ``vt('<Suffix>')`` calls in
``frontend/src/components/VendorSection.tsx`` (the fixed UI strings)
plus the shared per-code status keys.
"""

import json
from pathlib import Path

import pytest

from app.browser.vendors import (
    PICKER_GENERIC,
    registered_vendor_specs,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
LOCALES = ROOT / 'frontend' / 'src' / 'i18n' / 'locales'

# The strings VendorSection derives (vt('<Suffix>')): the fixed UI
# strings plus the shared status keys for the error codes the route can
# surface.
DERIVED_SUFFIXES = (
    # fixed UI strings
    'NoAccount',
    'OneAccount',
    'LoginAllRequired',
    'LoginSaving',
    'SelectStore',
    'Loading',
    'NoStores',
    'LoadFailed',
    'LoginRequired',
    'ClientNotRunning',
    # shared per-code status keys (error codes the route can surface)
    'AccountMismatch',
    'ApiNotEnabled',
    'PlanRequired',
    'StoreNotFound',
    'StoreUnavailable',
    'UnsupportedKernel',
    'CdpUnreachable',
    'UnsupportedPlatform',
)


def _load(locale: str) -> dict:
    with open(LOCALES / locale / 'translation.json', encoding='utf-8') as fh:
        return json.load(fh)


def _lookup(strings: dict, dotted: str):
    """The value at a dotted key path, or None when any hop is absent."""
    node = strings
    for part in dotted.split('.'):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def _missing_keys(strings: dict, spec) -> list:
    """Derived keys + field labels of one vendor that resolve NOWHERE."""
    missing = []
    for suffix in DERIVED_SUFFIXES:
        own = _lookup(strings, f'{spec.i18n_prefix}{suffix}')
        shared = _lookup(strings, f'browserVendor.{suffix}')
        if not (own or shared):
            missing.append(f'{spec.i18n_prefix}{suffix} (no shared fallback)')
    for field in spec.credential_fields:
        if not _lookup(strings, field.label_i18n):
            missing.append(field.label_i18n)
    return missing


def _generic_specs():
    return [
        spec
        for spec in registered_vendor_specs().values()
        if spec.picker_kind == PICKER_GENERIC
    ]


@pytest.mark.parametrize('locale', ['en', 'zh'])
def test_every_generic_vendor_string_resolves(locale):
    specs = _generic_specs()
    assert specs, 'the registry must have generic-picker vendors'
    strings = _load(locale)
    broken = {
        f'{spec.key} ({locale})': _missing_keys(strings, spec) for spec in specs
    }
    broken = {k: v for k, v in broken.items() if v}
    assert not broken, (
        'Vendor UI strings that render as raw keys:\n'
        + json.dumps(broken, indent=1, ensure_ascii=False)
    )


def test_the_checker_catches_a_missing_key():
    """Negative control: delete one vendor override AND its shared
    fallback and the checker reports it (a vendor with only the shared
    fallback still passes — that is the A3 design)."""
    strings = _load('en')
    spec = _generic_specs()[0]

    class _Fake:
        i18n_prefix = spec.i18n_prefix
        credential_fields = ()

    # A suffix with a shared fallback resolves even without an override.
    assert _missing_keys(strings, _Fake) == []
    # Delete the shared fallback for one suffix and only the vendor's
    # own override is left; delete that too and it is reported. (The
    # keys are flat: `<prefix><Suffix>` concatenates inside the last
    # namespace hop, e.g. settings.yuehulianNoAccount.)
    stripped = json.loads(json.dumps(strings))
    own_parts = f'{spec.i18n_prefix}NoAccount'.split('.')
    node = stripped
    for part in own_parts[:-1]:
        node = node[part]
    del node[own_parts[-1]]
    del stripped['browserVendor']['NoAccount']
    missing = _missing_keys(stripped, _Fake)
    assert any(m.startswith(f'{spec.i18n_prefix}NoAccount') for m in missing), (
        missing
    )
