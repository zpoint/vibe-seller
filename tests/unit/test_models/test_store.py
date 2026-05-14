"""Tests for Store model."""

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.store import Store


class TestStoreModel:
    """Tests for Store database model."""

    @pytest.mark.asyncio
    async def test_create_store(self, async_db_session: AsyncSession):
        """Test creating a store."""
        store = Store(
            name='Test Store',
            browser_backend='chrome',
            browser_config=json.dumps({'headless': True}),
        )
        async_db_session.add(store)
        await async_db_session.commit()
        await async_db_session.refresh(store)

        assert store.id is not None
        assert store.name == 'Test Store'
        assert store.browser_backend == 'chrome'
        assert store.created_at is not None
        assert store.updated_at is not None

    @pytest.mark.asyncio
    async def test_store_json_fields(self, async_db_session: AsyncSession):
        """Test store JSON fields work correctly."""
        config = {'headless': True, 'args': ['--no-sandbox']}
        platforms = ['amazon', 'noon']
        countries = ['US', 'AE']

        store = Store(
            name='Multi-Platform Store',
            browser_backend='chrome',
            browser_config=json.dumps(config),
            platforms=json.dumps(platforms),
            countries=json.dumps(countries),
        )
        async_db_session.add(store)
        await async_db_session.commit()
        await async_db_session.refresh(store)

        assert json.loads(store.browser_config) == config
        assert json.loads(store.platforms) == platforms
        assert json.loads(store.countries) == countries
