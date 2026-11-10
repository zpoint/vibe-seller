"""Schedule plan-lifecycle state machine.

Scheduled tasks with ``plan_mode=True`` author their plan once at
schedule creation (or after a prompt edit) and freeze it into every
fire. ``PlanStatus`` tracks that lifecycle on the ``Schedule`` row:

- ``NONE``     — plan_mode=False schedule, or no plan yet authored.
- ``PLANNING`` — a plan-only Task is currently authoring the plan.
- ``READY``    — plan committed; schedule is eligible to fire.
- ``STALE``    — the prompt was edited, or the plan was frozen under an
  older :data:`PLAN_CONTRACT`; the stored plan is no longer valid.
- ``FAILED``   — planner task failed; user must /replan.

The scheduler fire-gate refuses to fire a schedule unless
``plan_status == READY`` OR the schedule is a system schedule
(``is_system=True``) OR ``plan_mode=False``.
"""

import enum

# The harness contract a frozen plan was written against: the file
# layout, data contracts and gates the prompts and built-in skills
# describe. A frozen plan spells those out step by step, and every fire
# replays it verbatim, so a plan frozen under an older contract keeps
# driving the old mechanics after an upgrade (old paths, reusing last
# week's data, a review step that no longer exists). Bump this when a
# change makes plans written before it wrong; every such schedule then
# goes STALE at boot (``database._stale_outdated_plans``) and must be
# re-planned before it fires again.
#   2 — ad tasks checked from TSVs under store-data/, no reflection.
PLAN_CONTRACT = 2


class PlanStatus(enum.StrEnum):
    NONE = 'none'
    PLANNING = 'planning'
    READY = 'ready'
    STALE = 'stale'
    FAILED = 'failed'


# Statuses for which a plan-mode schedule is NOT eligible to fire.
BLOCKS_FIRE: set[PlanStatus] = {
    PlanStatus.NONE,
    PlanStatus.PLANNING,
    PlanStatus.STALE,
    PlanStatus.FAILED,
}


def freeze_schedule_plan(sched, plan_text: str | None, now: str) -> None:
    """Freeze *plan_text* onto the schedule row: the one commit point.

    Marks it READY, bumps ``plan_version`` (the optimistic-lock token)
    and stamps the :data:`PLAN_CONTRACT` it was written under. Callers
    commit the session.
    """
    sched.plan = plan_text
    sched.plan_status = PlanStatus.READY.value
    sched.plan_version = (sched.plan_version or 0) + 1
    sched.plan_contract = PLAN_CONTRACT
    sched.plan_error = None
    sched.current_planning_task_id = None
    sched.updated_at = now


def allows_fire(status: str) -> bool:
    """Return True if a plan-mode schedule with this status may fire."""
    try:
        return PlanStatus(status) == PlanStatus.READY
    except ValueError:
        return False
