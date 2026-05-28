"""Workflow tests for skills sync endpoints."""

import pytest

from app.models.app_settings import AppSettings

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


class TestSkillsAutoSyncSetting:
    """skills_auto_sync_enabled flag exposed via /api/settings.

    The flag controls the 24h GitHub poll that runs before each
    task (see app/workspace/skills_sync.py check_and_sync_remote);
    manual sync via POST /api/workspace/skills/sync is intentionally
    not gated and is covered by ``TestSkillsSync`` above.
    """

    async def test_get_default_is_true(self, admin_client):
        r = await admin_client.get('/api/settings')
        assert r.status_code == 200
        assert r.json()['skills_auto_sync_enabled'] == 'true'

    async def test_put_disables_persists(
        self, admin_client, override_async_session
    ):
        r = await admin_client.put(
            '/api/settings',
            json={'skills_auto_sync_enabled': False},
        )
        assert r.status_code == 200
        assert r.json()['ok'] is True

        async with override_async_session() as db:
            row = await db.get(AppSettings, 'skills_auto_sync_enabled')
            assert row is not None
            assert row.value == 'false'

        r = await admin_client.get('/api/settings')
        assert r.json()['skills_auto_sync_enabled'] == 'false'

    async def test_put_truthy_string_normalized(
        self, admin_client, override_async_session
    ):
        # The boolean normalizer accepts 'true'/'1' as truthy; anything
        # else degrades to 'false'. Lock that contract — the UI sends
        # raw booleans today but the DB column is text.
        await admin_client.put(
            '/api/settings',
            json={'skills_auto_sync_enabled': '1'},
        )
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'skills_auto_sync_enabled')
            assert row is not None
            assert row.value == 'true'

        await admin_client.put(
            '/api/settings',
            json={'skills_auto_sync_enabled': 'nope'},
        )
        async with override_async_session() as db:
            row = await db.get(AppSettings, 'skills_auto_sync_enabled')
            assert row is not None
            assert row.value == 'false'

    async def test_put_requires_admin(self, member_client):
        r = await member_client.put(
            '/api/settings',
            json={'skills_auto_sync_enabled': False},
        )
        assert r.status_code == 403

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
