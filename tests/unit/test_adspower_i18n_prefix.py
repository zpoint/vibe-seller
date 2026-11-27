"""The AdsPower i18n keys resolve at the UI's flat-concatenation form.

The generic VendorSection derives keys as ``<i18n_prefix><Suffix>``
(FLAT concatenation, no dot inserted). The V3 overrides were nested
under ``vendors.adspower.<Suffix>`` but the spec's prefix lacked the
trailing dot, so the UI looked up ``vendors.adspowerAccountMismatch``
— nothing resolved and the generic account wording showed (REVIEW-3
R15). This test loads both locale files the way i18next would and
asserts every suffix the UI asks for resolves.
"""

import json
from pathlib import Path

import pytest

from app.browser.adapters.adspower_backend import ADSPOWER_VENDOR

pytestmark = pytest.mark.unit

_LOCALES = Path(__file__).resolve().parents[2] / 'frontend/src/i18n/locales'

# Every suffix the generic VendorSection / StoreForm / App derive via
# ``${i18n_prefix}<Suffix>`` (grep VendorSection.tsx: vt('…'),
# camel(status), unavailable tokens; StoreForm.tsx / App.tsx:
# SelectStore).
_SUFFIXES = [
    'SelectStore',
    'Loading',
    'LoadFailed',
    'NoAccount',
    'OneAccount',
    'LoginAllRequired',
    'LoginSaving',
    'LoginRequired',
    'AccountMismatch',
    'ApiNotEnabled',
    'StoreNotFound',
    'UnsupportedKernel',
    'StoreUnavailable',
    'ClientNotRunning',
    'LoginAllRequired',
]
_UNAVAILABLE_TOKENS = ['unsupported_kernel']


def _resolve(d: dict, key: str):
    """Walk a dot-separated key through nested dicts (i18next shape)."""
    for part in key.split('.'):
        if not isinstance(d, dict):
            return None
        d = d.get(part)
    return d


@pytest.mark.parametrize('suffix', sorted(set(_SUFFIXES)))
@pytest.mark.parametrize('locale', ['en', 'zh'])
def test_every_derived_suffix_resolves(locale, suffix):
    data = json.loads(
        (_LOCALES / locale / 'translation.json').read_text(encoding='utf-8')
    )
    key = f'{ADSPOWER_VENDOR.i18n_prefix}{suffix}'
    value = _resolve(data, key)
    assert value, (
        f'{locale}: {key} does not resolve — the UI would render '
        'the raw key or fall back to the generic wording'
    )
    assert isinstance(value, str) and value.strip()


@pytest.mark.parametrize('locale', ['en', 'zh'])
def test_unsupported_kernel_token_resolves(locale):
    data = json.loads(
        (_LOCALES / locale / 'translation.json').read_text(encoding='utf-8')
    )
    for token in _UNAVAILABLE_TOKENS:
        key = f'{ADSPOWER_VENDOR.i18n_prefix}unavailable.{token}'
        value = _resolve(data, key)
        assert value, f'{locale}: {key} does not resolve'


def test_the_prefix_has_the_trailing_dot():
    # The negative control's root cause: without the trailing dot the
    # flat concatenation misses the nested keys.
    assert ADSPOWER_VENDOR.i18n_prefix == 'vendors.adspower.'
