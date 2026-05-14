"""Workflow tests for skills sync endpoints."""

import pytest

pytestmark = pytest.mark.workflow


class TestSkillsSync:
    async def test_sync_endpoint(self, admin_client):
        r = await admin_client.post('/api/workspace/skills/sync')
        assert r.status_code == 200
        data = r.json()
        assert 'status' in data or 'synced' in data

    async def test_sync_meta_endpoint(self, admin_client):
        r = await admin_client.get('/api/workspace/skills/sync-meta')
        assert r.status_code == 200

    async def test_reserved_slug_rejection(self, admin_client):
        r = await admin_client.post(
            '/api/workspace/skill',
            json={'name': '_builtin', 'description': 'test'},
        )
        assert r.status_code == 400
        assert 'reserved' in r.json()['detail'].lower()

    async def test_underscore_prefix_rejected(self, admin_client):
        r = await admin_client.post(
            '/api/workspace/skill',
            json={'name': '_secret', 'description': 'test'},
        )
        assert r.status_code == 400


class TestSkillCRUD:
    async def test_create_skill_with_origin_url(self, admin_client):
        r = await admin_client.post(
            '/api/workspace/skill',
            json={
                'name': 'imported-skill',
                'description': 'From URL',
                'origin_url': 'https://github.com/test/repo',
            },
        )
        assert r.status_code == 200
        assert r.json()['status'] == 'created'

    async def test_delete_skill(self, admin_client):
        # Create first
        await admin_client.post(
            '/api/workspace/skill',
            json={'name': 'del-me', 'description': 'test'},
        )
        # Delete
        r = await admin_client.delete('/api/workspace/skills/del-me')
        assert r.status_code == 200
        assert r.json()['status'] == 'deleted'

    async def test_delete_builtin_rejected(self, admin_client):
        r = await admin_client.delete('/api/workspace/skills/_builtin')
        assert r.status_code == 400

    async def test_delete_nonexistent(self, admin_client):
        r = await admin_client.delete('/api/workspace/skills/no-such-skill')
        assert r.status_code == 404
