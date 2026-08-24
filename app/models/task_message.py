"""Task messages model: stores agent chat history (user prompts + assistant responses)."""

from datetime import UTC, datetime
import uuid

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.text_utils import SafeText


class TaskMessage(Base):
    __tablename__ = 'task_messages'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    role: Mapped[str] = mapped_column(
        String, nullable=False
    )  # user, assistant, system, tool_use, result, delta
    content: Mapped[str] = mapped_column(SafeText, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profile_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
