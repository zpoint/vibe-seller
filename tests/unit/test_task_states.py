"""Unit tests for task state machine logic."""

import pytest

from app.task_states import (
    ACTIVE,
    DESIGNABLE,
    RETRIABLE,
    STARTABLE,
    STOPPABLE,
    TRANSITIONS,
    WAKEABLE,
    TaskStatus,
    assert_transition,
    can_transition,
)

pytestmark = pytest.mark.unit


class TestTaskStates:
    def test_all_statuses_have_transitions(self):
        """Every status must have an entry in TRANSITIONS."""
        for status in TaskStatus:
            assert status in TRANSITIONS, f'{status} missing from TRANSITIONS'

    def test_valid_transitions(self):
        """Spot-check several expected valid transitions."""
        assert can_transition('pending', 'designing')
        assert can_transition('pending', 'queued')
        assert can_transition('designing', 'planned')
        assert can_transition('designing', 'running')
        assert can_transition('designing', 'failed')
        assert can_transition('planned', 'running')
        assert can_transition('running', 'completed')
        assert can_transition('running', 'failed')
        assert can_transition('running', 'waiting')
        assert can_transition('waiting', 'queued')
        assert can_transition('waiting', 'failed')
        assert can_transition('failed', 'pending')
        assert can_transition('completed', 'pending')

    def test_invalid_transitions(self):
        """Spot-check several disallowed transitions."""
        assert not can_transition('pending', 'completed')
        assert not can_transition('running', 'designing')
        assert not can_transition('queued', 'completed')

    def test_terminal_states_limited(self):
        """completed/failed can go to retry or follow-up states."""
        completed_targets = TRANSITIONS[TaskStatus.COMPLETED]
        assert completed_targets == {
            TaskStatus.PENDING,
            TaskStatus.DESIGNING,  # follow-up (plan mode)
            TaskStatus.RUNNING,  # follow-up (auto mode)
            TaskStatus.WAITING,  # child resumed → parent reopened
        }

        failed_targets = TRANSITIONS[TaskStatus.FAILED]
        assert failed_targets == {
            TaskStatus.PENDING,
            TaskStatus.DESIGNING,
            TaskStatus.RUNNING,  # follow-up (auto mode)
        }

    def test_auto_mode_transitions(self):
        """Auto mode: PENDING → RUNNING → COMPLETED."""
        assert can_transition('pending', 'running')
        assert can_transition('running', 'completed')
        assert can_transition('running', 'failed')
        # Follow-up / continue in auto mode
        assert can_transition('completed', 'running')
        assert can_transition('failed', 'running')

    def test_assert_transition_raises(self):
        """assert_transition raises ValueError for invalid transitions."""
        with pytest.raises(ValueError, match='Invalid task transition'):
            assert_transition('pending', 'completed')

    def test_assert_transition_passes(self):
        """assert_transition does not raise for valid transitions."""
        assert_transition('pending', 'designing')
        assert_transition('running', 'completed')

    def test_named_groups_consistent(self):
        """Named groups only contain valid TaskStatus values."""
        all_statuses = set(TaskStatus)
        assert STOPPABLE <= all_statuses
        assert ACTIVE <= all_statuses
        assert RETRIABLE <= all_statuses
        assert STARTABLE <= all_statuses
        assert DESIGNABLE <= all_statuses

    def test_stoppable_group(self):
        assert STOPPABLE == {
            TaskStatus.DESIGNING,
            TaskStatus.PLANNED,
            TaskStatus.RUNNING,
            TaskStatus.WAITING,
        }

    def test_active_group(self):
        assert ACTIVE == {
            TaskStatus.DESIGNING,
            TaskStatus.RUNNING,
        }

    def test_wakeable_group(self):
        assert WAKEABLE == {TaskStatus.WAITING}

    def test_waiting_transitions(self):
        """Verify WAITING status transitions."""
        assert can_transition('running', 'waiting')
        assert can_transition('waiting', 'queued')
        assert can_transition('waiting', 'failed')
        assert can_transition(
            'waiting', 'completed'
        )  # children-wait auto-complete

    def test_queueable_source_state_transitions(self):
        """State machine: QUEUED is reachable from these states."""
        assert can_transition('pending', 'queued')
        assert can_transition('planned', 'queued')
        assert can_transition('waiting', 'queued')

    def test_queue_to_active_transitions(self):
        """State machine: QUEUED can transition to active states."""
        assert can_transition('queued', 'running')
        assert can_transition('queued', 'designing')
        assert can_transition('queued', 'failed')

    def test_queue_disallowed_transitions(self):
        """State machine: QUEUED cannot skip to terminal or plan states."""
        assert not can_transition('queued', 'completed')
        assert not can_transition('queued', 'planned')

    def test_invalid_status_string(self):
        """Unknown status strings return False."""
        assert not can_transition('unknown', 'pending')
        assert not can_transition('pending', 'unknown')

    def test_enum_is_str(self):
        """TaskStatus members are usable as plain strings."""
        assert TaskStatus.PENDING == 'pending'
        assert f'{TaskStatus.RUNNING}' == 'running'


# Full transition table (authoritative). Tests below iterate this
# and cross-check against `TRANSITIONS`, `can_transition`, and
# `assert_transition`. New entries in TRANSITIONS must be mirrored
# here, which forces a conscious review at change time.
_EXPECTED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {
        TaskStatus.QUEUED,
        TaskStatus.DESIGNING,
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
    },
    TaskStatus.QUEUED: {
        TaskStatus.DESIGNING,
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
    },
    TaskStatus.DESIGNING: {
        TaskStatus.PLANNED,
        TaskStatus.RUNNING,
        TaskStatus.COMPLETED,
        TaskStatus.WAITING,
        TaskStatus.FAILED,
    },
    TaskStatus.PLANNED: {
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.DESIGNING,
        # is_plan_only tasks (app/plan_states.py) commit a plan to
        # the owning Schedule and terminate without executing.
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
    },
    TaskStatus.RUNNING: {
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.WAITING,
    },
    TaskStatus.WAITING: {
        TaskStatus.QUEUED,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
    },
    TaskStatus.COMPLETED: {
        TaskStatus.PENDING,
        TaskStatus.DESIGNING,
        TaskStatus.RUNNING,
        TaskStatus.WAITING,
    },
    TaskStatus.FAILED: {
        TaskStatus.PENDING,
        TaskStatus.DESIGNING,
        TaskStatus.RUNNING,
    },
}


class TestTransitionTableExhaustive:
    """Cross-check every (from, to) pair against the expected table.

    This is the one place that asserts the COMPLETE set of allowed
    and disallowed transitions. Adding a new TaskStatus value or
    changing TRANSITIONS must also update `_EXPECTED_TRANSITIONS`
    above — otherwise these tests fail and force a review.
    """

    def test_every_status_listed(self):
        """Every enum value must appear as a key in both maps."""
        for status in TaskStatus:
            assert status in TRANSITIONS, f'TRANSITIONS missing {status}'
            assert status in _EXPECTED_TRANSITIONS, (
                f'_EXPECTED_TRANSITIONS missing {status}'
            )

    def test_transitions_match_expected(self):
        """TRANSITIONS exactly matches _EXPECTED_TRANSITIONS."""
        assert TRANSITIONS == _EXPECTED_TRANSITIONS, (
            'TRANSITIONS diverged from _EXPECTED_TRANSITIONS — '
            'update both together after intentional changes.'
        )

    @pytest.mark.parametrize('from_status', list(TaskStatus))
    @pytest.mark.parametrize('to_status', list(TaskStatus))
    def test_every_pair(self, from_status, to_status):
        """For every (from, to) pair, `can_transition` matches the
        expected table. This is the O(n²) exhaustive check.
        """
        expected = to_status in _EXPECTED_TRANSITIONS[from_status]
        actual = can_transition(from_status, to_status)
        assert actual is expected, (
            f'can_transition({from_status}, {to_status}) = '
            f'{actual}, expected {expected}'
        )

    def test_no_self_transition(self):
        """No state transitions to itself. Re-running a task always
        goes through an intermediate status (PENDING / QUEUED /
        DESIGNING / RUNNING), so self-loops must be rejected to
        catch programming errors that skip reset.
        """
        for status in TaskStatus:
            assert not can_transition(status, status), (
                f'{status} -> {status} must be disallowed'
            )

    def test_assert_transition_raises_on_every_invalid(self):
        """`assert_transition` must raise for every disallowed pair."""
        for from_status in TaskStatus:
            for to_status in TaskStatus:
                if to_status in _EXPECTED_TRANSITIONS[from_status]:
                    continue
                with pytest.raises(ValueError, match='Invalid task transition'):
                    assert_transition(from_status, to_status)


class TestStateGroupSemantics:
    """Group membership must make semantic sense.

    `hasRunningTask` (frontend) and `STOPPABLE` / `ACTIVE` (backend)
    rely on these; a regression here means UI gating breaks.
    """

    def test_terminal_enough_for_trigger_button(self):
        """The schedule "Run Now" button is disabled while any task
        is still progressing. A task is progressing if it is NOT in
        one of:
          - completed (happy path)
          - failed    (terminal error)
          - waiting   (agent paused with a wait-condition; a new
                       Trigger should be allowed)
        Any additions to the active-status set must come with a
        corresponding frontend change.
        """
        non_progressing = {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.WAITING,
        }
        progressing = set(TaskStatus) - non_progressing
        # Every progressing status is in either ACTIVE, STOPPABLE,
        # or is a transient queue state — never a pure terminal.
        for status in progressing:
            assert (
                status in ACTIVE
                or status in STOPPABLE
                or status in {TaskStatus.PENDING, TaskStatus.QUEUED}
            ), f'{status} is neither active, stoppable, nor queued'

    def test_active_subset_of_stoppable(self):
        """Anything ACTIVE must also be STOPPABLE — an active task
        must always be cancelable.
        """
        assert ACTIVE <= STOPPABLE

    def test_wakeable_is_strict_subset_of_stoppable(self):
        """Waiting tasks can be woken AND stopped. These sets may
        overlap but wakeable is narrower (only waiting).
        """
        assert WAKEABLE <= STOPPABLE
        assert WAKEABLE == {TaskStatus.WAITING}

    def test_retriable_is_terminal_only(self):
        """/retry is only legal from terminal states."""
        assert RETRIABLE == {
            TaskStatus.FAILED,
            TaskStatus.PENDING,  # canonical retry source
            TaskStatus.COMPLETED,  # follow-up via retry
        }
