"""Workflow tests for password change endpoint."""

import pytest

from app.models.app_settings import AppSettings

pytestmark = pytest.mark.workflow


class TestPasswordChange:
    async def test_admin_change_own_password(
        self,
        admin_client,
        admin_user,
        unauthed_client,
        override_async_session,
    ):
        """Admin changes password and can log in with new one."""
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='true'))
            await db.commit()

        r = await admin_client.patch(
            '/api/auth/me/password',
            json={
                'current_password': 'admin123',
                'new_password': 'newpass456',
            },
        )
        assert r.status_code == 200

        # Login with new password
        r = await unauthed_client.post(
            '/api/auth/login',
            json={
                'identifier': 'admin@test.com',
                'password': 'newpass456',
            },
        )
        assert r.status_code == 200

    async def test_wrong_current_password_rejected(
        self, admin_client, override_async_session
    ):
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='true'))
            await db.commit()

        r = await admin_client.patch(
            '/api/auth/me/password',
            json={
                'current_password': 'wrong',
                'new_password': 'newpass',
            },
        )
        assert r.status_code == 400

    async def test_member_change_own_password(
        self, member_client, override_async_session
    ):
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='true'))
            await db.commit()

        r = await member_client.patch(
            '/api/auth/me/password',
            json={
                'current_password': 'member123',
                'new_password': 'newmember',
            },
        )
        assert r.status_code == 200

    async def test_password_change_skips_current_when_auth_off(
        self, admin_client, override_async_session
    ):
        """When auth is off, current_password is not required."""
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='false'))
            await db.commit()

        r = await admin_client.patch(
            '/api/auth/me/password',
            json={'new_password': 'newpass'},
        )
        assert r.status_code == 200
