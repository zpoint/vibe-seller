from datetime import UTC, datetime
import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.text_utils import SafeText


class Task(Base):
    __tablename__ = 'tasks'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    parent_task_id: Mapped[str | None] = mapped_column(
        String, ForeignKey('tasks.id'), nullable=True
    )
    store_id: Mapped[str | None] = mapped_column(
        String, ForeignKey('stores.id'), nullable=True
    )
    schedule_id: Mapped[str | None] = mapped_column(
        String,
        ForeignKey('schedules.id', ondelete='SET NULL'),
        nullable=True,
        index=True,
    )
    template_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str] = mapped_column(
        String, ForeignKey('users.id'), nullable=False
    )
    assigned_to: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    platform: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default='pending'
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    input_data: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    plan: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    plan_history: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    # DERIVED VIEW of the outcome — what the user sees. Written ONLY
    # by ``app.task_outcome.apply_outcome``; never an input to
    # resolution. Keeping it output-only is what stops a prose fallback
    # that has been materialised here from later being mistaken for an
    # accepted deliverable and outranking fresher prose.
    result: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    # A submission that cleared every gate. Written only by the result
    # endpoint on accept — the top of the precedence order.
    accepted_result: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    # The last thing the agent SUBMITTED, accepted or not. A gate
    # refusal used to raise before `result` was assigned, so N refusals
    # left the run holding nothing and the finalizer had only chat
    # narration to fall back on. Submission and verdict are separate
    # facts: this is written on every submit, the verdict lands in
    # `review_gaps`, and `result` is set only once a verdict accepts.
    submitted_result: Mapped[str | None] = mapped_column(
        SafeText, nullable=True
    )
    # JSON list of unmet gaps from the most recent refusal; NULL once a
    # submission is accepted. Becomes the caveat list on an INCOMPLETE
    # outcome so a partial deliverable ships with its own gap report.
    review_gaps: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    # How many times the agent submitted this task's result. Pure
    # observability: a high count with no `result` is the signature of
    # a gate the agent cannot satisfy.
    submission_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    # Streamed assistant prose, saved at end of turn. A FALLBACK
    # deliverable — legitimate when the chat output IS the answer
    # (a lookup, a question), never allowed to outrank a real
    # submission. Precedence lives in app/task_outcome.py.
    transcript_tail: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    todos: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    error: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    error_category: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    plan_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Plan-only tasks author a plan for a Schedule and terminate at
    # COMPLETED without ever entering RUNNING. See plan_states.py.
    is_plan_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    # Snapshot of Schedule.plan_version at fire time (audit only —
    # NOT used to cascade edits onto in-flight child tasks).
    plan_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skip_reflection: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    wait_condition: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    batch_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )
    # The single "finalize" task of a fanout batch: created by
    # finalize_reaper after every per-store child is terminal. It
    # carries the batch's batch_id but no store_id. The explicit flag
    # (rather than "store_id IS NULL") disambiguates it from a
    # two_phase L2 prereq task, which is ALSO store_id=None+batch_id.
    # See app/scheduler/finalize_reaper.py.
    is_finalize: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    ai_profile_id: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default='default'
    )
    # One of app.telemetry_events.TaskSource.
    source: Mapped[str | None] = mapped_column(String, nullable=True)
    # Accumulated from stream-json result events.
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    num_turns: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # User re-runs of this task.
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Parked-time accounting; active = duration - paused.
    paused_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    paused_since: Mapped[str | None] = mapped_column(String, nullable=True)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )


# Parked-time clock

PARKED_STATES = frozenset({'waiting', 'planned'})


def _seconds_between(start_iso: str, end_iso: str) -> int:
    try:
        start = datetime.fromisoformat(start_iso)
        end = datetime.fromisoformat(end_iso)
    except (ValueError, TypeError):
        return 0
    return max(0, int((end - start).total_seconds()))


def open_pause(task: 'Task', at_iso: str | None = None) -> None:
    """Start the pause clock (idempotent)."""
    if not task.paused_since:
        task.paused_since = at_iso or datetime.now(UTC).isoformat()


def close_pause(task: 'Task', at_iso: str | None = None) -> None:
    """Fold the open pause into paused_seconds."""
    if not task.paused_since:
        return
    end_iso = at_iso or datetime.now(UTC).isoformat()
    task.paused_seconds = (task.paused_seconds or 0) + _seconds_between(
        task.paused_since, end_iso
    )
    task.paused_since = None


def paused_total(task: 'Task', at_iso: str | None = None) -> int:
    """paused_seconds plus any open pause, as of `at_iso`."""
    total = task.paused_seconds or 0
    if task.paused_since:
        total += _seconds_between(
            task.paused_since, at_iso or datetime.now(UTC).isoformat()
        )
    return total


@event.listens_for(Task.status, 'set')
def _park_clock(target: 'Task', value, oldvalue, _initiator) -> None:
    new = str(value) if value is not None else ''
    old = str(oldvalue) if oldvalue is not None else ''
    if new == old:
        return
    if new in PARKED_STATES:
        open_pause(target)
    elif old in PARKED_STATES:
        close_pause(target)
