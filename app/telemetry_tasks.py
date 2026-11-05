"""Task-level telemetry helpers — kept out of task_runner_exec.py
so that file stays under the 800-line limit. Anonymous + privacy-safe;
see app.telemetry."""

from datetime import UTC, datetime
import json
import re

from sqlalchemy import func, select

from app import telemetry
from app.ai.profiles import profile_kind_for_id
from app.database import async_session
from app.models.task import Task, close_pause, open_pause, paused_total
from app.models.task_message import TaskMessage
from app.telemetry_events import TaskSource, TelemetryEvent

_SKILL_PATH_RE = re.compile(r'\.claude/skills/([^/]+)/SKILL\.md$')


async def _skills_used_in_task(db, task_id: str) -> list[str]:
    """Derive which built-in skills the agent loaded.

    Looks for ``Read`` tool-use rows whose ``file_path`` matches a
    skill SKILL.md. Privacy-safe: skill names are our own internal
    identifiers (e.g. ``amazon-ads``).
    """
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
            path = (
                data.get('tool_input', {}).get('file_path')
                or data.get('input', {}).get('file_path')
                or ''
            )
        except (ValueError, TypeError, AttributeError):
            continue
        m = _SKILL_PATH_RE.search(path or '')
        if m and not m.group(1).startswith('__'):
            skills.add(m.group(1))
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
        'user_hash': telemetry.user_hash(user_id),
        'is_store_task': task.store_id is not None,
        'plan_mode': bool(task.plan_mode),
        'schedule_id': task.schedule_id,
        'has_parent': task.parent_task_id is not None,
        'ai_profile_kind': profile_kind_for_id(task.ai_profile_id),
        'description_length_bucket': telemetry.length_bucket(
            len(task.description or '')
        ),
    }
    if created_by_role is not None:
        props['created_by_role'] = created_by_role
    telemetry.send(TelemetryEvent.TASK_CREATED, props)


def _usage_props(task: Task) -> dict:
    """Accumulated agent-run usage."""
    return {
        'cost_usd': task.cost_usd,
        'input_tokens': task.input_tokens,
        'output_tokens': task.output_tokens,
        'num_turns': task.num_turns,
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
        'ai_profile_kind': profile_kind_for_id(task.ai_profile_id),
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


def send_task_cancelled(task: Task) -> None:
    telemetry.send(
        TelemetryEvent.TASK_CANCELLED,
        {
            'task_id': task.id,
            'status_at_cancel': task.status,
            'source': task.source or TaskSource.OTHER,
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


async def send_question_answered(task_id: str, asked_at) -> None:
    """Close the question pause; ``answer_latency_seconds`` is the
    time since ``asked_at``."""
    latency = max(0, int((datetime.now(UTC) - asked_at).total_seconds()))
    await _pause_row(task_id, close_pause)
    telemetry.send(
        TelemetryEvent.AGENT_QUESTION_ANSWERED,
        {
            'task_id': task_id,
            'answer_latency_seconds': latency,
        },
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
    """Add one run's usage to the task row's totals (best-effort)."""
    cost, in_tok, out_tok, turns = result_usage(event)
    try:
        async with async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return
            task.cost_usd = (task.cost_usd or 0.0) + cost
            task.input_tokens = (task.input_tokens or 0) + in_tok
            task.output_tokens = (task.output_tokens or 0) + out_tok
            task.num_turns = (task.num_turns or 0) + turns
            await db.commit()
    except Exception:
        pass
