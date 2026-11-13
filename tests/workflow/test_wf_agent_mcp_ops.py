"""Workflow tests for the operator-parity MCP tools (the B rows):
stores, browsers, Ziniao accounts, AI profiles, app settings +
Google Workspace + vision, email accounts, chat bots, TickTick.

Same pattern as ``test_wf_agent_mcp.py``: real HTTP → the real MCP
endpoint → the real REST route handlers → the real DB. Nothing here
launches a browser, sends mail, or calls a model: the network edges
the routes own (Ziniao client, IMAP/SMTP, kie.ai, TickTick API, the
gws binary, profile validation) are monkeypatched AT the route's
own import site, so the route code itself still runs.

Per the brief's rules, every tool + action gets:

* a happy path through the real route;
* a validation error naming the missing field (ToolError);
* a permission case where the route restricts (member vs admin on
  the admin-only settings routes — the same 403 the UI gets);
* a redaction assertion on every secret-bearing resource: the
  plaintext never appears anywhere in the JSON the tool returns.
"""

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from app.models.chat_bot import ChatBot
from app.models.email_account import EmailAccount
from app.models.user import User
from app.models.ziniao_account import ZiniaoAccount
from app.utils.crypto import decrypt_password, encrypt_password
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario
from tests.workflow.test_wf_agent_mcp import call_tool, rpc

pytestmark = pytest.mark.workflow

WECOM_WEBHOOK = (
    'https://qyapi.weixin.qq.com/cgi-bin/webhook/send'
    '?key=00000000-0000-0000-0000-000000000000'
)
DINGTALK_WEBHOOK = (
    'https://oapi.dingtalk.com/robot/send'
    '?access_token=000000000000000000000000000000000000000000000000'
    '0000000000000000'
)


# ── manage_store ──────────────────────────────────────


class TestManageStore:
    async def test_list_get_create_update_delete(self, admin_client):
        created, _ = await call_tool(
            admin_client,
            'manage_store',
            {
                'action': 'create',
                'name': 'MCP Ops Store',
                'browser_backend': 'chrome',
                'browser_config': {'proxy_server': 'http://127.0.0.1:7890'},
            },
        )
        store = created['created']
        assert store['name'] == 'MCP Ops Store'
        assert store['browser_backend'] == 'chrome'
        assert store['browser_config']['proxy_server'] == (
            'http://127.0.0.1:7890'
        )
        # Create mirrors the UI's payload: platforms/countries come
        # from the schema defaults, not from the caller.
        assert store['platforms'] == ['amazon']
        assert store['countries'] == ['US']
        store_id = store['id']

        listed, _ = await call_tool(
            admin_client, 'manage_store', {'action': 'list'}
        )
        assert store_id in [s['id'] for s in listed['stores']]

        got, _ = await call_tool(
            admin_client,
            'manage_store',
            {'action': 'get', 'store_id': store_id},
        )
        assert got['store']['id'] == store_id

        updated, _ = await call_tool(
            admin_client,
            'manage_store',
            {
                'action': 'update',
                'store_id': store_id,
                'platform_countries': {'amazon': ['US', 'AE']},
            },
        )
        assert updated['updated']['platform_countries'] == {
            'amazon': ['US', 'AE']
        }

        bookmarks, _ = await call_tool(
            admin_client,
            'manage_store',
            {'action': 'bookmarks', 'store_id': store_id},
        )
        assert bookmarks == {'bookmarks': []}  # no profile dir yet

        deleted, _ = await call_tool(
            admin_client,
            'manage_store',
            {'action': 'delete', 'store_id': store_id},
        )
        assert deleted['ok'] is True

    async def test_create_requires_a_name(self, admin_client):
        _, envelope = await call_tool(
            admin_client, 'manage_store', {'action': 'create'}
        )
        assert envelope['isError'] is True
        assert 'name' in envelope['content'][0]['text']

    async def test_update_requires_a_field(self, admin_client):
        created = await admin_client.post(
            '/api/stores', json={'name': 'No Fields Store'}
        )
        _, envelope = await call_tool(
            admin_client,
            'manage_store',
            {'action': 'update', 'store_id': created.json()['id']},
        )
        assert envelope['isError'] is True
        assert 'platform_countries' in envelope['content'][0]['text']

    async def test_delete_refuses_a_store_with_active_tasks(
        self, admin_client, install_fake_agent
    ):
        """The route's own rule, surfaced through the tool. A RUNNING
        task is what blocks deletion (PENDING does not), so this uses
        the FakeAgent like the lifecycle tests do."""
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='later', gate=gate
        )
        created = await admin_client.post(
            '/api/stores', json={'name': 'Busy Store'}
        )
        store_id = created.json()['id']
        task = await admin_client.post(
            '/api/tasks',
            json={'title': 'Blocks delete', 'store_id': store_id},
        )
        task_id = task.json()['id']
        await install_fake_agent.wait_started(task_id)

        _, envelope = await call_tool(
            admin_client,
            'manage_store',
            {'action': 'delete', 'store_id': store_id},
        )
        assert envelope['isError'] is True
        assert 'active' in envelope['content'][0]['text'].lower()

        gate.set()
        await wait_for_task(admin_client, task_id)
        # Now nothing runs — the delete goes through.
        deleted, _ = await call_tool(
            admin_client,
            'manage_store',
            {'action': 'delete', 'store_id': store_id},
        )
        assert deleted['ok'] is True

    async def test_unknown_action_names_the_valid_ones(self, admin_client):
        # Refused by the dispatcher's schema validation (INVALID_PARAMS),
        # naming the bad value and the valid choices.
        r = await rpc(
            admin_client,
            'tools/call',
            {'name': 'manage_store', 'arguments': {'action': 'remove'}},
        )
        error = r.json()['error']
        assert error['code'] == -32602
        assert 'remove' in error['message']
        assert 'delete' in error['message']


# ── browser ───────────────────────────────────────────


class TestBrowserTool:
    async def _make_store(self, admin_client) -> str:
        r = await admin_client.post(
            '/api/stores', json={'name': 'Browser Store'}
        )
        return r.json()['id']

    async def test_start_store_uses_the_route(self, admin_client):
        store_id = await self._make_store(admin_client)
        data, envelope = await call_tool(
            admin_client,
            'browser',
            {'action': 'start_store', 'store_id': store_id},
        )
        assert envelope['isError'] is False
        assert data['ok'] is True

    async def test_start_store_requires_store_id(self, admin_client):
        _, envelope = await call_tool(
            admin_client, 'browser', {'action': 'start_store'}
        )
        assert envelope['isError'] is True
        assert 'store_id' in envelope['content'][0]['text']

    async def test_start_store_missing_store_is_an_error(self, admin_client):
        _, envelope = await call_tool(
            admin_client,
            'browser',
            {'action': 'start_store', 'store_id': 'nope'},
        )
        assert envelope['isError'] is True
        assert 'not found' in envelope['content'][0]['text'].lower()

    async def test_start_aux(self, admin_client, monkeypatch):
        store_id = await self._make_store(admin_client)
        started = mock.AsyncMock(return_value={'ok': True, 'ws': 'ws://x'})
        monkeypatch.setattr(
            'app.routers.browser.aux_browser.start_aux', started
        )
        data, _ = await call_tool(
            admin_client,
            'browser',
            {'action': 'start_aux', 'store_id': store_id},
        )
        assert data['ws'] == 'ws://x'
        started.assert_awaited_once()

    async def test_start_web(self, admin_client, monkeypatch):
        started = mock.AsyncMock(return_value=None)
        monkeypatch.setattr(
            'app.routers.browser.browser_manager.start_web_session',
            started,
        )
        data, _ = await call_tool(
            admin_client, 'browser', {'action': 'start_web'}
        )
        assert data['ok'] is True
        started.assert_awaited_once()

    async def test_ziniao_launcher_returns_a_download_url(self, admin_client):
        data, _ = await call_tool(
            admin_client, 'browser', {'action': 'ziniao_launcher'}
        )
        assert data['ok'] is True
        assert data['download_url'].endswith('/api/ziniao/launcher')
        assert data['download_url'].startswith('http')
        # The URL is for a human — the tool says so.
        assert 'user' in data['note'].lower()


# ── manage_ziniao_account ─────────────────────────────


class TestManageZiniaoAccount:
    async def test_crud_and_password_never_comes_back(self, admin_client):
        created, _ = await call_tool(
            admin_client,
            'manage_ziniao_account',
            {
                'action': 'create',
                'company': 'acme',
                'username': 'ops@acme.example',
                'password': 'ziniao-pass-1234',
                'name': '',
            },
        )
        account = created['created']
        # The UI's rule: a blank name falls back to the company.
        assert account['name'] == 'acme'
        assert 'password' not in json.dumps(created)

        listed, _ = await call_tool(
            admin_client, 'manage_ziniao_account', {'action': 'list'}
        )
        assert account['id'] in [a['id'] for a in listed['accounts']]
        assert 'ziniao-pass-1234' not in json.dumps(listed)

        updated, _ = await call_tool(
            admin_client,
            'manage_ziniao_account',
            {
                'action': 'update',
                'account_id': account['id'],
                'socket_port': 16861,
            },
        )
        assert updated['updated']['socket_port'] == 16861
        assert 'ziniao-pass-1234' not in json.dumps(updated)

        deleted, _ = await call_tool(
            admin_client,
            'manage_ziniao_account',
            {'action': 'delete', 'account_id': account['id']},
        )
        assert deleted['ok'] is True

    async def test_create_requires_password(self, admin_client):
        _, envelope = await call_tool(
            admin_client,
            'manage_ziniao_account',
            {'action': 'create', 'company': 'acme', 'username': 'u'},
        )
        assert envelope['isError'] is True
        assert 'password' in envelope['content'][0]['text']

    async def test_browsers_lists_profiles(self, admin_client, monkeypatch):
        created, _ = await call_tool(
            admin_client,
            'manage_ziniao_account',
            {
                'action': 'create',
                'company': 'acme',
                'username': 'ops@acme.example',
                'password': 'ziniao-pass-1234',
            },
        )
        account_id = created['created']['id']
        monkeypatch.setattr(
            'app.routers.ziniao_accounts.ensure_ziniao_running',
            mock.AsyncMock(return_value=True),
        )

        async def fake_try_connect(port, data, timeout=0):
            return (
                {
                    'statusCode': '0',
                    'browserList': [
                        {'browserName': 'Main', 'browserOauth': 'oauth-1'}
                    ],
                },
                '127.0.0.1',
            )

        monkeypatch.setattr(
            'app.routers.ziniao_accounts.try_connect_ziniao', fake_try_connect
        )
        data, _ = await call_tool(
            admin_client,
            'manage_ziniao_account',
            {'action': 'browsers', 'account_id': account_id},
        )
        assert data['browsers'] == [
            {'browser_name': 'Main', 'browser_oauth': 'oauth-1'}
        ]

    async def test_restart_only_in_normal_mode(self, admin_client, monkeypatch):
        created, _ = await call_tool(
            admin_client,
            'manage_ziniao_account',
            {
                'action': 'create',
                'company': 'acme',
                'username': 'ops@acme.example',
                'password': 'ziniao-pass-1234',
            },
        )
        account_id = created['created']['id']
        relaunch = mock.AsyncMock(return_value=True)
        monkeypatch.setattr(
            'app.routers.ziniao_accounts.get_ziniao_status',
            mock.AsyncMock(return_value={'status': 'running_normal'}),
        )
        monkeypatch.setattr(
            'app.routers.ziniao_accounts.kill_and_relaunch_ziniao', relaunch
        )
        data, _ = await call_tool(
            admin_client,
            'manage_ziniao_account',
            {'action': 'restart', 'account_id': account_id},
        )
        assert data['ok'] is True
        relaunch.assert_awaited_once()

        # A WebDriver session is live → the route refuses (409).
        monkeypatch.setattr(
            'app.routers.ziniao_accounts.get_ziniao_status',
            mock.AsyncMock(return_value={'status': 'running_webdriver'}),
        )
        _, envelope = await call_tool(
            admin_client,
            'manage_ziniao_account',
            {'action': 'restart', 'account_id': account_id},
        )
        assert envelope['isError'] is True
        assert 'running_normal' in envelope['content'][0]['text']


# ── manage_profile ────────────────────────────────────


PROFILE_ENV = {
    'ANTHROPIC_BASE_URL': 'https://api.acme.example/anthropic',
    'ANTHROPIC_MODEL': 'acme-large',
    'ANTHROPIC_AUTH_TOKEN': 'sk-profile-secret-1234',
}


class TestManageProfile:
    async def test_list_get_create_update_set_default_delete(
        self, admin_client, isolated_profiles
    ):
        created, _ = await call_tool(
            admin_client,
            'manage_profile',
            {
                'action': 'create',
                'profile_id': 'acme',
                'name': 'Acme',
                'env': PROFILE_ENV,
            },
        )
        assert created['created']['id'] == 'acme'
        # The API key is write-only: masked in the response, and the
        # STORED profile keeps the real one (the write path works).
        assert created['created']['env']['ANTHROPIC_AUTH_TOKEN'] != (
            'sk-profile-secret-1234'
        )
        stored = json.loads(isolated_profiles.read_text())
        assert (
            stored['profiles']['acme']['env']['ANTHROPIC_AUTH_TOKEN']
            == 'sk-profile-secret-1234'
        )
        # The non-secret env rows stay readable so an agent can tell
        # profiles apart.
        assert created['created']['env']['ANTHROPIC_MODEL'] == 'acme-large'

        listed, _ = await call_tool(
            admin_client, 'manage_profile', {'action': 'list'}
        )
        entry = next(p for p in listed['profiles'] if p['id'] == 'acme')
        assert entry['env']['ANTHROPIC_AUTH_TOKEN'] != (
            'sk-profile-secret-1234'
        )
        assert 'sk-profile-secret-1234' not in json.dumps(listed)

        got, _ = await call_tool(
            admin_client,
            'manage_profile',
            {'action': 'get', 'profile_id': 'acme'},
        )
        assert got['profile']['name'] == 'Acme'

        updated, _ = await call_tool(
            admin_client,
            'manage_profile',
            {
                'action': 'update',
                'profile_id': 'acme',
                'env': dict(PROFILE_ENV, ANTHROPIC_MODEL='acme-small'),
            },
        )
        assert updated['updated']['env']['ANTHROPIC_MODEL'] == ('acme-small')
        assert updated['updated']['env']['ANTHROPIC_AUTH_TOKEN'] != (
            'sk-profile-secret-1234'
        )

        defaulted, _ = await call_tool(
            admin_client,
            'manage_profile',
            {'action': 'set_default', 'profile_id': 'acme'},
        )
        assert defaulted['default_profile_id'] == 'acme'

        deleted, _ = await call_tool(
            admin_client,
            'manage_profile',
            {'action': 'delete', 'profile_id': 'acme'},
        )
        assert deleted['ok'] is True

    async def test_create_with_set_default_is_opt_in(
        self,
        admin_client,
        isolated_profiles,
        override_async_session,
        admin_user,
    ):
        await call_tool(
            admin_client,
            'manage_profile',
            {
                'action': 'create',
                'profile_id': 'beta',
                'name': 'Beta',
                'env': PROFILE_ENV,
            },
        )
        # Not asked → the default profile is untouched.
        async with override_async_session() as db:
            user = await db.get(User, admin_user.id)
            assert user.default_profile_id != 'beta'

        out, _ = await call_tool(
            admin_client,
            'manage_profile',
            {
                'action': 'create',
                'profile_id': 'gamma',
                'name': 'Gamma',
                'env': PROFILE_ENV,
                'set_default': True,
            },
        )
        assert out['default']['default_profile_id'] == 'gamma'

    async def test_get_unknown_profile(self, admin_client):
        _, envelope = await call_tool(
            admin_client,
            'manage_profile',
            {'action': 'get', 'profile_id': 'nope'},
        )
        assert envelope['isError'] is True

    async def test_create_requires_a_name(self, admin_client):
        _, envelope = await call_tool(
            admin_client,
            'manage_profile',
            {'action': 'create', 'profile_id': 'x'},
        )
        assert envelope['isError'] is True
        assert 'name' in envelope['content'][0]['text']

    async def test_validate_reports_without_saving(
        self, admin_client, isolated_profiles, monkeypatch
    ):
        verdict = SimpleNamespace(
            to_dict=lambda: {
                'ok': True,
                'code': 'ok',
                'error': None,
                'reported_model': 'acme-large',
            }
        )
        probe = mock.AsyncMock(return_value=verdict)
        monkeypatch.setattr('app.routers.profiles.validate_profile_env', probe)
        data, _ = await call_tool(
            admin_client,
            'manage_profile',
            {'action': 'validate', 'env': PROFILE_ENV},
        )
        assert data['ok'] is True
        probe.assert_awaited_once_with(PROFILE_ENV)
        # Nothing was persisted.
        assert not isolated_profiles.exists()

    async def test_presets(self, admin_client):
        data, _ = await call_tool(
            admin_client, 'manage_profile', {'action': 'presets'}
        )
        assert data['presets']


# ── app_settings ──────────────────────────────────────


class TestAppSettings:
    async def test_get_and_update_round_trip(self, admin_client):
        current, _ = await call_tool(
            admin_client, 'app_settings', {'action': 'get'}
        )
        assert 'max_agent_concurrency' in current

        updated, _ = await call_tool(
            admin_client,
            'app_settings',
            {'action': 'update', 'settings': {'max_agent_concurrency': 3}},
        )
        # The values echo what LANDED (clamped/normalized by the
        # route), not what was sent.
        assert updated['values']['max_agent_concurrency'] == '3'

        clamped, _ = await call_tool(
            admin_client,
            'app_settings',
            {'action': 'update', 'settings': {'max_agent_concurrency': 99}},
        )
        assert clamped['values']['max_agent_concurrency'] == '10'

    async def test_update_rejects_unknown_keys(self, admin_client):
        _, envelope = await call_tool(
            admin_client,
            'app_settings',
            {'action': 'update', 'settings': {'max_agent_concurency': 3}},
        )
        assert envelope['isError'] is True
        assert 'max_agent_concurency' in envelope['content'][0]['text']

    async def test_update_requires_a_settings_object(self, admin_client):
        _, envelope = await call_tool(
            admin_client, 'app_settings', {'action': 'update'}
        )
        assert envelope['isError'] is True
        assert 'settings' in envelope['content'][0]['text']

    async def test_update_is_admin_only(self, member_client):
        """A member token gets the same 403 the UI gets."""
        _, envelope = await call_tool(
            member_client,
            'app_settings',
            {'action': 'update', 'settings': {'browser_headless': True}},
        )
        assert envelope['isError'] is True
        assert 'admin' in envelope['content'][0]['text'].lower()

    async def test_gws_status(self, admin_client, monkeypatch):
        monkeypatch.setattr(
            'app.routers.app_settings.gws_integration',
            SimpleNamespace(
                check_status=mock.AsyncMock(
                    return_value={
                        'binary': True,
                        'auth': True,
                        'version': '1.0.0',
                        'detail': {},
                    }
                ),
                is_installed=lambda: False,
            ),
        )
        data, _ = await call_tool(
            admin_client, 'app_settings', {'action': 'gws_status'}
        )
        assert data['binary'] is True
        assert data['enabled'] is False

    async def test_gws_enable_disable(self, admin_client, monkeypatch):
        marks = []
        monkeypatch.setattr(
            'app.routers.app_settings.gws_integration',
            SimpleNamespace(
                check_status=mock.AsyncMock(
                    return_value={'binary': True, 'auth': True}
                ),
                install_skills=mock.AsyncMock(
                    return_value={'installed': True, 'count': 3}
                ),
                uninstall_skills=mock.AsyncMock(return_value={'removed': True}),
                is_installed=lambda: True,
            ),
        )
        monkeypatch.setattr(
            'app.routers.app_settings.skills_sync',
            SimpleNamespace(mark_gws_installed=marks.append),
        )
        enabled, _ = await call_tool(
            admin_client, 'app_settings', {'action': 'gws_enable'}
        )
        assert enabled['ok'] is True
        assert enabled['count'] == 3
        disabled, _ = await call_tool(
            admin_client, 'app_settings', {'action': 'gws_disable'}
        )
        assert disabled['ok'] is True
        assert marks == [True, False]

    async def test_gws_enable_is_admin_only(self, member_client, monkeypatch):
        install = mock.AsyncMock()
        monkeypatch.setattr(
            'app.routers.app_settings.gws_integration.install_skills',
            install,
        )
        _, envelope = await call_tool(
            member_client, 'app_settings', {'action': 'gws_enable'}
        )
        assert envelope['isError'] is True
        install.assert_not_awaited()

    async def test_vision_get_update(self, admin_client, monkeypatch):
        saved = {}

        def fake_save(key: str) -> None:
            saved['key'] = key

        monkeypatch.setattr('app.vision.save_vision_config', fake_save)
        monkeypatch.setattr(
            'app.vision.get_kie_api_key',
            lambda: saved.get('key'),
        )
        empty, _ = await call_tool(
            admin_client, 'app_settings', {'action': 'vision_get'}
        )
        assert empty['kie_api_key_set'] is False

        updated, _ = await call_tool(
            admin_client,
            'app_settings',
            {'action': 'vision_update', 'kie_api_key': 'kie-key-12345678'},
        )
        assert saved['key'] == 'kie-key-12345678'
        assert updated['kie_api_key_set'] is True
        # Write-only: the plaintext never comes back, and the
        # route's own mask is preserved, not double-masked away.
        assert updated['kie_api_key_masked']
        assert 'kie-key-12345678' not in json.dumps(updated)

    async def test_vision_update_is_admin_only(self, member_client):
        _, envelope = await call_tool(
            member_client,
            'app_settings',
            {'action': 'vision_update', 'kie_api_key': 'nope'},
        )
        assert envelope['isError'] is True
        assert 'admin' in envelope['content'][0]['text'].lower()


# ── manage_email_account ──────────────────────────────


class TestManageEmailAccount:
    async def _create(self, admin_client) -> str:
        created, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {
                'action': 'create',
                'email': 'ops@acme.example',
                'password': 'mail-pass-1234',
                'imap_host': 'imap.acme.example',
                'smtp_host': 'smtp.acme.example',
            },
        )
        account = created['created']
        assert account['email'] == 'ops@acme.example'
        assert 'mail-pass-1234' not in json.dumps(created)
        assert 'password' not in account
        return account['id']

    async def test_list_get_delete(self, admin_client):
        account_id = await self._create(admin_client)
        listed, _ = await call_tool(
            admin_client, 'manage_email_account', {'action': 'list'}
        )
        assert account_id in [a['id'] for a in listed['accounts']]
        assert 'mail-pass-1234' not in json.dumps(listed)

        got, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {'action': 'get', 'account_id': account_id},
        )
        assert got['account']['id'] == account_id

        deleted, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {'action': 'delete', 'account_id': account_id},
        )
        assert deleted['ok'] is True

    async def test_create_requires_a_password(self, admin_client):
        _, envelope = await call_tool(
            admin_client,
            'manage_email_account',
            {'action': 'create', 'email': 'ops@acme.example'},
        )
        assert envelope['isError'] is True
        assert 'password' in envelope['content'][0]['text']

    async def test_update_smtp_settings(self, admin_client):
        account_id = await self._create(admin_client)
        updated, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {
                'action': 'update',
                'account_id': account_id,
                'smtp_host': 'smtp2.acme.example',
                'smtp_port': 587,
            },
        )
        assert updated['updated']['smtp_host'] == 'smtp2.acme.example'
        assert updated['updated']['smtp_port'] == 587
        assert 'mail-pass-1234' not in json.dumps(updated)

        _, envelope = await call_tool(
            admin_client,
            'manage_email_account',
            {'action': 'update', 'account_id': account_id},
        )
        assert envelope['isError'] is True

    async def test_test_smtp(self, admin_client, monkeypatch):
        account_id = await self._create(admin_client)
        seen = {}

        class _FakeSMTP:
            def __init__(self, host, port):
                seen['host'] = host

            def ehlo(self):
                pass

            def starttls(self):
                pass

            def login(self, user, password):
                seen['password'] = password

            def quit(self):
                pass

        monkeypatch.setattr(
            'app.routers.email_accounts.smtplib',
            SimpleNamespace(SMTP=_FakeSMTP, SMTP_SSL=_FakeSMTP),
        )
        data, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {'action': 'test_smtp', 'account_id': account_id},
        )
        assert data['ok'] is True
        assert seen['host'] == 'smtp.acme.example'
        # The route decrypted the stored password for the login; the
        # tool result never carries it.
        assert 'mail-pass-1234' not in json.dumps(data)

    async def test_test_connection_with_raw_credentials(
        self, admin_client, monkeypatch
    ):
        class _FakeIMAP:
            def __init__(self, host, port):
                assert host == 'imap.acme.example'

            def login(self, user, password):
                assert password == 'mail-pass-1234'

            def logout(self):
                pass

        monkeypatch.setattr(
            'app.routers.email_accounts.imaplib',
            SimpleNamespace(IMAP4_SSL=_FakeIMAP, IMAP4=_FakeIMAP),
        )
        data, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {
                'action': 'test',
                'email': 'ops@acme.example',
                'imap_host': 'imap.acme.example',
                'password': 'mail-pass-1234',
            },
        )
        assert data['ok'] is True
        assert 'mail-pass-1234' not in json.dumps(data)

    async def test_send_goes_through_the_send_route(
        self, admin_client, monkeypatch
    ):
        account_id = await self._create(admin_client)
        sent = {}

        async def fake_send(
            *, account, password, to, subject, body, body_html=None
        ):
            sent['to'] = to
            sent['password'] = password
            return {'ok': True, 'message_id': '<test-1@acme>'}

        monkeypatch.setattr('app.routers.email_accounts.send_email', fake_send)
        data, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {
                'action': 'send',
                'account_id': account_id,
                'to': 'buyer@example.com',
                'subject': 'Order shipped',
                'body': 'It is on the way.',
            },
        )
        assert data['ok'] is True
        assert sent['to'] == ['buyer@example.com']
        # The route decrypted the stored password — the tool result
        # never carries it.
        assert 'mail-pass-1234' not in json.dumps(data)

    async def test_send_refused_when_turned_off(self, admin_client):
        """The external toolset hits the same gate: once the user turns
        sending off (web UI), refused as an MCP tool error the agent
        can read, before any SMTP connection."""
        account_id = await self._create(admin_client)
        off = await admin_client.put(
            f'/api/email-accounts/{account_id}/send-permission',
            json={'allow_send': False},
        )
        assert off.status_code == 200, off.text
        with (
            mock.patch('smtplib.SMTP_SSL') as ssl_cls,
            mock.patch('smtplib.SMTP') as plain_cls,
        ):
            _, envelope = await call_tool(
                admin_client,
                'manage_email_account',
                {
                    'action': 'send',
                    'account_id': account_id,
                    'to': 'buyer@example.com',
                    'subject': 'Order shipped',
                    'body': 'It is on the way.',
                },
            )
        assert envelope['isError'] is True
        text = envelope['content'][0]['text']
        assert 'turned sending off' in text
        assert 'Settings → Email' in text
        ssl_cls.assert_not_called()
        plain_cls.assert_not_called()

    async def test_update_cannot_change_send_permission(self, admin_client):
        """``allow_send`` is not an editable field of the tool: the
        user sets it in the web UI, not the agent."""
        account_id = await self._create(admin_client)
        await admin_client.put(
            f'/api/email-accounts/{account_id}/send-permission',
            json={'allow_send': False},
        )
        r = await rpc(
            admin_client,
            'tools/call',
            {
                'name': 'manage_email_account',
                'arguments': {
                    'action': 'update',
                    'account_id': account_id,
                    'allow_send': True,
                },
            },
        )
        assert 'allow_send' in r.json()['error']['message']
        listed, _ = await call_tool(
            admin_client, 'manage_email_account', {'action': 'list'}
        )
        assert listed['accounts'][0]['allow_send'] is False

    async def test_send_requires_subject(self, admin_client):
        _, envelope = await call_tool(
            admin_client,
            'manage_email_account',
            {'action': 'send', 'account_id': 'x', 'to': 'a@b.c', 'body': 'x'},
        )
        assert envelope['isError'] is True
        assert 'subject' in envelope['content'][0]['text']

    async def test_sync_now(self, admin_client, monkeypatch):
        await self._create(admin_client)
        monkeypatch.setattr(
            'app.routers.email_accounts.get_sync_state',
            lambda *a, **k: {},
        )
        monkeypatch.setattr(
            'app.routers.email_accounts.maybe_seed_sync_state',
            mock.AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            'app.routers.email_accounts.sync_account_emails',
            mock.AsyncMock(return_value=3),
        )
        data, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {'action': 'sync_now', 'account_email': 'ops@acme.example'},
        )
        assert data['ok'] is True
        assert data['new_emails'] == 3

    async def test_links_link_unlink(self, admin_client):
        account_id = await self._create(admin_client)
        store = await admin_client.post(
            '/api/stores', json={'name': 'Mail Store'}
        )
        store_id = store.json()['id']

        empty, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {'action': 'links', 'account_id': account_id},
        )
        assert empty['links'] == []

        linked, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {
                'action': 'link',
                'store_id': store_id,
                'email_account_id': account_id,
            },
        )
        assert linked['linked']['store_id'] == store_id

        links, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {'action': 'links', 'account_id': account_id},
        )
        assert len(links['links']) == 1

        info, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {'action': 'info_by_store', 'store_id': store_id},
        )
        assert info['accounts'][0]['email'] == 'ops@acme.example'

        unlinked, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {
                'action': 'unlink',
                'store_id': store_id,
                'link_id': links['links'][0]['link_id'],
            },
        )
        assert unlinked['ok'] is True

    async def test_info_by_store_does_not_touch_the_real_home(
        self, admin_client, monkeypatch, tmp_path
    ):
        """The email DB helpers write under the real VIBE_HOME; the
        tool test pins them to a temp dir (and to the route's use of
        them) rather than poking the developer's machine."""
        account_id = await self._create(admin_client)
        store = await admin_client.post(
            '/api/stores', json={'name': 'Info Store'}
        )
        await admin_client.post(
            f'/api/stores/{store.json()["id"]}/emails',
            json={'email_account_id': account_id},
        )
        monkeypatch.setattr(
            'app.routers.email_accounts.init_email_db',
            lambda account_id: tmp_path / f'{account_id}.db',
        )
        monkeypatch.setattr(
            'app.routers.email_accounts.db_path_for_account',
            lambda account_id: tmp_path / f'{account_id}.db',
        )
        data, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {'action': 'info_by_store', 'store_id': store.json()['id']},
        )
        assert str(tmp_path) in data['accounts'][0]['db_path']


# ── manage_chat_bot ───────────────────────────────────


class TestManageChatBot:
    async def _create(self, admin_client) -> str:
        created, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'create',
                'name': 'ops-room',
                'webhook_url': WECOM_WEBHOOK,
            },
        )
        bot = created['created']
        # Webhook URL and (absent) secret are write-only: the raw
        # key never comes back — not from create, list, or get.
        assert bot['webhook_url'] == '****'
        assert WECOM_WEBHOOK not in json.dumps(created)
        return bot['id']

    async def test_platforms_list_get_delete(self, admin_client):
        platforms, _ = await call_tool(
            admin_client, 'manage_chat_bot', {'action': 'platforms'}
        )
        names = [p['name'] for p in platforms['platforms']]
        assert 'wecom' in names

        bot_id = await self._create(admin_client)
        listed, _ = await call_tool(
            admin_client, 'manage_chat_bot', {'action': 'list'}
        )
        entry = next(b for b in listed['bots'] if b['id'] == bot_id)
        assert entry['webhook_url_masked'].startswith('https://')
        assert '00000000' not in entry['webhook_url_masked']

        got, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {'action': 'get', 'bot_id': bot_id},
        )
        # Even the edit-form route (raw URL over REST) is masked
        # over MCP.
        assert got['bot']['webhook_url'] == '****'
        assert WECOM_WEBHOOK not in json.dumps(got)

        deleted, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {'action': 'delete', 'bot_id': bot_id},
        )
        assert deleted['ok'] is True

    async def test_create_rejects_an_unknown_platform(self, admin_client):
        _, envelope = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'create',
                'name': 'bad',
                'webhook_url': 'https://example.com/hook',
            },
        )
        assert envelope['isError'] is True
        assert 'webhook' in envelope['content'][0]['text'].lower()

    async def test_create_honours_the_platform_security_rule(
        self, admin_client
    ):
        """The tool is the same route the form runs, so DingTalk's rule
        (at least one 安全设置) applies here too — and the agent can
        state the one setting that has no value to send: the IP range.
        """
        _, envelope = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'create',
                'name': 'ding-bare',
                'webhook_url': DINGTALK_WEBHOOK,
            },
        )
        assert envelope['isError'] is True
        assert 'security setting' in envelope['content'][0]['text']

        created, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'create',
                'name': 'ding-ip',
                'webhook_url': DINGTALK_WEBHOOK,
                'security_mode': 'ip',
            },
        )
        assert created['created']['security_mode'] == 'ip'
        # The mode is not a credential — the agent may read it back.
        assert '****' not in json.dumps(created['created']['security_mode'])

    async def test_update_changes_the_name_and_masks(self, admin_client):
        bot_id = await self._create(admin_client)
        updated, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {'action': 'update', 'bot_id': bot_id, 'name': 'ops-room-2'},
        )
        assert updated['updated']['name'] == 'ops-room-2'
        assert WECOM_WEBHOOK not in json.dumps(updated)

        _, envelope = await call_tool(
            admin_client,
            'manage_chat_bot',
            {'action': 'update', 'bot_id': bot_id},
        )
        assert envelope['isError'] is True

    async def test_test_and_send_post_for_real(self, admin_client, monkeypatch):
        bot_id = await self._create(admin_client)
        posted = []

        async def fake_send(platform, url, secret, content, **kwargs):
            posted.append((platform, url, content, kwargs))
            return True, None

        monkeypatch.setattr(
            'app.routers.chat_bots.base.send_message', fake_send
        )
        tested, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {'action': 'test', 'bot_id': bot_id},
        )
        assert tested['ok'] is True
        assert posted[0][0] == 'wecom'
        # The mock sees the RAW url (the route uses it); the tool
        # result never does.
        assert posted[0][1] == WECOM_WEBHOOK
        assert WECOM_WEBHOOK not in json.dumps(tested)

        sent, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'send',
                'bot_id': bot_id,
                'content': 'deploy done',
                'msgtype': 'markdown',
            },
        )
        assert sent['ok'] is True
        assert posted[-1][2] == 'deploy done'

    async def test_send_file_decodes_base64_and_cleans_up(
        self, admin_client, monkeypatch
    ):
        bot_id = await self._create(admin_client)
        uploaded = {}

        async def fake_send_file(url, path):
            uploaded['url'] = url
            uploaded['path'] = Path(path)
            uploaded['existed'] = Path(path).exists()
            uploaded['content'] = Path(path).read_bytes()
            return True, None

        monkeypatch.setattr(
            'app.routers.chat_bots.send_file_webhook', fake_send_file
        )
        payload = base64.b64encode(b'report,attach').decode()
        data, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'send_file',
                'bot_id': bot_id,
                'content_base64': payload,
                'filename': 'report.txt',
            },
        )
        assert data['ok'] is True
        assert uploaded['url'] == WECOM_WEBHOOK
        assert uploaded['existed'] is True
        assert uploaded['content'] == b'report,attach'
        assert uploaded['path'].name.endswith('report.txt')
        # The temp file is gone once the tool returns.
        assert not uploaded['path'].exists()

    async def test_send_file_rejects_bad_base64_and_oversize(
        self, admin_client
    ):
        bot_id = await self._create(admin_client)
        _, envelope = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'send_file',
                'bot_id': bot_id,
                'content_base64': 'not base64!!',
                'filename': 'x.txt',
            },
        )
        assert envelope['isError'] is True
        assert 'base64' in envelope['content'][0]['text']

        big = base64.b64encode(b'x' * (10 * 1024 * 1024 + 1)).decode()
        _, envelope = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'send_file',
                'bot_id': bot_id,
                'content_base64': big,
                'filename': 'big.bin',
            },
        )
        assert envelope['isError'] is True
        assert 'large' in envelope['content'][0]['text'].lower()


# ── manage_ticktick ───────────────────────────────────


class _FakeDidaConfig:
    """Dict-backed stand-in for the dida365 backend config file."""

    def __init__(self, monkeypatch, initial=None):
        self.store = dict(initial or {})

        def load(name):
            return dict(self.store.get(name, {}))

        def save(name, config):
            self.store[name] = dict(config)

        monkeypatch.setattr(
            'app.routers.dida365_oauth.load_backend_config', load
        )
        monkeypatch.setattr(
            'app.routers.dida365_oauth.save_backend_config', save
        )


class TestManageTicktick:
    async def test_status_when_not_connected(self, admin_client, monkeypatch):
        _FakeDidaConfig(monkeypatch)
        data, _ = await call_tool(
            admin_client, 'manage_ticktick', {'action': 'status'}
        )
        assert data['connected'] is False

    async def test_authorize_returns_a_url_for_a_human(
        self, admin_client, monkeypatch
    ):
        fake = _FakeDidaConfig(monkeypatch)
        data, _ = await call_tool(
            admin_client,
            'manage_ticktick',
            {
                'action': 'authorize',
                'client_id': 'cid-123',
                'client_secret': 'csecret-123',
                'service_type': 'ticktick',
            },
        )
        assert data['auth_url'].startswith(
            'https://ticktick.com/oauth/authorize?'
        )
        assert 'state=' in data['auth_url']
        assert data['callback_url'].endswith('/api/dida365/callback')
        assert 'user' in data['note'].lower()
        # The client secret is write-only: encrypted into the
        # config store, never echoed.
        assert 'csecret-123' not in json.dumps(data)
        stored = fake.store['dida365']
        assert stored['client_id'] == 'cid-123'
        assert stored['client_secret_enc']
        assert stored['client_secret_enc'] != 'csecret-123'

    async def test_projects_configure_disconnect(
        self, admin_client, monkeypatch
    ):
        _FakeDidaConfig(
            monkeypatch,
            {
                'dida365': {
                    'access_token_enc': encrypt_password('tok'),
                    'base_url': 'https://api.ticktick.com/open/v1',
                }
            },
        )
        monkeypatch.setattr(
            'app.routers.dida365_oauth.refresh_token_if_needed',
            mock.AsyncMock(return_value=False),
        )

        class _Resp:
            status_code = 200

            def json(self):
                return [{'id': 'p1', 'name': 'Work'}]

            def raise_for_status(self):
                pass

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, url, headers=None):
                return _Resp()

        monkeypatch.setattr(
            'app.routers.dida365_oauth.httpx',
            SimpleNamespace(AsyncClient=_Client),
        )
        projects, _ = await call_tool(
            admin_client, 'manage_ticktick', {'action': 'projects'}
        )
        assert projects['projects'] == [{'id': 'p1', 'name': 'Work'}]

        monkeypatch.setattr(
            'app.routers.dida365_oauth._register_ticktick_mcp',
            lambda config: None,
        )
        configured, _ = await call_tool(
            admin_client,
            'manage_ticktick',
            {'action': 'configure', 'project_id': 'p1'},
        )
        assert configured['ok'] is True

        monkeypatch.setattr(
            'app.routers.dida365_oauth.read_mcp_config',
            lambda: {'mcpServers': {'ticktick': {'command': 'uv'}}},
        )
        written = {}
        monkeypatch.setattr(
            'app.routers.dida365_oauth.atomic_write_json',
            lambda path, data: written.update(data=data, path=path),
        )
        disconnected, _ = await call_tool(
            admin_client, 'manage_ticktick', {'action': 'disconnect'}
        )
        assert disconnected['ok'] is True
        assert 'ticktick' not in written['data']['mcpServers']

    async def test_configure_requires_project_id(self, admin_client):
        _, envelope = await call_tool(
            admin_client, 'manage_ticktick', {'action': 'configure'}
        )
        assert envelope['isError'] is True
        assert 'project_id' in envelope['content'][0]['text']

    async def test_setup_mcp(self, admin_client, monkeypatch):
        ensure = mock.AsyncMock(return_value='/tmp/ticktick-mcp')
        monkeypatch.setattr(
            'app.routers.dida365_oauth.ensure_ticktick_mcp', ensure
        )
        data, _ = await call_tool(
            admin_client, 'manage_ticktick', {'action': 'setup_mcp'}
        )
        assert data['ok'] is True
        ensure.assert_awaited_once()


# ── the tool list itself ──────────────────────────────


class TestOpsToolset:
    async def test_tools_list_includes_the_ops_tools(self, admin_client):
        r = await admin_client.post(
            '/api/mcp',
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
        )
        names = [t['name'] for t in r.json()['result']['tools']]
        for name in (
            'manage_store',
            'browser',
            'manage_ziniao_account',
            'manage_profile',
            'app_settings',
            'manage_email_account',
            'manage_chat_bot',
            'manage_ticktick',
        ):
            assert name in names


# ── masked secrets must round-trip safely ─────────────
#
# Every read returns masks; the natural get → edit one field →
# update flow therefore posts masks back. These pin the write side
# of the redaction contract (app/agent_mcp/redact.py
# restore_masked): an echoed mask keeps the stored secret, a mask
# with nothing stored is an error, a real new value replaces. The
# stored value is always read from the STORE (profiles.json, the DB
# row), never through MCP.


class TestMaskedRoundTripProfile:
    async def test_echoed_mask_keeps_the_stored_key(
        self, admin_client, isolated_profiles
    ):
        await call_tool(
            admin_client,
            'manage_profile',
            {
                'action': 'create',
                'profile_id': 'acme',
                'name': 'Acme',
                'env': PROFILE_ENV,
            },
        )
        got, _ = await call_tool(
            admin_client,
            'manage_profile',
            {'action': 'get', 'profile_id': 'acme'},
        )
        masked_env = got['profile']['env']

        # The natural flow: edit ONE key, echo everything else.
        updated, _ = await call_tool(
            admin_client,
            'manage_profile',
            {
                'action': 'update',
                'profile_id': 'acme',
                'env': dict(masked_env, ANTHROPIC_MODEL='acme-small'),
            },
        )
        assert updated['updated']['env']['ANTHROPIC_MODEL'] == ('acme-small')
        stored = json.loads(isolated_profiles.read_text())
        assert (
            stored['profiles']['acme']['env']['ANTHROPIC_AUTH_TOKEN']
            == 'sk-profile-secret-1234'
        ), 'the masked echo overwrote the real key'

    async def test_env_merges_and_null_deletes(
        self, admin_client, isolated_profiles
    ):
        await call_tool(
            admin_client,
            'manage_profile',
            {
                'action': 'create',
                'profile_id': 'acme',
                'name': 'Acme',
                'env': PROFILE_ENV,
            },
        )
        updated, _ = await call_tool(
            admin_client,
            'manage_profile',
            {
                'action': 'update',
                'profile_id': 'acme',
                # only the key being changed; the rest is omitted
                'env': {
                    'ANTHROPIC_MODEL': 'acme-small',
                    'API_TIMEOUT_MS': None,
                },
            },
        )
        env = updated['updated']['env']
        assert env['ANTHROPIC_MODEL'] == 'acme-small'
        # omitted keys stay (masked on the way out)
        assert env['ANTHROPIC_BASE_URL'] == (
            'https://api.acme.example/anthropic'
        )
        assert env['ANTHROPIC_AUTH_TOKEN'].startswith('****')
        stored = json.loads(isolated_profiles.read_text())['profiles']['acme'][
            'env'
        ]
        assert stored['ANTHROPIC_AUTH_TOKEN'] == 'sk-profile-secret-1234'
        assert 'API_TIMEOUT_MS' not in stored  # null deleted it

    async def test_mask_with_no_stored_key_is_an_error(
        self, admin_client, isolated_profiles
    ):
        _, envelope = await call_tool(
            admin_client,
            'manage_profile',
            {
                'action': 'create',
                'profile_id': 'ghost',
                'name': 'Ghost',
                'env': {'ANTHROPIC_AUTH_TOKEN': '****5678'},
            },
        )
        assert envelope['isError'] is True
        assert 'real secret' in envelope['content'][0]['text']
        assert not isolated_profiles.exists()

    async def test_a_real_new_key_replaces_the_old(
        self, admin_client, isolated_profiles
    ):
        await call_tool(
            admin_client,
            'manage_profile',
            {
                'action': 'create',
                'profile_id': 'acme',
                'name': 'Acme',
                'env': PROFILE_ENV,
            },
        )
        await call_tool(
            admin_client,
            'manage_profile',
            {
                'action': 'update',
                'profile_id': 'acme',
                'env': {'ANTHROPIC_AUTH_TOKEN': 'sk-rotated-9999'},
            },
        )
        stored = json.loads(isolated_profiles.read_text())
        assert (
            stored['profiles']['acme']['env']['ANTHROPIC_AUTH_TOKEN']
            == 'sk-rotated-9999'
        )


class TestMaskedRoundTripEmail:
    async def test_echoed_mask_keeps_the_stored_password(
        self, admin_client, override_async_session
    ):
        created, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {
                'action': 'create',
                'email': 'ops@acme.example',
                'password': 'mail-pass-1234',
                'imap_host': 'imap.acme.example',
            },
        )
        account_id = created['created']['id']
        updated, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {
                'action': 'update',
                'account_id': account_id,
                'password': '****1234',  # what a read would show
                'smtp_host': 'smtp.acme.example',
            },
        )
        assert updated['updated']['smtp_host'] == 'smtp.acme.example'
        async with override_async_session() as db:
            row = await db.get(EmailAccount, account_id)
            assert decrypt_password(row.encrypted_password) == (
                'mail-pass-1234'
            ), 'the masked echo overwrote the real password'

    async def test_mask_with_no_stored_password_is_an_error(self, admin_client):
        created, _ = await call_tool(
            admin_client,
            'manage_email_account',
            {
                'action': 'create',
                'email': 'ops@acme.example',
                'password': 'mail-pass-1234',
                'imap_host': 'imap.acme.example',
            },
        )
        _, envelope = await call_tool(
            admin_client,
            'manage_email_account',
            {
                'action': 'update',
                'account_id': 'no-such-account',
                'password': '****1234',
            },
        )
        assert envelope['isError'] is True
        assert 'real secret' in envelope['content'][0]['text']


class TestMaskedRoundTripZiniao:
    async def test_echoed_mask_keeps_the_stored_password(
        self, admin_client, override_async_session
    ):
        created, _ = await call_tool(
            admin_client,
            'manage_ziniao_account',
            {
                'action': 'create',
                'company': 'acme',
                'username': 'ops@acme.example',
                'password': 'ziniao-pass-1234',
            },
        )
        account_id = created['created']['id']
        await call_tool(
            admin_client,
            'manage_ziniao_account',
            {
                'action': 'update',
                'account_id': account_id,
                'password': '****1234',
                'name': 'renamed',
            },
        )
        async with override_async_session() as db:
            row = await db.get(ZiniaoAccount, account_id)
            assert decrypt_password(row.encrypted_password) == (
                'ziniao-pass-1234'
            )


class TestMaskedRoundTripChatBot:
    async def test_echoed_masks_keep_url_and_secret(
        self, admin_client, override_async_session
    ):
        created, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'create',
                'name': 'ops-room',
                'webhook_url': WECOM_WEBHOOK,
            },
        )
        bot_id = created['created']['id']
        got, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {'action': 'get', 'bot_id': bot_id},
        )
        assert got['bot']['webhook_url'] == '****'

        updated, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'update',
                'bot_id': bot_id,
                'webhook_url': '****',  # what the read showed
                'name': 'renamed',
            },
        )
        assert updated['updated']['name'] == 'renamed'
        async with override_async_session() as db:
            row = await db.get(ChatBot, bot_id)
            assert row.webhook_url == WECOM_WEBHOOK

    async def test_mask_with_no_stored_secret_is_an_error(self, admin_client):
        _, envelope = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'create',
                'name': 'ghost-bot',
                'webhook_url': '****',
            },
        )
        assert envelope['isError'] is True
        assert 'real secret' in envelope['content'][0]['text']

    async def test_a_real_new_url_replaces_the_old(
        self, admin_client, override_async_session
    ):
        created, _ = await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'create',
                'name': 'ops-room',
                'webhook_url': WECOM_WEBHOOK,
            },
        )
        bot_id = created['created']['id']
        new_url = (
            'https://qyapi.weixin.qq.com/cgi-bin/webhook/send'
            '?key=11111111-1111-1111-1111-111111111111'
        )
        await call_tool(
            admin_client,
            'manage_chat_bot',
            {
                'action': 'update',
                'bot_id': bot_id,
                'webhook_url': new_url,
            },
        )
        async with override_async_session() as db:
            row = await db.get(ChatBot, bot_id)
            assert row.webhook_url == new_url


class TestMaskedRoundTripVisionKey:
    async def test_echoed_mask_keeps_the_stored_key(
        self, admin_client, monkeypatch
    ):
        saved = {}

        def fake_save(key: str) -> None:
            saved['key'] = key

        monkeypatch.setattr('app.vision.save_vision_config', fake_save)
        monkeypatch.setattr(
            'app.vision.get_kie_api_key', lambda: saved.get('key')
        )
        await call_tool(
            admin_client,
            'app_settings',
            {'action': 'vision_update', 'kie_api_key': 'kie-real-123456'},
        )
        # The only mask an agent can echo is the GET's
        # kie_api_key_masked shape; sending it back must keep the
        # stored key, not write '****'.
        await call_tool(
            admin_client,
            'app_settings',
            {'action': 'vision_update', 'kie_api_key': '****3456'},
        )
        assert saved['key'] == 'kie-real-123456'

    async def test_mask_with_no_stored_key_is_an_error(
        self, admin_client, monkeypatch
    ):
        saved = {}
        monkeypatch.setattr(
            'app.vision.save_vision_config',
            lambda key: saved.__setitem__('key', key),
        )
        monkeypatch.setattr(
            'app.vision.get_kie_api_key', lambda: saved.get('key')
        )
        _, envelope = await call_tool(
            admin_client,
            'app_settings',
            {'action': 'vision_update', 'kie_api_key': '****3456'},
        )
        assert envelope['isError'] is True
        assert 'real secret' in envelope['content'][0]['text']
        assert 'key' not in saved


class TestMaskedRoundTripTicktick:
    async def test_echoed_mask_keeps_the_stored_secret(
        self, admin_client, monkeypatch
    ):
        fake = _FakeDidaConfig(
            monkeypatch,
            {'dida365': {'client_secret_enc': encrypt_password('cs-real')}},
        )
        data, _ = await call_tool(
            admin_client,
            'manage_ticktick',
            {
                'action': 'authorize',
                'client_id': 'cid',
                'client_secret': '****real',  # the read-side mask
            },
        )
        assert data['auth_url']
        assert (
            decrypt_password(fake.store['dida365']['client_secret_enc'])
            == 'cs-real'
        )

    async def test_mask_with_no_stored_secret_is_an_error(
        self, admin_client, monkeypatch
    ):
        _FakeDidaConfig(monkeypatch)
        _, envelope = await call_tool(
            admin_client,
            'manage_ticktick',
            {
                'action': 'authorize',
                'client_id': 'cid',
                'client_secret': '****real',
            },
        )
        assert envelope['isError'] is True
        assert 'real secret' in envelope['content'][0]['text']
