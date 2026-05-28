"""Unit tests for ``app.ai.external_config``.

Detection of cc-switch-style overrides written into
``~/.claude/settings.json``. The hook lives here because both the
profile router and the task runner need the exact same predicate,
and we want a single source of truth for the user-facing message.
"""

import json
from pathlib import Path

import pytest

from app.ai.external_config import (
    ExternalConfigOverrideError,
    assert_profile_compatible,
    detect_claude_settings_overrides,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_settings(monkeypatch, tmp_path):
    """Redirect ``claude_settings_path()`` at a tmp file the test
    controls. Returns a writer callable that takes a dict and dumps
    it to the file; returns None for the "no settings.json" case."""
    path = tmp_path / 'settings.json'
    monkeypatch.setattr(
        'app.ai.external_config.claude_settings_path', lambda: path
    )

    def write(payload: dict | None) -> Path:
        if payload is None:
            if path.exists():
                path.unlink()
        else:
            path.write_text(json.dumps(payload))
        return path

    return write


class TestDetect:
    def test_no_settings_file(self, fake_settings):
        fake_settings(None)
        assert detect_claude_settings_overrides() == []

    def test_settings_without_env_block(self, fake_settings):
        fake_settings({'model': 'opus'})
        assert detect_claude_settings_overrides() == []

    def test_env_block_with_non_anthropic_keys_only(self, fake_settings):
        fake_settings({'env': {'PATH': '/usr/bin', 'DEBUG': '1'}})
        assert detect_claude_settings_overrides() == []

    def test_env_block_with_anthropic_base_url(self, fake_settings):
        fake_settings({'env': {'ANTHROPIC_BASE_URL': 'https://x.test'}})
        assert detect_claude_settings_overrides() == ['ANTHROPIC_BASE_URL']

    def test_multiple_overrides_detected(self, fake_settings):
        fake_settings({
            'env': {
                'ANTHROPIC_BASE_URL': 'https://x',
                'ANTHROPIC_AUTH_TOKEN': 'tok',
                'ANTHROPIC_MODEL': 'deepseek-v4-pro',
                'PATH': '/usr/bin',
            }
        })
        out = detect_claude_settings_overrides()
        assert 'ANTHROPIC_BASE_URL' in out
        assert 'ANTHROPIC_AUTH_TOKEN' in out
        assert 'ANTHROPIC_MODEL' in out
        assert 'PATH' not in out

    def test_malformed_json_degrades_to_empty(self, fake_settings):
        path = fake_settings({'env': {'ANTHROPIC_BASE_URL': 'https://x'}})
        path.write_text('not valid json {')
        # Detection should not raise — degrade to empty.
        assert detect_claude_settings_overrides() == []

    def test_env_field_not_a_dict_degrades(self, fake_settings):
        fake_settings({'env': 'not a dict'})
        assert detect_claude_settings_overrides() == []

    def test_unknown_anthropic_key_still_detected(self, fake_settings):
        """Any future ``ANTHROPIC_*`` Anthropic adds (or that the
        user sets manually) must be flagged. Detection is
        prefix-based so we don't have to chase the upstream list."""
        fake_settings({
            'env': {
                'ANTHROPIC_FUTURE_FLAG_2027': '1',
                'PATH': '/usr/bin',
            }
        })
        assert detect_claude_settings_overrides() == [
            'ANTHROPIC_FUTURE_FLAG_2027'
        ]


class TestAssertProfileCompatible:
    def test_default_profile_always_ok(self, fake_settings):
        # Even with overrides present, the default profile is allowed
        # — it's the documented escape hatch that lets the external
        # tool fully own provider routing.
        fake_settings({'env': {'ANTHROPIC_BASE_URL': 'https://x'}})
        assert_profile_compatible('default')  # no raise

    def test_default_profile_none_passes(self, fake_settings):
        fake_settings({'env': {'ANTHROPIC_BASE_URL': 'https://x'}})
        assert_profile_compatible(None)  # no raise

    def test_non_default_profile_with_no_override_ok(self, fake_settings):
        fake_settings({'model': 'opus'})  # no env block
        assert_profile_compatible('deepseek')  # no raise

    def test_non_default_profile_with_override_raises(self, fake_settings):
        fake_settings({'env': {'ANTHROPIC_BASE_URL': 'https://x.test'}})
        with pytest.raises(ExternalConfigOverrideError) as excinfo:
            assert_profile_compatible('deepseek')
        assert excinfo.value.profile_id == 'deepseek'
        assert 'ANTHROPIC_BASE_URL' in excinfo.value.overriding_keys


class TestUserMessage:
    """The error message is user-facing; verify its load-bearing
    pieces so a copy-edit doesn't accidentally drop the actionable
    instructions."""

    def _err(self, fake_settings, env) -> ExternalConfigOverrideError:
        fake_settings({'env': env})
        try:
            assert_profile_compatible('deepseek')
        except ExternalConfigOverrideError as e:
            return e
        raise AssertionError('expected ExternalConfigOverrideError')

    def test_message_names_the_profile_and_keys(self, fake_settings):
        err = self._err(fake_settings, {'ANTHROPIC_BASE_URL': 'https://x'})
        msg = err.user_message()
        assert 'deepseek' in msg
        assert 'ANTHROPIC_BASE_URL' in msg

    def test_message_offers_default_profile_path(self, fake_settings):
        err = self._err(fake_settings, {'ANTHROPIC_BASE_URL': 'https://x'})
        msg = err.user_message()
        assert 'default' in msg.lower()

    def test_message_offers_clear_command(self, fake_settings):
        err = self._err(fake_settings, {'ANTHROPIC_BASE_URL': 'https://x'})
        msg = err.user_message()
        # User can copy-paste a runnable command.
        assert 'python3 -c' in msg

    def test_message_tells_user_to_quit_the_tool(self, fake_settings):
        """Otherwise cc-switch / similar will rewrite the env on next
        launch and the user thinks the fix didn't stick."""
        err = self._err(fake_settings, {'ANTHROPIC_BASE_URL': 'https://x'})
        msg = err.user_message()
        assert 'cc-switch' in msg.lower() or 'quit' in msg.lower()
