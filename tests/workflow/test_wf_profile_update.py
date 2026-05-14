"""Workflow tests for profile update endpoint."""

import pytest

from app.models.app_settings import AppSettings

pytestmark = pytest.mark.workflow


class TestProfileUpdate:
    async def test_admin_change_email(
        self, admin_client, override_async_session
    ):
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='true'))
            await db.commit()

        r = await admin_client.patch(
            '/api/auth/me/profile',
            json={'email': 'newemail@test.com'},
        )
        assert r.status_code == 200

        # Verify email changed
        r = await admin_client.get('/api/auth/me')
        assert r.json()['email'] == 'newemail@test.com'

    async def test_member_cannot_change_email(
        self, member_client, override_async_session
    ):
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='true'))
            await db.commit()

        r = await member_client.patch(
            '/api/auth/me/profile',
            json={'email': 'hack@test.com'},
        )
        assert r.status_code == 403

    async def test_admin_change_username_and_email(
        self, admin_client, override_async_session
    ):
        """Admin can change both username and email."""
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='true'))
            await db.commit()

        r = await admin_client.patch(
            '/api/auth/me/profile',
            json={
                'username': 'newadmin',
                'email': 'changed@test.com',
            },
        )
        assert r.status_code == 200

        r = await admin_client.get('/api/auth/me')
        assert r.json()['username'] == 'newadmin'
        assert r.json()['email'] == 'changed@test.com'
