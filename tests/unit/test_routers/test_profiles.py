"""Tests for profiles router — ensures create/update return full profile.

These tests patch only the PROFILES_PATH to use a temp file, so the
real ProfileManager code runs end-to-end.  Before the fix, create and
update returned ``{'ok': True}`` instead of the full profile dict,
which made the frontend crash on ``Object.keys(profile.env)``.
"""

from pathlib import Path
from unittest import mock

from httpx import AsyncClient
import pytest


@pytest.fixture(autouse=True)
def _isolated_profiles(tmp_path: Path):
    """Redirect ProfileManager storage to a temp file per test."""
    profiles_file = tmp_path / 'profiles.json'
    with mock.patch('app.ai.profiles.PROFILES_PATH', profiles_file):
        yield


class TestProfilesRouter:
    """Tests for /api/profiles endpoints."""

    @pytest.mark.asyncio
    async def test_create_profile_returns_full_object(
        self, authenticated_client: AsyncClient
    ):
        """POST /api/profiles must return id, name, description, env.

        Before fix: returned ``{'ok': True}`` → frontend crash on
        ``Object.keys(undefined)``.
        """
        profile_data = {
            'id': 'test-kimi',
            'name': 'Kimi',
            'description': 'Kimi K2.5',
            'env': {'ANTHROPIC_BASE_URL': 'https://api.kimi.com'},
        }

        response = await authenticated_client.post(
            '/api/profiles', json=profile_data
        )

        assert response.status_code == 200
        data = response.json()
        # These assertions would FAIL with the old {'ok': True}
        assert data['id'] == 'test-kimi'
        assert data['name'] == 'Kimi'
        assert data['env'] == {'ANTHROPIC_BASE_URL': 'https://api.kimi.com'}
        assert 'description' in data

    @pytest.mark.asyncio
    async def test_create_profile_env_is_dict(
        self, authenticated_client: AsyncClient
    ):
        """The returned profile must have env as a dict, never None."""
        response = await authenticated_client.post(
            '/api/profiles',
            json={'id': 'no-env', 'name': 'Bare'},
        )

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data.get('env'), dict)

    @pytest.mark.asyncio
    async def test_update_profile_returns_full_object(
        self, authenticated_client: AsyncClient
    ):
        """PUT /api/profiles/{id} must return the updated profile."""
        # Create first
        await authenticated_client.post(
            '/api/profiles',
            json={
                'id': 'to-update',
                'name': 'Original',
                'env': {'KEY': 'old'},
            },
        )

        # Update
        response = await authenticated_client.put(
            '/api/profiles/to-update',
            json={'name': 'Updated', 'env': {'KEY': 'new'}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data['id'] == 'to-update'
        assert data['name'] == 'Updated'
        assert data['env'] == {'KEY': 'new'}

    @pytest.mark.asyncio
    async def test_create_profile_load_global_mcp_default_false(
        self, authenticated_client: AsyncClient
    ):
        """load_global_mcp defaults to False when not specified."""
        response = await authenticated_client.post(
            '/api/profiles',
            json={'id': 'no-mcp', 'name': 'NoMCP', 'env': {}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data['load_global_mcp'] is False

    @pytest.mark.asyncio
    async def test_create_profile_load_global_mcp_true(
        self, authenticated_client: AsyncClient
    ):
        """load_global_mcp can be set to True explicitly."""
        response = await authenticated_client.post(
            '/api/profiles',
            json={
                'id': 'with-mcp',
                'name': 'WithMCP',
                'env': {},
                'load_global_mcp': True,
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data['load_global_mcp'] is True

    @pytest.mark.asyncio
    async def test_update_profile_load_global_mcp(
        self, authenticated_client: AsyncClient
    ):
        """load_global_mcp can be toggled via update."""
        await authenticated_client.post(
            '/api/profiles',
            json={
                'id': 'toggle-mcp',
                'name': 'Toggle',
                'env': {},
                'load_global_mcp': False,
            },
        )
        response = await authenticated_client.put(
            '/api/profiles/toggle-mcp',
            json={'load_global_mcp': True},
        )
        assert response.status_code == 200
        assert response.json()['load_global_mcp'] is True
