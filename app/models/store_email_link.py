from datetime import UTC, datetime
import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StoreEmailLink(Base):
    __tablename__ = 'store_email_links'
    __table_args__ = (
        UniqueConstraint(
            'store_id',
            'email_account_id',
            name='uq_store_email',
        ),
    )

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    store_id: Mapped[str] = mapped_column(
        String, ForeignKey('stores.id'), nullable=False
    )
    email_account_id: Mapped[str] = mapped_column(
        String, ForeignKey('email_accounts.id'), nullable=False
    )
    # DEPRECATED: sync state now lives in per-account SQLite DB
    # (app/email/db.py sync_state table). Kept for backward compat.
    watermark_date: Mapped[str | None] = mapped_column(String, nullable=True)
    last_polled_at: Mapped[str | None] = mapped_column(String, nullable=True)
    seen_message_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
