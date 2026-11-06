"""Workflow tests for the knowledge sync job's API surface.

Replaces the tests for the retired knowledge sync route
endpoints: the knowledge job is on the central sync scheduler now, and it has no toggle (an explicit product decision).
"""

import pytest

from app.sync.scheduler import Cadence, SyncJob, SyncResult, sync_scheduler

pytestmark = pytest.mark.workflow


@pytest.fixture
async def knowledge_job():
    runs = []

    async def run(ctx):
        runs.append(ctx)
        return SyncResult(status='unchanged', version='b' * 40)

    sync_scheduler.reset()
    await sync_scheduler.register(
        SyncJob(name='knowledge', cadence=Cadence.DAILY, run=run)
    )
    yield runs
    sync_scheduler.reset()


class TestKnowledgeSync:
    async def test_manual_run(self, admin_client, knowledge_job):
        r = await admin_client.post('/api/sync/knowledge/run')
        assert r.status_code == 200
        assert r.json()['status'] == 'unchanged'

    async def test_status_shows_the_last_run(self, admin_client, knowledge_job):
        await admin_client.post('/api/sync/knowledge/run')
        r = await admin_client.get('/api/sync/status')
        row = next(j for j in r.json()['jobs'] if j['name'] == 'knowledge')
        assert row['last_ok_at'] is not None
        assert row['version'] == 'b' * 40

    async def test_manual_runs_repeat_cleanly(
        self, admin_client, knowledge_job
    ):
        """Pressing Sync twice runs twice, with the same result shape."""
        r1 = await admin_client.post('/api/sync/knowledge/run')
        r2 = await admin_client.post('/api/sync/knowledge/run')
        assert r1.status_code == r2.status_code == 200
        assert r1.json()['status'] == r2.json()['status']
        assert len(knowledge_job) == 2

    async def test_knowledge_has_no_toggle(self, admin_client, knowledge_job):
        r = await admin_client.get('/api/sync/status')
        row = next(j for j in r.json()['jobs'] if j['name'] == 'knowledge')
        assert row['enabled'] is True
