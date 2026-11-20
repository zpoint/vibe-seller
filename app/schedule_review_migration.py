"""One-time move of forced-plan schedules to auto mode.

Before plan review became a choice, every user schedule was created
with ``plan_mode=True`` and had to author a plan (accepted unread)
before it could fire. None of those owners chose review, so on the
first boot of a version with the choice, every such schedule becomes an
AUTO schedule: it fires its prompt on its cron. Its plan artefacts are
dropped, and any planner still in flight for it is failed in the same
transaction — boot recovery would otherwise re-launch planners for
schedules that no longer plan.

Not touched: system schedules, ``is_active`` (a paused schedule stays
paused), and every schedule created after the marker exists — those
chose their mode.

Runs from ``init_db`` before the APScheduler jobs are rebuilt and the
task queue recovers its rows. Idempotent via an ``AppSettings`` marker.
"""

from datetime import UTC, datetime
import logging

from sqlalchemy import select

from app.models.app_settings import AppSettings
from app.models.schedule import Schedule
from app.models.task import Task
from app.plan_states import PlanStatus
from app.task_states import TaskStatus

logger = logging.getLogger(__name__)

_ENDED = (TaskStatus.COMPLETED, TaskStatus.FAILED)

MARKER_KEY = 'schedules_plan_review_optin'

PLANNER_RETIRED = (
    'This schedule now runs automatically, so its plan is no longer needed.'
)


async def migrate_forced_plan_schedules(session) -> int:
    """Convert pre-choice plan-mode schedules to auto (once).

    Commits in the caller's session. Returns how many were converted.
    """
    if await session.get(AppSettings, MARKER_KEY) is not None:
        return 0
    now = datetime.now(UTC).isoformat()
    rows = (
        await session.execute(
            select(Schedule).where(
                Schedule.plan_mode.is_(True),
                Schedule.is_system.is_not(True),
            )
        )
    ).scalars()
    converted = [s.id for s in rows]
    for schedule_id in converted:
        sched = await session.get(Schedule, schedule_id)
        sched.plan_mode = False
        sched.plan_status = PlanStatus.NONE.value
        sched.plan = None
        sched.proposed_plan = None
        sched.plan_feedback = None
        sched.plan_error = None
        sched.plan_error_code = None
        sched.current_planning_task_id = None
        sched.updated_at = now
    if converted:
        planners = (
            await session.execute(
                select(Task).where(
                    Task.schedule_id.in_(converted),
                    Task.is_plan_only.is_(True),
                    Task.status.not_in(_ENDED),
                )
            )
        ).scalars()
        for task in planners:
            task.status = TaskStatus.FAILED
            task.error = PLANNER_RETIRED
            task.error_category = 'superseded_by_edit'
            task.updated_at = now
    session.add(AppSettings(key=MARKER_KEY, value=now))
    await session.commit()
    if converted:
        logger.info(
            'Plan review is now opt-in: %d schedule(s) switched to auto',
            len(converted),
        )
    return len(converted)
