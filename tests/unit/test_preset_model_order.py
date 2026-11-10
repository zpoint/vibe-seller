"""A provider's sub-1M model options come after its 1M ones.

The window is the one Claude Code actually uses (the rule the UI badge
shows, frontend/src/components/contextWindow.ts):
CLAUDE_CODE_MAX_CONTEXT_TOKENS if the preset sets it, else 1M for an
id ending in ``[1m]``, else the 200K default, capped at the model's
own ``context``. Few users want a 200K session, so those options must
not sit between the 1M ones. The first option is the preset's default
and stays first.
"""

import re

import pytest

from app.ai.provider_presets import PROVIDER_MODELS, PROVIDER_PRESETS

pytestmark = pytest.mark.unit

ONE_M = 1_000_000


def _native(context):
    if not context:
        return None
    m = re.fullmatch(r'(\d+(?:\.\d+)?)([KM])', context)
    return int(float(m.group(1)) * (ONE_M if m.group(2) == 'M' else 1000))


def _window(preset_id, model):
    override = PROVIDER_PRESETS[preset_id]['env'].get(
        'CLAUDE_CODE_MAX_CONTEXT_TOKENS'
    )
    if override:
        window = int(override)
    elif model['id'].endswith('[1m]'):
        window = ONE_M
    else:
        window = 200_000
    native = _native(model.get('context'))
    return min(window, native) if native else window


@pytest.mark.parametrize('preset_id', sorted(PROVIDER_MODELS))
def test_sub_1m_options_come_last(preset_id):
    rest = PROVIDER_MODELS[preset_id][1:]
    full = [_window(preset_id, m) >= ONE_M for m in rest]
    assert full == sorted(full, reverse=True), [
        (m['id'], _window(preset_id, m)) for m in rest
    ]


def test_bare_window_terms_say_context():
    # "Flash V4.1 (1M, vision)" read as a speed tag next to its
    # "(fast, vision)" twin; a window in a label always says "context".
    for preset_id, models in PROVIDER_MODELS.items():
        for m in models:
            assert not re.search(
                r'[(,]\s*\d+(?:\.\d+)?[KM]\s*[,)]', m['label']
            ), (
                preset_id,
                m['label'],
            )


def _group(preset_id):
    return PROVIDER_PRESETS[preset_id].get('group') or preset_id


def test_one_model_reads_the_same_across_a_providers_presets():
    # The same id under two presets of one provider (two regions, two
    # plans) is the same model: one label, one vision flag, one native
    # window. A flag missing in one region because nobody probed it
    # there would tell users the regions differ when they do not.
    seen = {}
    for preset_id, models in PROVIDER_MODELS.items():
        for m in models:
            key = (_group(preset_id), m['id'])
            facts = (m['label'], m.get('vision'), m.get('context'))
            if key in seen:
                assert seen[key][1] == facts, (key, seen[key][0], preset_id)
            else:
                seen[key] = (preset_id, facts)


def test_labels_do_not_repeat_the_vision_badge():
    for preset_id, models in PROVIDER_MODELS.items():
        for m in models:
            assert 'vision' not in m['label'].lower(), (preset_id, m['label'])
