"""Per-call usage metering from the CLI's stream-json."""

import pytest

from app.ai.model_pricing import (
    BASIS_CATALOG,
    BASIS_INCLUDED,
    BASIS_UNPRICED,
    UNPRICED,
    ModelPrice,
)
from app.ai.usage_meter import UsageMeter

pytestmark = pytest.mark.unit

# $3 / $15 / $0.30 / $3.75 per 1M — round illustrative rates.
RATES = {'input': 3.0, 'output': 15.0, 'cache_read': 0.3, 'cache_write': 3.75}
PRICED = ModelPrice(rates=RATES, context=200_000)


def _meter(price=PRICED) -> UsageMeter:
    return UsageMeter(lambda model: price if model else UNPRICED)


def _start(msg_id, usage, model='claude-test', parent=None):
    return {
        'type': 'stream_event',
        'parent_tool_use_id': parent,
        'event': {
            'type': 'message_start',
            'message': {'id': msg_id, 'model': model, 'usage': usage},
        },
    }


def _delta(usage, parent=None):
    return {
        'type': 'stream_event',
        'parent_tool_use_id': parent,
        'event': {'type': 'message_delta', 'usage': usage},
    }


def _assistant(msg_id, usage, parent=None, model='claude-test'):
    return {
        'type': 'assistant',
        'parent_tool_use_id': parent,
        'message': {
            'id': msg_id,
            'model': model,
            'usage': usage,
            'content': [{'type': 'text', 'text': 'x'}],
        },
    }


START_USAGE = {
    'input_tokens': 1_000,
    'cache_read_input_tokens': 20_000,
    'cache_creation_input_tokens': 4_000,
    'output_tokens': 1,
}


def _totals(deltas):
    deltas = [d for d in deltas if d is not None]
    return {
        'input': sum(d.input_tokens for d in deltas),
        'output': sum(d.output_tokens for d in deltas),
        'cache_read': sum(d.cache_read_tokens for d in deltas),
        'cache_write': sum(d.cache_write_tokens for d in deltas),
        'cost': sum(d.cost_usd for d in deltas),
    }


def test_start_plus_delta_counts_one_call_exactly():
    meter = _meter()
    deltas = [
        meter.feed(_start('m1', START_USAGE)),
        meter.feed(_delta({'output_tokens': 500})),
    ]
    totals = _totals(deltas)
    assert totals == {
        'input': 1_000,
        'output': 500,
        'cache_read': 20_000,
        'cache_write': 4_000,
        'cost': pytest.approx(
            (1_000 * 3 + 500 * 15 + 20_000 * 0.3 + 4_000 * 3.75) / 1e6
        ),
    }
    last = deltas[-1]
    assert last.context_tokens == 1_000 + 20_000 + 4_000 + 500
    assert last.context_window == 200_000
    assert last.basis == BASIS_CATALOG


def test_assistant_blocks_repeating_usage_are_not_double_counted():
    """The CLI emits one assistant event per content block, each with
    the message's usage. Plus the stream parts for the same message.
    One API call must be counted once."""
    meter = _meter()
    deltas = [
        meter.feed(_start('m1', START_USAGE)),
        meter.feed(_assistant('m1', START_USAGE)),  # thinking block
        meter.feed(_assistant('m1', START_USAGE)),  # text block
        meter.feed(_assistant('m1', START_USAGE)),  # tool_use block
        meter.feed(_delta({'output_tokens': 800})),
    ]
    assert deltas[1:4] == [None, None, None]
    assert _totals(deltas)['output'] == 800
    assert _totals(deltas)['input'] == 1_000


def test_a_late_provisional_count_never_shrinks_the_final_one():
    meter = _meter()
    meter.feed(_start('m1', START_USAGE))
    meter.feed(_delta({'output_tokens': 800}))
    # A block event that closed before the delta, delivered after it.
    assert (
        meter.feed(_assistant('m1', {**START_USAGE, 'output_tokens': 40}))
        is None
    )


def test_consecutive_calls_add_up_and_context_tracks_the_latest():
    meter = _meter()
    meter.feed(_start('m1', START_USAGE))
    meter.feed(_delta({'output_tokens': 500}))
    second = {**START_USAGE, 'cache_read_input_tokens': 25_000}
    d1 = meter.feed(_start('m2', second))
    d2 = meter.feed(_delta({'output_tokens': 300}))
    assert d1.cache_read_tokens == 25_000  # a new call, fully added
    assert d2.context_tokens == 1_000 + 25_000 + 4_000 + 300


def test_subagent_calls_cost_money_but_do_not_move_the_context_bar():
    meter = _meter()
    meter.feed(_start('m1', START_USAGE))
    sub = meter.feed(
        _assistant(
            'sub-1',
            {'input_tokens': 9_000, 'output_tokens': 100},
            parent='toolu_1',
        )
    )
    assert sub.input_tokens == 9_000
    assert sub.cost_usd > 0
    assert sub.context_tokens is None
    assert sub.context_window is None


def test_subagent_message_delta_does_not_land_on_the_main_call():
    meter = _meter()
    meter.feed(_start('main', START_USAGE))
    meter.feed(_start('sub', {'input_tokens': 10}, parent='toolu_1'))
    d = meter.feed(_delta({'output_tokens': 70}, parent='toolu_1'))
    assert d.context_tokens is None
    main = meter.feed(_delta({'output_tokens': 5}))
    assert main.context_tokens == 25_000 + 5


def test_events_without_usage_or_ids_are_ignored():
    meter = _meter()
    assert meter.feed({'type': 'system', 'subtype': 'init'}) is None
    assert meter.feed(_delta({'output_tokens': 5})) is None  # no start
    assert meter.feed(_assistant(None, START_USAGE)) is None
    assert meter.feed(_start('m1', None)) is None
    assert meter.feed({'type': 'stream_event', 'event': 'garbage'}) is None
    assert meter.feed({'type': 'result', 'total_cost_usd': 9.0}) is None


def test_plan_runs_count_tokens_at_zero_cost():
    meter = _meter(
        ModelPrice(rates=dict.fromkeys(RATES, 0.0), context=1_000_000)
    )
    d = meter.feed(_start('m1', START_USAGE))
    assert d.basis == BASIS_INCLUDED
    assert d.cost_usd == 0
    assert d.input_tokens == 1_000


def test_unknown_model_counts_tokens_and_reports_unpriced():
    meter = _meter(ModelPrice(rates=None, context=128_000))
    d = meter.feed(_start('m1', START_USAGE))
    assert d.basis == BASIS_UNPRICED
    assert d.cost_usd == 0
    assert d.context_window == 128_000


def test_cli_reported_window_overrides_the_catalog_for_later_calls():
    meter = _meter()
    meter.feed(_start('m1', START_USAGE))
    assert meter.main_model == 'claude-test'
    meter.cli_windows['claude-test'] = 1_000_000
    d = meter.feed(_start('m2', START_USAGE))
    assert d.context_window == 1_000_000
