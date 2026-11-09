"""Workflow tests: the vision_catalog sync job, end to end in-process.

API → scheduler → job → (mocked) kie.ai / GitHub → cache → the
``GET /api/vision/config`` catalog the confirm card reads. The job is
the real one from ``core_jobs()``; only the network is a MockTransport.

Pins the activation rule: an install without a kie.ai key (or in the
offline fake mode) syncs nothing and touches no network.
"""

import json
from pathlib import Path

import httpx
import pytest

from app import vision, vision_catalog as vc, vision_models
from app.sync import jobs as sync_jobs
from app.sync.scheduler import sync_scheduler

pytestmark = pytest.mark.workflow

PRICING = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / 'fixtures'
        / 'kie_pricing_records.json'
    ).read_text(encoding='utf-8')
)


class FakeNet:
    def __init__(self):
        self.requests: list[str] = []
        slugs = {f['slug'] for f in vision_models.IMAGE_FAMILIES}
        slugs.discard('grok-imagine-image-2-0/image-edit')  # delisted
        self.slugs = sorted(slugs)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url == vc.DEFAULT_CATALOG_URL:
            return httpx.Response(
                200,
                text=Path(vision_models.__file__).read_text(encoding='utf-8'),
            )
        if url == vc.PRICING_URL:
            rows = [
                dict(r, usdPrice='0.07')
                if r['modelDescription'] == 'Black Forest Labs flux-2 pro, '
                'image to image, 1.0s-2K'
                else r
                for r in PRICING
            ]
            return httpx.Response(
                200,
                json={'data': {'records': rows, 'total': len(rows)}},
            )
        if url == vc.MODELS_URL:
            models = [{'model': s} for s in self.slugs]
            return httpx.Response(
                200,
                json={
                    'code': 200,
                    'data': {'total': len(models), 'models': models},
                },
            )
        return httpx.Response(404)


@pytest.fixture
async def net(tmp_path, monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(
        sync_jobs,
        '_client_factory',
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake)),
    )
    monkeypatch.setattr(vision, 'VISION_CONFIG_PATH', tmp_path / 'vision.json')
    monkeypatch.setattr(
        vc, '_cache_path', lambda: tmp_path / 'cache' / 'vision_catalog.json'
    )
    monkeypatch.delenv('KIE_API_KEY', raising=False)
    monkeypatch.delenv('VISION_FAKE', raising=False)
    vc.reset()
    sync_scheduler.reset()
    job = next(
        j for j in sync_jobs.core_jobs() if j.name == sync_jobs.VISION_JOB
    )
    await sync_scheduler.register(job)
    yield fake
    sync_scheduler.reset()
    vc.reset()


async def _card(client) -> dict:
    resp = await client.get('/api/vision/config')
    assert resp.status_code == 200
    return {m['id']: m for m in resp.json()['models']}


async def _run(client) -> dict:
    resp = await client.post(f'/api/sync/{sync_jobs.VISION_JOB}/run')
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_no_key_syncs_nothing(admin_client, net):
    body = await _run(admin_client)
    assert body['status'] == 'skipped'
    assert body['skip_reason'] == 'precondition'
    assert net.requests == []
    card = await _card(admin_client)
    assert card['flux-2-pro-2k']['usd'] == 0.035  # bundled
    assert 'grok-imagine-2' in card


async def test_fake_mode_syncs_nothing(admin_client, net, monkeypatch):
    monkeypatch.setenv('VISION_FAKE', '1')
    vision.save_vision_config('kie-test-key')
    body = await _run(admin_client)
    assert body['status'] == 'skipped'
    assert net.requests == []


async def test_activated_install_gets_live_prices_and_availability(
    admin_client, net
):
    resp = await admin_client.put(
        '/api/vision/config', json={'kie_api_key': 'kie-test-key'}
    )
    assert resp.status_code == 200
    # Saving the key schedules a refresh only when sync timers are armed;
    # tests run disarmed, so the save itself reached no network.
    assert net.requests == []

    body = await _run(admin_client)

    assert body['status'] == 'updated', body
    assert body['detail']['available_hidden'] == 1
    assert body['detail']['prices_priced'] == len(vision.image_models()) + 1
    card = await _card(admin_client)
    # Live price replaces the bundled one…
    assert card['flux-2-pro-2k']['usd'] == 0.07
    assert card['flux-2-pro-2k']['cny'] == round(0.07 * vision.USD_CNY, 2)
    # …and the delisted model is gone from the card.
    assert 'grok-imagine-2' not in card

    # A restart serves the synced catalog from the cache.
    vc.reset()
    card = await _card(admin_client)
    assert card['flux-2-pro-2k']['usd'] == 0.07
    assert 'grok-imagine-2' not in card


async def test_retired_id_from_an_agent_lands_on_its_successor(
    admin_client, net
):
    """The tool enum drops old ids, but a schedule prompt written last
    month still says gpt-image-2-2k; the card must propose GPT Image 2.5
    2K, not jump to the default model."""
    assert vision.resolve_model_id('gpt-image-2-2k') == 'gpt-image-2-5-2k'
    assert 'gpt-image-2-2k' not in await _card(admin_client)
