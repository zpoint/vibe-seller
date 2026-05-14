from datetime import UTC, datetime
import uuid

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = 'users'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(
        String, unique=True, nullable=True
    )
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, default='member')
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    plan_mode_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    debug_mode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    default_profile_id: Mapped[str] = mapped_column(
        String(50), nullable=False, default='default'
    )
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
