from datetime import UTC, datetime
import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Event(Base):
    __tablename__ = 'events'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    channel_message_id: Mapped[str | None] = mapped_column(
        String, nullable=True
    )
    channel_type: Mapped[str | None] = mapped_column(String, nullable=True)
    store_id: Mapped[str | None] = mapped_column(
        String, ForeignKey('stores.id'), nullable=True
    )
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    event_date: Mapped[str | None] = mapped_column(String, nullable=True)
    deadline: Mapped[str | None] = mapped_column(String, nullable=True)
    platform: Mapped[str | None] = mapped_column(String, nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default='draft')
    sync_backend: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # New tracking fields
    case_id: Mapped[str | None] = mapped_column(String, nullable=True)
    assignees: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON array of user IDs
    created_by: Mapped[str | None] = mapped_column(
        String, ForeignKey('users.id'), nullable=True
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )  # 0=normal, 1=high, 2=urgent
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
