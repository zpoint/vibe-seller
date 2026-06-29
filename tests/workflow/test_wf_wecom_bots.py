"""Workflow tests for WeChat Work bot CRUD + webhook test endpoint.

Covers:
- list returns masked webhook URL (secret protection)
- single-bot GET returns the full URL (for edit)
- create/update/delete + DB state
- update rejects None / blank required fields with 400
- /test endpoint delegates to notifiers.wecom.send_webhook, trims
  whitespace-only content to the default, surfaces ok/failure
- auth required (anonymous request gets 401)
- send_webhook never leaks the webhook URL in error messages
"""

import httpx
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import select

from app.config import VIBE_SELLER_DIR
from app.main import app
from app.models.wecom_bot import WeComBot
from app.notifiers.wecom import (
    send_file_webhook,
    send_webhook,
    webhook_key,
)

pytestmark = pytest.mark.workflow


WEBHOOK = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdefgh1234'


class TestWeComBotCRUD:
    async def test_list_empty_initially(self, admin_client):
        r = await admin_client.get('/api/wecom-bots')
        assert r.status_code == 200
        assert r.json() == []

    async def test_create_persists_to_db(
        self, admin_client, override_async_session
    ):
        r = await admin_client.post(
            '/api/wecom-bots',
            json={'name': 'Ops Alerts', 'webhook_url': WEBHOOK},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['name'] == 'Ops Alerts'
        # Create returns the full URL (caller just supplied it)
        assert body['webhook_url'] == WEBHOOK
        assert body['id']

        async with override_async_session() as db:
            rows = (await db.execute(select(WeComBot))).scalars().all()
            assert len(rows) == 1
            assert rows[0].id == body['id']
            assert rows[0].webhook_url == WEBHOOK

    async def test_create_rejects_blank_fields(self, admin_client):
        r = await admin_client.post(
            '/api/wecom-bots',
            json={'name': '   ', 'webhook_url': WEBHOOK},
        )
        assert r.status_code == 400

        r = await admin_client.post(
            '/api/wecom-bots',
            json={'name': 'ok', 'webhook_url': ''},
        )
        assert r.status_code == 400

    async def test_list_masks_webhook_url(self, admin_client):
        await admin_client.post(
            '/api/wecom-bots',
            json={'name': 'A', 'webhook_url': WEBHOOK},
        )
        r = await admin_client.get('/api/wecom-bots')
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        # List must not expose the raw URL / key
        assert 'webhook_url' not in body[0]
        masked = body[0]['webhook_url_masked']
        assert 'qyapi.weixin.qq.com' in masked
        assert '1234' in masked  # last 4 of key preserved
        assert 'abcdefgh' not in masked  # rest of key hidden

    async def test_get_single_returns_full_url(self, admin_client):
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'A', 'webhook_url': WEBHOOK},
            )
        ).json()
        r = await admin_client.get(f'/api/wecom-bots/{created["id"]}')
        assert r.status_code == 200
        assert r.json()['webhook_url'] == WEBHOOK

    async def test_get_single_missing_returns_404(self, admin_client):
        r = await admin_client.get('/api/wecom-bots/missing')
        assert r.status_code == 404

    async def test_update_changes_fields_and_updated_at(
        self, admin_client, override_async_session
    ):
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Old', 'webhook_url': WEBHOOK},
            )
        ).json()
        original_updated = created['updated_at']

        r = await admin_client.put(
            f'/api/wecom-bots/{created["id"]}',
            json={'name': 'New'},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['name'] == 'New'
        assert body['webhook_url'] == WEBHOOK  # untouched
        assert body['updated_at'] != original_updated

        async with override_async_session() as db:
            row = await db.get(WeComBot, created['id'])
            assert row.name == 'New'

    async def test_update_rejects_null_required_field(self, admin_client):
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'A', 'webhook_url': WEBHOOK},
            )
        ).json()
        r = await admin_client.put(
            f'/api/wecom-bots/{created["id"]}',
            json={'webhook_url': None},
        )
        assert r.status_code == 400

    async def test_update_rejects_blank_required_field(self, admin_client):
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'A', 'webhook_url': WEBHOOK},
            )
        ).json()
        r = await admin_client.put(
            f'/api/wecom-bots/{created["id"]}',
            json={'name': '   '},
        )
        assert r.status_code == 400

    async def test_update_missing_bot_returns_404(self, admin_client):
        r = await admin_client.put(
            '/api/wecom-bots/does-not-exist',
            json={'name': 'x'},
        )
        assert r.status_code == 404

    async def test_delete_removes_from_db(
        self, admin_client, override_async_session
    ):
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Doomed', 'webhook_url': WEBHOOK},
            )
        ).json()

        r = await admin_client.delete(f'/api/wecom-bots/{created["id"]}')
        assert r.status_code == 200
        assert r.json() == {'ok': True}

        async with override_async_session() as db:
            row = await db.get(WeComBot, created['id'])
            assert row is None

    async def test_delete_missing_returns_404(self, admin_client):
        r = await admin_client.delete('/api/wecom-bots/missing')
        assert r.status_code == 404


class TestWeComBotTest:
    async def test_test_endpoint_success(self, admin_client, monkeypatch):
        calls = []

        async def _fake_send(url, content, msgtype='text'):
            calls.append((url, content, msgtype))
            return True, ''

        monkeypatch.setattr('app.routers.wecom_bots.send_webhook', _fake_send)
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()

        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/test',
            json={},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is True
        assert len(calls) == 1
        assert calls[0][0] == WEBHOOK
        assert calls[0][1]  # default message was used

    async def test_test_endpoint_blank_content_uses_default(
        self, admin_client, monkeypatch
    ):
        calls = []

        async def _fake_send(url, content, msgtype='text'):
            calls.append(content)
            return True, ''

        monkeypatch.setattr('app.routers.wecom_bots.send_webhook', _fake_send)
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()

        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/test',
            json={'content': '   '},
        )
        assert r.status_code == 200
        # Whitespace-only content was replaced with the default
        assert calls[0].strip() != ''
        assert calls[0] != '   '

    async def test_test_endpoint_failure_surfaces_error(
        self, admin_client, monkeypatch
    ):
        async def _fake_send(url, content, msgtype='text'):
            return False, 'invalid webhook url'

        monkeypatch.setattr('app.routers.wecom_bots.send_webhook', _fake_send)
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()

        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/test',
            json={},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is False
        assert 'invalid webhook url' in body['message']

    async def test_test_endpoint_missing_bot(self, admin_client):
        r = await admin_client.post(
            '/api/wecom-bots/missing/test',
            json={},
        )
        assert r.status_code == 404


class TestWeComBotSend:
    async def test_send_text_success(self, admin_client, monkeypatch):
        calls = []

        async def _fake_send(url, content, msgtype='text'):
            calls.append((url, content, msgtype))
            return True, ''

        monkeypatch.setattr('app.routers.wecom_bots.send_webhook', _fake_send)
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()

        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/send',
            json={'content': 'hello world'},
        )
        assert r.status_code == 200
        assert r.json()['ok'] is True
        assert calls == [(WEBHOOK, 'hello world', 'text')]

    async def test_send_markdown_success(self, admin_client, monkeypatch):
        calls = []

        async def _fake_send(url, content, msgtype='text'):
            calls.append(msgtype)
            return True, ''

        monkeypatch.setattr('app.routers.wecom_bots.send_webhook', _fake_send)
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()

        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/send',
            json={'content': '# heading', 'msgtype': 'markdown'},
        )
        assert r.status_code == 200
        assert calls == ['markdown']

    async def test_send_rejects_blank_content(self, admin_client):
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()
        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/send',
            json={'content': '   '},
        )
        assert r.status_code == 400

    async def test_send_rejects_unknown_msgtype(self, admin_client):
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()
        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/send',
            json={'content': 'hi', 'msgtype': 'image'},
        )
        assert r.status_code == 400

    async def test_send_missing_bot_returns_404(self, admin_client):
        r = await admin_client.post(
            '/api/wecom-bots/missing/send',
            json={'content': 'hi'},
        )
        assert r.status_code == 404

    async def test_send_failure_surfaces_error(self, admin_client, monkeypatch):
        async def _fake_send(url, content, msgtype='text'):
            return False, 'invalid webhook url'

        monkeypatch.setattr('app.routers.wecom_bots.send_webhook', _fake_send)
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()
        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/send',
            json={'content': 'hi'},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is False
        assert 'invalid webhook url' in body['message']


class TestWeComBotSendFile:
    async def test_send_file_success(self, admin_client, monkeypatch):
        calls = []

        async def _fake_send_file(url, path):
            calls.append((url, path))
            return True, ''

        monkeypatch.setattr(
            'app.routers.wecom_bots.send_file_webhook', _fake_send_file
        )
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()

        # A path under an allowed root (need not exist — the guard only
        # checks the location; existence is the notifier's job, mocked).
        path = str(VIBE_SELLER_DIR / 'downloads' / 't2-brml' / 'receipt.pdf')
        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/send-file',
            json={'path': path},
        )
        assert r.status_code == 200, r.text
        assert r.json()['ok'] is True
        assert len(calls) == 1
        assert calls[0][0] == WEBHOOK

    async def test_send_file_rejects_path_outside_roots(self, admin_client):
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()
        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/send-file',
            json={'path': '/etc/passwd'},
        )
        assert r.status_code == 400
        assert 'allowed' in r.json()['detail']

    async def test_send_file_rejects_traversal_escape(self, admin_client):
        # A path that starts under an allowed root but climbs out via
        # `..` must resolve and be rejected.
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()
        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/send-file',
            json={'path': '/tmp/../etc/passwd'},
        )
        assert r.status_code == 400

    async def test_send_file_rejects_blank_path(self, admin_client):
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()
        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/send-file',
            json={'path': '   '},
        )
        assert r.status_code == 400

    async def test_send_file_missing_bot_returns_404(self, admin_client):
        r = await admin_client.post(
            '/api/wecom-bots/missing/send-file',
            json={'path': '/tmp/x.pdf'},
        )
        assert r.status_code == 404

    async def test_send_file_failure_surfaces_error(
        self, admin_client, monkeypatch
    ):
        async def _fake_send_file(url, path):
            return False, 'File too large for WeCom: 30.0 MB > 20 MB.'

        monkeypatch.setattr(
            'app.routers.wecom_bots.send_file_webhook', _fake_send_file
        )
        created = (
            await admin_client.post(
                '/api/wecom-bots',
                json={'name': 'Bot', 'webhook_url': WEBHOOK},
            )
        ).json()
        path = str(VIBE_SELLER_DIR / 'downloads' / 'big.pdf')
        r = await admin_client.post(
            f'/api/wecom-bots/{created["id"]}/send-file',
            json={'path': path},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is False
        assert '20 MB' in body['message']


class TestSendFileNotifierGuards:
    """send_file_webhook pre-network validation (no WeCom call)."""

    def test_webhook_key_extraction(self):
        assert webhook_key(WEBHOOK) == 'abcdefgh1234'
        assert webhook_key('https://x.test/send') == ''

    async def test_missing_key_fails_clean(self, tmp_path):
        f = tmp_path / 'a.txt'
        f.write_text('content here')
        ok, msg = await send_file_webhook('https://x.test/send', str(f))
        assert ok is False
        assert 'key' in msg

    async def test_nonexistent_file_fails(self):
        ok, msg = await send_file_webhook(WEBHOOK, '/tmp/nope-xyz-123.pdf')
        assert ok is False
        assert 'not found' in msg.lower()

    async def test_too_small_file_fails(self, tmp_path):
        f = tmp_path / 'tiny.txt'
        f.write_text('x')  # 1 byte < 6
        ok, msg = await send_file_webhook(WEBHOOK, str(f))
        assert ok is False
        assert 'too small' in msg.lower()


class TestNotifierNoLeak:
    """send_webhook must never echo the URL/key back to callers."""

    async def test_http_error_does_not_leak_url(self, monkeypatch):
        async def _raise(*a, **kw):
            # HTTPStatusError exposes the URL in its default str(); we
            # use a plain RequestError for the same leak risk.
            raise httpx.RequestError(f'connection failed to {WEBHOOK}')

        monkeypatch.setattr(httpx.AsyncClient, 'post', _raise)
        ok, msg = await send_webhook(WEBHOOK, 'hi')
        assert ok is False
        assert 'abcdefgh1234' not in msg
        assert WEBHOOK not in msg

    async def test_unexpected_error_does_not_leak_url(self, monkeypatch):
        async def _raise(*a, **kw):
            raise RuntimeError(f'boom with {WEBHOOK}')

        monkeypatch.setattr(httpx.AsyncClient, 'post', _raise)
        ok, msg = await send_webhook(WEBHOOK, 'hi')
        assert ok is False
        assert WEBHOOK not in msg
        assert 'abcdefgh1234' not in msg

    async def test_send_file_http_error_does_not_leak_url(
        self, monkeypatch, tmp_path
    ):
        f = tmp_path / 'report.pdf'
        f.write_bytes(b'%PDF-1.5 fake content here')

        async def _raise(*a, **kw):
            raise httpx.RequestError(f'upload failed to {WEBHOOK}')

        monkeypatch.setattr(httpx.AsyncClient, 'post', _raise)
        ok, msg = await send_file_webhook(WEBHOOK, str(f))
        assert ok is False
        assert WEBHOOK not in msg
        assert 'abcdefgh1234' not in msg


class TestAuth:
    async def test_unauthenticated_list_rejected(self, override_async_session):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url='http://test'
        ) as client:
            r = await client.get('/api/wecom-bots')
            assert r.status_code == 401
