"""Unit tests for the session-cookie persistence pref.

Taobao/Tmall logins ride on SESSION cookies (``is_persistent=0``).
Chromium deletes session cookies at profile startup unless the
profile carries ``session.restore_on_startup == 1`` — without it,
every browser restart logged the store out even though the profile
dir persisted. ``_enable_session_restore`` writes that pref before
every launch; these tests pin the contract.
"""

import json

import pytest

from app.browser import chrome as chrome_mod

pytestmark = pytest.mark.unit

_enable_session_restore = chrome_mod._enable_session_restore


def _prefs_path(tmp_path):
    return tmp_path / 'Default' / 'Preferences'


class TestEnableSessionRestore:
    def test_creates_pref_for_fresh_profile(self, tmp_path):
        _enable_session_restore(tmp_path)
        prefs = json.loads(_prefs_path(tmp_path).read_text())
        assert prefs['session']['restore_on_startup'] == 1

    def test_preserves_existing_preferences(self, tmp_path):
        """Chrome's own settings must survive the merge — we only add
        the one key, never rewrite the profile's config."""
        p = _prefs_path(tmp_path)
        p.parent.mkdir(parents=True)
        p.write_text(
            json.dumps({
                'profile': {'exit_type': 'Normal'},
                'session': {'startup_urls': ['https://x.test']},
            })
        )
        _enable_session_restore(tmp_path)
        prefs = json.loads(p.read_text())
        assert prefs['session']['restore_on_startup'] == 1
        assert prefs['session']['startup_urls'] == ['https://x.test']
        assert prefs['profile']['exit_type'] == 'Normal'

    def test_idempotent_when_already_set(self, tmp_path):
        """Chrome rewrites Preferences on exit; we re-assert the pref
        before every launch. When it's already set, the file must not
        be rewritten (no mtime churn, no corruption window)."""
        _enable_session_restore(tmp_path)
        p = _prefs_path(tmp_path)
        before = p.stat().st_mtime_ns
        _enable_session_restore(tmp_path)
        assert p.stat().st_mtime_ns == before

    def test_corrupt_preferences_recovered(self, tmp_path):
        """A truncated Preferences (killed mid-write) must not abort
        the launch — rewrite minimal prefs with the key set."""
        p = _prefs_path(tmp_path)
        p.parent.mkdir(parents=True)
        p.write_text('{"session": {tru')
        _enable_session_restore(tmp_path)
        prefs = json.loads(p.read_text())
        assert prefs['session']['restore_on_startup'] == 1
