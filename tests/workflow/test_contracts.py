"""API response shape guards — catch drift between backend and frontend.

Expected key sets are derived from TypeScript interfaces in
frontend/src/App.tsx:9-96.
"""

from pathlib import Path
import re

import pytest

from app.task_states import TaskStatus
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow

# ── Expected key sets (from TS interfaces) ───────────

STORE_KEYS = {
    'id',
    'name',
    'browser_backend',
    'browser_config',
    'ziniao_account_id',
    'browser_oauth',
    'platforms',
    'countries',
    'platform_countries',
    'created_at',
}

TASK_KEYS = {
    'id',
    'store_id',
    'title',
    'description',
    'status',
    'plan',
    'plan_history',
    'result',
    'todos',
    'error',
    'error_category',
    'plan_mode',
    'ai_profile_id',
    'wait_condition',
    'schedule_id',
    'batch_id',
    'created_at',
    'started_at',
    'completed_at',
}

TASK_MESSAGE_KEYS = {
    'id',
    'role',
    'content',
    'created_at',
}

PROFILE_KEYS = {'id', 'name', 'description', 'env', 'load_global_mcp'}

EVENT_KEYS = {
    'id',
    'channel_message_id',
    'channel_type',
    'store_id',
    'title',
    'description',
    'event_date',
    'deadline',
    'platform',
    'source_text',
    'status',
    'sync_backend',
    'sync_id',
    'sync_error',
    'case_id',
    'assignees',
    'created_by',
    'priority',
    'created_at',
    'updated_at',
}

EVENT_ACTIVITY_KEYS = {
    'id',
    'event_id',
    'user_id',
    'actor_type',
    'action',
    'content',
    'extra_data',
    'created_at',
}

AUTH_USER_KEYS = {
    'id',
    'username',
    'email',
    'role',
    'is_active',
    'avatar_url',
    'plan_mode_default',
    'default_profile_id',
    'created_at',
}

WS_STRUCTURED_KEYS = {
    'skills',
    'store_profiles',
    'project_knowledge',
    'local_knowledge',
    'root_files',
}


# ── Store contracts ──────────────────────────────────


class TestStoreContracts:
    async def test_store_response_shape(self, admin_client):
        r = await admin_client.post(
            '/api/stores',
            json={'name': 'Shape Test Store'},
        )
        assert r.status_code == 200
        store = r.json()
        assert STORE_KEYS <= set(store.keys()), (
            f'Missing: {STORE_KEYS - set(store.keys())}'
        )
        # Fetch by id
        r2 = await admin_client.get(f'/api/stores/{store["id"]}')
        assert r2.status_code == 200
        assert STORE_KEYS <= set(r2.json().keys())

    async def test_store_list_shape(self, admin_client):
        await admin_client.post('/api/stores', json={'name': 'List Shape'})
        r = await admin_client.get('/api/stores')
        assert r.status_code == 200
        items = r.json()
        assert len(items) >= 1
        for item in items:
            assert STORE_KEYS <= set(item.keys()), (
                f'Missing: {STORE_KEYS - set(item.keys())}'
            )


# ── Task contracts ───────────────────────────────────


class TestTaskContracts:
    async def test_task_response_shape(self, admin_client):
        r = await admin_client.post('/api/tasks', json={'title': 'Shape task'})
        assert r.status_code == 200
        task = r.json()
        assert TASK_KEYS <= set(task.keys()), (
            f'Missing: {TASK_KEYS - set(task.keys())}'
        )

    async def test_task_create_shape(self, admin_client):
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Create shape', 'description': 'desc'},
        )
        assert r.status_code == 200
        assert TASK_KEYS <= set(r.json().keys())


# ── Task message contracts ───────────────────────────


class TestTaskMessageContract:
    async def test_task_message_response_shape(
        self, admin_client, install_fake_agent
    ):
        """GET /api/tasks/{id}/messages returns correct shape."""
        r = await admin_client.post(
            '/api/tasks', json={'title': 'Message shape task'}
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        await wait_for_task(admin_client, task_id)

        r2 = await admin_client.get(f'/api/tasks/{task_id}/messages')
        assert r2.status_code == 200
        msgs = r2.json()
        assert len(msgs) >= 1
        for msg in msgs:
            assert TASK_MESSAGE_KEYS <= set(msg.keys()), (
                f'Missing: {TASK_MESSAGE_KEYS - set(msg.keys())}'
            )


# ── Profile contracts ────────────────────────────────


class TestProfileContracts:
    async def test_profile_list_shape(self, admin_client):
        r = await admin_client.get('/api/profiles')
        assert r.status_code == 200
        data = r.json()
        assert 'profiles' in data
        for p in data['profiles']:
            assert PROFILE_KEYS <= set(p.keys()), (
                f'Missing: {PROFILE_KEYS - set(p.keys())}'
            )

    async def test_presets_shape(self, admin_client):
        r = await admin_client.get('/api/profiles/presets')
        assert r.status_code == 200
        data = r.json()
        assert 'presets' in data
        for _name, preset in data['presets'].items():
            assert {'name', 'env'} <= set(preset.keys())


# ── Event contracts ──────────────────────────────────


class TestEventContracts:
    async def test_event_shape(self, admin_client):
        r = await admin_client.post(
            '/api/events', json={'title': 'Shape event'}
        )
        assert r.status_code == 200
        assert EVENT_KEYS <= set(r.json().keys()), (
            f'Missing: {EVENT_KEYS - set(r.json().keys())}'
        )

    async def test_event_activity_shape(self, admin_client):
        # Create event, then add an activity note
        r = await admin_client.post(
            '/api/events', json={'title': 'Activity shape'}
        )
        eid = r.json()['id']
        r2 = await admin_client.post(
            f'/api/events/{eid}/activities',
            json={'content': 'test note', 'action': 'note_added'},
        )
        assert r2.status_code == 200
        assert EVENT_ACTIVITY_KEYS <= set(r2.json().keys()), (
            f'Missing: {EVENT_ACTIVITY_KEYS - set(r2.json().keys())}'
        )


# ── Auth contracts ───────────────────────────────────


class TestAuthContracts:
    async def test_me_shape(self, admin_client):
        r = await admin_client.get('/api/auth/me')
        assert r.status_code == 200
        assert AUTH_USER_KEYS <= set(r.json().keys()), (
            f'Missing: {AUTH_USER_KEYS - set(r.json().keys())}'
        )


# ── Workspace contracts ──────────────────────────────


EMAIL_ACCOUNT_KEYS = {
    'id',
    'email',
    'imap_host',
    'imap_port',
    'use_ssl',
    'smtp_host',
    'smtp_port',
    'smtp_use_tls',
    'created_at',
    'updated_at',
}

EMAIL_INFO_KEYS = {
    'store_id',
    'accounts',
    'schema',
    'sample_queries',
    'sync_interval',
}

WS_SKILL_KEYS = {
    'slug',
    'path',
    'files',
    'file_count',
    'description',
    'source',
    'origin_url',
}


class TestWorkspaceContracts:
    async def test_workspace_structured_shape(self, admin_client):
        r = await admin_client.get('/api/workspace/structured')
        assert r.status_code == 200
        assert WS_STRUCTURED_KEYS <= set(r.json().keys()), (
            f'Missing: {WS_STRUCTURED_KEYS - set(r.json().keys())}'
        )

    async def test_skill_has_source_field(self, admin_client):
        """User-created skills must have source='user'."""
        await admin_client.post(
            '/api/workspace/skill',
            json={'name': 'contract-test-skill', 'description': 'test'},
        )
        r = await admin_client.get('/api/workspace/structured')
        assert r.status_code == 200
        skills = r.json()['skills']
        for skill in skills:
            assert WS_SKILL_KEYS <= set(skill.keys()), (
                f'Missing: {WS_SKILL_KEYS - set(skill.keys())}'
            )
            assert skill['source'] in (
                'builtin',
                'custom',
                'imported',
            )


# ── Task status contract ────────────────────────────


class TestTaskStatusContract:
    def test_task_statuses_match_frontend(self):
        """Backend and frontend agree on task status values."""
        ts_path = (
            Path(__file__).resolve().parents[2]
            / 'frontend'
            / 'src'
            / 'taskStates.ts'
        )
        content = ts_path.read_text()
        # Extract status keys from TASK_UI record
        fe_statuses = set(re.findall(r'^\s+(\w+)\s*:', content, re.MULTILINE))
        # Filter to only valid status names (exclude interface
        # field names by checking against known statuses)
        be_statuses = {s.value for s in TaskStatus}
        # Frontend must have all backend statuses
        assert be_statuses <= fe_statuses, (
            f'Frontend missing statuses: {be_statuses - fe_statuses}'
        )
        # Frontend should not have extra statuses
        extra = fe_statuses & be_statuses  # intersection
        assert extra == be_statuses, (
            f'Status mismatch: backend={be_statuses}, '
            f'frontend keys matching={extra}'
        )


# ── Email account contracts ─────────────────────────


class TestEmailAccountContracts:
    async def test_email_account_response_shape(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'contract@gmail.com',
                'password': 'test',
            },
        )
        assert r.status_code == 200
        assert EMAIL_ACCOUNT_KEYS <= set(r.json().keys()), (
            f'Missing: {EMAIL_ACCOUNT_KEYS - set(r.json().keys())}'
        )

    async def test_email_info_response_shape(self, admin_client):
        # Create store + account + link
        sr = await admin_client.post(
            '/api/stores', json={'name': 'Contract Store'}
        )
        store_id = sr.json()['id']
        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'info-contract@gmail.com',
                'password': 'test',
            },
        )
        acct_id = er.json()['id']
        await admin_client.post(
            f'/api/stores/{store_id}/emails',
            json={'email_account_id': acct_id},
        )

        r = await admin_client.get(
            f'/api/email-accounts/info-by-store/{store_id}'
        )
        assert r.status_code == 200
        assert EMAIL_INFO_KEYS <= set(r.json().keys()), (
            f'Missing: {EMAIL_INFO_KEYS - set(r.json().keys())}'
        )
