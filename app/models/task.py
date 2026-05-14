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
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
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
