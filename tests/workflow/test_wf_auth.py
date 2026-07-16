"""Workflow tests for authentication and admin user management."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.auth import ALGORITHM
from app.config import JWT_SECRET
from app.models.user import User
from app.password import hash_password

pytestmark = pytest.mark.workflow


class TestLogin:
    async def test_login_success_sets_cookie(self, unauthed_client, admin_user):
        r = await unauthed_client.post(
            '/api/auth/login',
            json={
                'identifier': 'admin@test.com',
                'password': 'admin123',
            },
        )
        assert r.status_code == 200
        assert 'auth_token' in r.cookies
        body = r.json()
        assert body['id'] == admin_user.id
        assert body['username'] == 'admin'

    async def test_login_wrong_password_401(self, unauthed_client, admin_user):
        r = await unauthed_client.post(
            '/api/auth/login',
            json={
                'identifier': 'admin@test.com',
                'password': 'wrong',
            },
        )
        assert r.status_code == 401

    async def test_login_inactive_user_401(
        self, unauthed_client, override_async_session
    ):
        async with override_async_session() as db:
            user = User(
                username='inactive',
                email='inactive@test.com',
                password_hash=hash_password('pass123'),
                role='member',
                is_active=False,
            )
            db.add(user)
            await db.commit()

        r = await unauthed_client.post(
            '/api/auth/login',
            json={
                'identifier': 'inactive@test.com',
                'password': 'pass123',
            },
        )
        assert r.status_code == 401

    async def test_login_ai_bot_rejected(
        self, unauthed_client, override_async_session
    ):
        async with override_async_session() as db:
            user = User(
                username='bot',
                email='bot@test.com',
                password_hash=hash_password('bot123'),
                role='ai_bot',
                is_active=True,
            )
            db.add(user)
            await db.commit()

        r = await unauthed_client.post(
            '/api/auth/login',
            json={'identifier': 'bot@test.com', 'password': 'bot123'},
        )
        assert r.status_code == 401


class TestMe:
    async def test_me_returns_user(self, admin_client, admin_user):
        r = await admin_client.get('/api/auth/me')
        assert r.status_code == 200
        assert r.json()['id'] == admin_user.id
        assert r.json()['email'] == 'admin@test.com'

    async def test_me_without_auth_401(self, unauthed_client):
        r = await unauthed_client.get('/api/auth/me')
        assert r.status_code == 401


class TestLogout:
    async def test_logout_clears_cookie(self, admin_client):
        r = await admin_client.post('/api/auth/logout')
        assert r.status_code == 200
        assert r.json() == {'ok': True}


class TestRefresh:
    """Sliding-session refresh — rolls the cookie forward on activity."""

    async def test_refresh_rolls_cookie(self, admin_client):
        r = await admin_client.post('/api/auth/refresh')
        assert r.status_code == 200
        assert r.json() == {'ok': True}
        # A fresh session cookie is re-issued on the response.
        assert 'auth_token' in r.cookies

    async def test_refresh_without_auth_401(self, unauthed_client):
        """No cookie + auth required → 401 (drives the login redirect)."""
        r = await unauthed_client.post('/api/auth/refresh')
        assert r.status_code == 401

    async def test_refresh_expired_token_401(self, unauthed_client, admin_user):
        """An expired session cookie is rejected, not silently accepted.

        This is the contract the frontend heartbeat relies on: once the
        24h window lapses, the next refresh/heartbeat 401s so the client
        is bounced to login instead of hanging on a dead session.
        """
        expired = jwt.encode(
            {
                'sub': admin_user.id,
                'role': 'admin',
                'exp': datetime.now(UTC) - timedelta(minutes=1),
            },
            JWT_SECRET,
            algorithm=ALGORITHM,
        )
        unauthed_client.cookies.set('auth_token', expired)
        r = await unauthed_client.post('/api/auth/refresh')
        assert r.status_code == 401


class TestUserCrudAuthBoundary:
    async def test_unauthenticated_cannot_create_user(self, unauthed_client):
        """POST /api/users without auth cookie returns 401."""
        r = await unauthed_client.post(
            '/api/users',
            json={'username': 'hacker', 'password': 'pw123'},
        )
        assert r.status_code == 401

    async def test_unauthenticated_cannot_list_users(self, unauthed_client):
        r = await unauthed_client.get('/api/users')
        assert r.status_code == 401

    async def test_unauthenticated_cannot_delete_user(self, unauthed_client):
        r = await unauthed_client.delete('/api/users/fake-id')
        assert r.status_code == 401

    async def test_member_cannot_create_user(self, member_client):
        r = await member_client.post(
            '/api/users',
            json={'username': 'sneaky', 'password': 'pw123'},
        )
        assert r.status_code == 403

    async def test_member_cannot_list_users(self, member_client):
        r = await member_client.get('/api/users')
        assert r.status_code == 403

    async def test_member_cannot_delete_user(self, member_client):
        r = await member_client.delete('/api/users/fake-id')
        assert r.status_code == 403


class TestAdminUserCrud:
    async def test_create_user(self, admin_client):
        r = await admin_client.post(
            '/api/users',
            json={
                'username': 'newuser',
                'email': 'new@test.com',
                'password': 'newpass',
                'role': 'member',
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data['username'] == 'newuser'
        assert data['email'] == 'new@test.com'
        assert data['role'] == 'member'
        assert data['is_active'] is True
        assert 'id' in data

    async def test_new_user_inherits_admin_profile(
        self, admin_client, override_async_session, admin_user
    ):
        """New user gets the creating admin's default_profile_id."""
        # Change admin's default profile
        async with override_async_session() as db:
            admin = await db.get(User, admin_user.id)
            admin.default_profile_id = 'custom-prof'
            await db.commit()

        r = await admin_client.post(
            '/api/users',
            json={'username': 'inheritor', 'password': 'pw123'},
        )
        assert r.status_code == 200
        assert r.json()['default_profile_id'] == 'custom-prof'

    async def test_create_user_without_email(self, admin_client):
        r = await admin_client.post(
            '/api/users',
            json={'username': 'noemail', 'password': 'pw123'},
        )
        assert r.status_code == 200
        assert r.json()['email'] is None

    async def test_list_users(self, admin_client):
        await admin_client.post(
            '/api/users',
            json={'username': 'listme', 'password': 'pw123'},
        )
        r = await admin_client.get('/api/users')
        assert r.status_code == 200
        usernames = [u['username'] for u in r.json()]
        assert 'listme' in usernames

    async def test_update_user_role(self, admin_client):
        r = await admin_client.post(
            '/api/users',
            json={
                'username': 'roletest',
                'password': 'pw123',
                'role': 'member',
            },
        )
        uid = r.json()['id']

        r = await admin_client.put(
            f'/api/users/{uid}',
            json={'role': 'admin'},
        )
        assert r.status_code == 200
        assert r.json()['role'] == 'admin'

    async def test_update_user_password(self, admin_client, unauthed_client):
        r = await admin_client.post(
            '/api/users',
            json={'username': 'pwtest', 'password': 'old123'},
        )
        uid = r.json()['id']

        # Update password
        r = await admin_client.put(
            f'/api/users/{uid}',
            json={'password': 'new456'},
        )
        assert r.status_code == 200

        # Login with new password works
        r = await unauthed_client.post(
            '/api/auth/login',
            json={'identifier': 'pwtest', 'password': 'new456'},
        )
        assert r.status_code == 200

    async def test_update_user_username(self, admin_client):
        r = await admin_client.post(
            '/api/users',
            json={'username': 'oldname', 'password': 'pw123'},
        )
        uid = r.json()['id']

        r = await admin_client.put(
            f'/api/users/{uid}',
            json={'username': 'newname'},
        )
        assert r.status_code == 200
        assert r.json()['username'] == 'newname'

    async def test_delete_user(self, admin_client):
        r = await admin_client.post(
            '/api/users',
            json={'username': 'deleteme', 'password': 'pw123'},
        )
        uid = r.json()['id']

        # Delete
        r = await admin_client.delete(f'/api/users/{uid}')
        assert r.status_code == 200

        # User gone from list
        r = await admin_client.get('/api/users')
        usernames = [u['username'] for u in r.json()]
        assert 'deleteme' not in usernames

    async def test_recreate_after_delete(self, admin_client):
        """Can recreate a user with same username after deletion."""
        r = await admin_client.post(
            '/api/users',
            json={'username': 'reuse', 'password': 'pw123'},
        )
        uid = r.json()['id']

        await admin_client.delete(f'/api/users/{uid}')

        # Recreate with same username succeeds
        r = await admin_client.post(
            '/api/users',
            json={'username': 'reuse', 'password': 'pw456'},
        )
        assert r.status_code == 200
        assert r.json()['id'] != uid

    async def test_cannot_delete_self(self, admin_client, admin_user):
        r = await admin_client.delete(f'/api/users/{admin_user.id}')
        assert r.status_code == 400
        assert 'yourself' in r.json()['detail'].lower()

    async def test_cannot_delete_ai_bot(
        self, admin_client, override_async_session
    ):
        async with override_async_session() as db:
            bot = User(
                username='ai_bot',
                email='ai@test.local',
                password_hash='disabled',
                role='ai_bot',
                is_active=True,
            )
            db.add(bot)
            await db.commit()
            bot_id = bot.id

        r = await admin_client.delete(f'/api/users/{bot_id}')
        assert r.status_code == 400

    async def test_deleted_user_cannot_login(
        self, admin_client, unauthed_client
    ):
        r = await admin_client.post(
            '/api/users',
            json={
                'username': 'gonuser',
                'password': 'pw123',
                'email': 'gone@test.com',
            },
        )
        uid = r.json()['id']

        await admin_client.delete(f'/api/users/{uid}')

        # Login fails
        r = await unauthed_client.post(
            '/api/auth/login',
            json={'identifier': 'gonuser', 'password': 'pw123'},
        )
        assert r.status_code == 401

    async def test_duplicate_username_rejected(self, admin_client):
        await admin_client.post(
            '/api/users',
            json={'username': 'taken', 'password': 'pw123'},
        )
        r = await admin_client.post(
            '/api/users',
            json={'username': 'taken', 'password': 'pw456'},
        )
        assert r.status_code == 400
