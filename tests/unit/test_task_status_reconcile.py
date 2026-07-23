"""Unit tests for the pure task-status reconcile helpers.

`reconcile_streaming_run_status` (P1 backend backstop) and
`qa_followup_needs_input` (P2 decision) are pure functions so they can
be pinned without spinning up a session or DB.
"""

from types import SimpleNamespace

import pytest

from app.ai.task_status_reconcile import (
    agent_completed_with_result,
    qa_followup_needs_input,
    reconcile_streaming_run_status,
    should_park_qa_followup,
)
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit


def _task(status, plan_mode=False, started_at=None):
    return SimpleNamespace(
        status=status, plan_mode=plan_mode, started_at=started_at
    )


class TestReconcileStreamingRunStatus:
    def test_queued_auto_promotes_to_running_and_stamps_started(self):
        t = _task(TaskStatus.QUEUED)
        assert reconcile_streaming_run_status(t) == TaskStatus.RUNNING
        assert t.status == TaskStatus.RUNNING
        assert t.started_at  # stamped

    def test_pending_auto_promotes_to_running(self):
        t = _task(TaskStatus.PENDING)
        assert reconcile_streaming_run_status(t) == TaskStatus.RUNNING

    def test_queued_plan_mode_promotes_to_designing_no_started(self):
        t = _task(TaskStatus.QUEUED, plan_mode=True)
        assert reconcile_streaming_run_status(t) == TaskStatus.DESIGNING
        assert t.status == TaskStatus.DESIGNING
        assert t.started_at is None  # only RUNNING stamps started_at

    def test_running_is_noop(self):
        t = _task(TaskStatus.RUNNING, started_at='x')
        assert reconcile_streaming_run_status(t) is None
        assert t.status == TaskStatus.RUNNING

    def test_terminal_is_noop(self):
        for s in (TaskStatus.COMPLETED, TaskStatus.WAITING, TaskStatus.FAILED):
            t = _task(s)
            assert reconcile_streaming_run_status(t) is None
            assert t.status == s


class TestQaFollowupNeedsInput:
    def test_asked_and_no_tool_since_answer_needs_input(self):
        s = SimpleNamespace(
            _asked_user_question=True, _tool_use_since_answer=False
        )
        assert qa_followup_needs_input(s) is True

    def test_asked_but_tool_ran_after_answer_is_done(self):
        s = SimpleNamespace(
            _asked_user_question=True, _tool_use_since_answer=True
        )
        assert qa_followup_needs_input(s) is False

    def test_never_asked_is_done(self):
        s = SimpleNamespace(
            _asked_user_question=False, _tool_use_since_answer=False
        )
        assert qa_followup_needs_input(s) is False

    def test_missing_attrs_default_false(self):
        # FakeAgent / non-ClaudeCode sessions lack the attrs entirely.
        assert qa_followup_needs_input(SimpleNamespace()) is False


class TestAgentCompletedWithResult:
    """The guard that stops qa_followup_needs_input from stranding a
    genuinely-finished task in WAITING (regression: rc=0 + result parked)."""

    def _session(self, success=True, tool=True):
        return SimpleNamespace(_agent_success=success, _had_tool_use=tool)

    def test_success_plus_tool_plus_result_is_complete(self):
        assert (
            agent_completed_with_result(
                self._session(),
                '白底主图已生成，路径：generated_images/main.png',
            )
            is True
        )

    def test_no_result_is_not_complete(self):
        assert agent_completed_with_result(self._session(), '') is False
        assert agent_completed_with_result(self._session(), '   ') is False
        assert agent_completed_with_result(self._session(), None) is False

    def test_no_success_is_not_complete(self):
        assert (
            agent_completed_with_result(self._session(success=False), 'done')
            is False
        )

    def test_no_tool_work_is_not_complete(self):
        # Prose-only with no real work is exactly the "asking again" shape
        # the heuristic must still park.
        assert (
            agent_completed_with_result(self._session(tool=False), 'done')
            is False
        )

    def test_missing_attrs_default_false(self):
        assert agent_completed_with_result(SimpleNamespace(), 'done') is False

    def test_combined_gate_completes_finished_qa_task(self):
        # The exact stuck-task shape: asked a question, prose-only after
        # the last answer, BUT finished cleanly with a real result → the
        # finalizer must NOT park (qa_followup True yet completion True).
        s = SimpleNamespace(
            _asked_user_question=True,
            _tool_use_since_answer=False,
            _agent_success=True,
            _had_tool_use=True,
        )
        assert qa_followup_needs_input(s) is True
        assert (
            agent_completed_with_result(s, 'image generated: main.png') is True
        )
        # The finalizer uses the combined decision: DO NOT park.
        assert should_park_qa_followup(s, 'image generated: main.png') is False

    def test_should_park_when_prose_only_and_not_finished(self):
        # Asked, prose-only after answer, and did NOT finish → park.
        s = SimpleNamespace(
            _asked_user_question=True,
            _tool_use_since_answer=False,
            _agent_success=True,
            _had_tool_use=False,  # no real work
        )
        assert should_park_qa_followup(s, '') is True
