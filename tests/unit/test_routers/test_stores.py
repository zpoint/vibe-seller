"""Tests for stores router."""

import json
from unittest import mock

from httpx import AsyncClient
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.store import Store
from app.models.user import User


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
