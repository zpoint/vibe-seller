"""Task-level telemetry helpers — kept out of task_runner_exec.py
so that file stays under the 800-line limit. Anonymous + privacy-safe;
see app.telemetry."""

import asyncio
from contextvars import ContextVar
from datetime import UTC, datetime
import json
import logging
import re

from sqlalchemy import event, func, select
from sqlalchemy.orm import Session as _SyncSession

# Side effect: registers the Schedule ORM telemetry listeners.
from app import (
    telemetry,
    telemetry_actions,  # noqa: F401
)
from app.ai.profiles import profile_kind_for_id, profile_telemetry
from app.database import async_session
from app.models.task import Task, close_pause, open_pause, paused_total
from app.models.task_message import TaskMessage
from app.telemetry_events import TaskSource, TelemetryEvent
from app.telemetry_via import (
    current_via,
    take_answer_via,
    via_for_task,
)

logger = logging.getLogger(__name__)

_SKILL_FILE_RE = re.compile(r'(?:^|[/\s])skills/([^/\s]+)/SKILL\.md')
_SKILL_NAME_RE = re.compile(r'[^\s/\\]{1,64}')


def skill_names(tool: str, tool_input: dict) -> list[str]:
    if tool == 'Skill':
        found = [str(tool_input.get('skill') or '')]
    elif tool == 'Read':
        found = _SKILL_FILE_RE.findall(str(tool_input.get('file_path') or ''))
    elif tool == 'Bash':
        found = _SKILL_FILE_RE.findall(str(tool_input.get('command') or ''))
    else:
        return []
    return [
        n
        for n in (f.strip() for f in found)
        if _SKILL_NAME_RE.fullmatch(n) and not n.startswith('__')
    ]


async def _skills_used_in_task(db, task_id: str) -> list[str]:
    try:
        result = await db.execute(
            select(TaskMessage.content).where(
                TaskMessage.task_id == task_id,
                TaskMessage.role == 'tool_use',
            )
        )
        contents = result.scalars().all()
    except Exception:
        return []
    skills: set[str] = set()
    for content in contents:
        if not isinstance(content, str):
            continue
        try:
            data = json.loads(content)
            tool = data.get('tool') or data.get('tool_name') or ''
            tool_input = data.get('input') or data.get('tool_input') or {}
            if not isinstance(tool_input, dict):
                continue
        except (ValueError, TypeError, AttributeError):
            continue
        skills.update(skill_names(tool, tool_input))
    return sorted(skills)[:20]


async def _tool_use_count(db, task_id: str) -> int:
    try:
        result = await db.execute(
            select(func.count(TaskMessage.id)).where(
                TaskMessage.task_id == task_id,
                TaskMessage.role == 'tool_use',
            )
        )
        return result.scalar() or 0
    except Exception:
        return 0


async def _followup_count(db, task_id: str) -> int:
    """User messages after the first — how much the user had to push."""
    try:
        result = await db.execute(
            select(func.count(TaskMessage.id)).where(
                TaskMessage.task_id == task_id,
                TaskMessage.role == 'user',
            )
        )
        return max(0, (result.scalar() or 0) - 1)
    except Exception:
        return 0


def send_task_created(
    task: Task,
    *,
    user_id: str | None = None,
    created_by_role: str | None = None,
) -> None:
    """Emit ``task_created`` from a Task creation path."""
    props = {
        'task_id': task.id,
        'source': task.source or TaskSource.OTHER,
        'via': via_for_task(task),
        'user_hash': telemetry.user_hash(user_id),
        'is_store_task': task.store_id is not None,
        'plan_mode': bool(task.plan_mode),
        'schedule_id': task.schedule_id,
        'has_parent': task.parent_task_id is not None,
        'description_length_bucket': telemetry.length_bucket(
            len(task.description or '')
        ),
        **profile_telemetry(task.ai_profile_id),
    }
    if created_by_role is not None:
        props['created_by_role'] = created_by_role
    telemetry.send(TelemetryEvent.TASK_CREATED, props)


def send_task_started(task: Task) -> None:
    """Emit ``task_started`` — the task's run actually began."""
    telemetry.send(
        TelemetryEvent.TASK_STARTED,
        {
            'task_id': task.id,
            'source': task.source or TaskSource.OTHER,
            'is_store_task': task.store_id is not None,
            'plan_mode': bool(task.plan_mode),
            'is_scheduled': task.schedule_id is not None,
            'has_parent': task.parent_task_id is not None,
            **profile_telemetry(task.ai_profile_id),
        },
    )


def _usage_props(task: Task) -> dict:
    """Accumulated agent-run usage."""
    return {
        'cost_usd': task.cost_usd,
        'input_tokens': task.input_tokens,
        'output_tokens': task.output_tokens,
        'num_turns': task.num_turns,
        'cost_basis': task.cost_basis,
        'context_fill_bucket': telemetry.fill_bucket(
            task.context_tokens, task.context_window
        ),
        'cache_read_tokens_bucket': telemetry.token_bucket(
            task.cache_read_tokens
        ),
        'cache_write_tokens_bucket': telemetry.token_bucket(
            task.cache_write_tokens
        ),
        'usage_model': (task.usage_model or '')[:64] or None,
    }


def _timing_props(task: Task) -> dict:
    """duration / paused / active_seconds for the outcome events."""
    end_iso = task.completed_at or datetime.now(UTC).isoformat()
    duration = telemetry.duration_seconds_from_iso(task.started_at, end_iso)
    paused = paused_total(task, end_iso)
    active = None if duration is None else max(0, duration - paused)
    return {
        'duration_seconds': duration,
        'paused_seconds': paused,
        'active_seconds': active,
    }


def _outcome_props(task: Task) -> dict:
    return {
        'task_id': task.id,
        'source': task.source or TaskSource.OTHER,
        'is_store_task': task.store_id is not None,
        'was_planned': bool(task.plan_mode),
        'is_scheduled': task.schedule_id is not None,
        **profile_telemetry(task.ai_profile_id),
        'followup_count': 0,
        'retry_count': task.retry_count or 0,
        **_usage_props(task),
        **_timing_props(task),
    }


async def send_task_completed(task: Task) -> None:
    skills_used: list[str] = []
    tool_count = 0
    followups = 0
    try:
        async with async_session() as db:
            skills_used = await _skills_used_in_task(db, task.id)
            tool_count = await _tool_use_count(db, task.id)
            followups = await _followup_count(db, task.id)
    except Exception:
        pass
    props = _outcome_props(task)
    props.update({
        'tool_use_count': tool_count,
        'skills_used': skills_used,
        'followup_count': followups,
    })
    telemetry.send(TelemetryEvent.TASK_COMPLETED, props)


async def send_task_failed(task: Task, *, phase: str, category: str) -> None:
    skills_used: list[str] = []
    followups = 0
    try:
        async with async_session() as db:
            skills_used = await _skills_used_in_task(db, task.id)
            followups = await _followup_count(db, task.id)
    except Exception:
        pass
    props = _outcome_props(task)
    props.update({
        'phase': phase,
        'error_category': (getattr(task, 'error_category', None) or category),
        'skills_used': skills_used,
        'followup_count': followups,
    })
    telemetry.send(TelemetryEvent.TASK_FAILED, props)


def send_task_cancelled(task: Task, *, status_at: str | None = None) -> None:
    telemetry.send(
        TelemetryEvent.TASK_CANCELLED,
        {
            'task_id': task.id,
            'status_at_cancel': status_at or task.status,
            'source': task.source or TaskSource.OTHER,
            'via': via_for_task(task),
            **profile_telemetry(task.ai_profile_id),
            **_usage_props(task),
        },
    )


def send_task_resumed(
    task: Task, *, wait_secs: float | None, wake_reason: str
) -> None:
    telemetry.send(
        TelemetryEvent.TASK_RESUMED,
        {
            'task_id': task.id,
            'is_store_task': task.store_id is not None,
            'wait_duration_bucket': telemetry.duration_bucket(wait_secs),
            'wake_reason': wake_reason,
            'ai_profile_kind': profile_kind_for_id(task.ai_profile_id),
        },
    )


async def send_question_asked(task_id: str):
    """Emit the ask event, open the question pause, return the ask
    time."""
    telemetry.send(
        TelemetryEvent.AGENT_QUESTION_ASKED,
        {'task_id': task_id},
    )
    await _pause_row(task_id, open_pause)
    return datetime.now(UTC)


async def send_question_answered(
    task_id: str, asked_at, request_id: str | None = None
) -> None:
    """Close the question pause; ``answer_latency_seconds`` is the
    time since ``asked_at``. ``via`` is the channel that ANSWERED —
    handed over by request_id (the agent's own task emits this)."""
    latency = max(0, int((datetime.now(UTC) - asked_at).total_seconds()))
    await _pause_row(task_id, close_pause)
    telemetry.send(
        TelemetryEvent.AGENT_QUESTION_ANSWERED,
        {
            'task_id': task_id,
            'answer_latency_seconds': latency,
            'via': take_answer_via(request_id) if request_id else current_via(),
        },
    )


def send_message_submitted(task_id: str) -> None:
    """A user message sent to a task — both surfaces (the web UI and
    the hosted MCP ``send_message``) land here."""
    telemetry.send(
        TelemetryEvent.TASK_MESSAGE_SUBMITTED,
        {'task_id': task_id, 'via': current_via()},
    )


def send_plan_verdict(task_id: str, *, approved: bool) -> None:
    """A human plan verdict — approval or requested changes."""
    telemetry.send(
        TelemetryEvent.PLAN_APPROVED
        if approved
        else TelemetryEvent.PLAN_CHANGES_REQUESTED,
        {'task_id': task_id, 'via': current_via()},
    )


async def _pause_row(task_id: str, op) -> None:
    """Apply a pause-clock operation to a task row (best-effort)."""
    try:
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if task is None:
                return
            op(task)
            await db.commit()
    except Exception:
        pass


def result_usage(event: dict) -> tuple[float, int, int, int]:
    """(cost_usd, input_tokens, output_tokens, num_turns) from a
    stream-json ``result`` event; missing fields are zero."""
    usage = event.get('usage') or {}

    def _num(container: dict, key: str, cast):
        try:
            return cast(container.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    return (
        _num(event, 'total_cost_usd', float),
        _num(usage, 'input_tokens', int),
        _num(usage, 'output_tokens', int),
        _num(event, 'num_turns', int),
    )


async def accumulate_run_usage(task_id: str, event: dict) -> None:
    """Add one ``result``'s turn count to the task row (best-effort).

    Tokens and cost are NOT taken from here: the result's
    ``total_cost_usd`` is process-cumulative (a follow-up in the same
    process reports earlier turns again) and prices every model as
    Claude. They are metered per API call instead — see
    app/ai/usage_meter.py.
    """
    _, _, _, turns = result_usage(event)
    try:
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return
            task.num_turns = (task.num_turns or 0) + turns
            await db.commit()
    except Exception:
        pass


# ── Terminal-event emission ───────────────────────────────────

terminal_failure_phase: ContextVar[str | None] = ContextVar(
    'terminal_failure_phase', default=None
)


class PipelinePhase:
    """Mark terminal transitions inside as lifecycle phase 'pipeline'
    (queue/session plumbing rather than the agent run)."""

    def __enter__(self):
        self._token = terminal_failure_phase.set('pipeline')
        return self

    def __exit__(self, *exc):
        terminal_failure_phase.reset(self._token)


_pending_terminal_emits: set = set()


@event.listens_for(_SyncSession, 'after_commit')
def _emit_terminal_on_commit(sync_session) -> None:
    transitions = sync_session.info.pop('terminal_transitions', None)
    starts = sync_session.info.pop('start_transitions', None) or set()
    if not transitions and not starts:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug('task telemetry emit skipped (no running loop)')
        return
    for task_id in starts:
        _spawn_emit(loop, _emit_started(task_id))
    for task_id, (from_status, to_status) in (transitions or {}).items():
        _spawn_emit(loop, _emit_terminal(task_id, from_status, to_status))


async def drain_terminal_emits() -> None:
    while _pending_terminal_emits:
        await asyncio.gather(
            *list(_pending_terminal_emits), return_exceptions=True
        )


def _spawn_emit(loop, coro) -> None:
    task = loop.create_task(coro)
    _pending_terminal_emits.add(task)
    task.add_done_callback(_pending_terminal_emits.discard)


@event.listens_for(_SyncSession, 'after_rollback')
def _drop_terminal_on_rollback(sync_session) -> None:
    sync_session.info.pop('terminal_transitions', None)
    sync_session.info.pop('start_transitions', None)


async def _emit_started(task_id: str) -> None:
    try:
        async with async_session() as db:
            task = await db.get(Task, task_id)
        if task is not None:
            send_task_started(task)
    except Exception:
        pass


async def _emit_terminal(task_id: str, from_status: str, to_status: str):
    try:
        async with async_session() as db:
            task = await db.get(Task, task_id)
        if task is None:
            return
        if to_status == 'completed':
            await send_task_completed(task)
            return
        if (task.error_category or '') == 'stopped_by_user':
            send_task_cancelled(task, status_at=from_status)
            return
        phase = terminal_failure_phase.get()
        if not phase:
            phase = (
                'pre_running'
                if from_status in ('pending', 'queued')
                else 'running'
            )
        await send_task_failed(
            task, phase=phase, category='unhandled_exception'
        )
    except Exception:
        pass
