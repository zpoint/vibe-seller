from datetime import UTC, datetime
import uuid

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class BrowserSession(Base):
    __tablename__ = 'browser_sessions'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    store_id: Mapped[str] = mapped_column(
        String, ForeignKey('stores.id'), unique=True, nullable=False
    )
    cdp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proxy_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chrome_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default='idle')
    current_platform: Mapped[str | None] = mapped_column(String, nullable=True)
    current_country: Mapped[str | None] = mapped_column(String, nullable=True)
    current_url: Mapped[str | None] = mapped_column(String, nullable=True)
    active_tab_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
