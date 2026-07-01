"""A no-auth server must never force login — even with a stale cookie.

JWT_SECRET is per-install (random, persisted). A fresh install gets a
new secret, so a leftover ``auth_token`` cookie from a prior install is
"invalid". The bug: get_current_user raised 401 on an invalid token
even when auth was disabled, so the first page load showed the login
page. When auth is off it must fall back to the default admin.
"""

from datetime import UTC, datetime

import pytest
import pytest_asyncio

from app.auth import COOKIE_NAME
from app.config import DEFAULT_USER_ID
from app.models.app_settings import AppSettings
from app.models.user import User

pytestmark = pytest.mark.workflow


@pytest_asyncio.fixture
async def default_admin(async_db_session) -> User:
    user = User(
        id=DEFAULT_USER_ID,
        username='admin',
        email='admin@example.com',
        password_hash='$2b$12$test_hash',
        role='admin',
        is_active=True,
        created_at=datetime.now(UTC).isoformat(),
    )
    async_db_session.add(user)
    await async_db_session.commit()
    return user


async def test_no_auth_no_cookie_returns_default_admin(
    async_client, default_admin
):
    # Baseline: auth disabled (default), no cookie → default admin.
    r = await async_client.get('/api/auth/me')
    assert r.status_code == 200
    assert r.json()['id'] == DEFAULT_USER_ID


async def test_no_auth_ignores_invalid_cookie(async_client, default_admin):
    # Stale/invalid cookie from a prior install (different JWT secret).
    # With auth disabled this must NOT force login — fall back to admin.
    async_client.cookies.set(COOKIE_NAME, 'stale.invalid.jwt')
    r = await async_client.get('/api/auth/me')
    assert r.status_code == 200
    assert r.json()['id'] == DEFAULT_USER_ID


async def test_auth_required_still_rejects_invalid_cookie(
    async_client, default_admin, async_db_session
):
    # Security regression guard: when auth IS required, an invalid token
    # must still 401 (the no-auth fallback must not weaken this).
    async_db_session.add(AppSettings(key='auth_required', value='true'))
    await async_db_session.commit()
    async_client.cookies.set(COOKIE_NAME, 'stale.invalid.jwt')
    r = await async_client.get('/api/auth/me')
    assert r.status_code == 401
