"""Guard: the auto_approve_plan gate on task_runner_auto.py.

The gate decides whether the hook auto-approves the agent's plan at
ExitPlanMode or blocks waiting for the user to click approve.

Rule (one line):

    auto_approve_plan = bool(task.schedule_id) or not task.plan_mode

- Any task with a schedule_id (plan-only authoring OR a scheduled
  fire) auto-approves. The frozen-plan architecture handles review
  via SchedulePlanPanel + Re-plan; the fanout-plan validator
  enforces structural rules at ExitPlanMode time.
- Standalone tasks honor task.plan_mode (True → manual, False →
  auto). user.plan_mode_default feeds into task.plan_mode at create
  time for standalone tasks.

This test pins the gate expression so a refactor must update both
the code and this table at once.
"""

from dataclasses import dataclass

import pytest

pytestmark = pytest.mark.unit


@dataclass
class _FakeTask:
    schedule_id: str | None
    plan_mode: bool
    is_plan_only: bool = False


def _gate(task: _FakeTask) -> bool:
    """Mirrors the gate expression in app/task_runner_auto.py."""
    return bool(task.schedule_id) or not task.plan_mode


class TestAutoApproveGate:
    def test_plan_only_schedule_task_auto_approves(self):
        """Plan-only authoring: always auto — creator pref does not
        gate schedule review. Review is via SchedulePlanPanel (user
        can Re-plan after the frozen plan lands)."""
        task = _FakeTask(schedule_id='s1', plan_mode=True, is_plan_only=True)
        assert _gate(task) is True

    def test_regular_scheduled_fire_auto_approves(self):
        """Per-store fire: human already reviewed at schedule create."""
        task = _FakeTask(schedule_id='s1', plan_mode=True, is_plan_only=False)
        assert _gate(task) is True

    def test_standalone_plan_mode_waits(self):
        """No schedule + plan_mode=True → manual approve."""
        task = _FakeTask(schedule_id=None, plan_mode=True)
        assert _gate(task) is False

    def test_standalone_auto_mode_proceeds(self):
        """No schedule + plan_mode=False → immediate execute."""
        task = _FakeTask(schedule_id=None, plan_mode=False)
        assert _gate(task) is True

    def test_schedule_with_auto_mode_also_proceeds(self):
        """plan_mode=False + schedule_id (future: non-plan-mode
        schedules) also auto-approves."""
        task = _FakeTask(schedule_id='s1', plan_mode=False)
        assert _gate(task) is True
