"""An external MCP server an admin added for every task's agent.

The OTHER direction from ``/api/mcp`` (external agents calling Vibe
Seller with a ``vst_`` token): here Vibe Seller's own task agents are
the client, and the row is one entry of their ``mcpServers`` map.

``config_enc`` holds the server object exactly as Claude Code
receives it — already normalized by
:func:`app.custom_mcp.normalize_server`, so what is stored is what
launches — Fernet-encrypted (:func:`app.custom_mcp.seal`). It carries
the server's credentials (an ``Authorization`` header, an ``env``
key, a token in the URL): encrypted, a DB file that is copied or
shared, or ``--dev``'s statement-level SQL log, does not hand them
out. Every response masks them (:func:`app.custom_mcp_view.public_view`).
"""

from datetime import UTC, datetime
import uuid

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class CustomMcpServer(Base):
    __tablename__ = 'custom_mcp_servers'

    id: Mapped[str] = mapped_column(
        String, primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # The key in ``mcpServers`` — the agent sees its tools as
    # ``mcp__<name>__<tool>``. Unique: two rows with one name would
    # collapse into one entry of the launch config.
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    # seal()ed JSON of the normalized server object.
    config_enc: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str] = mapped_column(
        String, ForeignKey('users.id'), nullable=False
    )
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
    updated_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(UTC).isoformat()
    )
