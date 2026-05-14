"""Centralized task status state machine.

Defines all valid task statuses, transitions between them, and named
status groups that replace scattered inline tuples throughout the codebase.
"""

import enum


class TaskStatus(enum.StrEnum):
    PENDING = 'pending'
    QUEUED = 'queued'
    DESIGNING = 'designing'
    PLANNED = 'planned'
    RUNNING = 'running'
    WAITING = 'waiting'
    COMPLETED = 'completed'
    FAILED = 'failed'


# Valid transitions: from_status -> {to_statuses}
TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {
        TaskStatus.QUEUED,
        TaskStatus.DESIGNING,
        TaskStatus.RUNNING,  # auto mode skips designing
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
        TaskStatus.COMPLETED,  # agent skipped planning
        TaskStatus.WAITING,  # orchestrator waiting for children
        TaskStatus.FAILED,
    },
    TaskStatus.PLANNED: {
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.DESIGNING,
        # is_plan_only tasks author a plan for a Schedule and terminate
        # without executing — see app/plan_states.py.
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
        TaskStatus.COMPLETED,  # children-wait strategy auto-complete
        TaskStatus.FAILED,
    },
    TaskStatus.COMPLETED: {
        TaskStatus.PENDING,
        TaskStatus.DESIGNING,  # follow-up message (plan mode)
        TaskStatus.RUNNING,  # follow-up message (auto mode)
        TaskStatus.WAITING,  # child resumed → parent reopened
    },
    TaskStatus.FAILED: {
        TaskStatus.PENDING,
        TaskStatus.DESIGNING,
        TaskStatus.RUNNING,  # follow-up (auto mode)
    },
}

# Named groups replacing inline tuples
STOPPABLE = {
    TaskStatus.DESIGNING,
    TaskStatus.PLANNED,
    TaskStatus.RUNNING,
    TaskStatus.WAITING,
}
ACTIVE = {TaskStatus.DESIGNING, TaskStatus.RUNNING}
WAKEABLE = {TaskStatus.WAITING}
RETRIABLE = {TaskStatus.FAILED, TaskStatus.PENDING, TaskStatus.COMPLETED}
STARTABLE = {TaskStatus.PENDING, TaskStatus.FAILED}
DESIGNABLE = {
    TaskStatus.PENDING,
    TaskStatus.FAILED,
    TaskStatus.PLANNED,
}


def can_transition(from_s: str, to_s: str) -> bool:
    """Check if a status transition is valid."""
    try:
        from_status = TaskStatus(from_s)
        to_status = TaskStatus(to_s)
    except ValueError:
        return False
    return to_status in TRANSITIONS.get(from_status, set())


def assert_transition(from_s: str, to_s: str) -> None:
    """Raise ValueError if the status transition is invalid."""
    if not can_transition(from_s, to_s):
        raise ValueError(f'Invalid task transition: {from_s!r} -> {to_s!r}')
