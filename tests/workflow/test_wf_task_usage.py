"""Workflow: the usage meter's numbers reach the task API, and the
``model_pricing`` sync job prices with what it fetched.

API → scheduler → real job → (mocked) models.dev → served catalog →
meter → task row → ``GET /api/tasks/{id}``.
"""

import httpx
import pytest

from app.ai import model_pricing as mp, model_pricing_sync as mps
from app.ai.usage_meter import UsageMeter
from app.sync import jobs as sync_jobs
from app.sync.scheduler import sync_scheduler
from app.task_usage import record_usage

pytestmark = pytest.mark.workflow


def _models_dev(input_rate: float) -> dict:
    body = {
        pid: {'models': {'filler': {'cost': {'input': 1, 'output': 2}}}}
        for pid in mp.TRACKED_PROVIDERS
    }
    body['minimax-cn'] = {
        'models': {
            'MiniMax-Test': {
                'limit': {'context': 1_000_000},
                'cost': {
                    'input': input_rate,
                    'output': 1.2,
                    'cache_read': 0.03,
                },
            }
        }
    }
    return body


@pytest.fixture
async def models_dev(tmp_path, monkeypatch):
    seen: list[str] = []
    state = {'rate': 0.5}

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=_models_dev(state['rate']))

    monkeypatch.setattr(
        mps,
        '_client_factory',
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(mp, 'cache_path', lambda: tmp_path / 'mp.json')
    mp.reset()
    sync_scheduler.reset()
    job = next(
        j for j in sync_jobs.core_jobs() if j.name == sync_jobs.PRICING_JOB
    )
    await sync_scheduler.register(job)
    yield seen, state
    sync_scheduler.reset()
    mp.reset()


async def _meter_one_call(task_id: str) -> None:
    meter = UsageMeter(lambda m: mp.price_for('minimax-cn', m))
    for ev in (
        {
            'type': 'stream_event',
            'event': {
                'type': 'message_start',
                'message': {
                    'id': 'm1',
                    'model': 'MiniMax-Test',
                    'usage': {
                        'input_tokens': 1_000_000,
                        'cache_read_input_tokens': 200_000,
                        'output_tokens': 1,
                    },
                },
            },
        },
        {
            'type': 'stream_event',
            'event': {
                'type': 'message_delta',
                'usage': {'output_tokens': 100_000},
            },
        },
    ):
        delta = meter.feed(ev)
        if delta is not None:
            await record_usage(task_id, delta)


async def test_job_is_registered_daily():
    job = next(
        j for j in sync_jobs.core_jobs() if j.name == sync_jobs.PRICING_JOB
    )
    assert job.cadence.name == 'DAILY'
    assert job.toggle_key is None  # pricing is not something to opt out of


async def test_synced_prices_reach_the_task_api(admin_client, models_dev):
    seen, state = models_dev
    resp = await admin_client.post(f'/api/sync/{sync_jobs.PRICING_JOB}/run')
    assert resp.status_code == 200, resp.text
    assert resp.json()['status'] == 'updated'
    assert seen == [mps.DEFAULT_URL]

    task = (
        await admin_client.post(
            '/api/tasks',
            json={
                'title': 'usage',
                'defer_start': True,
            },
        )
    ).json()
    assert task['cost_usd'] is None and task['context_tokens'] is None

    await _meter_one_call(task['id'])
    got = (await admin_client.get(f'/api/tasks/{task["id"]}')).json()
    assert got['input_tokens'] == 1_000_000
    assert got['output_tokens'] == 100_000
    assert got['cache_read_tokens'] == 200_000
    assert got['context_tokens'] == 1_300_000
    assert got['context_window'] == 1_000_000
    assert got['usage_model'] == 'MiniMax-Test'
    assert got['cost_basis'] == mp.BASIS_CATALOG
    assert got['cost_usd'] == pytest.approx(0.5 + 0.12 + 0.006)

    # A price change upstream applies to calls metered after the sync.
    state['rate'] = 1.0
    resp = await admin_client.post(f'/api/sync/{sync_jobs.PRICING_JOB}/run')
    assert resp.json()['status'] == 'updated'
    await _meter_one_call(task['id'])
    got = (await admin_client.get(f'/api/tasks/{task["id"]}')).json()
    assert got['cost_usd'] == pytest.approx(0.626 + 1.0 + 0.12 + 0.006)

    listed = (await admin_client.get('/api/tasks')).json()
    row = next(t for t in listed if t['id'] == task['id'])
    assert row['cost_usd'] == got['cost_usd']
