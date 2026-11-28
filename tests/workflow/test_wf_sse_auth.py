"""SSE follows the same session authentication boundary as private APIs."""

from datetime import UTC, datetime

from fastapi import Response
import pytest

from app.config import DEFAULT_USER_ID
from app.models.app_settings import AppSettings
from app.models.user import User
from app.routers import sse

pytestmark = pytest.mark.workflow


@pytest.fixture(autouse=True)
def immediate_event_source(monkeypatch):
    """Exercise routing/auth without waiting on the infinite event stream."""
    monkeypatch.setattr(
        sse,
        'EventSourceResponse',
        lambda _stream: Response(status_code=200),
    )


async def _require_auth(db) -> None:
    await db.merge(AppSettings(key='auth_required', value='true'))
    await db.commit()


async def test_sse_rejects_anonymous_when_auth_is_required(
    async_client, async_db_session
):
    await _require_auth(async_db_session)

    response = await async_client.get('/api/sse')

    assert response.status_code == 401


async def test_sse_accepts_authorized_user(
    authenticated_client, async_db_session
):
    await _require_auth(async_db_session)

    response = await authenticated_client.get('/api/sse')

    assert response.status_code == 200


async def test_sse_preserves_auth_disabled_mode(async_client, async_db_session):
    await db_default_admin(async_db_session)

    response = await async_client.get('/api/sse')

    assert response.status_code == 200


async def db_default_admin(db) -> None:
    db.add(
        User(
            id=DEFAULT_USER_ID,
            username='admin',
            email='admin@example.com',
            password_hash='$2b$12$test_hash',
            role='admin',
            is_active=True,
            created_at=datetime.now(UTC).isoformat(),
        )
    )
    await db.commit()
