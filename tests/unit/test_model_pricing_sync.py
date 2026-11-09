"""The model_pricing sync: fetch → validate → cache → serve, failing soft."""

import json

import httpx
import pytest

from app.ai import model_pricing as mp, model_pricing_sync as sync

pytestmark = pytest.mark.unit


def _body(price: float = 3.0) -> dict:
    body = {
        pid: {'models': {'filler': {'cost': {'input': 1, 'output': 2}}}}
        for pid in mp.TRACKED_PROVIDERS
    }
    body['anthropic'] = {
        'models': {
            'claude-test': {
                'limit': {'context': 200_000},
                'cost': {'input': price, 'output': 15},
            }
        }
    }
    return body


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(mp, 'cache_path', lambda: tmp_path / 'mp.json')
    mp.reset()
    yield tmp_path / 'mp.json'
    mp.reset()


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_good_body_is_cached_and_served(_isolated):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=_body(price=7.0))

    async with _client(handler) as client:
        out = await sync.fetch_once(client)
    assert out['status'] == 'updated'
    assert seen == [sync.DEFAULT_URL]
    assert mp.price_for('anthropic', 'claude-test').rates['input'] == 7.0
    # Survives a restart: the cache, not the snapshot, is served.
    mp.reset()
    assert mp.price_for('anthropic', 'claude-test').rates['input'] == 7.0
    cached = json.loads(_isolated.read_text())
    assert cached['anthropic']['models']['claude-test'] == {
        'limit': {'context': 200_000},
        'cost': {'input': 7.0, 'output': 15},
    }

    async with _client(handler) as client:
        again = await sync.fetch_once(client)
    assert again['status'] == 'unchanged'
    assert again['sha256'] == out['sha256']


@pytest.mark.parametrize(
    ('response', 'status'),
    [
        (httpx.Response(503), 'failed'),
        (httpx.Response(200, content=b'<html>maintenance</html>'), 'rejected'),
        (httpx.Response(200, json={'anthropic': {'models': {}}}), 'rejected'),
    ],
)
async def test_a_bad_answer_keeps_what_is_served(_isolated, response, status):
    before = mp.current()
    async with _client(lambda r: response) as client:
        out = await sync.fetch_once(client)
    assert out['status'] == status
    assert mp.current() is before
    assert not _isolated.exists()


async def test_a_network_error_never_raises():
    def handler(request):
        raise httpx.ConnectError('offline')

    async with _client(handler) as client:
        out = await sync.fetch_once(client)
    assert out['status'] == 'failed'
    assert 'offline' in out['error']


async def test_an_oversized_body_is_cut_off(monkeypatch):
    monkeypatch.setattr(sync, 'MAX_BODY_BYTES', 1024)
    async with _client(
        lambda r: httpx.Response(200, content=b'x' * 4096)
    ) as client:
        out = await sync.fetch_once(client)
    assert out['status'] == 'rejected'


async def test_source_url_is_overridable(monkeypatch):
    monkeypatch.setenv('MODEL_PRICING_URL', 'https://mirror.example/api.json')
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=_body())

    async with _client(handler) as client:
        await sync.fetch_once(client)
    assert seen == ['https://mirror.example/api.json']
