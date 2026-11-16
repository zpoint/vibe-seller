"""Workflow: a cc-switch style user config no longer blocks a profile.

cc-switch writes ``ANTHROPIC_*`` into the user's Claude Code
``settings.json``. Agents on a non-default profile now run with the
user setting source off (``app.ai.claude_isolation``), so that env can
no longer reroute them — and the user can pick, create and run any
profile while it is there. The default profile keeps using it.
"""

import json

import pytest

from app.ai.claude_isolation import setting_sources_flags
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow


@pytest.fixture
def cc_switch_user_config(monkeypatch, tmp_path):
    cfg = tmp_path / 'claude-home'
    cfg.mkdir()
    (cfg / 'settings.json').write_text(
        json.dumps({
            'env': {
                'ANTHROPIC_BASE_URL': 'https://cc-switch.example/api',
                'ANTHROPIC_AUTH_TOKEN': 'sk-fake',
                'ANTHROPIC_MODEL': 'cc-model',
            }
        })
    )
    monkeypatch.setenv('CLAUDE_CONFIG_DIR', str(cfg))
    return cfg


async def _store(client, name):
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200, r.text
    return r.json()['id']


async def test_non_default_profile_runs_under_a_cc_switch_config(
    admin_client, cc_switch_user_config, install_fake_agent
):
    r = await admin_client.post(
        '/api/profiles',
        json={
            'id': 'wf-deepseek',
            'name': 'DeepSeek',
            'env': {
                'ANTHROPIC_BASE_URL': 'https://api.deepseek.example/anthropic',
                'ANTHROPIC_AUTH_TOKEN': 'sk-profile',
            },
        },
    )
    assert r.status_code == 200, r.text
    r = await admin_client.patch('/api/profiles/wf-deepseek/set-default')
    assert r.status_code == 200, r.text
    assert r.json()['default_profile_id'] == 'wf-deepseek'

    store_id = await _store(admin_client, 'isolation-store')
    r = await admin_client.post(
        '/api/tasks',
        json={
            'title': 'Reply OK',
            'plan_mode': False,
            'store_id': store_id,
            'profile_id': 'wf-deepseek',
        },
    )
    assert r.status_code == 200, r.text
    data = await wait_for_task(admin_client, r.json()['id'])
    assert data['status'] == 'completed', data.get('error')

    runs = [c for c in install_fake_agent.calls if c.task_id == data['id']]
    assert runs and runs[0].profile_id == 'wf-deepseek'
    # ...and that profile launches with the user source off, which is
    # what keeps the cc-switch env above out of its session.
    assert setting_sources_flags(runs[0].profile_id) == [
        '--setting-sources',
        'project,local',
    ]


async def test_default_profile_still_runs_under_a_cc_switch_config(
    admin_client, cc_switch_user_config, install_fake_agent
):
    store_id = await _store(admin_client, 'default-store')
    r = await admin_client.post(
        '/api/tasks',
        json={
            'title': 'Reply OK',
            'plan_mode': False,
            'store_id': store_id,
            'profile_id': 'default',
        },
    )
    data = await wait_for_task(admin_client, r.json()['id'])
    assert data['status'] == 'completed', data.get('error')
    runs = [c for c in install_fake_agent.calls if c.task_id == data['id']]
    assert runs and setting_sources_flags(runs[0].profile_id) == []
