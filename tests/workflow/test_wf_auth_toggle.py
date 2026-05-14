"""Workflow tests for auth toggle and settings API."""

import pytest

from app.config import DEFAULT_USER_ID
from app.models.app_settings import AppSettings
from app.models.user import User
from app.password import hash_password

pytestmark = pytest.mark.workflow


class TestAuthStatus:
    async def test_auth_status_public(
        self, unauthed_client, override_async_session
    ):
        """GET /api/auth/status works without auth cookie."""
        # Seed auth_required=false
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='false'))
            await db.commit()

        r = await unauthed_client.get('/api/auth/status')
        assert r.status_code == 200
        assert r.json()['auth_required'] is False


class TestAuthBypass:
    async def test_auth_disabled_skips_login(
        self, unauthed_client, override_async_session
    ):
        """When auth is off, /api/auth/me returns default admin."""
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='false'))
            db.add(
                User(
                    id=DEFAULT_USER_ID,
                    username='admin',
                    email='admin@test.com',
                    password_hash=hash_password('admin'),
                    role='admin',
                )
            )
            await db.commit()

        r = await unauthed_client.get('/api/auth/me')
        assert r.status_code == 200
        assert r.json()['id'] == DEFAULT_USER_ID

    async def test_auth_enabled_requires_login(
        self, unauthed_client, override_async_session
    ):
        """When auth is on, /api/auth/me without cookie → 401."""
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='true'))
            await db.commit()

        r = await unauthed_client.get('/api/auth/me')
        assert r.status_code == 401


class TestToggle:
    async def test_toggle_auth_on_off(
        self, admin_client, unauthed_client, override_async_session
    ):
        """Admin can toggle auth via PUT /api/settings."""
        # Start with auth off
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='false'))
            await db.commit()

        # Turn on
        r = await admin_client.put(
            '/api/settings',
            json={'auth_required': 'true'},
        )
        assert r.status_code == 200

        # Verify unauthed is now blocked
        r = await unauthed_client.get('/api/auth/me')
        assert r.status_code == 401

        # Turn off
        r = await admin_client.put(
            '/api/settings',
            json={'auth_required': 'false'},
        )
        assert r.status_code == 200

    async def test_member_cannot_update_settings(
        self, member_client, override_async_session
    ):
        """Member cannot PUT /api/settings."""
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='true'))
            await db.commit()

        r = await member_client.put(
            '/api/settings',
            json={'auth_required': 'false'},
        )
        assert r.status_code == 403
