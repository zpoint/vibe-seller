from datetime import UTC, datetime
import uuid

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Store(Base):
    __tablename__ = 'stores'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    browser_backend: Mapped[str] = mapped_column(
        String, nullable=False, default='chrome'
    )
    browser_config: Mapped[str] = mapped_column(
        Text, nullable=False, default='{}'
    )
    ziniao_account_id: Mapped[str | None] = mapped_column(String, nullable=True)
    browser_oauth: Mapped[str | None] = mapped_column(String, nullable=True)
    platforms: Mapped[str] = mapped_column(
        Text, nullable=False, default='["amazon"]'
    )
    countries: Mapped[str] = mapped_column(
        Text, nullable=False, default='["SA"]'
    )
    platform_countries: Mapped[str] = mapped_column(
        Text, nullable=False, default='{}'
    )
    config: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
