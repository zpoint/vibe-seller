from datetime import UTC, datetime
import uuid

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ZiniaoAccount(Base):
    __tablename__ = 'ziniao_accounts'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    company: Mapped[str] = mapped_column(String, nullable=False)
    username: Mapped[str] = mapped_column(String, nullable=False)
    encrypted_password: Mapped[str] = mapped_column(
        String, nullable=False
    )  # Fernet-encrypted
    socket_port: Mapped[int] = mapped_column(
        Integer, nullable=False, default=16851
    )
    client_path: Mapped[str | None] = mapped_column(
        Text, nullable=True, default='ziniao'
    )
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
