"""The browser's telemetry switch follows the backend's.

The frontend inits PostHog from ``telemetry_enabled`` in /api/settings.
Under CI or pytest the backend never starts its client, so the setting
must read false there even when the stored value is true — otherwise a
CI job that forgot ``VIBE_SELLER_TELEMETRY=0`` ships its Playwright
sessions as real installs.
"""

import pytest

from app import telemetry

pytestmark = pytest.mark.workflow


async def test_settings_report_off_while_the_backend_is_silent(
    admin_client, monkeypatch
):
    # A CI job that forgot the env var: only CI/pytest detection is left.
    monkeypatch.delenv('VIBE_SELLER_TELEMETRY', raising=False)
    r = await admin_client.put(
        '/api/settings', json={'telemetry_enabled': 'true'}
    )
    assert r.status_code == 200
    assert telemetry.is_enabled() is False  # pytest: client never starts
    data = (await admin_client.get('/api/settings')).json()
    assert data['telemetry_enabled'] == 'false'


async def test_settings_report_the_stored_value_when_the_backend_sends(
    admin_client, monkeypatch
):
    monkeypatch.setattr(telemetry, 'is_enabled', lambda: True)
    await admin_client.put('/api/settings', json={'telemetry_enabled': 'true'})
    data = (await admin_client.get('/api/settings')).json()
    assert data['telemetry_enabled'] == 'true'
