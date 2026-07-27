from datetime import UTC, datetime
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


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
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    platform: Mapped[str | None] = mapped_column(String, nullable=True)
    country: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default='pending'
    )
    priority: Mapped[int] = mapped_column(Integer, default=0)
    input_data: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_history: Mapped[str | None] = mapped_column(Text, nullable=True)
    # DERIVED VIEW of the outcome — what the user sees. Written ONLY
    # by ``app.task_outcome.apply_outcome``; never an input to
    # resolution. Keeping it output-only is what stops a prose fallback
    # that has been materialised here from later being mistaken for an
    # accepted deliverable and outranking fresher prose.
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A submission that cleared every gate. Written only by the result
    # endpoint on accept — the top of the precedence order.
    accepted_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # The last thing the agent SUBMITTED, accepted or not. A gate
    # refusal used to raise before `result` was assigned, so N refusals
    # left the run holding nothing and the finalizer had only chat
    # narration to fall back on. Submission and verdict are separate
    # facts: this is written on every submit, the verdict lands in
    # `review_gaps`, and `result` is set only once a verdict accepts.
    submitted_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # JSON list of unmet gaps from the most recent refusal; NULL once a
    # submission is accepted. Becomes the caveat list on an INCOMPLETE
    # outcome so a partial deliverable ships with its own gap report.
    review_gaps: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    transcript_tail: Mapped[str | None] = mapped_column(Text, nullable=True)
    todos: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    wait_condition: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
