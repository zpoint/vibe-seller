"""Every preset display string ships its Chinese text in the catalog.

The catalog (app/ai/provider_presets.py) syncs from main, and the UI
renders ``i18n[lang]`` from it, falling back to English. A preset
or model added without a ``zh`` entry would show English in a Chinese
UI; one carrying a stale translation would show the wrong text. This
pins both, and that the sync validator serves the translations.
"""

import pytest

from app.ai import provider_presets_sync as sync
from app.ai.provider_presets import PROVIDER_MODELS, PROVIDER_PRESETS

pytestmark = pytest.mark.unit


def test_every_preset_has_zh_for_each_display_field():
    for pid, p in PROVIDER_PRESETS.items():
        zh = p.get('i18n', {}).get('zh', {})
        for field in ('name', 'group', 'variant'):
            if p.get(field):
                assert zh.get(field), (pid, field)


def test_every_model_has_a_zh_label():
    for pid, models in PROVIDER_MODELS.items():
        for m in models:
            assert m.get('i18n', {}).get('zh', {}).get('label'), (pid, m['id'])


def test_the_sync_validator_serves_translations():
    presets, models = sync.validate(PROVIDER_PRESETS, PROVIDER_MODELS)
    assert presets['qwen']['i18n']['zh']['group'] == '阿里云'
    assert models['deepseek'][0]['i18n']['zh']['label']


def test_the_validator_drops_unknown_languages_and_rejects_bad_text():
    base = dict(PROVIDER_PRESETS['deepseek'])
    base['i18n'] = {'zh': {'name': 'DeepSeek', 'evil': 'x'}, 'xx': {}}
    served = sync._validate_preset('deepseek', base)
    assert served['i18n'] == {'zh': {'name': 'DeepSeek'}}
    base['i18n'] = {'zh': {'name': 123}}
    assert sync._validate_preset('deepseek', base) is None
