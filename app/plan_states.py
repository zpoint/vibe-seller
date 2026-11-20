"""Schedule plan-lifecycle state machine.

A schedule runs in one of two modes, chosen by its owner:

- **Auto** (``plan_mode=False``, the default): every fire runs the
  schedule's prompt directly. No plan, no review; ``plan_status``
  stays ``none``.
- **Review** (``plan_mode=True``): a plan-only Task writes a plan, the
  owner (or an MCP agent acting for them) approves it, and every fire
  runs the APPROVED plan. The server never approves a plan itself.

``PlanStatus`` tracks the review lifecycle on the ``Schedule`` row:

- ``NONE``             — auto schedule, or a review schedule with no
  plan yet (legacy rows; re-plan).
- ``PLANNING``         — a plan-only Task is writing a plan.
- ``PENDING_APPROVAL`` — the planner finished; ``proposed_plan`` waits
  for the owner. Waiting costs nothing (no agent, no slot) and never
  expires.
- ``READY``            — ``plan`` is the approved plan; the schedule
  fires it.
- ``STALE``            — legacy: the prompt changed and no new planner
  could be started. Re-plan.
- ``FAILED``           — the planner failed or died; re-plan.

The fire gate is :func:`schedule_fires`: system and auto schedules
always fire; a review schedule fires its APPROVED plan (and keeps doing
so while a revision is in progress). See ``docs/subsystems.md``
§ Plan review.
"""

import enum


class PlanStatus(enum.StrEnum):
    NONE = 'none'
    PLANNING = 'planning'
    # 16 chars: the column is String(16).
    PENDING_APPROVAL = 'pending_approval'
    READY = 'ready'
    STALE = 'stale'
    FAILED = 'failed'


class PlanFailure(enum.StrEnum):
    """Why a review schedule's plan could not be written.

    Stored as ``Schedule.plan_error_code`` so the UI can say it in the
    user's language; ``plan_error`` keeps the technical detail.
    """

    # The planning agent stopped and nothing could resume it.
    PLANNER_DIED = 'planner_died'
    # The server restarted while the plan was being written.
    SERVER_RESTART = 'server_restart'
    # Someone stopped the planning run.
    STOPPED = 'stopped'
    # The planning run finished without writing a plan.
    NO_PLAN = 'no_plan'
    # The planning run could not be started.
    START_FAILED = 'start_failed'
    # Any other error (see plan_error for the detail).
    OTHER = 'other'


def fail_plan(sched, code: PlanFailure, detail: str | None) -> None:
    """Mark the plan failed with a reason code and the technical detail."""
    sched.plan_status = PlanStatus.FAILED.value
    sched.plan_error_code = code.value
    sched.plan_error = detail


def clear_plan_failure(sched) -> None:
    sched.plan_error_code = None
    sched.plan_error = None


def freeze_schedule_plan(sched, plan_text: str | None, now: str) -> None:
    """Make *plan_text* the schedule's approved plan: the one commit point.

    Only an explicit approval (``POST /plan/approve``) reaches this —
    never the planner itself. Marks it READY, clears the proposal and
    the reviewer's feedback, and bumps ``plan_version`` (the
    optimistic-lock token). Callers commit the session.
    """
    sched.plan = plan_text
    sched.proposed_plan = None
    sched.plan_feedback = None
    sched.plan_status = PlanStatus.READY.value
    sched.plan_version = (sched.plan_version or 0) + 1
    clear_plan_failure(sched)
    sched.current_planning_task_id = None
    sched.updated_at = now


def review_plan_fires(sched) -> bool:
    """Whether a REVIEW schedule may fire now: the one fire-gate rule.

    A review schedule fires its approved ``plan``. It keeps firing that
    plan while a revision is planned, waits for approval, or fails —
    asking for changes must not silently stop a working schedule. A
    prompt edit clears the approved plan (it answered the old prompt),
    so nothing fires until the new plan is approved. ``stale``/``none``
    are legacy rows whose text may belong to an older prompt.
    """
    if not (sched.plan or '').strip():
        return False
    return sched.plan_status not in (
        PlanStatus.STALE.value,
        PlanStatus.NONE.value,
    )


def schedule_fires(sched) -> bool:
    """The fire gate (ignoring pause): system/auto always, review on approval."""
    if not is_review_schedule(sched):
        return True
    return review_plan_fires(sched)


def plan_auto_accepted(task, schedule=None) -> bool:
    """Whether this task's ExitPlanMode is accepted without a human.

    The ONE rule for every launch path (``task_runner_auto`` and the
    follow-up paths in ``routers/tasks_conversation``); *schedule* is
    the task's schedule row, if it has one:

    - a non-plan-mode task has no plan to review;
    - a plan-only planner's "approval" is only the hand-off of its plan
      to the schedule as a PROPOSAL — the owner still approves it;
    - a task of an AUTO schedule (e.g. a no-store task, which is always
      plan-mode) runs without review, as its schedule does;
    - anything else — standalone plan-mode tasks, and a task of a
      REVIEW schedule that ends up planning for itself (a retry or
      follow-up with no approved plan to copy) — waits for a human, so
      no plan runs unapproved.
    """
    if task.is_plan_only or not task.plan_mode:
        return True
    return schedule is not None and not is_review_schedule(schedule)


def is_review_schedule(sched) -> bool:
    """True for a review schedule (a user schedule with plan review on)."""
    return bool(sched.plan_mode) and not sched.is_system


def fire_plan(sched) -> tuple[bool, str | None, int | None]:
    """What a fire of *sched* runs, read from the row at fire time.

    ``(plan_mode, plan, plan_version)`` for the fired task: a review
    schedule's fire carries its approved plan (and skips planning); an
    auto or system schedule's fire carries no plan, whatever text is
    left on the row — the mode is never taken from the APScheduler
    job's kwargs, which were frozen when the job was registered.
    """
    if is_review_schedule(sched):
        return True, sched.plan, sched.plan_version
    return False, None, None
