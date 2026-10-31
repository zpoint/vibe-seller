"""Chat bot (group robot) configuration.

The table keeps its original name ``wecom_bots`` even though the model
now covers four platforms: vibe-seller has no alembic, so renaming a
SQLite table would orphan every existing install's rows, and bot ids
are referenced by saved skills and schedules — the ids MUST survive.
"""

from datetime import UTC, datetime
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ChatBot(Base):
    __tablename__ = 'wecom_bots'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    webhook_url: Mapped[str] = mapped_column(String, nullable=False)
    # Which platform this webhook belongs to. DERIVED from the URL
    # host by the server (app/notifiers/base.py:detect_platform) —
    # never taken from the client.
    platform: Mapped[str] = mapped_column(
        String, nullable=False, default='wecom'
    )
    # Optional signing secret (DingTalk 加签 / Feishu 签名校验).
    # Meaningless for WeCom and Slack, which the API rejects.
    secret: Mapped[str | None] = mapped_column(String, nullable=True)
    # Optional security keyword (DingTalk 自定义关键词 / Feishu 关键词).
    # NOT a secret: the UI shows it and the server appends it to any
    # outgoing message that doesn't already contain it. The agent never
    # sees it — keyword bots must work without the LLM knowing.
    keyword: Mapped[str | None] = mapped_column(String, nullable=True)
    created_by: Mapped[str] = mapped_column(
        String, ForeignKey('users.id'), nullable=False
    )
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
