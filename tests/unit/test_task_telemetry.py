"""Pins the telemetry-v3 task invariants. telemetry.send is stubbed
to capture calls (telemetry is disabled under pytest)."""

import ast
import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import telemetry, telemetry_via
from app.models.base import Base
from app.models.task import (
    Task,
    close_pause,
    open_pause,
    paused_total,
)
from app.scheduler.heartbeat import heartbeat_trigger
from app.telemetry_tasks import (
    accumulate_run_usage,
    send_question_answered,
    send_task_cancelled,
    send_task_completed,
    send_task_created,
    send_task_failed,
    skill_names,
)
from app.telemetry_via import unattributed

pytestmark = pytest.mark.unit


# ── Every Task creation path declares a source ────────────


def _task_constructions():
    app_root = Path(__file__).resolve().parents[2] / 'app'
    for path in sorted(app_root.rglob('*.py')):
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == 'Task'
            ):
                yield path, node


def test_every_task_creation_passes_explicit_source():
    missing = [
        f'{path}:{node.lineno}'
        for path, node in _task_constructions()
        if not any(kw.arg == 'source' for kw in node.keywords)
    ]
    assert not missing, (
        'Task( constructed without an explicit source= — every '
        f'creation path must declare one: {missing}'
    )


# ── Outcome events carry task_id (+ source on creation) ────


@pytest_asyncio.fixture
async def task_env(async_engine, monkeypatch):
    """A task row in the test DB + a capture stub for telemetry.send."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr('app.telemetry_tasks.async_session', maker)
    calls: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        telemetry,
        'send',
        MagicMock(
            side_effect=lambda event, props=None: calls.append((
                event,
                dict(props or {}),
            ))
        ),
    )
    async with maker() as db:
        task = Task(
            id='t-telemetry-1',
            title='telemetry fixture',
            created_by='u-1',
            status='pending',
            source='ui',
        )
        db.add(task)
        await db.commit()
    yield maker, task, calls


async def test_outcome_events_carry_task_id(task_env):
    _maker, task, calls = task_env
    send_task_created(task, created_by_role='admin')
    task.status = 'completed'
    await send_task_completed(task)
    await send_task_failed(task, phase='running', category='cli_error')
    by_name = dict(calls)
    for name in ('task_created', 'task_completed', 'task_failed'):
        assert by_name[name]['task_id'] == 't-telemetry-1', name
    assert by_name['task_created']['source'] == 'ui'
    assert by_name['task_failed']['source'] == 'ui'


async def test_cancel_carries_usage_and_context_fill(task_env):
    _maker, task, calls = task_env
    task.context_tokens = 60_000
    task.context_window = 100_000
    task.cache_read_tokens = 250_000
    task.cache_write_tokens = 0
    task.usage_model = 'model-x'
    send_task_cancelled(task, status_at='running')
    props = dict(calls)['task_cancelled']
    assert props['context_fill_bucket'] == '50-75%'
    assert props['cache_read_tokens_bucket'] == '100k-1m'
    assert props['cache_write_tokens_bucket'] == '0'
    assert props['usage_model'] == 'model-x'
    assert 'input_tokens' in props


# ── Run-start event ──────────────────────────────────────────


async def test_run_start_emits_task_started_once(task_env):
    maker, _task, calls = task_env
    async with maker() as db:
        task = await db.get(Task, 't-telemetry-1')
        task.status = 'running'
        await db.commit()
    await asyncio.sleep(0.05)
    started = [p for e, p in calls if e == 'task_started']
    assert len(started) == 1, calls
    assert started[0]['task_id'] == 't-telemetry-1'
    assert started[0]['source'] == 'ui'
    assert started[0]['ai_profile_kind'] == 'default'
    # A wake from WAITING is a resume (task_resumed), not a new run.
    async with maker() as db:
        task = await db.get(Task, 't-telemetry-1')
        task.status = 'waiting'
        task.status = 'running'
        await db.commit()
    await asyncio.sleep(0.05)
    started = [p for e, p in calls if e == 'task_started']
    assert len(started) == 1, calls


# ── Stream-json result usage accumulates ────────────────────

# Stream-json `result` event shape; cache_* fields stay out of
# input_tokens.
RESULT_EVENT = {
    'type': 'result',
    'subtype': 'success',
    'is_error': False,
    'result': 'done',
    'total_cost_usd': 0.25,
    'num_turns': 4,
    'usage': {
        'input_tokens': 1200,
        'output_tokens': 300,
        'cache_read_input_tokens': 5000,
        'cache_creation_input_tokens': 400,
    },
}


async def test_result_adds_turns_but_never_its_cumulative_cost(task_env):
    """``result`` totals are process-cumulative (a second turn in the
    same process re-reports the first), so a result contributes only
    its turn count. Tokens and cost are metered per API call — see
    tests/unit/test_task_usage.py."""
    maker, _task, _calls = task_env
    await accumulate_run_usage('t-telemetry-1', RESULT_EVENT)
    await accumulate_run_usage('t-telemetry-1', RESULT_EVENT)
    # A result without usage fields must not crash or pollute totals.
    await accumulate_run_usage('t-telemetry-1', {'type': 'result'})
    async with maker() as db:
        task = await db.get(Task, 't-telemetry-1')
        assert task.num_turns == 8
        assert task.cost_usd is None
        assert task.input_tokens is None
        assert task.output_tokens is None


# ── Parked-time clock / active_seconds ──────────────────────


def test_status_transitions_drive_the_pause_clock():
    task = Task(status='pending')
    task.status = 'running'
    assert task.paused_since is None
    # Entering a parked state stamps the clock…
    task.status = 'waiting'
    assert task.paused_since is not None
    # …and leaving folds the elapsed time in. The stamp is pre-aged
    # so the fold is deterministic.
    task.paused_since = (datetime.now(UTC) - timedelta(seconds=90)).isoformat()
    task.status = 'running'
    assert task.paused_since is None
    assert 85 <= task.paused_seconds <= 95


def test_question_pause_folds_through_the_end_time():
    t0 = datetime(2026, 11, 5, 10, 0, 0, tzinfo=UTC)
    task = Task(status='running')
    open_pause(task, at_iso=t0.isoformat())
    later = (t0 + timedelta(seconds=42)).isoformat()
    assert paused_total(task, later) == 42
    close_pause(task, at_iso=later)
    assert task.paused_seconds == 42
    assert task.paused_since is None
    # A still-open pause counts up to the end time at read time.
    open_pause(task, at_iso=later)
    end = (t0 + timedelta(seconds=72)).isoformat()
    assert paused_total(task, end) == 72


async def test_active_seconds_excludes_parked_time(task_env):
    _maker, task, calls = task_env
    task.started_at = '2026-11-05T10:00:00+00:00'
    task.completed_at = '2026-11-05T11:00:00+00:00'
    task.paused_seconds = 600
    task.status = 'completed'
    await send_task_completed(task)
    props = dict(calls)['task_completed']
    assert props['duration_seconds'] == 3600
    assert props['paused_seconds'] == 600
    assert props['active_seconds'] == 3000
    await send_task_failed(task, phase='running', category='cli_error')
    props = dict(calls)['task_failed']
    assert props['active_seconds'] == 3000


# ── Heartbeat scheduling ────────────────────────────────────


def test_heartbeat_fires_at_most_once_per_day():
    trigger = heartbeat_trigger()
    cur = datetime(2026, 11, 3, tzinfo=UTC)
    fires = []
    for _ in range(4):
        nxt = trigger.get_next_fire_time(cur, cur)
        assert nxt is not None
        fires.append(nxt)
        cur = nxt
    # Daily — allow DST-sized slack, never a sub-daily repeat.
    gaps_h = [(b - a).total_seconds() / 3600 for a, b in zip(fires, fires[1:])]
    assert all(22 <= g <= 26 for g in gaps_h), gaps_h


async def test_question_answered_carries_the_answering_via(task_env):
    """§10: the answered event's `via` is the channel that answered —
    handed over by request_id, because the agent's own task emits it
    long after the answering request returned."""
    _maker, _task, calls = task_env
    telemetry_via.set_via(telemetry_via.MCP)
    telemetry_via.note_answer_via('req-9')
    telemetry_via.set_via(None)  # the agent's own context
    await send_question_answered('t-telemetry-1', datetime.now(UTC), 'req-9')
    props = dict(calls)['agent_question_answered']
    assert props['via'] == 'mcp'
    # A question answered with no handover reports 'other', never a
    # stale channel.
    await send_question_answered('t-telemetry-1', datetime.now(UTC), 'req-x')
    props = dict(calls)['agent_question_answered']
    assert props['via'] == 'other'


async def test_scheduler_jobs_never_run_in_a_request_channel(task_env):
    """apscheduler snapshots the REGISTERING request's
    context, so a job added inside a request inherits its `via` —
    fired tasks then report 'ui' instead of 'schedule'. The
    registration-time `unattributed` wrapper clears the channel; the
    task then falls back to its own source."""
    _maker, _task, _calls = task_env

    class _Sched:
        schedule_id = 'sched-1'
        parent_task_id = None
        source = 'schedule'

    telemetry_via.set_via(telemetry_via.UI)
    seen = {}

    @unattributed
    async def job():
        seen['via'] = telemetry_via.via_for_task(_Sched())

    # The leak shape: the job runs in a task spawned from a context
    # where a request's channel is set.
    await asyncio.create_task(job())
    assert seen['via'] == 'schedule'

    # An unwrapped in-request call keeps the caller's channel (the
    # manual-trigger path is deliberately NOT wrapped).
    seen.clear()

    async def direct():
        seen['via'] = telemetry_via.via_for_task(_Sched())

    await asyncio.create_task(direct())
    assert seen['via'] == 'ui'
    telemetry_via.set_via(None)


# ── skills_used reports every skill name, built-in or custom ──


@pytest.mark.parametrize(
    ('tool', 'tool_input', 'expected'),
    [
        ('Skill', {'skill': 'my-custom-skill'}, ['my-custom-skill']),
        ('Skill', {'skill': '选品助手'}, ['选品助手']),
        (
            'Read',
            {'file_path': '/w/.claude/skills/amazon-ads/SKILL.md'},
            ['amazon-ads'],
        ),
        (
            'Bash',
            {'command': 'cat /w/.claude/skills/widget-tool/SKILL.md'},
            ['widget-tool'],
        ),
        ('Read', {'file_path': '/w/.claude/skills/x/notes.md'}, []),
        ('Write', {'file_path': '/w/.claude/skills/x/SKILL.md'}, []),
        ('Skill', {'skill': '__internal'}, []),
        ('Skill', {'skill': 'a b'}, []),
        ('Skill', {'skill': 'x' * 65}, []),
    ],
)
def test_skill_names(tool, tool_input, expected):
    assert skill_names(tool, tool_input) == expected
