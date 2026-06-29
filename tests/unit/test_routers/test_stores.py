"""Tests for stores router."""

import json
from unittest import mock
import uuid

from httpx import AsyncClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.browser.ziniao_utils import ZiniaoNormalModeError
from app.models.store import Store
from app.models.user import User
from app.models.ziniao_account import ZiniaoAccount
from app.utils.crypto import encrypt_password


class TestStoresRouter:
    """Tests for /api/stores endpoints."""

    @pytest.mark.asyncio
    async def test_create_store(self, authenticated_client: AsyncClient):
        """Test POST /api/stores creates a store."""
        store_data = {
            'name': 'Test Store',
            'browser_backend': 'chrome',
            'browser_config': {'headless': True},
            'platforms': ['amazon'],
            'countries': ['US'],
        }

        with mock.patch('app.routers.stores.workspace_manager') as mock_ws:
            mock_ws.root = mock.MagicMock()
            mock_ws.root.__truediv__ = mock.MagicMock(
                return_value=mock.MagicMock()
            )

            response = await authenticated_client.post(
                '/api/stores', json=store_data
            )

        assert response.status_code == 200
        data = response.json()
        assert data['name'] == 'Test Store'
        assert data['browser_backend'] == 'chrome'
        assert data['browser_config'] == {'headless': True}
        assert data['platforms'] == ['amazon']
        assert data['countries'] == ['US']
        assert 'id' in data

    @pytest.mark.asyncio
    async def test_list_stores(
        self, authenticated_client: AsyncClient, test_store: Store
    ):
        """Test GET /api/stores lists stores."""
        response = await authenticated_client.get('/api/stores')

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        # Find our test store in the list
        store_ids = [s['id'] for s in data]
        assert test_store.id in store_ids

    @pytest.mark.asyncio
    async def test_get_store(
        self, authenticated_client: AsyncClient, test_store: Store
    ):
        """Test GET /api/stores/{id} returns store."""
        response = await authenticated_client.get(
            f'/api/stores/{test_store.id}'
        )

        assert response.status_code == 200
        data = response.json()
        assert data['id'] == test_store.id
        assert data['name'] == test_store.name
        assert data['browser_backend'] == test_store.browser_backend

    @pytest.mark.asyncio
    async def test_get_store_not_found(self, authenticated_client: AsyncClient):
        """Test GET /api/stores/{id} returns 404 for non-existent store."""
        response = await authenticated_client.get('/api/stores/non-existent-id')

        assert response.status_code == 404
        assert response.json()['detail'] == 'Store not found'

    @pytest.mark.asyncio
    async def test_delete_store(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
        test_user: User,
    ):
        """Test DELETE /api/stores/{id} deletes store."""
        # Create a store to delete

        store = Store(
            name='Delete Me',
            browser_backend='chrome',
            browser_config=json.dumps({}),
        )
        async_db_session.add(store)
        await async_db_session.commit()

        # Delete it
        response = await authenticated_client.delete(f'/api/stores/{store.id}')
        assert response.status_code == 200
        assert response.json() == {'ok': True}

        # Verify it's gone
        get_response = await authenticated_client.get(f'/api/stores/{store.id}')
        assert get_response.status_code == 404

    @pytest.mark.asyncio
    async def test_create_store_requires_auth(self, async_client: AsyncClient):
        """Test store creation requires authentication."""
        store_data = {
            'name': 'Test Store',
            'browser_backend': 'chrome',
            'browser_config': {},
        }

        response = await async_client.post('/api/stores', json=store_data)

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_list_stores_requires_auth(self, async_client: AsyncClient):
        """Test listing stores requires authentication."""
        response = await async_client.get('/api/stores')

        assert response.status_code == 401


class TestBrowserStartForce:
    """POST /api/stores/{id}/browser/start with optional ?force=1.

    Covers the agent-side opt-in recovery when Ziniao is sitting in
    normal (non-WebDriver) mode. Default behavior (no force) must
    keep the original 500 so the UI's Force Restart button stays the
    sole entry point for the kill-and-relaunch path.
    """

    async def _make_ziniao_store(self, async_db_session: AsyncSession) -> Store:
        account = ZiniaoAccount(
            id=str(uuid.uuid4()),
            name='test-account',
            company='c',
            username='u',
            encrypted_password=encrypt_password('p'),
            socket_port=16851,
        )
        async_db_session.add(account)
        store = Store(
            id=str(uuid.uuid4()),
            name='Z Store',
            browser_backend='ziniao',
            browser_config='{}',
            ziniao_account_id=account.id,
            platforms='["amazon"]',
            countries='["US"]',
        )
        async_db_session.add(store)
        await async_db_session.commit()
        return store

    @pytest.mark.asyncio
    async def test_force_false_propagates_normal_mode_error(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        """Without ?force, ZiniaoNormalModeError still becomes 500."""
        store = await self._make_ziniao_store(async_db_session)
        with mock.patch(
            'app.routers.stores.browser_manager.start_session',
            side_effect=ZiniaoNormalModeError('normal mode'),
        ):
            response = await authenticated_client.post(
                f'/api/stores/{store.id}/browser/start'
            )
        assert response.status_code == 500
        assert 'normal mode' in response.json()['detail']

    @pytest.mark.asyncio
    async def test_force_true_relaunches_and_retries(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        """force=1: kill_and_relaunch then start_session succeeds."""
        store = await self._make_ziniao_store(async_db_session)
        start_session = mock.AsyncMock(
            side_effect=[ZiniaoNormalModeError('normal'), None]
        )
        relaunch = mock.AsyncMock(return_value=True)
        with (
            mock.patch(
                'app.routers.stores.browser_manager.start_session',
                start_session,
            ),
            mock.patch('app.routers.stores.kill_and_relaunch_ziniao', relaunch),
        ):
            response = await authenticated_client.post(
                f'/api/stores/{store.id}/browser/start?force=1'
            )
        assert response.status_code == 200
        assert response.json() == {'ok': True}
        relaunch.assert_awaited_once()
        assert start_session.await_count == 2

    @pytest.mark.asyncio
    async def test_force_true_relaunch_failure_returns_502(
        self,
        authenticated_client: AsyncClient,
        async_db_session: AsyncSession,
    ):
        """force=1: 502 when kill_and_relaunch raises RuntimeError."""
        store = await self._make_ziniao_store(async_db_session)
        with (
            mock.patch(
                'app.routers.stores.browser_manager.start_session',
                side_effect=ZiniaoNormalModeError('normal'),
            ),
            mock.patch(
                'app.routers.stores.kill_and_relaunch_ziniao',
                side_effect=RuntimeError('relaunch boom'),
            ),
        ):
            response = await authenticated_client.post(
                f'/api/stores/{store.id}/browser/start?force=1'
            )
        assert response.status_code == 502
        assert 'relaunch boom' in response.json()['detail']
