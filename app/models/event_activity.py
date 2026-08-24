from datetime import UTC, datetime
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.text_utils import SafeText


class EventActivity(Base):
    __tablename__ = 'event_activities'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    event_id: Mapped[str] = mapped_column(
        String, ForeignKey('events.id'), nullable=False
    )
    user_id: Mapped[str | None] = mapped_column(
        String, ForeignKey('users.id'), nullable=True
    )
    actor_type: Mapped[str] = mapped_column(
        String, nullable=False, default='system'
    )  # user, ai, system, channel
    action: Mapped[str] = mapped_column(
        String, nullable=False
    )  # created, status_changed, note_added, etc.
    content: Mapped[str] = mapped_column(SafeText, nullable=False)
    extra_data: Mapped[str | None] = mapped_column(
        SafeText, nullable=True
    )  # JSON
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
