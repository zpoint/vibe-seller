"""Workflow tests for the cc-switch / external-config-override guard.

Covers the two surfaces that share ``assert_profile_compatible``:

1. Profile router rejects non-default profile selection (create /
   update / set-default) with HTTP 409 + the actionable message.
2. Task runner fails fast in ``auto_run_task`` — task transitions
   to FAILED with ``error_category='external_config_override'``
   *before* the agent is ever spawned, and the user-facing error
   includes the clear command + the "quit cc-switch" instruction.

Both paths must surface the same message so the user sees
identical guidance whether they hit it in the Settings UI or via
a failed task card.
"""

import json

import pytest

from app.ai.profiles import ProfileManager
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow


@pytest.fixture
def claude_settings_with_override(monkeypatch, tmp_path):
    """Redirect ``claude_settings_path()`` at a tmp file the test
    populates with an override. Returns the path."""
    path = tmp_path / 'settings.json'
    path.write_text(
        json.dumps({
            'env': {
                'ANTHROPIC_BASE_URL': 'https://cc-switch.example/api',
                'ANTHROPIC_AUTH_TOKEN': 'sk-fake',
            }
        })
    )
    monkeypatch.setattr(
        'app.ai.external_config.claude_settings_path', lambda: path
    )
    return path


@pytest.fixture
def claude_settings_clean(monkeypatch, tmp_path):
    """Redirect at a tmp settings.json with no env block."""
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({'model': 'opus'}))
    monkeypatch.setattr(
        'app.ai.external_config.claude_settings_path', lambda: path
    )
    return path


class TestProfileRouterBlocksNonDefaultUnderOverride:
    """The profile config page surfaces the override conflict at
    the moment the user tries to commit a non-default profile."""

    async def test_create_non_default_profile_returns_409(
        self, admin_client, claude_settings_with_override
    ):
        r = await admin_client.post(
            '/api/profiles',
            json={
                'id': 'deepseek',
                'name': 'DeepSeek',
                'env': {
                    'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic'
                },
            },
        )
        assert r.status_code == 409, r.text
        detail = r.json()['detail']
        # The structured payload carries everything the frontend
        # needs to render an i18n message in the user's locale —
        # no English string in the wire format.
        assert detail['code'] == 'external_config_override'
        assert detail['profile_id'] == 'deepseek'
        assert 'ANTHROPIC_BASE_URL' in detail['overriding_keys']
        assert detail['settings_path'].endswith('settings.json')
        # The copy-paste cleanup command iterates by ``ANTHROPIC_*``
        # prefix so it works for any current or future key without
        # shell-escaping the names. Verify the prefix-iteration form,
        # not specific names.
        assert 'python3 -c' in detail['clear_command']
        assert "startswith('ANTHROPIC_')" in detail['clear_command']
        # The English ``message`` field is kept as a fallback for
        # non-i18n consumers (logs, task.error).
        assert isinstance(detail['message'], str) and detail['message']

    async def test_set_default_non_default_profile_returns_409(
        self, admin_client, claude_settings_with_override
    ):
        # ``default`` profile always exists and is allowed
        # — first set-default succeeds, no override is checked.
        r = await admin_client.patch('/api/profiles/default/set-default')
        assert r.status_code == 200

        # But trying to set a non-default profile as default while
        # override is present → 409.
        # First create the profile via a temporarily-clean settings.
        # (Then we re-poison settings.json before set-default.)
        # Simpler: create the profile by bypassing the route directly
        # via ProfileManager — that exercises only the set-default
        # path's check.

        ProfileManager.create_profile(
            profile_id='custom-deepseek',
            name='Custom DeepSeek',
            env={'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic'},
        )
        r = await admin_client.patch(
            '/api/profiles/custom-deepseek/set-default'
        )
        assert r.status_code == 409, r.text

    async def test_default_profile_always_allowed(
        self, admin_client, claude_settings_with_override
    ):
        """The escape hatch — ``default`` profile must be settable
        as default even with overrides present, since that's the
        documented way to let cc-switch own routing."""
        r = await admin_client.patch('/api/profiles/default/set-default')
        assert r.status_code == 200
        assert r.json()['default_profile_id'] == 'default'


class TestProfileRouterAllowsWhenNoOverride:
    """Sanity check: with a clean settings.json the profile router
    behaves normally."""

    async def test_create_non_default_profile_succeeds(
        self, admin_client, claude_settings_clean
    ):
        r = await admin_client.post(
            '/api/profiles',
            json={
                'id': 'deepseek-clean',
                'name': 'DeepSeek (clean)',
                'env': {
                    'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic'
                },
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()['id'] == 'deepseek-clean'


class TestTaskFailsFastUnderOverride:
    """The task runner must abort *before* spawning the agent —
    the user sees a structured failure with the same actionable
    message, not an opaque API error from whatever endpoint
    cc-switch routed them to."""

    async def test_task_aborts_with_external_config_override_category(
        self, admin_client, claude_settings_with_override, install_fake_agent
    ):
        # Pre-create the conflicting profile via ProfileManager so
        # we can exercise the task runner's check, not the route's.

        ProfileManager.create_profile(
            profile_id='task-deepseek',
            name='Task DeepSeek',
            env={'ANTHROPIC_BASE_URL': 'https://api.deepseek.com/anthropic'},
        )

        store_r = await admin_client.post(
            '/api/stores', json={'name': 'override-store'}
        )
        store_id = store_r.json()['id']

        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Will fail fast',
                'plan_mode': False,
                'store_id': store_id,
                # NB: TaskCreate uses ``profile_id`` (not
                # ``ai_profile_id``) — the router maps it onto
                # ``Task.ai_profile_id``.
                'profile_id': 'task-deepseek',
            },
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id, target='failed')
        assert data['status'] == 'failed'
        # Debug: surface the actual error/category so a regression
        # tells us which fail-path fired.
        assert data['error_category'] == 'external_config_override', (
            f'error_category={data["error_category"]!r} '
            f'error={data.get("error")!r}'
        )
        # task.error is JSON-encoded so the frontend can render it
        # in the user's locale; non-i18n consumers still get the
        # English ``message`` field by parsing the JSON.
        payload = json.loads(data['error'])
        assert payload['code'] == 'external_config_override'
        assert payload['profile_id'] == 'task-deepseek'
        assert 'ANTHROPIC_BASE_URL' in payload['overriding_keys']
        assert 'python3 -c' in payload['clear_command']
        assert isinstance(payload['message'], str) and payload['message']

    async def test_task_runs_when_default_profile_even_under_override(
        self, admin_client, claude_settings_with_override, install_fake_agent
    ):
        """The escape hatch end-to-end: a task on the ``default``
        profile runs to completion even when settings.json has
        overrides — because cc-switch is the intended owner."""
        store_r = await admin_client.post(
            '/api/stores', json={'name': 'default-profile-store'}
        )
        store_id = store_r.json()['id']

        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Should complete on default',
                'plan_mode': False,
                'store_id': store_id,
                'profile_id': 'default',
            },
        )
        assert r.status_code == 200
        data = await wait_for_task(admin_client, r.json()['id'])
        assert data['status'] == 'completed'
