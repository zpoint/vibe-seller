"""Workflow tests for skills sync endpoints."""

import pytest

from app import ads_routing
from app.models.app_settings import AppSettings
from app.sync.scheduler import (
    Cadence,
    SyncJob,
    SyncResult,
    sync_scheduler,
)

pytestmark = pytest.mark.workflow


class TestSkillsSync:
    """The skills sync is the scheduler's ``skills`` job now.

    The per-module skills sync endpoints are gone; these exercise the replacement.
    """

    @pytest.fixture
    async def skills_job(self):
        async def run(ctx):
            return SyncResult(status='updated', version='a' * 40)

        sync_scheduler.reset()
        await sync_scheduler.register(
            SyncJob(name='skills', cadence=Cadence.DAILY, run=run)
        )
        yield
        sync_scheduler.reset()

    async def test_sync_endpoint(self, admin_client, skills_job):
        r = await admin_client.post('/api/sync/skills/run')
        assert r.status_code == 200
        assert r.json()['status'] == 'updated'

    async def test_sync_meta_endpoint(self, admin_client, skills_job):
        await admin_client.post('/api/sync/skills/run')
        r = await admin_client.get('/api/sync/status')
        assert r.status_code == 200
        row = next(j for j in r.json()['jobs'] if j['name'] == 'skills')
        assert row['version'] == 'a' * 40


class TestSkillsAutoSyncSetting:
    """skills_auto_sync_enabled flag exposed via /api/settings.

    The flag gates the scheduled ``skills`` sync job (and nothing
    else); a manual run is an explicit user action and is not gated.
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


class TestSaveSkillEndpoints:
    """Contract for the save-skill flow: GET /skills + PUT /skills/{slug}."""

    SKILL_MD = (
        '---\nname: WF Save Test\n'
        'description: "Contract-test skill. Use when running the '
        'save-skill workflow test."\n---\n\n# WF Save Test\n\nBody.\n'
    )

    async def test_list_skills_shape(self, admin_client):
        r = await admin_client.get('/api/workspace/skills')
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        for s in data:
            assert set(s) == {
                'slug',
                'name',
                'description',
                'source',
                'updatable',
            }

    async def test_create_then_extend_same_slug(self, admin_client):
        # Create
        r = await admin_client.put(
            '/api/workspace/skills/wf-save-test',
            json={'skill_md': self.SKILL_MD},
        )
        assert r.status_code == 200
        assert r.json()['action'] == 'created'

        # Now it shows up as an updatable custom skill.
        listing = (await admin_client.get('/api/workspace/skills')).json()
        entry = next(s for s in listing if s['slug'] == 'wf-save-test')
        assert entry['updatable'] is True
        assert entry['source'] == 'custom'

        # Extend (same slug → updated, not a duplicate)
        r = await admin_client.put(
            '/api/workspace/skills/wf-save-test',
            json={'skill_md': self.SKILL_MD + '\n## More\n'},
        )
        assert r.status_code == 200
        assert r.json()['action'] == 'updated'

        listing = (await admin_client.get('/api/workspace/skills')).json()
        slugs = [s['slug'] for s in listing]
        assert slugs.count('wf-save-test') == 1

    async def test_invalid_slug_is_400(self, admin_client):
        r = await admin_client.put(
            '/api/workspace/skills/Bad Slug',
            json={'skill_md': self.SKILL_MD},
        )
        assert r.status_code == 400


async def test_a_task_is_not_listed_the_skills_routed_away_from_it(
    admin_client,
):
    """A store not bound to the ads service: its workspace leaves out the
    API skill, and so must the skill list the agent can ask for —
    observed, an agent shown it spent minutes probing whether its store
    was authorized."""
    r = await admin_client.put(
        '/api/workspace/skills/wf-routed-away',
        json={'skill_md': TestSaveSkillEndpoints.SKILL_MD},
    )
    assert r.status_code == 200, r.text
    ads_routing.remember('t-routed', {'wf-routed-away'})
    try:
        mine = (
            await admin_client.get('/api/workspace/skills?task_id=t-routed')
        ).json()
        assert 'wf-routed-away' not in {s['slug'] for s in mine}
        everyone = (await admin_client.get('/api/workspace/skills')).json()
        assert 'wf-routed-away' in {s['slug'] for s in everyone}
    finally:
        ads_routing.remember('t-routed', set())
