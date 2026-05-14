"""Workflow tests for username/email login and profile management."""

import pytest

pytestmark = pytest.mark.workflow


class TestLoginByIdentifier:
    async def test_login_by_username(self, admin_client, admin_user):
        r = await admin_client.post(
            '/api/auth/login',
            json={
                'identifier': 'admin',
                'password': 'admin123',
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data['username'] == 'admin'
        assert 'username' in data

    async def test_login_by_email(self, admin_client, admin_user):
        r = await admin_client.post(
            '/api/auth/login',
            json={
                'identifier': 'admin@test.com',
                'password': 'admin123',
            },
        )
        assert r.status_code == 200
        assert r.json()['username'] == 'admin'

    async def test_login_wrong_password(self, admin_client, admin_user):
        r = await admin_client.post(
            '/api/auth/login',
            json={'identifier': 'admin', 'password': 'wrong'},
        )
        assert r.status_code == 401

    async def test_login_nonexistent(self, admin_client, admin_user):
        r = await admin_client.post(
            '/api/auth/login',
            json={
                'identifier': 'nobody',
                'password': 'admin123',
            },
        )
        assert r.status_code == 401


class TestUserCreateValidation:
    async def test_invalid_email_rejected(self, admin_client):
        r = await admin_client.post(
            '/api/users',
            json={
                'username': 'newuser',
                'email': 'notanemail',
                'password': 'pw123',
            },
        )
        assert r.status_code == 422

    async def test_duplicate_username_rejected(self, admin_client):
        r1 = await admin_client.post(
            '/api/users',
            json={
                'username': 'dupuser',
                'password': 'pw123',
            },
        )
        assert r1.status_code == 200
        r2 = await admin_client.post(
            '/api/users',
            json={
                'username': 'dupuser',
                'password': 'pw123',
            },
        )
        assert r2.status_code == 400

    async def test_email_matching_existing_username_rejected(
        self, admin_client
    ):
        """Cross-column uniqueness: new email == existing username."""
        # Create user1 with username 'crosstest'
        r1 = await admin_client.post(
            '/api/users',
            json={
                'username': 'crosstest',
                'password': 'pw123',
            },
        )
        assert r1.status_code == 200
        # Create user2 whose email matches user1's username
        r2 = await admin_client.post(
            '/api/users',
            json={
                'username': 'other',
                'email': 'crosstest',
                'password': 'pw123',
            },
        )
        # 'crosstest' is not a valid email, so schema rejects it
        assert r2.status_code == 422

    async def test_username_at_sign_rejected(self, admin_client):
        """Username containing @ is rejected at schema level."""
        r = await admin_client.post(
            '/api/users',
            json={
                'username': 'test@user',
                'password': 'pw',
            },
        )
        assert r.status_code == 422

    async def test_create_user_without_email(self, admin_client):
        r = await admin_client.post(
            '/api/users',
            json={
                'username': 'noemail',
                'password': 'pw123',
            },
        )
        assert r.status_code == 200
        assert r.json()['email'] is None


class TestProfileUpdate:
    async def test_update_username(self, admin_client, admin_user):
        # Update username
        r = await admin_client.patch(
            '/api/auth/me/profile',
            json={'username': 'newadmin'},
        )
        assert r.status_code == 200

        # Login with new username works
        r2 = await admin_client.post(
            '/api/auth/login',
            json={
                'identifier': 'newadmin',
                'password': 'admin123',
            },
        )
        assert r2.status_code == 200

    async def test_update_bad_email_rejected(self, admin_client):
        r = await admin_client.patch(
            '/api/auth/me/profile',
            json={'email': 'bad'},
        )
        assert r.status_code == 422

    async def test_username_with_at_rejected(self, admin_client):
        r = await admin_client.patch(
            '/api/auth/me/profile',
            json={'username': 'bad@name'},
        )
        assert r.status_code == 422
