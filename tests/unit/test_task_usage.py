"""Usage totals on the task row + the ``task_usage`` SSE event.

Drives the real ``AgentSession._handle_event`` with the stream-json the
CLI prints, so the wiring (meter → row → SSE) is what is under test.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ai import model_pricing as mp
from app.ai.claude_backend import AgentSession
from app.database import Base
from app.events.bus import event_bus
from app.models.task import Task
from app.task_usage import USAGE_FIELDS

pytestmark = pytest.mark.unit

TASK_ID = 'task-usage-meter'


@pytest.fixture
async def db(monkeypatch):
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    now = datetime.now(UTC).isoformat()
    async with maker() as s:
        s.add(
            Task(
                id=TASK_ID,
                title='t',
                created_by='u',
                status='running',
                priority=1,
                plan_mode=False,
                created_at=now,
                updated_at=now,
            )
        )
        await s.commit()
    for mod in (
        'app.task_usage',
        'app.ai.claude_backend_stream',
        'app.telemetry_tasks',
    ):
        monkeypatch.setattr(f'{mod}.async_session', maker)
    yield maker
    await engine.dispose()


@pytest.fixture
def sse(monkeypatch):
    emit = AsyncMock()
    monkeypatch.setattr(event_bus, 'emit', emit)
    return emit


@pytest.fixture(autouse=True)
def catalog():
    body = {
        pid: {'models': {'filler': {'cost': {'input': 1, 'output': 2}}}}
        for pid in mp.TRACKED_PROVIDERS
    }
    body['anthropic'] = {
        'models': {
            'claude-test': {
                'limit': {'context': 200_000},
                'cost': {
                    'input': 3,
                    'output': 15,
                    'cache_read': 0.3,
                    'cache_write': 3.75,
                },
            }
        }
    }
    body['minimax-cn'] = {
        'models': {
            'MiniMax-Test': {
                'limit': {'context': 1_000_000},
                'cost': {'input': 0.3, 'output': 1.2},
            }
        }
    }
    mp.apply(mp.normalize_catalog(body))
    yield
    mp.reset()


def _session(provider='anthropic') -> AgentSession:
    s = AgentSession(task_id=TASK_ID, prompt='hi', mode='auto')
    s._usage_provider = provider  # what start() derives from the profile
    return s


def _call(msg_id, inp, out, cached=0, model='claude-test'):
    return [
        {
            'type': 'stream_event',
            'event': {
                'type': 'message_start',
                'message': {
                    'id': msg_id,
                    'model': model,
                    'usage': {
                        'input_tokens': inp,
                        'cache_read_input_tokens': cached,
                        'output_tokens': 1,
                    },
                },
            },
        },
        {
            'type': 'stream_event',
            'event': {'type': 'message_delta', 'usage': {'output_tokens': out}},
        },
    ]


def _cumulative_result(total_cost):
    """What the CLI prints at the end of every turn: process-wide
    totals, priced as Claude."""
    return {
        'type': 'result',
        'result': 'done',
        'num_turns': 2,
        'total_cost_usd': total_cost,
        'usage': {'input_tokens': 999_999, 'output_tokens': 999_999},
        'modelUsage': {'claude-test': {'contextWindow': 1_000_000}},
    }


async def _row(db) -> Task:
    async with db() as s:
        return await s.get(Task, TASK_ID)


async def test_turns_in_one_process_are_counted_once(db, sse):
    """Negative control for the old accounting: it added the result's
    total_cost_usd on every result, but that figure is cumulative for
    the process — the second turn re-billed the first."""
    s = _session()
    for ev in _call('m1', 1_000, 100) + [_cumulative_result(5.0)]:
        await s._handle_event(ev)
    for ev in _call('m2', 2_000, 200) + [_cumulative_result(10.0)]:
        await s._handle_event(ev)
    task = await _row(db)
    assert task.input_tokens == 3_000
    assert task.output_tokens == 300
    assert task.cost_usd == pytest.approx((3_000 * 3 + 300 * 15) / 1e6)
    assert task.num_turns == 4
    assert task.cost_basis == mp.BASIS_CATALOG


async def test_a_second_process_adds_to_the_first(db, sse):
    """Retry / follow-up spawn a new process (a new meter); the row
    keeps the running total across them."""
    for msg_id in ('a', 'b'):
        s = _session()
        for ev in _call(msg_id, 1_000, 100):
            await s._handle_event(ev)
    task = await _row(db)
    assert task.input_tokens == 2_000
    assert task.cost_usd == pytest.approx(2 * (1_000 * 3 + 100 * 15) / 1e6)


async def test_every_call_update_is_pushed_over_sse(db, sse):
    s = _session()
    for ev in _call('m1', 1_000, 100, cached=50_000):
        await s._handle_event(ev)
    usage = [c.args[1] for c in sse.call_args_list if c.args[0] == 'task_usage']
    assert len(usage) == 2  # message_start + message_delta
    last = usage[-1]
    assert set(last) == {'task_id', *USAGE_FIELDS}
    assert last['task_id'] == TASK_ID
    assert last['context_tokens'] == 1_000 + 50_000 + 100
    assert last['context_window'] == 200_000
    assert last['usage_model'] == 'claude-test'


async def test_text_deltas_do_not_write_or_push(db, sse):
    s = _session()
    await s._handle_event({
        'type': 'stream_event',
        'event': {
            'type': 'content_block_delta',
            'index': 0,
            'delta': {'type': 'text_delta', 'text': 'hi'},
        },
    })
    assert not [c for c in sse.call_args_list if c.args[0] == 'task_usage']


async def test_anthropic_window_follows_the_cli(db, sse):
    s = _session('anthropic')
    for ev in _call('m1', 1_000, 100) + [_cumulative_result(1.0)]:
        await s._handle_event(ev)
    assert (await _row(db)).context_window == 1_000_000
    for ev in _call('m2', 1_000, 100):  # later calls keep the CLI's
        await s._handle_event(ev)
    assert (await _row(db)).context_window == 1_000_000


async def test_third_party_window_ignores_the_cli_guess(db, sse):
    """The CLI assumes Claude's window for a model it doesn't know."""
    s = _session('minimax-cn')
    events = _call('m1', 1_000, 100, model='MiniMax-Test')
    events.append({
        **_cumulative_result(1.0),
        'modelUsage': {'MiniMax-Test': {'contextWindow': 200_000}},
    })
    for ev in events:
        await s._handle_event(ev)
    task = await _row(db)
    assert task.context_window == 1_000_000
    assert task.cost_usd == pytest.approx((1_000 * 0.3 + 100 * 1.2) / 1e6)


async def test_a_custom_profile_counts_tokens_without_a_price(db, sse):
    s = _session(None)
    for ev in _call('m1', 1_000, 100, model='MiniMax-Test'):
        await s._handle_event(ev)
    task = await _row(db)
    assert task.cost_basis == mp.BASIS_UNPRICED
    assert task.cost_usd == 0
    assert task.input_tokens == 1_000
    assert task.context_window == 1_000_000  # the model's, from anywhere


# ── Real CLI streams (tests/fixtures/cli_usage_streams.json) ─────
#
# Captured from `claude -p --output-format stream-json
# --include-partial-messages` against each provider (ids replaced,
# text dropped). They pin the provider quirks the meter must absorb:
# Anthropic's one-hour cache writes; MiniMax reporting zeros in
# message_start and the real counts only in message_delta; a token plan.

_STREAMS = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / 'fixtures'
        / 'cli_usage_streams.json'
    ).read_text(encoding='utf-8')
)


@pytest.fixture
def snapshot_catalog():
    """Price with the bundled snapshot, not the toy catalog."""
    mp.apply(mp.normalize_catalog(mp.SNAPSHOT))


async def _replay(name, db):
    stream = _STREAMS[name]
    s = _session(mp.provider_for_preset(stream['preset']))
    for ev in stream['events']:
        await s._handle_event(ev)
    return stream, await _row(db)


async def test_anthropic_cost_matches_the_clis_own(db, sse, snapshot_catalog):
    """On api.anthropic.com the CLI's figure is authoritative — and the
    meter must agree with it to the cent-fraction, including the
    one-hour cache writes models.dev's cache_write rate does not cover.
    (One process, one result, so total_cost_usd is not cumulative
    here.)"""
    stream, task = await _replay('anthropic_1h_cache_multi_call', db)
    result = next(e for e in stream['events'] if e['type'] == 'result')
    assert task.cost_usd == pytest.approx(result['total_cost_usd'], abs=1e-9)
    (mu,) = result['modelUsage'].values()
    assert task.input_tokens == mu['inputTokens']
    assert task.output_tokens == mu['outputTokens']
    assert task.cache_read_tokens == mu['cacheReadInputTokens']
    assert task.cache_write_tokens == mu['cacheCreationInputTokens']
    assert task.context_window == mu['contextWindow']


async def test_minimax_counts_come_from_the_delta(db, sse, snapshot_catalog):
    stream, task = await _replay('minimax_usage_only_in_delta', db)
    result = next(e for e in stream['events'] if e['type'] == 'result')
    (mu,) = result['modelUsage'].values()
    assert task.input_tokens == mu['inputTokens'] > 0
    assert task.output_tokens == mu['outputTokens'] > 0
    assert task.cost_basis == mp.BASIS_CATALOG
    # The CLI's own figure prices MiniMax as if it were Claude.
    assert 0 < task.cost_usd < result['total_cost_usd'] / 10
    assert task.context_window == 1_000_000


async def test_token_plan_is_included(db, sse, snapshot_catalog):
    _stream, task = await _replay('mimo_token_plan', db)
    assert task.cost_basis == mp.BASIS_INCLUDED
    assert task.cost_usd == 0
    assert task.input_tokens > 0 and task.context_tokens > 0
