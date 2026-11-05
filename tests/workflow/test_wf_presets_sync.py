"""Workflow tests: GET /api/profiles/presets follows the sync.

The presets endpoint is what the Settings → AI page reads on open, so
it must serve the auto-synced catalog when one is valid and the
bundled catalog when the cache is corrupt — with no network on the
request path (every fetch here goes through a mocked transport).
"""

import json

import httpx
import pytest

from app.ai import provider_presets_sync as sync
from app.ai.provider_presets import PROVIDER_PRESETS

pytestmark = pytest.mark.workflow

ACME_URL = 'https://api.acme.example/anthropic'


def acme_body(name: str = 'Acme') -> str:
    presets = {
        'acme_test': {
            'name': name,
            'description': 'Acme test preset',
            'group': 'Acme Cloud',
            'variant': 'Standard',
            'load_global_mcp': False,
            'env': {
                'ANTHROPIC_BASE_URL': ACME_URL,
                'ANTHROPIC_MODEL': 'acme-1',
            },
        }
    }
    models = {
        'acme_test': [
            {
                'id': 'acme-1',
                'label': 'Acme One',
                'context': '1M',
                'vision': True,
            }
        ]
    }
    return f'PROVIDER_PRESETS = {presets!r}\n\nPROVIDER_MODELS = {models!r}\n'


async def fetch(body: str) -> dict:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=body)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        return await sync.fetch_once(client)


class TestPresetsSync:
    async def test_endpoint_serves_the_synced_catalog(self, admin_client):
        result = await fetch(acme_body('Acme Two'))
        assert result['status'] == 'updated'

        r = await admin_client.get('/api/profiles/presets')
        assert r.status_code == 200
        presets = r.json()['presets']
        assert presets['acme_test']['name'] == 'Acme Two'
        assert presets['acme_test']['group'] == 'Acme Cloud'
        assert presets['acme_test']['models'][0]['id'] == 'acme-1'
        # The bundled catalog rides along (remote is the newer whole
        # catalog, not a patch on top of the bundled one).
        assert 'mimo' in presets
        assert presets['mimo']['group'] == 'MiMo'
        assert presets['mimo_token']['variant'] == 'Token Plan (China cluster)'

    async def test_endpoint_reflects_a_changed_body(self, admin_client):
        await fetch(acme_body('Acme One'))
        await fetch(acme_body('Acme Two'))

        r = await admin_client.get('/api/profiles/presets')
        assert r.json()['presets']['acme_test']['name'] == 'Acme Two'

    async def test_corrupt_cache_serves_bundled(self, admin_client):
        cache = sync._cache_path()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text('{truncated', encoding='utf-8')
        sync.reset()

        r = await admin_client.get('/api/profiles/presets')
        assert r.status_code == 200
        presets = r.json()['presets']
        assert set(presets) == set(PROVIDER_PRESETS)
        assert 'acme_test' not in presets

    async def test_rejected_body_keeps_the_last_good_catalog(
        self, admin_client
    ):
        await fetch(acme_body('Acme One'))
        bad = httpx.MockTransport(
            lambda request: httpx.Response(200, text='PROVIDER_PRESETS = {')
        )
        async with httpx.AsyncClient(transport=bad) as client:
            result = await sync.fetch_once(client)
        assert result['status'] == 'rejected'

        r = await admin_client.get('/api/profiles/presets')
        assert r.json()['presets']['acme_test']['name'] == 'Acme One'

    async def test_cache_file_holds_the_validated_catalog(self, admin_client):
        await fetch(acme_body())
        blob = json.loads(sync._cache_path().read_text())
        assert blob['presets']['acme_test']['env']['ANTHROPIC_MODEL'] == (
            'acme-1'
        )
        assert blob['sha256'] and blob['fetched_at'] and blob['source_url']
