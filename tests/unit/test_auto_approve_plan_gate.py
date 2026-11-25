"""Guard: the one rule for whether a task's ExitPlanMode waits for a human.

``app.plan_states.plan_auto_accepted`` decides it for every launch path
(``task_runner_auto``, the follow-up paths, and the Auto/Review switch
on a running session). It is called here directly, so a change to the
rule shows up in this table.

- A plan-only schedule planner hands its plan to the schedule as a
  proposal; the owner approves it there, not at ExitPlanMode.
- A task of an AUTO schedule runs without review, as its schedule does.
- A task of a REVIEW schedule that plans for itself waits.
- A standalone task honours its own ``plan_mode``.
- A plan is reviewed once per task: after approval (``started_at``),
  the agent's re-plans run.
"""

from types import SimpleNamespace

import pytest

from app.plan_states import plan_auto_accepted

pytestmark = pytest.mark.unit


def _task(**kw):
    base = dict(
        schedule_id=None, plan_mode=True, is_plan_only=False, started_at=None
    )
    return SimpleNamespace(**{**base, **kw})


def _schedule(*, review: bool, system: bool = False):
    return SimpleNamespace(plan_mode=review, is_system=system)


class TestAutoApproveGate:
    def test_plan_only_schedule_task_auto_approves(self):
        task = _task(schedule_id='s1', is_plan_only=True)
        assert plan_auto_accepted(task, _schedule(review=True)) is True

    def test_auto_schedule_task_auto_approves(self):
        task = _task(schedule_id='s1')
        assert plan_auto_accepted(task, _schedule(review=False)) is True

    def test_system_schedule_task_auto_approves(self):
        task = _task(schedule_id='s1')
        assert (
            plan_auto_accepted(task, _schedule(review=True, system=True))
            is True
        )

    def test_review_schedule_task_planning_for_itself_waits(self):
        task = _task(schedule_id='s1')
        assert plan_auto_accepted(task, _schedule(review=True)) is False

    def test_standalone_plan_mode_waits(self):
        assert plan_auto_accepted(_task()) is False

    def test_standalone_auto_mode_proceeds(self):
        assert plan_auto_accepted(_task(plan_mode=False)) is True

    def test_schedule_with_auto_mode_also_proceeds(self):
        task = _task(schedule_id='s1', plan_mode=False)
        assert plan_auto_accepted(task, _schedule(review=True)) is True

    def test_approved_task_is_not_reviewed_again(self):
        task = _task(started_at='2026-01-01T00:00:00+00:00')
        assert plan_auto_accepted(task) is True
