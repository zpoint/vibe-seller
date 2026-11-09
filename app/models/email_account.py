from datetime import UTC, datetime
import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EmailAccount(Base):
    __tablename__ = 'email_accounts'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    encrypted_password: Mapped[str] = mapped_column(
        String, nullable=False
    )  # Fernet-encrypted
    imap_host: Mapped[str] = mapped_column(String, nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, default=993)
    use_ssl: Mapped[bool] = mapped_column(Boolean, default=True)
    smtp_host: Mapped[str | None] = mapped_column(String, nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_use_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    # Whether agents may SEND from this mailbox. Off by default — also
    # for rows that predate the column (app/database.py); the user turns
    # it on per mailbox in Settings → Email. Enforced in
    # app/email/sender.py, the one function that opens SMTP.
    allow_send: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default='0'
    )
    created_by: Mapped[str] = mapped_column(
        String, ForeignKey('users.id'), nullable=False
    )
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
