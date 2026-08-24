from datetime import UTC, datetime
import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.text_utils import SafeText


class TaskStep(Base):
    __tablename__ = 'task_steps'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(
        String, ForeignKey('tasks.id'), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    action_type: Mapped[str] = mapped_column(String, nullable=False)
    action_data: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    status: Mapped[str] = mapped_column(
        String, nullable=False, default='pending'
    )
    screenshot_id: Mapped[str | None] = mapped_column(String, nullable=True)
    result: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    error: Mapped[str | None] = mapped_column(SafeText, nullable=True)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
