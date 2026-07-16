"""Unit tests for JWT token TTL semantics.

The interactive user-session TTL must be short (24h, sliding) while the
service-token default (ai_bot wrapper auto-start, `system`) stays long so
scheduled / long-running browser work keeps authenticating. These two
must not be coupled — see app/auth.py.
"""

from datetime import UTC, datetime

import jwt
import pytest

from app.auth import (
    ALGORITHM,
    SERVICE_TOKEN_EXPIRE,
    USER_SESSION_EXPIRE,
    create_token,
)
from app.config import JWT_SECRET

pytestmark = pytest.mark.unit


def _exp(token: str) -> datetime:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
    return datetime.fromtimestamp(payload['exp'], tz=UTC)


def test_user_session_ttl_is_24h():
    token = create_token('u1', 'admin', expires_delta=USER_SESSION_EXPIRE)
    ttl = _exp(token) - datetime.now(UTC)
    # ~24h, allowing a little slack for test execution time.
    assert USER_SESSION_EXPIRE.total_seconds() == 24 * 3600
    assert 23 * 3600 < ttl.total_seconds() <= 24 * 3600 + 60


def test_service_token_default_outlives_user_session():
    """create_token's default is the long service TTL, not the 24h one —
    so shortening login sessions doesn't shorten wrapper/bot tokens."""
    token = create_token('bot', 'ai_bot')
    ttl = _exp(token) - datetime.now(UTC)
    assert SERVICE_TOKEN_EXPIRE > USER_SESSION_EXPIRE
    assert ttl.total_seconds() > USER_SESSION_EXPIRE.total_seconds()
    # Defaults to the service TTL (7 days).
    assert (7 * 24 - 1) * 3600 < ttl.total_seconds() <= 7 * 24 * 3600 + 60
