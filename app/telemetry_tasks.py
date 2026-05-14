"""Task-level telemetry helpers — kept out of task_runner_exec.py
so that file stays under the 800-line limit. Anonymous + privacy-safe;
see app.telemetry."""

import json
import re

from sqlalchemy import func, select

from app import telemetry
from app.ai.profiles import profile_kind_for_id
from app.database import async_session
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.telemetry_events import TelemetryEvent

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


async def send_task_completed(task: Task) -> None:
    skills_used: list[str] = []
    tool_count = 0
    try:
        async with async_session() as db:
            skills_used = await _skills_used_in_task(db, task.id)
            tool_count = await _tool_use_count(db, task.id)
    except Exception:
        pass
    duration_secs = telemetry.duration_seconds_from_iso(
        task.started_at, task.completed_at
    )
    telemetry.send(
        TelemetryEvent.TASK_COMPLETED,
        {
            'is_store_task': task.store_id is not None,
            'was_planned': bool(task.plan_mode),
            'duration_bucket': telemetry.duration_bucket(duration_secs),
            'duration_seconds': duration_secs,
            'is_scheduled': task.schedule_id is not None,
            'ai_profile_kind': profile_kind_for_id(task.ai_profile_id),
            'tool_use_count_bucket': telemetry.count_bucket(tool_count),
            'tool_use_count': tool_count,
            'skills_used': skills_used,
        },
    )


def send_task_failed(task: Task, *, phase: str, category: str) -> None:
    telemetry.send(
        TelemetryEvent.TASK_FAILED,
        {
            'is_store_task': task.store_id is not None,
            'was_planned': bool(task.plan_mode),
            'phase': phase,
            'error_category': (
                getattr(task, 'error_category', None) or category
            ),
            'is_scheduled': task.schedule_id is not None,
            'ai_profile_kind': profile_kind_for_id(task.ai_profile_id),
        },
    )
