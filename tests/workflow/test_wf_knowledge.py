"""Workflow tests for knowledge sync API endpoints."""

import pytest

pytestmark = pytest.mark.workflow


class TestKnowledgeSync:
    async def test_sync_endpoint(self, admin_client):
        r = await admin_client.post('/api/workspace/knowledge/sync')
        assert r.status_code == 200
        data = r.json()
        assert 'status' in data

    async def test_sync_meta(self, admin_client):
        r = await admin_client.get('/api/workspace/knowledge/sync-meta')
        assert r.status_code == 200
        data = r.json()
        # Should return metadata dict with expected fields
        assert isinstance(data, dict)

    async def test_sync_idempotent(self, admin_client):
        """Second sync returns same result."""
        r1 = await admin_client.post('/api/workspace/knowledge/sync')
        r2 = await admin_client.post('/api/workspace/knowledge/sync')
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r1.json() == r2.json()
