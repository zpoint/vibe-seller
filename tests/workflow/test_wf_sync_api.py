"""Workflow tests for the sync API (API → DB → response shape).

Covers what the web UI and the hosted MCP endpoint now call:
``GET /api/sync/status`` and ``POST /api/sync/{name}/run``, plus the
retirement of the per-module sync endpoints.
"""

import asyncio

import pytest

from app.sync.scheduler import (
    REASON_EVENT,
    REASON_MANUAL,
    Cadence,
    SyncContext,
    SyncJob,
    SyncResult,
    sync_scheduler,
)

pytestmark = pytest.mark.workflow


class Recorder:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or SyncResult(status='updated', version='a' * 40)

    async def __call__(self, ctx: SyncContext) -> SyncResult:
        self.calls.append(ctx)
        return self.result


@pytest.fixture
async def jobs():
    """A few registered jobs on the process-wide scheduler, then clear."""
    skills = Recorder()
    knowledge = Recorder(SyncResult(status='unchanged', version='b' * 40))
    ads = Recorder()
    sync_scheduler.reset()
    for name, rec in (
        ('skills', skills),
        ('knowledge', knowledge),
        ('ads_skill', ads),
    ):
        await sync_scheduler.register(
            SyncJob(
                name=name,
                cadence=Cadence.DAILY,
                run=rec,
                toggle_key=(
                    'skills_auto_sync_enabled' if name == 'skills' else None
                ),
            )
        )
    yield skills, knowledge, ads
    sync_scheduler.reset()


async def test_status_shape(admin_client, jobs):
    resp = await admin_client.get('/api/sync/status')
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {'jobs', 'needs_restart'}
    names = {job['name'] for job in body['jobs']}
    assert names == {'skills', 'knowledge', 'ads_skill'}
    for job in body['jobs']:
        assert set(job) == {
            'name',
            'last_run_at',
            'last_ok_at',
            'version',
            'etag',
            'status',
            'error',
            'needs_restart',
            'detail',
            'cadence',
            'enabled',
            'running',
            'next_run_at',
        }


async def test_status_requires_auth(unauthed_client, jobs):
    resp = await unauthed_client.get('/api/sync/status')
    assert resp.status_code == 401


async def test_manual_run_records_state(admin_client, jobs):
    skills, _knowledge, _ads = jobs
    resp = await admin_client.post('/api/sync/skills/run')
    assert resp.status_code == 200
    body = resp.json()
    assert body['name'] == 'skills'
    assert body['status'] == 'updated'
    assert body['state']['version'] == 'a' * 40
    assert [c.reason for c in skills.calls] == [REASON_MANUAL]
    assert [c.force for c in skills.calls] == [True]

    status = (await admin_client.get('/api/sync/status')).json()
    row = next(j for j in status['jobs'] if j['name'] == 'skills')
    assert row['last_ok_at'] is not None
    assert row['version'] == 'a' * 40


async def test_unknown_job_is_404(admin_client, jobs):
    resp = await admin_client.post('/api/sync/nope/run')
    assert resp.status_code == 404


async def test_concurrent_runs_share_one_sync(admin_client, jobs):
    skills, _knowledge, _ads = jobs

    async def slow(ctx):
        skills.calls.append(ctx)
        await asyncio.sleep(0.05)
        return SyncResult(status='updated', version='a' * 40)

    await sync_scheduler.register(
        SyncJob(name='slow', cadence=Cadence.DAILY, run=slow)
    )
    first, second = await asyncio.gather(
        admin_client.post('/api/sync/slow/run'),
        admin_client.post('/api/sync/slow/run'),
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(skills.calls) == 1  # one run served both requests


async def test_toggle_reflected_in_status(admin_client, jobs):
    resp = await admin_client.get('/api/sync/status')
    assert all(job['enabled'] for job in resp.json()['jobs'])

    await admin_client.put(
        '/api/settings', json={'skills_auto_sync_enabled': False}
    )
    status = (await admin_client.get('/api/sync/status')).json()
    row = next(j for j in status['jobs'] if j['name'] == 'skills')
    assert row['enabled'] is False
    knowledge = next(j for j in status['jobs'] if j['name'] == 'knowledge')
    assert knowledge['enabled'] is True  # knowledge has no toggle


async def test_old_workspace_sync_endpoints_are_gone(admin_client, jobs):
    """The per-module sync endpoints are gone; the sync API replaced them.

    405 counts as gone: FastAPI answers it when the PATH still matches
    a different method's route (``PUT /skills/{slug}``) but no handler
    owns this one.
    """
    for method, path in (
        ('get', '/api/workspace/knowledge/sync-meta'),
        ('get', '/api/workspace/skills/sync-meta'),
        ('post', '/api/workspace/knowledge/sync'),
        ('post', '/api/workspace/skills/sync'),
    ):
        resp = await getattr(admin_client, method)(path)
        assert resp.status_code in (404, 405), path


async def test_ads_authorization_triggers_the_skill_job(
    admin_client, jobs, monkeypatch
):
    _skills, _knowledge, ads = jobs

    async def fake_sync_stores(db):
        return [{'store_id': 's1', 'authorized': True}]

    monkeypatch.setattr(
        'app.routers.ads.ads_client.sync_stores', fake_sync_stores
    )
    resp = await admin_client.post('/api/ads/stores/sync', json={})
    assert resp.status_code == 200
    assert [c.reason for c in ads.calls] == [REASON_EVENT]
    assert [c.force for c in ads.calls] == [True]
