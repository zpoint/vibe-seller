"""Unit tests for app.telemetry.send() property merging.

Guards the bug where only ``app_started`` carried ``app_version``: every
other event was sent with just its caller-supplied dict, so dashboards
couldn't segment task/store/browser events by release version or
platform. ``send()`` must merge ``base_properties()`` into EVERY event.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app import telemetry

pytestmark = pytest.mark.unit


def _capture_props(monkeypatch, event: str, properties: dict | None) -> dict:
    """Drive telemetry.send() with a mocked client and return the
    properties dict handed to client.capture()."""
    captured: dict = {}
    client = MagicMock()

    def fake_capture(distinct_id, event, properties):  # noqa: ARG001
        captured['properties'] = properties

    client.capture.side_effect = fake_capture
    # Bypass the env/db gates that conftest sets (VIBE_SELLER_TELEMETRY=0).
    monkeypatch.setattr(telemetry, '_client', client)
    monkeypatch.setattr(telemetry, '_install_id', 'test-install-id')
    monkeypatch.setattr(telemetry, '_db_disabled', False)
    monkeypatch.setattr(telemetry, '_disabled_via_env', lambda: False)
    telemetry.send(event, properties)
    return captured['properties']


def test_send_merges_base_properties_into_every_event(monkeypatch):
    # A non-app_started event with its own caller props.
    props = _capture_props(
        monkeypatch, 'task_created', {'has_schedule': 'false'}
    )
    # base_properties keys must be present…
    assert props['app_version'] == telemetry.APP_VERSION
    assert 'os' in props
    assert 'python_version' in props
    assert 'is_docker' in props
    # …and the caller's own property is preserved.
    assert props['has_schedule'] == 'false'


def test_send_includes_version_with_no_caller_properties(monkeypatch):
    props = _capture_props(monkeypatch, 'store_deleted', None)
    assert props['app_version'] == telemetry.APP_VERSION
    assert 'os' in props


def test_caller_property_overrides_base(monkeypatch):
    # If a caller ever passes app_version explicitly, it wins over the base.
    props = _capture_props(
        monkeypatch, 'app_started', {'app_version': 'override'}
    )
    assert props['app_version'] == 'override'
