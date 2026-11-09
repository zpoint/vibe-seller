"""Model pricing catalog: validation, lookup, cost math, preset mapping."""

import json

import pytest

from app.ai import model_pricing as mp
from app.ai.provider_presets import PROVIDER_MODELS, PROVIDER_PRESETS

pytestmark = pytest.mark.unit


def _body(**providers) -> dict:
    """A models.dev-shaped body with every tracked provider present."""
    body = {
        pid: {'models': {'filler': {'cost': {'input': 1, 'output': 2}}}}
        for pid in mp.TRACKED_PROVIDERS
    }
    body.update(providers)
    return body


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, 'cache_path', lambda: tmp_path / 'mp.json')
    mp.reset()
    yield
    mp.reset()


# ── Validation ───────────────────────────────────────────────


def test_untracked_providers_and_fields_are_dropped():
    body = _body(
        anthropic={
            'name': 'Anthropic',
            'models': {
                'claude-x': {
                    'name': 'Claude X',
                    'limit': {'context': 200_000, 'output': 64_000},
                    'cost': {'input': 3, 'output': 15, 'cache_read': 0.3},
                }
            },
        },
        **{'some-other-host': {'models': {'m': {'cost': {'input': 1}}}}},
    )
    sliced = mp.slice_catalog(body)
    assert 'some-other-host' not in sliced
    assert sliced['anthropic']['models']['claude-x'] == {
        'limit': {'context': 200_000},
        'cost': {'input': 3, 'output': 15, 'cache_read': 0.3},
    }
    entry = mp.normalize_catalog(sliced)['anthropic']['claude-x']
    # No cache_write listed → billed as plain input.
    assert entry['cost'] == {
        'input': 3.0,
        'output': 15.0,
        'cache_read': 0.3,
        'cache_write': 3.0,
    }
    assert entry['context'] == 200_000


@pytest.mark.parametrize(
    'body',
    [
        [],
        {},
        {'anthropic': {'models': {}}},
        {'error': 'rate limited'},
    ],
)
def test_a_body_that_is_not_models_dev_is_rejected(body):
    with pytest.raises(mp.PricingSourceError):
        mp.slice_catalog(body)


@pytest.mark.parametrize(
    'cost',
    [
        {'input': -1, 'output': 2},
        {'input': 'free', 'output': 2},
        {'input': True, 'output': 2},
        {'input': float('inf'), 'output': 2},
        {'output': 2},
    ],
)
def test_bad_rates_leave_the_model_unpriced(cost):
    body = _body(
        anthropic={'models': {'m': {'limit': {'context': 1000}, 'cost': cost}}}
    )
    entry = mp.normalize_catalog(body)['anthropic']['m']
    assert entry['cost'] is None
    assert entry['context'] == 1000


def test_corrupt_cache_falls_back_to_the_bundled_snapshot(tmp_path):
    (tmp_path / 'mp.json').write_text('{"not": "a catalog"')
    assert mp.current() == mp.normalize_catalog(mp.SNAPSHOT)


def test_a_valid_cache_wins_over_the_snapshot(tmp_path):
    body = _body(
        anthropic={
            'models': {'only-in-cache': {'cost': {'input': 1, 'output': 1}}}
        }
    )
    (tmp_path / 'mp.json').write_text(json.dumps(mp.slice_catalog(body)))
    assert 'only-in-cache' in mp.current()['anthropic']


# ── Lookup ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    'reported',
    [
        'glm-5.3',
        'glm-5.3[1m]',  # the Claude-Code-only context suffix
        'GLM-5.3',
        'zhipuai/glm-5.3',
    ],
)
def test_reported_model_ids_are_normalised(reported):
    mp.apply(
        mp.normalize_catalog(
            _body(
                zhipuai={
                    'models': {
                        'glm-5.3': {
                            'limit': {'context': 1_000_000},
                            'cost': {'input': 1, 'output': 4},
                        }
                    }
                }
            )
        )
    )
    price = mp.price_for('zhipuai', reported)
    assert price.basis == mp.BASIS_CATALOG
    assert price.context == 1_000_000


def test_dated_anthropic_ids_fall_back_to_the_alias():
    mp.apply(
        mp.normalize_catalog(
            _body(
                anthropic={
                    'models': {
                        'claude-sonnet-9': {'cost': {'input': 3, 'output': 15}}
                    }
                }
            )
        )
    )
    assert (
        mp.price_for('anthropic', 'claude-sonnet-9-20990101').rates['input']
        == 3
    )


def test_rates_never_come_from_another_provider():
    """The same model costs differently per host; a plan is free where
    pay-as-you-go is not. A custom profile gets the window, no price."""
    mp.apply(
        mp.normalize_catalog(
            _body(
                minimax={
                    'models': {
                        'M3': {
                            'limit': {'context': 1_000_000},
                            'cost': {'input': 0.3, 'output': 1.2},
                        }
                    }
                }
            )
        )
    )
    custom = mp.price_for(None, 'M3')
    assert custom.basis == mp.BASIS_UNPRICED
    assert custom.context == 1_000_000
    assert mp.price_for('minimax', 'M3').basis == mp.BASIS_CATALOG
    assert mp.price_for('minimax', 'unknown-model') == mp.UNPRICED
    assert mp.price_for('minimax', None) == mp.UNPRICED


def test_plan_providers_report_included_not_free_money():
    mp.apply(
        mp.normalize_catalog(
            _body(**{
                'kimi-code-plan-global': {
                    'models': {
                        'k3.1-pro': {
                            'cost': {'input': 0, 'output': 0, 'cache_read': 0}
                        }
                    }
                }
            })
        )
    )
    price = mp.price_for('kimi-code-plan-global', 'k3.1-pro')
    assert price.basis == mp.BASIS_INCLUDED
    assert (
        mp.cost_usd(
            price,
            input_tokens=10**6,
            output_tokens=10**6,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        == 0
    )


# ── Cost math ────────────────────────────────────────────────


def test_each_bucket_is_priced_at_its_own_rate():
    price = mp.ModelPrice(
        rates={
            'input': 3.0,
            'output': 15.0,
            'cache_read': 0.3,
            'cache_write': 3.75,
        }
    )
    cost = mp.cost_usd(
        price,
        input_tokens=1_000_000,
        output_tokens=100_000,
        cache_read_tokens=2_000_000,
        cache_write_tokens=400_000,
    )
    assert cost == pytest.approx(3.0 + 1.5 + 0.6 + 1.5)


def test_context_tier_prices_the_whole_call_once_prompt_exceeds_it():
    entry = mp.normalize_catalog(
        _body(
            zhipuai={
                'models': {
                    'glm': {
                        'cost': {
                            'input': 1,
                            'output': 4,
                            'cache_read': 0.2,
                            'tiers': [
                                {
                                    'input': 2,
                                    'output': 8,
                                    'cache_read': 0.4,
                                    'tier': {'type': 'context', 'size': 32_000},
                                }
                            ],
                        }
                    }
                }
            }
        )
    )
    mp.apply(entry)
    price = mp.price_for('zhipuai', 'glm')

    def cost(prompt_cached):
        return mp.cost_usd(
            price,
            input_tokens=1_000,
            output_tokens=1_000,
            cache_read_tokens=prompt_cached,
            cache_write_tokens=0,
        )

    small = cost(30_000)  # prompt 31K ≤ 32K → base rates
    assert small == pytest.approx((1_000 * 1 + 1_000 * 4 + 30_000 * 0.2) / 1e6)
    big = cost(40_000)  # prompt 41K > 32K → tier rates on every bucket
    assert big == pytest.approx((1_000 * 2 + 1_000 * 8 + 40_000 * 0.4) / 1e6)


def test_unpriced_cost_is_none_not_zero():
    assert (
        mp.cost_usd(
            mp.UNPRICED,
            input_tokens=5,
            output_tokens=5,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        is None
    )


# ── Every preset is priceable out of the box ─────────────────


@pytest.mark.parametrize('preset_id', sorted(PROVIDER_PRESETS))
def test_every_preset_maps_to_a_provider_in_the_snapshot(preset_id):
    """A new preset without a PRESET_PROVIDERS entry (or one whose
    provider vanished from the snapshot) would silently show no cost —
    this fails instead. Fix: add the mapping, regenerate the snapshot
    (scripts/gen_model_pricing_snapshot.py)."""
    provider = mp.provider_for_preset(preset_id)
    assert provider, f'{preset_id} has no PRESET_PROVIDERS entry'
    assert provider in mp.normalize_catalog(mp.SNAPSHOT), provider


@pytest.mark.parametrize('preset_id', sorted(PROVIDER_PRESETS))
def test_every_preset_default_model_has_a_context_window(preset_id):
    model = PROVIDER_PRESETS[preset_id]['env'].get('ANTHROPIC_MODEL')
    if not model or model == 'auto':  # provider-side routing
        pytest.skip('no fixed model id')
    price = mp.price_for(mp.provider_for_preset(preset_id), model)
    assert price.context, f'{preset_id}: {model} not in the snapshot'


def test_default_profile_prices_as_anthropic():
    assert mp.provider_for_preset('default') == 'anthropic'
    assert mp.provider_for_preset('custom') is None
    assert set(PROVIDER_MODELS) <= set(mp.PRESET_PROVIDERS)
