"""Workflow tests for chat bot (group robot) CRUD + send endpoints.

Covers:
- platform is DERIVED from the webhook URL on create AND on update
  (never accepted from the client, so a stored bot can never disagree
  with its own URL)
- unsupported / spoofed webhook URLs → 400
- signing secret only for DingTalk/Feishu (400 on WeCom/Slack)
- list returns masked URL + has_secret, never the raw URL or secret
- single-bot GET returns the full URL (for edit)
- create/update/delete + DB state; update rejects None/blank
- /test and /send delegate to the notifier dispatch
- /send-file refuses non-WeCom platforms BEFORE path resolution
- legacy rows (pre-platform schema) read back as wecom
- auth required (anonymous request gets 401)
"""

import sqlite3

from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    create_async_engine as sqlalchemy_create_async_engine,
)

from app.database import _ensure_added_columns  # noqa: PLC2701
from app.main import app
from app.models.base import Base
from app.models.chat_bot import ChatBot
from app.notifiers import wecom
from app.notifiers.base import PLATFORMS

pytestmark = pytest.mark.workflow

WECOM_URL = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdefgh1234'
DINGTALK_URL = 'https://oapi.dingtalk.com/robot/send?access_token=tok1234abcd'
FEISHU_URL = 'https://open.feishu.cn/open-apis/bot/v2/hook/hook-tok-9876'
SLACK_URL = 'https://hooks.slack.com/services/T000/B000/xxxxxxxxxxxx'

PLATFORM_URLS = {
    'wecom': WECOM_URL,
    'dingtalk': DINGTALK_URL,
    'feishu': FEISHU_URL,
    'slack': SLACK_URL,
}


class TestChatBotCRUD:
    async def test_list_empty_initially(self, admin_client):
        r = await admin_client.get('/api/chat-bots')
        assert r.status_code == 200
        assert r.json() == []

    @pytest.mark.parametrize('platform,url', sorted(PLATFORM_URLS.items()))
    async def test_create_derives_platform_from_url(
        self, admin_client, override_async_session, platform, url
    ):
        r = await admin_client.post(
            '/api/chat-bots',
            json={'name': f'Bot {platform}', 'webhook_url': url},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['platform'] == platform
        assert body['can_send_file'] == (platform == 'wecom')

        async with override_async_session() as db:
            row = await db.get(ChatBot, body['id'])
            assert row.platform == platform

    async def test_client_sent_platform_is_ignored(
        self, admin_client, override_async_session
    ):
        """`platform` is not a create field — the URL decides."""
        r = await admin_client.post(
            '/api/chat-bots',
            json={
                'name': 'Liar',
                'webhook_url': WECOM_URL,
                'platform': 'slack',
            },
        )
        assert r.status_code == 200
        assert r.json()['platform'] == 'wecom'

    @pytest.mark.parametrize(
        'url',
        [
            'https://evil.com/webhook',
            'http://oapi.dingtalk.com/robot/send?access_token=x',
            'https://oapi.dingtalk.com.evil.com/robot/send?access_token=x',
            'https://evil.com/?u=oapi.dingtalk.com',
            'https://oapi.dingtalk.com@evil.com/robot/send?access_token=x',
            'not a url',
            # Loopback is a test-only hatch behind
            # VIBE_ALLOW_LOOPBACK_WEBHOOKS; this process runs without
            # it, so the offline stub URL must be refused here.
            'http://127.0.0.1:9999/cgi-bin/webhook/send?key=stub',
            'http://127.evil.com/cgi-bin/webhook/send?key=stub',
        ],
    )
    async def test_create_rejects_unsupported_url(self, admin_client, url):
        r = await admin_client.post(
            '/api/chat-bots',
            json={'name': 'Bad', 'webhook_url': url},
        )
        assert r.status_code == 400, url
        assert 'Unsupported webhook URL' in r.json()['detail']

    async def test_loopback_needs_the_env_flag(self, admin_client):
        """The flag is read from THIS server process's env at call
        time; with it unset (production default) even a strict
        loopback IP is refused."""
        r = await admin_client.post(
            '/api/chat-bots',
            json={
                'name': 'Stub',
                'webhook_url': 'http://localhost:9999/robot/send?access_token=x',
            },
        )
        assert r.status_code == 400

    async def test_create_rejects_blank_fields(self, admin_client):
        r = await admin_client.post(
            '/api/chat-bots',
            json={'name': '   ', 'webhook_url': WECOM_URL},
        )
        assert r.status_code == 400

        r = await admin_client.post(
            '/api/chat-bots',
            json={'name': 'ok', 'webhook_url': ''},
        )
        assert r.status_code == 400

    async def test_secret_accepted_for_signing_platforms(self, admin_client):
        for platform in ('dingtalk', 'feishu'):
            r = await admin_client.post(
                '/api/chat-bots',
                json={
                    'name': f'Signed {platform}',
                    'webhook_url': PLATFORM_URLS[platform],
                    'secret': 'SECtest123',
                },
            )
            assert r.status_code == 200, platform
            body = r.json()
            assert body['secret'] == 'SECtest123'
            assert body['has_secret'] is True

    async def test_secret_rejected_for_wecom_and_slack(self, admin_client):
        for platform in ('wecom', 'slack'):
            r = await admin_client.post(
                '/api/chat-bots',
                json={
                    'name': f'NoSecret {platform}',
                    'webhook_url': PLATFORM_URLS[platform],
                    'secret': 'SECshouldfail',
                },
            )
            assert r.status_code == 400, platform
            assert 'signing secret' in r.json()['detail']

    async def test_list_masks_url_and_hides_secret(self, admin_client):
        await admin_client.post(
            '/api/chat-bots',
            json={
                'name': 'Signed DingTalk',
                'webhook_url': DINGTALK_URL,
                'secret': 'SECsupersecret99',
            },
        )
        r = await admin_client.get('/api/chat-bots')
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        row = body[0]
        # No raw URL, no secret, ever.
        assert 'webhook_url' not in row
        assert 'secret' not in row
        assert DINGTALK_URL not in str(row)
        assert 'SECsupersecret99' not in str(row)
        assert 'tok1234' not in str(row)
        masked = row['webhook_url_masked']
        assert 'oapi.dingtalk.com' in masked
        assert masked.endswith('****abcd')
        assert row['has_secret'] is True
        assert row['platform'] == 'dingtalk'
        assert row['can_send_file'] is False

    async def test_platforms_endpoint_returns_registry(self, admin_client):
        r = await admin_client.get('/api/chat-bots/platforms')
        assert r.status_code == 200
        platforms = {p['name']: p for p in r.json()}
        assert set(platforms) == {'wecom', 'dingtalk', 'feishu', 'slack'}
        assert platforms['slack']['uses_secret'] is False
        assert platforms['wecom']['supports_file'] is True
        assert 'hosts' in platforms['dingtalk']
        assert 'doc_url' in platforms['feishu']

    async def test_get_single_returns_full_url_and_secret(self, admin_client):
        created = (
            await admin_client.post(
                '/api/chat-bots',
                json={
                    'name': 'A',
                    'webhook_url': DINGTALK_URL,
                    'secret': 'SECeditme',
                },
            )
        ).json()
        r = await admin_client.get(f'/api/chat-bots/{created["id"]}')
        assert r.status_code == 200
        body = r.json()
        assert body['webhook_url'] == DINGTALK_URL
        assert body['secret'] == 'SECeditme'

    async def test_get_single_missing_returns_404(self, admin_client):
        r = await admin_client.get('/api/chat-bots/missing')
        assert r.status_code == 404

    async def test_update_url_recomputes_platform(
        self, admin_client, override_async_session
    ):
        created = (
            await admin_client.post(
                '/api/chat-bots',
                json={'name': 'Mover', 'webhook_url': WECOM_URL},
            )
        ).json()
        assert created['platform'] == 'wecom'

        r = await admin_client.put(
            f'/api/chat-bots/{created["id"]}',
            json={'webhook_url': DINGTALK_URL, 'secret': 'SECnew'},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['platform'] == 'dingtalk'
        assert body['webhook_url'] == DINGTALK_URL

        async with override_async_session() as db:
            row = await db.get(ChatBot, created['id'])
            assert row.platform == 'dingtalk'
            assert row.secret == 'SECnew'

    async def test_update_to_platform_without_secret_clears_it(
        self, admin_client
    ):
        """Moving a signed DingTalk bot to WeCom drops the secret.

        Keeping it would leave a secret stored on a row that never
        signs with it — and the single GET would keep handing it to
        the edit form for no reason.
        """
        created = (
            await admin_client.post(
                '/api/chat-bots',
                json={
                    'name': 'WasSigned',
                    'webhook_url': DINGTALK_URL,
                    'secret': 'SECold',
                },
            )
        ).json()
        r = await admin_client.put(
            f'/api/chat-bots/{created["id"]}',
            json={'webhook_url': WECOM_URL},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['platform'] == 'wecom'
        assert body['secret'] is None
        assert body['has_secret'] is False

    async def test_update_rejects_unsupported_url(self, admin_client):
        created = (
            await admin_client.post(
                '/api/chat-bots',
                json={'name': 'A', 'webhook_url': WECOM_URL},
            )
        ).json()
        r = await admin_client.put(
            f'/api/chat-bots/{created["id"]}',
            json={'webhook_url': 'https://evil.com/x'},
        )
        assert r.status_code == 400
        assert 'Unsupported webhook URL' in r.json()['detail']

    async def test_update_blank_secret_clears_it(self, admin_client):
        created = (
            await admin_client.post(
                '/api/chat-bots',
                json={
                    'name': 'Clearable',
                    'webhook_url': DINGTALK_URL,
                    'secret': 'SECold',
                },
            )
        ).json()
        r = await admin_client.put(
            f'/api/chat-bots/{created["id"]}',
            json={'secret': ''},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['secret'] is None
        assert body['has_secret'] is False

    async def test_update_secret_rejected_for_slack(self, admin_client):
        created = (
            await admin_client.post(
                '/api/chat-bots',
                json={'name': 'S', 'webhook_url': SLACK_URL},
            )
        ).json()
        r = await admin_client.put(
            f'/api/chat-bots/{created["id"]}',
            json={'secret': 'SECnope'},
        )
        assert r.status_code == 400
        assert 'signing secret' in r.json()['detail']

    async def test_update_changes_fields_and_updated_at(
        self, admin_client, override_async_session
    ):
        created = (
            await admin_client.post(
                '/api/chat-bots',
                json={'name': 'Old', 'webhook_url': WECOM_URL},
            )
        ).json()
        original_updated = created['updated_at']

        r = await admin_client.put(
            f'/api/chat-bots/{created["id"]}',
            json={'name': 'New'},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['name'] == 'New'
        assert body['webhook_url'] == WECOM_URL  # untouched
        assert body['updated_at'] != original_updated

        async with override_async_session() as db:
            row = await db.get(ChatBot, created['id'])
            assert row.name == 'New'

    async def test_update_rejects_null_required_field(self, admin_client):
        created = (
            await admin_client.post(
                '/api/chat-bots',
                json={'name': 'A', 'webhook_url': WECOM_URL},
            )
        ).json()
        r = await admin_client.put(
            f'/api/chat-bots/{created["id"]}',
            json={'webhook_url': None},
        )
        assert r.status_code == 400

    async def test_update_rejects_blank_required_field(self, admin_client):
        created = (
            await admin_client.post(
                '/api/chat-bots',
                json={'name': 'A', 'webhook_url': WECOM_URL},
            )
        ).json()
        r = await admin_client.put(
            f'/api/chat-bots/{created["id"]}',
            json={'name': '   '},
        )
        assert r.status_code == 400

    async def test_update_missing_bot_returns_404(self, admin_client):
        r = await admin_client.put(
            '/api/chat-bots/does-not-exist',
            json={'name': 'x'},
        )
        assert r.status_code == 404

    async def test_delete_removes_from_db(
        self, admin_client, override_async_session
    ):
        created = (
            await admin_client.post(
                '/api/chat-bots',
                json={'name': 'Doomed', 'webhook_url': WECOM_URL},
            )
        ).json()

        r = await admin_client.delete(f'/api/chat-bots/{created["id"]}')
        assert r.status_code == 200
        assert r.json() == {'ok': True}

        async with override_async_session() as db:
            row = await db.get(ChatBot, created['id'])
            assert row is None

    async def test_delete_missing_returns_404(self, admin_client):
        r = await admin_client.delete('/api/chat-bots/missing')
        assert r.status_code == 404

    async def test_old_wecom_bots_route_is_gone(self, admin_client):
        r = await admin_client.get('/api/wecom-bots')
        assert r.status_code == 404


class TestChatBotTest:
    async def _make_bot(self, admin_client, url=WECOM_URL):
        return (
            await admin_client.post(
                '/api/chat-bots',
                json={'name': 'Bot', 'webhook_url': url},
            )
        ).json()

    async def test_test_endpoint_success(self, admin_client, monkeypatch):
        calls = []

        async def _fake_send(platform, url, secret, content, msgtype='text'):
            calls.append((platform, url, secret, content, msgtype))
            return True, ''

        monkeypatch.setattr(
            'app.routers.chat_bots.base.send_message', _fake_send
        )
        created = await self._make_bot(admin_client)

        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/test',
            json={},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is True
        assert len(calls) == 1
        assert calls[0][0] == 'wecom'
        assert calls[0][1] == WECOM_URL
        assert calls[0][2] is None
        assert calls[0][3]  # default message was used

    async def test_test_endpoint_passes_secret(self, admin_client, monkeypatch):
        calls = []

        async def _fake_send(platform, url, secret, content, msgtype='text'):
            calls.append(secret)
            return True, ''

        monkeypatch.setattr(
            'app.routers.chat_bots.base.send_message', _fake_send
        )
        created = (
            await admin_client.post(
                '/api/chat-bots',
                json={
                    'name': 'Signed',
                    'webhook_url': DINGTALK_URL,
                    'secret': 'SECtest',
                },
            )
        ).json()

        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/test', json={}
        )
        assert r.status_code == 200
        assert calls == ['SECtest']

    async def test_test_endpoint_blank_content_uses_default(
        self, admin_client, monkeypatch
    ):
        calls = []

        async def _fake_send(platform, url, secret, content, msgtype='text'):
            calls.append(content)
            return True, ''

        monkeypatch.setattr(
            'app.routers.chat_bots.base.send_message', _fake_send
        )
        created = await self._make_bot(admin_client)

        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/test',
            json={'content': '   '},
        )
        assert r.status_code == 200
        # Whitespace-only content was replaced with the default
        assert calls[0].strip() != ''

    async def test_test_endpoint_failure_surfaces_error(
        self, admin_client, monkeypatch
    ):
        async def _fake_send(platform, url, secret, content, msgtype='text'):
            return False, 'keyword not in content'

        monkeypatch.setattr(
            'app.routers.chat_bots.base.send_message', _fake_send
        )
        created = await self._make_bot(admin_client)

        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/test',
            json={},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is False
        assert 'keyword' in body['message']

    async def test_test_endpoint_missing_bot(self, admin_client):
        r = await admin_client.post(
            '/api/chat-bots/missing/test',
            json={},
        )
        assert r.status_code == 404


class TestChatBotSend:
    async def _make_bot(self, admin_client, url=WECOM_URL):
        return (
            await admin_client.post(
                '/api/chat-bots',
                json={'name': 'Bot', 'webhook_url': url},
            )
        ).json()

    async def test_send_text_success(self, admin_client, monkeypatch):
        calls = []

        async def _fake_send(platform, url, secret, content, msgtype='text'):
            calls.append((platform, url, secret, content, msgtype))
            return True, ''

        monkeypatch.setattr(
            'app.routers.chat_bots.base.send_message', _fake_send
        )
        created = await self._make_bot(admin_client, DINGTALK_URL)

        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/send',
            json={'content': 'hello world'},
        )
        assert r.status_code == 200
        assert r.json()['ok'] is True
        assert calls == [
            ('dingtalk', DINGTALK_URL, None, 'hello world', 'text')
        ]

    async def test_send_markdown_success(self, admin_client, monkeypatch):
        calls = []

        async def _fake_send(platform, url, secret, content, msgtype='text'):
            calls.append(msgtype)
            return True, ''

        monkeypatch.setattr(
            'app.routers.chat_bots.base.send_message', _fake_send
        )
        created = await self._make_bot(admin_client)

        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/send',
            json={'content': '# heading', 'msgtype': 'markdown'},
        )
        assert r.status_code == 200
        assert calls == ['markdown']

    async def test_send_rejects_blank_content(self, admin_client):
        created = await self._make_bot(admin_client)
        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/send',
            json={'content': '   '},
        )
        assert r.status_code == 400

    async def test_send_rejects_unknown_msgtype(self, admin_client):
        created = await self._make_bot(admin_client)
        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/send',
            json={'content': 'hi', 'msgtype': 'image'},
        )
        assert r.status_code == 400

    async def test_send_missing_bot_returns_404(self, admin_client):
        r = await admin_client.post(
            '/api/chat-bots/missing/send',
            json={'content': 'hi'},
        )
        assert r.status_code == 404

    async def test_send_failure_surfaces_error(self, admin_client, monkeypatch):
        async def _fake_send(platform, url, secret, content, msgtype='text'):
            return False, 'Slack limit is 12000 chars; message is 99999'

        monkeypatch.setattr(
            'app.routers.chat_bots.base.send_message', _fake_send
        )
        created = await self._make_bot(admin_client, SLACK_URL)
        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/send',
            json={'content': 'hi'},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is False
        assert '12000' in body['message']


class TestChatBotSendFile:
    async def _make_bot(self, admin_client, url=WECOM_URL):
        return (
            await admin_client.post(
                '/api/chat-bots',
                json={'name': 'Bot', 'webhook_url': url},
            )
        ).json()

    async def test_send_file_success(self, admin_client, monkeypatch):
        calls = []

        async def _fake_send_file(url, path):
            calls.append((url, path))
            return True, ''

        monkeypatch.setattr(
            'app.routers.chat_bots.send_file_webhook', _fake_send_file
        )
        created = await self._make_bot(admin_client)

        # A path under an allowed root (need not exist — the guard only
        # checks the location; existence is the notifier's job, mocked).
        path = '/tmp/vibe-test-receipt.pdf'
        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/send-file',
            json={'path': path},
        )
        assert r.status_code == 200, r.text
        assert r.json()['ok'] is True
        assert len(calls) == 1
        assert calls[0][0] == WECOM_URL

    @pytest.mark.parametrize('platform', ['dingtalk', 'feishu', 'slack'])
    async def test_send_file_rejected_for_non_wecom(
        self, admin_client, monkeypatch, platform
    ):
        """Refused BEFORE path resolution — even /etc/passwd is never
        looked at, and the platform label names itself in the error."""

        def _boom(*a, **kw):
            raise AssertionError('path must not be resolved')

        monkeypatch.setattr('app.routers.chat_bots._resolve_safe_file', _boom)
        created = await self._make_bot(admin_client, PLATFORM_URLS[platform])
        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/send-file',
            json={'path': '/etc/passwd'},
        )
        assert r.status_code == 400, platform
        detail = r.json()['detail']
        label = PLATFORMS[platform].label
        assert f'File sending is not supported for {label} webhooks' in (detail)

    async def test_send_file_rejects_path_outside_roots(self, admin_client):
        created = await self._make_bot(admin_client)
        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/send-file',
            json={'path': '/etc/passwd'},
        )
        assert r.status_code == 400
        assert 'allowed' in r.json()['detail']

    async def test_send_file_rejects_traversal_escape(self, admin_client):
        created = await self._make_bot(admin_client)
        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/send-file',
            json={'path': '/tmp/../etc/passwd'},
        )
        assert r.status_code == 400

    async def test_send_file_rejects_blank_path(self, admin_client):
        created = await self._make_bot(admin_client)
        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/send-file',
            json={'path': '   '},
        )
        assert r.status_code == 400

    async def test_send_file_missing_bot_returns_404(self, admin_client):
        r = await admin_client.post(
            '/api/chat-bots/missing/send-file',
            json={'path': '/tmp/x.pdf'},
        )
        assert r.status_code == 404

    async def test_send_file_failure_surfaces_error(
        self, admin_client, monkeypatch
    ):
        async def _fake_send_file(url, path):
            return False, 'File too large for WeCom: 30.0 MB > 20 MB.'

        monkeypatch.setattr(
            'app.routers.chat_bots.send_file_webhook', _fake_send_file
        )
        created = await self._make_bot(admin_client)
        r = await admin_client.post(
            f'/api/chat-bots/{created["id"]}/send-file',
            json={'path': '/tmp/big.pdf'},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is False
        assert '20 MB' in body['message']


class TestWecomFileNotifierGuards:
    """wecom.send_file_webhook pre-network validation (no API call)."""

    def test_webhook_key_extraction(self):
        assert wecom.webhook_key(WECOM_URL) == 'abcdefgh1234'
        assert wecom.webhook_key('https://x.test/send') == ''

    async def test_missing_key_fails_clean(self, tmp_path):
        f = tmp_path / 'a.txt'
        f.write_text('content here')
        ok, msg = await wecom.send_file_webhook('https://x.test/send', str(f))
        assert ok is False
        assert 'key' in msg

    async def test_nonexistent_file_fails(self):
        ok, msg = await wecom.send_file_webhook(
            WECOM_URL, '/tmp/nope-xyz-123.pdf'
        )
        assert ok is False
        assert 'not found' in msg.lower()

    async def test_too_small_file_fails(self, tmp_path):
        f = tmp_path / 'tiny.txt'
        f.write_text('x')  # 1 byte < 6
        ok, msg = await wecom.send_file_webhook(WECOM_URL, str(f))
        assert ok is False
        assert 'too small' in msg.lower()


class TestLegacyRowSendGuard:
    """A row saved BEFORE URL validation must not be sendable.

    Create/update derive the platform from the URL, but a legacy row
    can still hold an arbitrary host. Every send endpoint re-derives
    and refuses, so the stored secret can never be posted to a host
    the detector no longer accepts.
    """

    LEGACY_URL = 'http://example.com/webhook?key=very-secret'

    async def _legacy_bot(self, override_async_session, admin_user):
        async with override_async_session() as db:
            bot = ChatBot(
                name='Ancient',
                webhook_url=self.LEGACY_URL,
                platform='wecom',
                created_by=admin_user.id,
            )
            db.add(bot)
            await db.commit()
            await db.refresh(bot)
            return bot

    @pytest.mark.parametrize('action', ['test', 'send', 'send-file'])
    async def test_send_endpoints_refuse_legacy_url(
        self, admin_client, override_async_session, admin_user, action
    ):
        bot = await self._legacy_bot(override_async_session, admin_user)
        body = (
            {'path': '/tmp/x.pdf'}
            if action == 'send-file'
            else {'content': 'hi'}
        )
        r = await admin_client.post(
            f'/api/chat-bots/{bot.id}/{action}', json=body
        )
        assert r.status_code == 400, action
        assert 'no longer accepted' in r.json()['detail']
        # The message tells the user what to do.
        assert 'edit the bot and re-save' in r.json()['detail']

    async def test_valid_legacy_row_still_sends(
        self, admin_client, override_async_session, admin_user, monkeypatch
    ):
        """A legacy row whose URL IS a real platform URL keeps working."""
        calls = []

        async def _fake_send(platform, url, secret, content, msgtype='text'):
            calls.append(url)
            return True, ''

        monkeypatch.setattr(
            'app.routers.chat_bots.base.send_message', _fake_send
        )
        async with override_async_session() as db:
            bot = ChatBot(
                name='Ancient but valid',
                webhook_url=WECOM_URL,
                platform='wecom',
                created_by=admin_user.id,
            )
            db.add(bot)
            await db.commit()
            await db.refresh(bot)
            id_ = bot.id

        r = await admin_client.post(f'/api/chat-bots/{id_}/test', json={})
        assert r.status_code == 200, r.text
        assert calls == [WECOM_URL]


class TestLegacyRows:
    """A DB created with the OLD (WeCom-only) schema must upgrade in
    place: existing rows read back as platform='wecom'."""

    async def test_ensure_added_columns_backfills_platform(self, tmp_path):
        db_path = tmp_path / 'legacy.db'

        # A real legacy DB has every table from its era — build the
        # full current schema, then swap wecom_bots back to the shape
        # it shipped with BEFORE `platform`/`secret` existed (no
        # alembic — _ensure_added_columns is the only migration path
        # an install has, and it walks every table).
        engine = sqlalchemy_create_async_engine(
            f'sqlite+aiosqlite:///{db_path}'
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        raw = sqlite3.connect(db_path)
        raw.execute('DROP TABLE wecom_bots')
        raw.execute(
            """
            CREATE TABLE wecom_bots (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                webhook_url VARCHAR NOT NULL,
                created_by VARCHAR NOT NULL REFERENCES users(id),
                created_at VARCHAR,
                updated_at VARCHAR
            )
            """
        )
        raw.execute(
            'INSERT INTO wecom_bots (id, name, webhook_url, created_by,'
            ' created_at, updated_at) VALUES'
            " ('legacy-1', 'Old Bot', ?, 'u1', 't1', 't1')",
            (WECOM_URL,),
        )
        raw.commit()
        raw.close()

        # Boot-time migration against the legacy DB, through the same
        # async-engine run_sync path init_db uses.
        engine = sqlalchemy_create_async_engine(
            f'sqlite+aiosqlite:///{db_path}'
        )
        async with engine.begin() as conn:
            await conn.run_sync(_ensure_added_columns)
        await engine.dispose()

        check = sqlite3.connect(db_path)
        cols = {
            row[1] for row in check.execute('PRAGMA table_info(wecom_bots)')
        }
        assert 'platform' in cols
        assert 'secret' in cols
        row = check.execute(
            'SELECT platform, secret FROM wecom_bots WHERE id=?',
            ('legacy-1',),
        ).fetchone()
        check.close()
        assert row[0] == 'wecom'
        assert row[1] is None

    async def test_model_reads_legacy_rows(
        self, override_async_session, admin_user
    ):
        """Through SQLAlchemy too: a row with platform=None (e.g.
        inserted by an old client mid-upgrade) resolves as wecom for
        capability purposes."""
        async with override_async_session() as db:
            db.add(
                ChatBot(
                    id='legacy-2',
                    name='Ancient',
                    webhook_url=WECOM_URL,
                    platform='wecom',
                    created_by=admin_user.id,
                )
            )
            await db.commit()
            rows = (await db.execute(select(ChatBot))).scalars().all()
            assert len(rows) == 1
            assert rows[0].platform == 'wecom'


class TestAuth:
    async def test_unauthenticated_list_rejected(self, override_async_session):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url='http://test'
        ) as client:
            r = await client.get('/api/chat-bots')
            assert r.status_code == 401

    async def test_unauthenticated_platforms_rejected(
        self, override_async_session
    ):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url='http://test'
        ) as client:
            r = await client.get('/api/chat-bots/platforms')
            assert r.status_code == 401
