"""Workflow test: an AdsPower store through the API, the UI's way.

The store form saves the profile id (``browser_oauth`` → the
engine's ``browser_ref``) and the API key inside ``browser_config``
under the neutral key — the same shape the future generic credential
storage produces. Pins the round-trip AND the response-side secret
contract: the key must never come back in plaintext.
"""

import pytest

pytestmark = pytest.mark.workflow


async def test_create_adspower_store_round_trip(admin_client):
    r = await admin_client.post(
        '/api/stores',
        json={
            'name': 'Adspower Store',
            'browser_backend': 'adspower',
            'browser_oauth': 'kaaaaaa1',
            'browser_config': {
                'api_key': 'sk-live-secret-key-123',
                'client_path': 'C:\\Program Files\\AdsPower Global',
            },
            'platforms': ['amazon'],
            'countries': ['US'],
        },
    )
    assert r.status_code == 200
    s = r.json()
    assert s['browser_backend'] == 'adspower'
    assert s['browser_oauth'] == 'kaaaaaa1'
    # The response-side secret contract: the stored key is masked for
    # every caller (the shared redact classifier keys on the name).
    cfg = s['browser_config']
    assert cfg['api_key'] != 'sk-live-secret-key-123'
    assert cfg['client_path'] == 'C:\\Program Files\\AdsPower Global'
    assert cfg['api_key']  # masked, not dropped


async def test_create_adspower_store_minimal(admin_client):
    r = await admin_client.post(
        '/api/stores',
        json={
            'name': 'Adspower Bare',
            'browser_backend': 'adspower',
        },
    )
    assert r.status_code == 200
    assert r.json()['browser_backend'] == 'adspower'
