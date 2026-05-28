"""Generic, deterministic gates that run at ``set_task_result``.

These are task-agnostic — every task's declared result is validated
the same way. The ad-tuning reviewer gates in ``bash_safety.py``
remain for the audit-specific structure checks; these gates target
problems that span every task (malformed markdown tables, prose in
the wrong language).

Each gate returns either ``None`` (pass / not applicable) or a
``GateDeny`` carrying a structured reason the agent will see when
``set_task_result`` raises 400. Per-task attempt counts live in
``_attempts`` so each gate stays soft: the agent gets one chance to
fix, then the second call is allowed through.
"""

from __future__ import annotations

from dataclasses import dataclass

# Module-level attempt counter, keyed by (task_id, gate_name). Lost
# on server restart, which is fine — the agent session would also be
# torn down.
_attempts: dict[tuple[str, str], int] = {}


@dataclass(frozen=True)
class GateDeny:
    """Reason a gate denied the result."""

    gate: str
    reason: str


def record_attempt(task_id: str, gate: str) -> int:
    """Increment and return the attempt count for (task_id, gate)."""
    key = (task_id, gate)
    _attempts[key] = _attempts.get(key, 0) + 1
    return _attempts[key]


def reset_attempts(task_id: str) -> None:
    """Drop all attempt counters for a task.

    Called by ``app/routers/tasks.py``:
    - after ``set_task_result`` persists a result (terminal success)
    - after ``delete_task`` removes a task

    Both paths guarantee no further gates will fire for that task,
    so the in-memory counters are dead weight from then on. This
    keeps the dict bounded over a long-running server.
    """
    for key in [k for k in _attempts if k[0] == task_id]:
        _attempts.pop(key, None)


# Cap: after this many denials per gate per task, the gate becomes a
# pass-through (the agent gets a warning, but the task finishes). The
# user explicitly asked for "let agent fix once but not mandatory" —
# 1 is the smallest value that still gives the agent feedback.
SOFT_GATE_MAX_DENIALS = 1
