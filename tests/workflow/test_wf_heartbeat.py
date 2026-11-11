"""The install heartbeat reports enum/count state — and nothing that
identifies a customer or a secret.

Two pins: the exact key set with its types, and the no-leak rule —
a profile seeded with a real-looking base URL, model and key, plus a
store named ``acme``, must leave none of those strings anywhere in
the payload (the one deliberate report is a custom provider's
user-typed NAME and its base URL's HOST).
"""

import json

import pytest

from app.ai.profiles import ProfileManager
from app.models.app_settings import AppSettings
from app.models.email_account import EmailAccount
from app.scheduler.heartbeat import send_install_heartbeat
from app.sync import state as sync_state
from app.sync.scheduler import sync_scheduler

pytestmark = pytest.mark.workflow

EXPECTED = {
    'store_count': int,
    'active_schedule_count': int,
    'user_count': int,
    'ai_profile_count': int,
    'email_account_count': int,
    'chat_bot_count': int,
    'tasks_last_24h': int,
    'ads_calls_last_24h': int,
    'ai_provider_kinds': list,
    'default_ai_provider_kinds': list,
    'ai_provider_hosts': list,
    'default_ai_provider_hosts': list,
    'store_backend_counts': dict,
    'store_platform_counts': dict,
    'ads_api_bound_store_count': int,
    'gws_enabled': bool,
    'ticktick_enabled': bool,
    'vision_key_set': bool,
    'chat_bot_platforms': list,
    'api_token_count': int,
    'mcp_calls_last_24h': int,
    'ai_custom_providers': list,
    'schedules_fired_7d': int,
    'email_send_disabled_count': int,
    'sync_failed_jobs': list,
}


def _capture(monkeypatch):
    events: list[tuple[str, dict]] = []
    person: list[dict] = []
    monkeypatch.setattr(
        'app.telemetry.send',
        lambda event, props=None: events.append((event, dict(props or {}))),
    )
    monkeypatch.setattr(
        'app.telemetry.set_person_properties',
        lambda props=None: person.append(dict(props or {})),
    )
    return events, person


class TestHeartbeatShape:
    async def test_keys_and_types(
        self,
        admin_client,
        override_async_session,
        isolated_profiles,
        monkeypatch,
    ):
        events, person = _capture(monkeypatch)
        r = await admin_client.post(
            '/api/stores', json={'name': 'Gadget Store'}
        )
        assert r.status_code == 200
        ProfileManager.create_profile(
            'prof-1',
            'MiniMax',
            {'ANTHROPIC_BASE_URL': 'https://x.example'},
        )
        r = await admin_client.post(
            '/api/auth/tokens', json={'name': 'hb-token'}
        )
        assert r.status_code == 200
        r = await admin_client.post(
            '/api/chat-bots',
            json={
                'name': 'Ops bot',
                'webhook_url': (
                    'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abc'
                ),
            },
        )
        assert r.status_code == 200
        async with override_async_session() as db:
            db.add(AppSettings(key='google_workspace_enabled', value='true'))
            await db.commit()

        await send_install_heartbeat()
        beats = [p for e, p in events if e == 'install_heartbeat']
        assert len(beats) == 1
        props = beats[0]
        assert set(props) == set(EXPECTED), set(props) ^ set(EXPECTED)
        for key, typ in EXPECTED.items():
            assert type(props[key]) is typ, (
                f'{key}: expected {typ.__name__}, got {type(props[key])}'
            )
        # Real seeded state is visible as counts/enums only.
        assert props['store_count'] == 1
        assert props['store_backend_counts'] == {'chrome': 1}
        assert props['store_platform_counts'] == {'amazon': 1}
        # The built-in 'default' profile plus the one just created.
        assert props['ai_profile_count'] == 2
        assert props['ai_provider_kinds'] == ['custom', 'default']
        assert props['ai_provider_hosts'] == [
            'api.anthropic.com',
            'x.example',
        ]
        assert props['default_ai_provider_hosts'] == ['api.anthropic.com']
        assert props['api_token_count'] == 1
        assert props['gws_enabled'] is True
        assert props['chat_bot_platforms'] == ['wecom']
        # The same keys land as person properties for breakdowns.
        assert len(person) == 1
        assert set(props) <= set(person[0])
        assert person[0]['app_version']


class TestHeartbeatHealth:
    async def test_send_disabled_mailboxes_and_failed_sync_jobs(
        self, override_async_session, isolated_profiles, monkeypatch
    ):
        events, _person = _capture(monkeypatch)
        async with override_async_session() as db:
            for i, allow in enumerate((True, False, False)):
                db.add(
                    EmailAccount(
                        email=f'box{i}@example.com',
                        encrypted_password='x',
                        imap_host='imap.example.com',
                        allow_send=allow,
                        created_by='u-1',
                    )
                )
            await db.commit()
        await sync_state.record('job-a', status='failed', error='boom')
        await sync_state.record('job-b', status='updated')
        monkeypatch.setattr(
            sync_scheduler, '_jobs', {'job-a': object(), 'job-b': object()}
        )
        await send_install_heartbeat()
        beat = next(p for e, p in events if e == 'install_heartbeat')
        assert beat['email_send_disabled_count'] == 2
        assert beat['sync_failed_jobs'] == ['job-a']


class TestHeartbeatNoLeaks:
    async def test_secrets_and_names_never_leave(
        self,
        admin_client,
        isolated_profiles,
        monkeypatch,
    ):
        events, person = _capture(monkeypatch)
        r = await admin_client.post('/api/stores', json={'name': 'acme'})
        assert r.status_code == 200
        base_url = 'https://api.llmbox.example/anthropic/v1'
        ProfileManager.create_profile(
            'prof-secret',
            'Bespoke LLM',
            {
                'ANTHROPIC_BASE_URL': base_url,
                'ANTHROPIC_MODEL': 'acme-model-9000',
                'ANTHROPIC_AUTH_TOKEN': 'sk-realkey-9f8e7d6c',
            },
        )
        await send_install_heartbeat()
        beats = [p for e, p in events if e == 'install_heartbeat']
        assert len(beats) == 1
        blob = json.dumps(
            {'events': [p for _, p in events], 'person': person},
            default=str,
        )
        for secret in (
            base_url,
            'acme-model-9000',
            'sk-realkey-9f8e7d6c',
            'acme',
        ):
            assert secret not in blob, f'leaked {secret!r} in heartbeat'
        # §6: a custom provider DOES report the user-typed name and
        # the base URL's host — and only those.
        assert beats[0]['ai_custom_providers'] == [
            {'name': 'Bespoke LLM', 'host': 'api.llmbox.example'},
        ]
        assert 'api.llmbox.example' in blob
