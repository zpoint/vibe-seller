"""Every English descriptor a provider preset shows has a Chinese term.

The UI localises preset display strings (``frontend/src/components/
presetI18n.ts``): ``Head`` or ``Head (term, term)``, each piece looked
up in ``profiles.presetTerms``. A descriptor added here without a zh
term would render in English in a Chinese UI — this test names it.
"""

import json
import pathlib
import re

import pytest

from app.ai.provider_presets import PROVIDER_MODELS, PROVIDER_PRESETS

pytestmark = pytest.mark.unit

ROOT = pathlib.Path(__file__).resolve().parents[2]
ZH = ROOT / 'frontend/src/i18n/locales/zh/translation.json'
PAREN = re.compile(r'^(.*?)\s*\((.+)\)$')

# Heads that are brand, product or model names: shown as-is in every
# language. A head is a name when it carries a digit (model ids) or is
# listed here.
NAMES = {
    'Kimi',
    'Kimi Code',
    'MiniMax',
    'GLM',
    'DeepSeek',
    'Qwen',
    'MiMo',
    'Token Plan',
    'Coding Plan',
    'M3',
    'V4 Pro',
}


def _pieces():
    for pid, p in PROVIDER_PRESETS.items():
        for field in ('name', 'group', 'variant'):
            if p.get(field):
                yield f'{pid}.{field}', p[field]
    for pid, models in PROVIDER_MODELS.items():
        for m in models:
            yield f'{pid}:{m["id"]}', m['label']


def _terms(text):
    m = PAREN.match(text)
    if not m:
        return [text]
    return [m.group(1)] + re.split(r'\s*,\s*', m.group(2))


def test_every_preset_descriptor_has_a_zh_term():
    terms = json.loads(ZH.read_text())['profiles']['presetTerms']
    missing = []
    for where, text in _pieces():
        for term in _terms(text):
            if term in terms or term in NAMES or re.search(r'\d', term):
                continue
            missing.append(f'{term!r} ({where})')
    assert not missing, 'no zh presetTerms entry for: ' + ', '.join(missing)
