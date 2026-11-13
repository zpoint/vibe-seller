"""API response shape guards — catch drift between backend and frontend.

Expected key sets are derived from TypeScript interfaces in
frontend/src/App.tsx:9-96.
"""

from pathlib import Path
import re

import pytest

from app.task_states import TaskStatus
from app.uploads import MAX_UPLOAD_SIZE
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
    'submitted_result',
    'review_gaps',
    'submission_count',
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
    # Live usage meter (app/task_usage.py) — the same names as the
    # ``task_usage`` SSE event.
    'cost_usd',
    'input_tokens',
    'output_tokens',
    'cache_read_tokens',
    'cache_write_tokens',
    'context_tokens',
    'context_window',
    'usage_model',
    'cost_basis',
    'num_turns',
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
    'sync_profile_to_schedules',
    'created_at',
}

API_TOKEN_KEYS = {
    'id',
    'name',
    'prefix',
    'created_at',
    'last_used_at',
    'expires_at',
}

WS_STRUCTURED_KEYS = {
    'skills',
    'store_profiles',
    'project_knowledge',
    'local_knowledge',
    'root_files',
}
WS_STORE_PROFILE_KEYS = {
    'slug',
    'path',
    'files',
    'file_count',
    'has_content',
    'data_path',
    'data_files',
    'data_file_count',
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
            json={
                'title': 'Create shape',
                'description': 'desc',
            },
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

        # No-store tasks are plan-mode and pause at PLANNED; the
        # shape contract only needs the run's messages to exist.
        await wait_for_task(admin_client, task_id, target='planned')

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


# ── API token contracts ──────────────────────────────


class TestApiTokenContracts:
    async def test_token_list_shape(self, admin_client):
        await admin_client.post(
            '/api/auth/tokens', json={'name': 'shape-token'}
        )
        r = await admin_client.get('/api/auth/tokens')
        assert r.status_code == 200
        entry = r.json()[0]
        assert API_TOKEN_KEYS <= set(entry.keys()), (
            f'Missing: {API_TOKEN_KEYS - set(entry.keys())}'
        )
        # The secret is never part of the list.
        assert 'token' not in entry

    async def test_token_created_shape_carries_the_secret(self, admin_client):
        r = await admin_client.post(
            '/api/auth/tokens', json={'name': 'shape-token'}
        )
        assert r.status_code == 200
        body = r.json()
        assert API_TOKEN_KEYS <= set(body.keys()), (
            f'Missing: {API_TOKEN_KEYS - set(body.keys())}'
        )
        assert body['token'].startswith('vst_')


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
    'allow_send',
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
        for store in r.json()['store_profiles']:
            assert WS_STORE_PROFILE_KEYS <= set(store.keys()), (
                f'Missing: {WS_STORE_PROFILE_KEYS - set(store.keys())}'
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


# ── Update-status contract ───────────────────────────

UPDATE_STATUS_KEYS = {
    'supported',
    'state',
    'version',
    'current_version',
    'downloaded_bytes',
    'total_bytes',
    'error',
    'running_tasks',
    'platform',
    # Which mechanism `ready` refers to, whether it applies itself, and
    # whether it may ask; plus what the last apply did, and whether
    # THIS release is the one it had to put back. The modal reads all
    # five — see app/auto_update.snapshot().
    'path',
    'auto_apply',
    'popup_due',
    'ready_since',
    'last_update',
    'refusal',
}

RELEASE_NOTES_KEYS = {
    'since',
    'until',
    'releases',
    'highlights',
    'notes',
}


# ── Version contract ─────────────────────────────────


class TestVersionContracts:
    async def test_version_endpoint(self, admin_client):
        r = await admin_client.get('/api/version')
        assert r.status_code == 200
        body = r.json()
        assert 'version' in body
        assert isinstance(body['version'], str) and body['version']
        # `vibe-seller upgrade` reads it to tell "restarting once tasks
        # finish" apart from "never picked the upgrade up".
        assert 'pending_version' in body
        assert body['pending_version'] is None

    async def test_health_includes_version(self, admin_client):
        r = await admin_client.get('/api/health')
        assert r.status_code == 200
        body = r.json()
        assert body.get('status') == 'ok'
        assert 'version' in body

    async def test_update_status_shape(self, admin_client):
        # The modal polls this — the key set is its interface.
        r = await admin_client.get('/api/system/update-status')
        assert r.status_code == 200
        assert set(r.json().keys()) == UPDATE_STATUS_KEYS
        body = r.json()
        # A test server neither downloads nor applies, so the shape must
        # still answer the two questions the modal asks: what would
        # apply, and may it ask yet. Absent answers here would make the
        # frontend branch on `undefined`.
        assert body['path'] in ('pip', 'installer', None)
        assert body['auto_apply'] is False
        assert body['popup_due'] is False
        assert body['ready_since'] is None
        assert body['last_update'] is None

    async def test_release_notes_shape(self, admin_client, monkeypatch):
        """`已更新到 v{x}` asks for the range it just crossed."""
        from unittest.mock import AsyncMock  # noqa: PLC0415 — test-local

        import app.routers.system as sr  # noqa: PLC0415

        monkeypatch.setattr(
            sr,
            'fetch_release_notes',
            AsyncMock(
                return_value=[
                    {
                        'version': '0.7.0',
                        'name': 'v0.7.0',
                        'body': '## Highlights\n\n- new thing\n',
                        'url': 'https://x',
                        'published_at': None,
                    }
                ]
            ),
        )
        r = await admin_client.get(
            '/api/system/release-notes', params={'since': '0.6.0'}
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == RELEASE_NOTES_KEYS
        assert body['since'] == '0.6.0'
        assert body['until'] == body['until']  # defaults to the running version
        assert body['releases'] == ['0.7.0']
        assert body['highlights'][0]['version'] == '0.7.0'

    async def test_release_notes_needs_a_start(self, admin_client):
        r = await admin_client.get('/api/system/release-notes')
        assert r.status_code == 422


class TestUpdateCheckContract:
    async def test_update_check_shape(self, admin_client, monkeypatch):
        from unittest.mock import AsyncMock  # noqa: PLC0415 — test-local

        import app.routers.system as sr  # noqa: PLC0415

        monkeypatch.setattr(sr, 'APP_VERSION', '0.6.0')
        monkeypatch.setattr(
            sr, 'fetch_latest_pypi_version', AsyncMock(return_value='0.7.0')
        )
        monkeypatch.setattr(
            sr,
            'fetch_release_notes',
            AsyncMock(
                return_value=[
                    {
                        'version': '0.7.0',
                        'name': 'v0.7.0',
                        'body': '## Highlights\n\n- a\n',
                        'url': 'https://x',
                        'published_at': None,
                    }
                ]
            ),
        )
        monkeypatch.setattr(sr, 'get_platform', lambda: 'linux')
        r = await admin_client.get('/api/system/update-check')
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) == {
            'dev',
            'update_available',
            'current_version',
            'latest_version',
            'channel',
            'platform',
            'upgrade_command',
            'download_url',
            'releases_page_url',
            'in_app_update',
            'releases',
            'highlights',
            'notes',
        }
        assert body['highlights'] == [
            {'version': '0.7.0', 'zh': '- a', 'en': ''}
        ]


# ── Chat-bot contracts ───────────────────────────────

#: What GET /api/chat-bots/platforms serves per platform. The form's
#: whole security section is built from ``security_options`` /
#: ``security_required``, so a renamed or dropped key silently turns
#: DingTalk's "at least one setting" rule back into "show every field"
#: — the bug this registry exists to prevent.
CHAT_PLATFORM_KEYS = {
    'name',
    'label',
    'hosts',
    'path_prefix',
    'uses_secret',
    'uses_keyword',
    'security_options',
    'security_required',
    'supports_file',
    'max_message',
    'limits',
    'limit_unit',
    'doc_url',
}

#: The edit form's payload (GET /api/chat-bots/{id}, create, update).
CHAT_BOT_KEYS = {
    'id',
    'name',
    'webhook_url',
    'platform',
    'secret',
    'keyword',
    'security_mode',
    'has_secret',
    'can_send_file',
    'created_at',
    'updated_at',
}

#: The list row — masked, so it is NOT the same shape on purpose.
CHAT_BOT_SUMMARY_KEYS = {
    'id',
    'name',
    'platform',
    'webhook_url_masked',
    'has_secret',
    'keyword',
    'can_send_file',
    'created_at',
    'updated_at',
}

DINGTALK_URL = (
    'https://oapi.dingtalk.com/robot/send?access_token=abcdef0123456789'
)


class TestChatBotContracts:
    async def test_platform_registry_shape(self, admin_client):
        r = await admin_client.get('/api/chat-bots/platforms')
        assert r.status_code == 200
        platforms = r.json()
        for p in platforms:
            assert set(p.keys()) == CHAT_PLATFORM_KEYS, (
                f'{p.get("name")}: '
                f'extra={set(p) - CHAT_PLATFORM_KEYS} '
                f'missing={CHAT_PLATFORM_KEYS - set(p)}'
            )
        by_name = {p['name']: p for p in platforms}
        assert set(by_name) == {'wecom', 'dingtalk', 'feishu', 'slack'}
        # The values the security section branches on.
        assert by_name['dingtalk']['security_required'] is True
        assert by_name['dingtalk']['security_options'] == [
            'sign',
            'keyword',
            'ip',
        ]
        assert by_name['feishu']['security_required'] is False
        assert by_name['feishu']['security_options'] == [
            'sign',
            'keyword',
            'ip',
        ]
        assert by_name['wecom']['security_options'] == []
        assert by_name['slack']['security_options'] == []

    async def test_platform_keys_match_the_ts_interface(self):
        """The component's ``ChatPlatform`` interface and the server's
        payload are one contract, kept honest in both directions."""
        src = (
            Path(__file__).resolve().parents[2]
            / 'frontend/src/components/ChatBotSection.tsx'
        ).read_text()
        block = re.search(
            r'export interface ChatPlatform \{(.*?)\n\}', src, re.S
        )
        assert block, 'ChatPlatform interface not found in ChatBotSection'
        keys = set(re.findall(r'^\s{2}(\w+)\??:', block.group(1), re.M))
        assert keys == CHAT_PLATFORM_KEYS, (
            f'extra={keys - CHAT_PLATFORM_KEYS} '
            f'missing={CHAT_PLATFORM_KEYS - keys}'
        )

    async def test_bot_shapes_create_read_list(self, admin_client):
        r = await admin_client.post(
            '/api/chat-bots',
            json={
                'name': 'Contract Bot',
                'webhook_url': DINGTALK_URL,
                'secret': 'SEC0123456789',
                'security_mode': 'sign',
            },
        )
        assert r.status_code == 200, r.text
        bot = r.json()
        assert set(bot.keys()) == CHAT_BOT_KEYS, (
            f'extra={set(bot) - CHAT_BOT_KEYS} '
            f'missing={CHAT_BOT_KEYS - set(bot)}'
        )
        assert bot['security_mode'] == 'sign'

        r2 = await admin_client.get(f'/api/chat-bots/{bot["id"]}')
        assert r2.status_code == 200
        assert set(r2.json().keys()) == CHAT_BOT_KEYS

        r3 = await admin_client.get('/api/chat-bots')
        assert r3.status_code == 200
        rows = r3.json()
        assert rows, 'the bot just created should be listed'
        for row in rows:
            assert set(row.keys()) == CHAT_BOT_SUMMARY_KEYS, (
                f'extra={set(row) - CHAT_BOT_SUMMARY_KEYS} '
                f'missing={CHAT_BOT_SUMMARY_KEYS - set(row)}'
            )


async def test_settings_serves_the_upload_cap_the_frontend_reads(
    admin_client,
):
    """`GET /api/settings` is how the browser learns the upload cap.

    The dialog's hint text, its size check, and its "too large" message
    all derive from this one key (see frontend/src/uploadLimits.ts). If
    it stops being served, or stops matching the constant the server
    actually enforces, the UI silently reverts to a hardcoded guess —
    which is how three different caps got out of step in the first
    place.
    """
    r = await admin_client.get('/api/settings')
    assert r.status_code == 200
    body = r.json()
    assert 'max_upload_size' in body, sorted(body)
    # Served as a string, because every value in this payload is.
    assert body['max_upload_size'] == str(MAX_UPLOAD_SIZE)
    assert int(body['max_upload_size']) > 0
