"""Task-status reconciliation helpers for the agent session lifecycle.

Two structural invariants extracted here so the AgentSession stream
mixin and the terminal-state finalizer stay thin (and under the
per-file line limit). Both are pure decision helpers — the caller owns
the DB transaction and any SSE emit.

1. ``reconcile_streaming_run_status`` — "a task emitting session output
   is never QUEUED/PENDING". ``on_start`` normally flips the status
   before the session streams, but a missed transition (or a re-queue
   racing a live stream) can leave it stranded — which disables the
   input bar client-side and hides the task from the RUNNING-only stall
   reaper. The stream's ``_emit_message`` calls this on its first
   message as a backstop.

2. ``qa_followup_needs_input`` — an interactive-Q&A turn that ended with
   PROSE only is awaiting input, not done. See the function docstring.
"""

from datetime import UTC, datetime

from app.task_states import TaskStatus, can_transition


def reconcile_streaming_run_status(task) -> str | None:
    """If ``task`` is still QUEUED/PENDING while its session streams,
    advance it to the live status and return that status; else None.

    Plan mode's first emits are the design phase → DESIGNING; auto mode
    → RUNNING (also stamps ``started_at`` if unset). Returns None when
    no change is warranted (already RUNNING/DESIGNING, or the transition
    isn't valid), so the caller only emits an SSE update on a real flip.
    """
    if task.status not in (TaskStatus.QUEUED, TaskStatus.PENDING):
        return None
    target = TaskStatus.DESIGNING if task.plan_mode else TaskStatus.RUNNING
    if not can_transition(task.status, target):
        return None
    task.status = target
    if target == TaskStatus.RUNNING and not task.started_at:
        task.started_at = datetime.now(UTC).isoformat()
    return target


def qa_followup_needs_input(session) -> bool:
    """True when the agent asked a question, was answered, then ended
    its final segment with PROSE ONLY — no follow-up tool action, not
    even the explicit ``vibe_seller_set_task_result`` (itself a
    tool_use). That shape is "asking again / awaiting more input", not
    "done" — but the streaming-prose fallback already populated
    ``task.result``, so the empty-result waiting-parks (all gated on
    ``not task.result``) miss it and the task would silently COMPLETE
    mid-conversation.

    Structural, NOT a content heuristic: ``_asked_user_question`` (a
    question was asked this session) AND NOT ``_tool_use_since_answer``
    (no real, non-reflection tool ran after the last answer). A
    single-turn task that never asked completes normally, and FakeAgent
    sessions lack these attrs (getattr default False), so workflow
    finalizer tests are unaffected.
    """
    return bool(
        getattr(session, '_asked_user_question', False)
    ) and not getattr(session, '_tool_use_since_answer', False)


def agent_completed_with_result(session, task_result) -> bool:
    """True when the agent GENUINELY finished this session: it exited
    successfully, did real (non-question) tool work, and produced a
    substantive result.

    Guard for ``qa_followup_needs_input``. That heuristic parks a task in
    WAITING when an interactive-Q&A turn ends with prose only (no closing
    tool / ``set_task_result``) — assuming the agent is "asking again".
    But an agent that actually finished (e.g. generated the image and
    reported its path in prose, exiting cleanly with a 200+char result)
    hits the same shape and would be stranded in WAITING forever. When
    this returns True the finalizer COMPLETEs instead of parking. If the
    agent truly meant to ask again, the task still COMPLETEs but a user
    follow-up resumes it — recoverable, unlike a permanent WAITING.
    """
    return (
        bool(getattr(session, '_agent_success', False))
        and bool(getattr(session, '_had_tool_use', False))
        and bool(task_result and str(task_result).strip())
    )


def should_park_qa_followup(session, task_result) -> bool:
    """Finalizer decision: park an interactive-Q&A turn in WAITING because
    it ended prose-only (``qa_followup_needs_input``) — UNLESS the agent
    genuinely completed (``agent_completed_with_result``). Combines the
    two so the finalizer call site stays a one-liner.
    """
    return qa_followup_needs_input(session) and not agent_completed_with_result(
        session, task_result
    )
