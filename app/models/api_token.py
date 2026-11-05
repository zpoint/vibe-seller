"""Personal API tokens — revocable credentials for external agents.

A JWT cannot be revoked without rotating the install-wide ``JWT_SECRET``,
and rotating it logs every human out and breaks the ``ai_bot`` JWT baked
into every store wrapper. External agents need a credential the operator
can cut off on its own, so those credentials are rows in this table.

Only the SHA-256 hash of a token is stored. The plaintext is returned
once, in the response that created it, and never again — which is why
``prefix`` exists: it is the only part of the token safe to keep.
"""

from datetime import UTC, datetime
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ApiToken(Base):
    """One personal access token, owned by one user."""

    __tablename__ = 'api_tokens'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey('users.id'), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    # SHA-256 hex of the full plaintext token. Unique — two tokens never
    # share a hash, so the lookup below is unambiguous.
    token_hash: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True
    )
    # First ~12 chars of the plaintext, for display only.
    prefix: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    # Stamped (at most once a minute) each time the token authenticates.
    last_used_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Null = never expires.
    expires_at: Mapped[str | None] = mapped_column(String, nullable=True)
    # Set when the owner revokes it. The row is kept (not deleted) so a
    # revoked token can never be resurrected by a stale client.
    revoked_at: Mapped[str | None] = mapped_column(String, nullable=True)
