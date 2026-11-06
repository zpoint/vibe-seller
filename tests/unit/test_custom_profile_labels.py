"""Custom-provider telemetry labels and the provider host dimension.

A base URL carrying its key in the userinfo
or a query string, and a path, must reduce to the host alone. A bare
IP or localhost reports as ``local``; a profile with no base URL
reports ``api.anthropic.com``. The user-typed name is an attribute
only — grouping is by host.
"""

import pytest

from app.ai.profiles import (
    custom_profile_labels,
    custom_profile_name,
    custom_provider_entries,
    profile_host,
    profile_telemetry,
)
from app.ai.provider_presets import PROVIDER_PRESETS

pytestmark = pytest.mark.unit


CUSTOM_URL = 'https://api.llmbox.example/anthropic/v1'


def _custom(**env_overrides):
    env = {
        'ANTHROPIC_BASE_URL': CUSTOM_URL,
        'ANTHROPIC_MODEL': 'acme-model-9000',
        'ANTHROPIC_AUTH_TOKEN': 'sk-secret-key-123',
    }
    env.update(env_overrides)
    return {'name': 'Bespoke LLM', 'env': env}


# ── Host reduction ───────────────────────────────────────────


@pytest.mark.parametrize(
    ('url', 'expected'),
    [
        (CUSTOM_URL, 'api.llmbox.example'),
        # Key in the userinfo → host alone.
        (
            'https://sk-secret-key-123@api.llmbox.example/v1',
            'api.llmbox.example',
        ),
        # Key in the query → host alone.
        (
            'https://api.llmbox.example/v1?api_key=sk-secret-key-123',
            'api.llmbox.example',
        ),
        # Path and port dropped.
        ('https://api.llmbox.example:8443/deep/path', 'api.llmbox.example'),
        # No scheme at all.
        ('api.llmbox.example', 'api.llmbox.example'),
        # Local endpoints collapse to 'local'.
        ('http://localhost:8080/v1', 'local'),
        ('http://127.0.0.1:3000/v1', 'local'),
        ('http://[::1]:9000/v1', 'local'),
        ('http://192.168.1.10/v1', 'local'),
    ],
)
def test_host_reduces_to_hostname(url, expected):
    profile = _custom(ANTHROPIC_BASE_URL=url)
    assert profile_host(profile) == expected


def test_host_never_contains_url_parts():
    profile = _custom(
        ANTHROPIC_BASE_URL=(
            'https://sk-secret-key-123@user:pass@api.llmbox.example'
            ':8443/deep/path?api_key=sk-secret-key-123'
        ),
    )
    host = profile_host(profile)
    assert host == 'api.llmbox.example'
    for fragment in ('sk-secret-key-123', '8443', 'deep', 'api_key', 'user'):
        assert fragment not in host


def test_no_base_url_reports_the_anthropic_api():
    assert profile_host(None) == 'api.anthropic.com'
    assert profile_host({'name': 'Claude', 'env': {}}) == 'api.anthropic.com'


def test_same_base_url_is_the_same_host_whatever_the_names():
    """§9: the provider dimension is the host — two profiles with
    different names but one base URL report one host."""
    one = {'name': 'Bespoke LLM', 'env': {'ANTHROPIC_BASE_URL': CUSTOM_URL}}
    two = {'name': 'My Gateway', 'env': {'ANTHROPIC_BASE_URL': CUSTOM_URL}}
    assert profile_host(one) == profile_host(two)


# ── Name ─────────────────────────────────────────────────────


def test_name_is_trimmed_and_capped():
    assert custom_profile_name({'name': '  My Provider  '}) == 'My Provider'
    assert len(custom_profile_name({'name': 'x' * 120})) == 40
    assert custom_profile_name({'name': ''}) == ''
    assert custom_profile_name({}) == ''


# ── Labels appear only for custom kinds ──────────────────────


def test_labels_present_only_for_custom_profiles():
    assert custom_profile_labels(_custom()) == {
        'ai_profile_custom_name': 'Bespoke LLM',
    }
    # A profile with no base URL is never custom: no name.
    assert custom_profile_labels({'name': 'Plain', 'env': {}}) == {}
    assert custom_profile_labels(None) == {}
    assert profile_telemetry(None) == {
        'ai_provider_host': 'api.anthropic.com',
        'ai_profile_kind': 'default',
    }
    assert profile_telemetry('default') == {
        'ai_provider_host': 'api.anthropic.com',
        'ai_profile_kind': 'default',
    }


def test_preset_profile_reports_host_and_kind_but_never_a_name(monkeypatch):
    preset_id, preset = next(iter(PROVIDER_PRESETS.items()))
    profile = {
        'name': 'My Moonshot',
        'env': dict(preset.get('env', {})),
    }
    monkeypatch.setattr(
        'app.ai.profiles.ProfileManager.get_profile',
        staticmethod(lambda _pid: profile),
    )
    assert custom_profile_labels(profile) == {}
    props = profile_telemetry('some-id')
    assert props['ai_profile_kind'] == preset_id
    assert props['ai_provider_host'] == profile_host(profile)
    assert 'ai_profile_custom_name' not in props


def test_custom_profile_reports_host_kind_and_name(monkeypatch):
    profile = _custom()
    monkeypatch.setattr(
        'app.ai.profiles.ProfileManager.get_profile',
        staticmethod(lambda _pid: profile),
    )
    assert profile_telemetry('some-id') == {
        'ai_provider_host': 'api.llmbox.example',
        'ai_profile_kind': 'custom',
        'ai_profile_custom_name': 'Bespoke LLM',
    }


# ── Heartbeat entries ────────────────────────────────────────


def test_custom_provider_entries(monkeypatch):
    profiles = [
        _custom(),
        {
            'name': 'Local vLLM',
            'env': {'ANTHROPIC_BASE_URL': 'http://localhost:8000/v1'},
        },
        {'name': 'Plain Claude', 'env': {}},
    ]
    monkeypatch.setattr(
        'app.ai.profiles.ProfileManager.list_profiles',
        staticmethod(lambda: profiles),
    )
    assert custom_provider_entries() == [
        {'name': 'Bespoke LLM', 'host': 'api.llmbox.example'},
        {'name': 'Local vLLM', 'host': 'local'},
    ]
