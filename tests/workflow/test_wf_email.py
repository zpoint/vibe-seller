"""Workflow tests for the email system (CRUD, SMTP, info)."""

from datetime import UTC, datetime
import json
import sqlite3
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine as sqlalchemy_create_async_engine,
)

from app import mcp_server
from app.auth import create_token
from app.config import AI_BOT_USER_ID, DEFAULT_USER_ID
from app.database import _ensure_added_columns  # noqa: PLC2701
from app.email.access import NO_STORE_MAILBOX, read_problem, send_problem
from app.models.app_settings import AppSettings
from app.models.base import Base
from app.models.email_account import EmailAccount
from app.models.task import Task
from app.models.user import User
from app.task_states import TaskStatus

pytestmark = pytest.mark.workflow


class TestEmailAccountCRUD:
    async def test_create_with_smtp_autodiscovery(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'test@gmail.com',
                'password': 'test123',
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data['email'] == 'test@gmail.com'
        assert data['imap_host'] == 'imap.gmail.com'
        assert data['smtp_host'] == 'smtp.gmail.com'
        assert data['smtp_port'] == 587
        assert data['smtp_use_tls'] is True

    async def test_create_with_explicit_smtp(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'custom@custom.com',
                'password': 'pw',
                'imap_host': 'imap.custom.com',
                'smtp_host': 'smtp.custom.com',
                'smtp_port': 465,
                'smtp_use_tls': False,
            },
        )
        assert r.status_code == 200
        data = r.json()
        assert data['smtp_host'] == 'smtp.custom.com'
        assert data['smtp_port'] == 465

    async def test_update_smtp_fields(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'upd@163.com',
                'password': 'pw',
            },
        )
        acct_id = r.json()['id']

        r2 = await admin_client.put(
            f'/api/email-accounts/{acct_id}',
            json={
                'smtp_host': 'new.smtp.com',
                'smtp_port': 587,
            },
        )
        assert r2.status_code == 200
        assert r2.json()['smtp_host'] == 'new.smtp.com'

    async def test_smtp_discover_endpoint(self, admin_client):
        r = await admin_client.get(
            '/api/email-accounts/discover-smtp',
            params={'email': 'user@gmail.com'},
        )
        assert r.status_code == 200
        data = r.json()
        assert data['smtp_host'] == 'smtp.gmail.com'
        assert data['source'] == 'known'

    async def test_response_includes_smtp_fields(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'resp@qq.com',
                'password': 'pw',
            },
        )
        data = r.json()
        assert 'smtp_host' in data
        assert 'smtp_port' in data
        assert 'smtp_use_tls' in data


class TestStoreEmailLinkLifecycle:
    async def test_link_and_unlink(self, admin_client):
        # Create store
        sr = await admin_client.post(
            '/api/stores', json={'name': 'Email Store'}
        )
        store_id = sr.json()['id']

        # Create email account
        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'link@gmail.com',
                'password': 'pw',
            },
        )
        acct_id = er.json()['id']

        # Link
        lr = await admin_client.post(
            f'/api/stores/{store_id}/emails',
            json={'email_account_id': acct_id},
        )
        assert lr.status_code == 200
        link_id = lr.json()['id']
        assert lr.json()['email'] == 'link@gmail.com'

        # List
        lsr = await admin_client.get(f'/api/stores/{store_id}/emails')
        assert len(lsr.json()) == 1

        # Unlink
        dr = await admin_client.delete(
            f'/api/stores/{store_id}/emails/{link_id}'
        )
        assert dr.json()['ok'] is True

        # Verify gone
        lsr2 = await admin_client.get(f'/api/stores/{store_id}/emails')
        assert len(lsr2.json()) == 0


class TestEmailInfoByStore:
    async def test_returns_db_paths(self, admin_client):
        # Setup store + account + link
        sr = await admin_client.post('/api/stores', json={'name': 'Info Store'})
        store_id = sr.json()['id']

        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'info@gmail.com',
                'password': 'pw',
            },
        )
        acct_id = er.json()['id']

        await admin_client.post(
            f'/api/stores/{store_id}/emails',
            json={'email_account_id': acct_id},
        )

        # Get info
        r = await admin_client.get(
            f'/api/email-accounts/info-by-store/{store_id}'
        )
        assert r.status_code == 200
        data = r.json()
        assert data['store_id'] == store_id
        assert len(data['accounts']) == 1
        assert data['accounts'][0]['email'] == 'info@gmail.com'
        assert 'db_path' in data['accounts'][0]
        assert 'schema' in data
        assert 'sample_queries' in data

    async def test_empty_store(self, admin_client):
        sr = await admin_client.post(
            '/api/stores', json={'name': 'Empty Store'}
        )
        store_id = sr.json()['id']

        r = await admin_client.get(
            f'/api/email-accounts/info-by-store/{store_id}'
        )
        assert r.status_code == 200
        assert len(r.json()['accounts']) == 0

    async def test_empty_store_says_this_task_has_no_mail(self, admin_client):
        """No schema and sample queries for mailboxes that don't exist:
        the answer is that this store's task cannot read or send mail."""
        sr = await admin_client.post('/api/stores', json={'name': 'No Mail'})
        r = await admin_client.get(
            f'/api/email-accounts/info-by-store/{sr.json()["id"]}'
        )
        assert r.status_code == 200
        data = r.json()
        assert data['note'] == NO_STORE_MAILBOX
        assert 'sample_queries' not in data

    async def test_slug_finds_the_stores_mailbox(self, admin_client):
        """Agents pass the store's slug (its stores/<slug>/ folder) as
        often as its id. Named either way, a store with a mailbox must
        never be answered as having none."""
        sr = await admin_client.post('/api/stores', json={'name': 'Slug Shop'})
        store_id = sr.json()['id']
        acct = await _create_account(admin_client, email='slug@example.com')
        await admin_client.post(
            f'/api/stores/{store_id}/emails',
            json={'email_account_id': acct['id']},
        )
        r = await admin_client.get(
            '/api/email-accounts/info-by-store/slug-shop'
        )
        assert r.status_code == 200
        data = r.json()
        assert data['store_id'] == store_id
        assert [a['email'] for a in data['accounts']] == ['slug@example.com']
        assert 'note' not in data

    async def test_unknown_store_is_refused_not_called_mailless(
        self, admin_client
    ):
        r = await admin_client.get(
            '/api/email-accounts/info-by-store/no-such-store'
        )
        assert r.status_code == 404
        assert 'No store' in r.json()['detail']


class TestSendEndpoint:
    async def test_send_404_bad_account(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts/nonexistent/send',
            json={
                'to': 'x@x.com',
                'subject': 'Hi',
                'body': 'Hello',
            },
        )
        assert r.status_code == 404

    async def test_send_no_smtp(self, admin_client):
        # Create account without SMTP
        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'nosmtp@unknown-domain-xyz.com',
                'password': 'pw',
                'imap_host': 'imap.unknown.com',
            },
        )
        acct_id = er.json()['id']

        # Clear SMTP via update (sending is on by default, so the only
        # thing stopping it is the missing outgoing server)
        await admin_client.put(
            f'/api/email-accounts/{acct_id}',
            json={'smtp_host': ''},
        )

        # Refused by the one send gate, with what the user changes —
        # before any SMTP connection (smtp_host '' is falsy).
        r = await admin_client.post(
            f'/api/email-accounts/{acct_id}/send',
            json={
                'to': 'x@x.com',
                'subject': 'Hi',
                'body': 'Hello',
            },
        )
        assert r.status_code == 403
        assert 'no outgoing (SMTP) server' in r.json()['detail']


SEND_BODY = {'to': 'buyer@example.com', 'subject': 'Hi', 'body': 'Hello'}


@pytest.fixture
def smtp_spy():
    """Both SMTP classes the sender can open, patched; nothing real."""
    with (
        patch('smtplib.SMTP_SSL') as ssl_cls,
        patch('smtplib.SMTP') as plain_cls,
    ):
        yield ssl_cls, plain_cls


def _smtp_untouched(spy) -> bool:
    ssl_cls, plain_cls = spy
    return not ssl_cls.called and not plain_cls.called


async def _create_account(client, email='shop@example.com') -> dict:
    r = await client.post(
        '/api/email-accounts',
        json={
            'email': email,
            'password': 'pw',
            'imap_host': 'imap.example.com',
            'smtp_host': 'smtp.example.com',
            'smtp_port': 465,
            'smtp_use_tls': False,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _permission_path(account_id: str) -> str:
    return f'/api/email-accounts/{account_id}/send-permission'


async def _permit(client, account_id: str, value: bool, **kwargs):
    """Flip the switch the way the web UI does; return the response."""
    return await client.put(
        _permission_path(account_id), json={'allow_send': value}, **kwargs
    )


async def _allow_send_of(client, account_id: str) -> bool:
    listed = (await client.get('/api/email-accounts')).json()
    return next(a['allow_send'] for a in listed if a['id'] == account_id)


async def _create_off(client) -> dict:
    """A mailbox the user has turned sending OFF for (via the web UI)."""
    acct = await _create_account(client)
    r = await _permit(client, acct['id'], False)
    assert r.status_code == 200, r.text
    assert r.json()['allow_send'] is False
    return acct


class TestSendPermission:
    """``allow_send`` is on by default; the user can turn it off per
    mailbox, and then sends are refused before any SMTP."""

    async def test_default_on_on_create_and_list(self, admin_client):
        acct = await _create_account(admin_client)
        assert acct['allow_send'] is True
        listed = (await admin_client.get('/api/email-accounts')).json()
        assert [a['allow_send'] for a in listed] == [True]

    async def test_new_account_sends_by_default(self, admin_client, smtp_spy):
        acct = await _create_account(admin_client)
        r = await admin_client.post(
            f'/api/email-accounts/{acct["id"]}/send', json=SEND_BODY
        )
        assert r.status_code == 200, r.text
        smtp_spy[0].return_value.sendmail.assert_called_once()

    async def test_create_cannot_set_it(self, admin_client):
        """A create body naming allow_send is refused, not silently
        ignored — the switch has its own interactive-only route."""
        r = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'eager@example.com',
                'password': 'pw',
                'imap_host': 'imap.example.com',
                'allow_send': False,
            },
        )
        assert r.status_code == 422
        assert '/send-permission' in r.text
        listed = (await admin_client.get('/api/email-accounts')).json()
        assert listed == []

    async def test_general_update_rejects_it(self, admin_client):
        acct = await _create_off(admin_client)
        r = await admin_client.put(
            f'/api/email-accounts/{acct["id"]}', json={'allow_send': True}
        )
        assert r.status_code == 422
        assert 'PUT /api/email-accounts/{id}/send-permission' in r.text
        assert await _allow_send_of(admin_client, acct['id']) is False

    async def test_send_refused_403_without_smtp(self, admin_client, smtp_spy):
        acct = await _create_off(admin_client)
        r = await admin_client.post(
            f'/api/email-accounts/{acct["id"]}/send', json=SEND_BODY
        )
        assert r.status_code == 403
        detail = r.json()['detail']
        assert 'shop@example.com' in detail
        assert 'turned sending off' in detail
        assert 'Settings → Email, "Email sending permission" switch' in detail
        assert 'draft' in detail
        assert _smtp_untouched(smtp_spy)

    async def test_toggle_round_trip_and_send(self, admin_client, smtp_spy):
        """The web UI's cookie session flips it, and it round-trips."""
        acct = await _create_account(admin_client)
        off = await _permit(admin_client, acct['id'], False)
        assert off.status_code == 200, off.text
        assert off.json()['allow_send'] is False
        assert await _allow_send_of(admin_client, acct['id']) is False
        r = await admin_client.post(
            f'/api/email-accounts/{acct["id"]}/send', json=SEND_BODY
        )
        assert r.status_code == 403
        assert _smtp_untouched(smtp_spy)

        # Back on → sends again.
        on = await _permit(admin_client, acct['id'], True)
        assert on.json()['allow_send'] is True
        assert await _allow_send_of(admin_client, acct['id']) is True
        r = await admin_client.post(
            f'/api/email-accounts/{acct["id"]}/send', json=SEND_BODY
        )
        assert r.status_code == 200, r.text
        assert r.json()['ok'] is True
        smtp_spy[0].return_value.sendmail.assert_called_once()

    async def test_unrelated_update_keeps_permission(self, admin_client):
        acct = await _create_off(admin_client)
        r = await admin_client.put(
            f'/api/email-accounts/{acct["id"]}',
            json={'smtp_port': 587},
        )
        assert r.json()['allow_send'] is False

    async def test_member_may_toggle(self, member_client):
        """Same role rule as every other email-account edit (any signed
        in user) — the permission adds no new role tier."""
        acct = await _create_account(member_client)
        r = await _permit(member_client, acct['id'], False)
        assert r.status_code == 200
        assert r.json()['allow_send'] is False

    async def test_unknown_account_404(self, admin_client):
        r = await _permit(admin_client, 'no-such-id', True)
        assert r.status_code == 404


class TestSendPermissionIsInteractiveOnly:
    """Only the web UI's own session may flip the switch — every
    credential an agent holds is refused (app/interactive_only.py).

    The attack: the user turned sending off; the agent tries to turn it
    back on for itself."""

    async def _refused(self, admin_client, client, account_id, **kwargs):
        r = await _permit(client, account_id, True, **kwargs)
        assert r.status_code == 403, r.text
        assert 'Only the user can change this' in r.json()['detail']
        assert await _allow_send_of(admin_client, account_id) is False

    async def test_vst_token_bearer_refused(
        self, admin_client, unauthed_client
    ):
        acct = await _create_off(admin_client)
        minted = await admin_client.post(
            '/api/auth/tokens', json={'name': 'agent'}
        )
        token = minted.json()['token']
        await self._refused(
            admin_client,
            unauthed_client,
            acct['id'],
            headers={'Authorization': f'Bearer {token}'},
        )
        # Other edits by the token still work — the rule is per route.
        r = await unauthed_client.put(
            f'/api/email-accounts/{acct["id"]}',
            json={'smtp_port': 587},
            headers={'Authorization': f'Bearer {token}'},
        )
        assert r.status_code == 200

    async def test_vst_token_cookie_refused(
        self, admin_client, unauthed_client
    ):
        acct = await _create_off(admin_client)
        minted = await admin_client.post(
            '/api/auth/tokens', json={'name': 'agent'}
        )
        unauthed_client.cookies.set('auth_token', minted.json()['token'])
        await self._refused(admin_client, unauthed_client, acct['id'])

    async def test_service_jwt_bearer_refused(
        self, admin_client, unauthed_client, admin_user
    ):
        """A JWT presented as Bearer (what the browser wrappers carry,
        and what a task agent with a shell can replay) — even the
        admin's own."""
        acct = await _create_off(admin_client)
        jwt_token = create_token(admin_user.id, 'admin')
        await self._refused(
            admin_client,
            unauthed_client,
            acct['id'],
            headers={'Authorization': f'Bearer {jwt_token}'},
        )

    async def test_ai_bot_jwt_cookie_refused(
        self, admin_client, unauthed_client
    ):
        """The in-task MCP server presents the AI-bot JWT as the cookie;
        a task agent can replay it the same way."""
        acct = await _create_off(admin_client)
        unauthed_client.cookies.set(
            'auth_token', create_token(AI_BOT_USER_ID, 'ai_bot')
        )
        await self._refused(admin_client, unauthed_client, acct['id'])

    async def test_no_auth_server_without_credential_allowed(
        self, admin_client, unauthed_client, override_async_session
    ):
        """Login turned off: a request with no credential is the web UI
        (the documented limit — it is also an agent's bare curl)."""
        acct = await _create_off(admin_client)
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='false'))
            await db.merge(
                User(
                    id=DEFAULT_USER_ID,
                    username='default-admin',
                    email='default-admin@example.com',
                    password_hash='x',
                    role='admin',
                    is_active=True,
                )
            )
            await db.commit()
        r = await _permit(unauthed_client, acct['id'], True)
        assert r.status_code == 200, r.text
        assert r.json()['allow_send'] is True

    async def test_send_still_refused_after_refused_toggle(
        self, admin_client, unauthed_client, admin_user, smtp_spy, store_task
    ):
        """The agent's story end to end: its toggle attempt is refused,
        and its send through the in-task tool stays refused."""
        acct = await _create_off(admin_client)
        await store_task(acct)
        r = await _permit(
            unauthed_client,
            acct['id'],
            True,
            headers={
                'Authorization': f'Bearer {create_token(admin_user.id, "admin")}'
            },
        )
        assert r.status_code == 403
        result = await _in_task_send()
        assert 'turned sending off' in result['detail']
        assert _smtp_untouched(smtp_spy)

    async def test_info_by_store_reports_permission(self, admin_client):
        store = (
            await admin_client.post('/api/stores', json={'name': 'Mail Store'})
        ).json()
        acct = await _create_account(admin_client)
        await admin_client.post(
            f'/api/stores/{store["id"]}/emails',
            json={'email_account_id': acct['id']},
        )
        info = (
            await admin_client.get(
                f'/api/email-accounts/info-by-store/{store["id"]}'
            )
        ).json()
        assert info['accounts'][0]['can_send'] is True
        assert info['accounts'][0]['send_problem'] is None
        await _permit(admin_client, acct['id'], False)
        info = (
            await admin_client.get(
                f'/api/email-accounts/info-by-store/{store["id"]}'
            )
        ).json()
        assert info['accounts'][0]['can_send'] is False
        assert (
            '"Email sending permission"' in info['accounts'][0]['send_problem']
        )


def _route_mcp_call_api(client, monkeypatch) -> None:
    """Send the in-task MCP server's API calls through the real app."""

    async def _call_api(method, path, body=None, timeout=60):
        resp = await client.request(method, path, json=body)
        return resp.json()

    monkeypatch.setattr('app.mcp_server.call_api', _call_api)


async def _in_task_send() -> dict:
    """What a task agent's ``vibe_seller_send_email`` call returns."""
    return json.loads(
        await mcp_server.handle_tool_call(
            'vibe_seller_send_email',
            {
                'account_email': 'shop@example.com',
                'to': 'buyer@example.com',
                'subject': 'Order update',
                'body': 'It shipped.',
            },
        )
    )


@pytest.fixture
async def store_task(
    admin_client, override_async_session, admin_user, monkeypatch
):
    """A running store task; its in-task MCP calls go through the app.

    Yields a function that links a mailbox to the task's store: a task
    sends only from its own store's mailboxes.
    """
    store = (
        await admin_client.post('/api/stores', json={'name': 'Send Store'})
    ).json()
    task_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with override_async_session() as db:
        db.add(
            Task(
                id=task_id,
                title='Send task',
                store_id=store['id'],
                created_by=admin_user.id,
                status=TaskStatus.RUNNING,
                created_at=now,
                updated_at=now,
                started_at=now,
            )
        )
        await db.commit()
    monkeypatch.setitem(mcp_server._config, 'task_id', task_id)
    _route_mcp_call_api(admin_client, monkeypatch)

    async def link(account: dict) -> None:
        r = await admin_client.post(
            f'/api/stores/{store["id"]}/emails',
            json={'email_account_id': account['id']},
        )
        assert r.status_code == 200, r.text

    return link


class TestInTaskSendTool:
    """The in-task stdio MCP tool ``vibe_seller_send_email`` (what a
    task agent calls) — routed through the real app via ``call_api``."""

    async def test_refused_when_off(self, admin_client, store_task, smtp_spy):
        await store_task(await _create_off(admin_client))
        result = await _in_task_send()
        assert 'turned sending off' in result['detail']
        assert 'draft' in result['detail']
        assert _smtp_untouched(smtp_spy)

    async def test_sends_by_default(self, admin_client, store_task, smtp_spy):
        await store_task(await _create_account(admin_client))
        result = await _in_task_send()
        assert result['ok'] is True
        smtp_spy[0].return_value.sendmail.assert_called_once()

    async def test_telemetry_error_never_fails_a_sent_email(
        self, admin_client, store_task, smtp_spy, monkeypatch
    ):
        """A send reported as failed gets resent: the buyer gets two."""
        await store_task(await _create_account(admin_client))

        def boom(_email):
            raise RuntimeError('telemetry')

        monkeypatch.setattr('app.telemetry.email_provider_kind', boom)
        result = await _in_task_send()
        assert result['ok'] is True
        smtp_spy[0].return_value.sendmail.assert_called_once()


class TestAllowSendMigration:
    """A DB from before ``allow_send`` upgrades in place, every
    existing mailbox coming out ON — upgrading changes nothing."""

    async def test_existing_rows_upgrade_to_true(self, tmp_path):
        db_path = tmp_path / 'legacy.db'
        engine = sqlalchemy_create_async_engine(
            f'sqlite+aiosqlite:///{db_path}'
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        raw = sqlite3.connect(db_path)
        raw.execute('ALTER TABLE email_accounts DROP COLUMN allow_send')
        raw.execute(
            'INSERT INTO email_accounts (id, email, encrypted_password,'
            ' imap_host, imap_port, use_ssl, smtp_use_tls, created_by,'
            " created_at, updated_at) VALUES ('legacy-1',"
            " 'shop@example.com', 'x', 'imap.example.com', 993, 1, 1,"
            " 'u1', 't1', 't1')"
        )
        raw.commit()
        raw.close()

        engine = sqlalchemy_create_async_engine(
            f'sqlite+aiosqlite:///{db_path}'
        )
        async with engine.begin() as conn:
            await conn.run_sync(_ensure_added_columns)
            # Idempotent: a second boot is a no-op.
            await conn.run_sync(_ensure_added_columns)
        await engine.dispose()

        check = sqlite3.connect(db_path)
        row = check.execute(
            'SELECT allow_send FROM email_accounts WHERE id=?',
            ('legacy-1',),
        ).fetchone()
        check.close()
        assert row[0] == 1


class TestSyncOutcomeMigration:
    """An install from before ``sync_error`` / ``synced_at`` upgrades in
    place: an existing, fully configured mailbox still reads and sends,
    and a store task still sends from it."""

    async def test_configured_mailbox_upgrades_able_to_read_and_send(
        self, tmp_path
    ):
        db_path = tmp_path / 'legacy.db'
        engine = sqlalchemy_create_async_engine(
            f'sqlite+aiosqlite:///{db_path}'
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        raw = sqlite3.connect(db_path)
        raw.execute('ALTER TABLE email_accounts DROP COLUMN sync_error')
        raw.execute('ALTER TABLE email_accounts DROP COLUMN synced_at')
        raw.execute(
            'INSERT INTO email_accounts (id, email, encrypted_password,'
            ' imap_host, imap_port, use_ssl, smtp_host, smtp_port,'
            ' smtp_use_tls, allow_send, created_by, created_at,'
            " updated_at) VALUES ('legacy-1', 'shop@example.com', 'x',"
            " 'imap.example.com', 993, 1, 'smtp.example.com', 465, 1, 1,"
            " 'u1', 't1', 't1')"
        )
        raw.commit()
        raw.close()

        engine = sqlalchemy_create_async_engine(
            f'sqlite+aiosqlite:///{db_path}'
        )
        async with engine.begin() as conn:
            await conn.run_sync(_ensure_added_columns)
            await conn.run_sync(_ensure_added_columns)  # idempotent
        async with AsyncSession(engine) as db:
            account = await db.get(EmailAccount, 'legacy-1')
            assert (account.sync_error, account.synced_at) == (None, None)
            assert read_problem(account) is None
            assert send_problem(account) is None
        await engine.dispose()

    async def test_upgraded_store_task_sends_from_its_mailbox(
        self, admin_client, store_task, smtp_spy
    ):
        """A mailbox that has never recorded a sync (every mailbox right
        after the upgrade) is read-and-send, and the task's send goes
        out."""
        await store_task(await _create_account(admin_client))
        result = await _in_task_send()
        assert result['ok'] is True
        smtp_spy[0].return_value.sendmail.assert_called_once()


class TestSyncNow:
    async def test_account_not_found(self, admin_client):
        r = await admin_client.post(
            '/api/email-accounts/sync-now',
            json={'account_email': 'nobody@example.com'},
        )
        assert r.status_code == 404

    async def test_happy_path(self, admin_client):
        # Create account
        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'sync@gmail.com',
                'password': 'pw',
            },
        )
        assert er.status_code == 200

        # Mock sync to avoid real IMAP
        with (
            patch(
                'app.scheduler.email_sync.sync_account_emails',
                new_callable=AsyncMock,
                return_value=3,
            ),
            patch(
                'app.scheduler.email_sync.maybe_seed_sync_state',
                new_callable=AsyncMock,
            ),
            patch(
                'app.scheduler.email_sync.get_sync_state',
                return_value={
                    'last_polled_at': '2020-01-01T00:00:00+00:00',
                },
            ),
        ):
            r = await admin_client.post(
                '/api/email-accounts/sync-now',
                json={'account_email': 'sync@gmail.com'},
            )

        assert r.status_code == 200
        data = r.json()
        assert data['ok'] is True
        assert data['account_email'] == 'sync@gmail.com'
        assert data['new_emails'] == 3
        assert 'last_polled_at' in data

    async def test_imap_failure(self, admin_client):
        # Create account
        er = await admin_client.post(
            '/api/email-accounts',
            json={
                'email': 'fail@gmail.com',
                'password': 'pw',
            },
        )
        assert er.status_code == 200

        # Mock sync to raise
        with (
            patch(
                'app.scheduler.email_sync.sync_account_emails',
                new_callable=AsyncMock,
                side_effect=Exception('IMAP connection refused'),
            ),
            patch(
                'app.scheduler.email_sync.maybe_seed_sync_state',
                new_callable=AsyncMock,
            ),
            patch(
                'app.scheduler.email_sync.get_sync_state',
                return_value={},
            ),
        ):
            r = await admin_client.post(
                '/api/email-accounts/sync-now',
                json={'account_email': 'fail@gmail.com'},
            )

        assert r.status_code == 502
        assert 'IMAP connection refused' in r.json()['detail']


class TestSendTelemetry:
    async def test_permission_change_and_sends_are_sent(
        self, admin_client, smtp_spy, monkeypatch
    ):
        events: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            'app.telemetry.send',
            lambda e, p=None: events.append((e, dict(p or {}))),
        )
        acct = await _create_account(admin_client)
        await _permit(admin_client, acct['id'], False)
        await admin_client.post(
            f'/api/email-accounts/{acct["id"]}/send', json=SEND_BODY
        )
        await _permit(admin_client, acct['id'], True)
        await admin_client.post(
            f'/api/email-accounts/{acct["id"]}/send', json=SEND_BODY
        )
        perms = [p for e, p in events if e == 'email_send_permission_changed']
        assert perms == [{'allow_send': False}, {'allow_send': True}]
        sent = [p['outcome'] for e, p in events if e == 'email_sent']
        assert sent == ['refused', 'ok']
