"""A task's mailboxes through its in-task email tools, end to end.

Each case calls the tool an agent calls (``app.mcp_server``), routed
through the real app. The cases:
- every mailbox state: configured, sending off, no outgoing server,
  sync failing;
- the store boundary: a store task reads, sends from and syncs only its
  own store's mailboxes, and a task with no store sees every mailbox;
- no mailbox at all;
- sync outcomes recorded and cleared;
- the call shape the synced skills already use
  (``vibe_seller_email_info(store_id=…)``).

Every answer comes from ``app.email.access``, the one place that decides.
"""

from datetime import UTC, datetime
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from sqlalchemy import update

from app import mcp_server
from app.ai.tool_call_guards import tool_call_refusal
from app.config import EMAIL_DBS_DIR
from app.email.access import NO_MAILBOX, NO_STORE_MAILBOX
from app.models.email_account import EmailAccount
from app.models.task import Task
from app.scheduler.email_sync import sync_all_email_accounts
from app.task_states import TaskStatus

pytestmark = pytest.mark.workflow


@pytest.fixture
def smtp_spy():
    with (
        patch('smtplib.SMTP_SSL') as ssl_cls,
        patch('smtplib.SMTP') as plain_cls,
    ):
        yield ssl_cls, plain_cls


def _smtp_untouched(spy) -> bool:
    return not spy[0].called and not spy[1].called


@pytest.fixture(autouse=True)
def _mail_dbs_in_tmp(monkeypatch, tmp_path):
    """Keep the per-mailbox SQLite files off the developer's machine."""
    monkeypatch.setattr('app.email.info.init_email_db', lambda account_id: None)
    monkeypatch.setattr(
        'app.email.access.db_path_for_account',
        lambda account_id: tmp_path / f'email_{account_id}.db',
    )


@pytest.fixture
def tool(admin_client, monkeypatch):
    """Call an in-task tool as the agent of ``task_id`` would."""

    async def _call_api(method, path, body=None, timeout=60):
        resp = await admin_client.request(method, path, json=body)
        return resp.json()

    monkeypatch.setattr('app.mcp_server.call_api', _call_api)

    async def call(task_id: str, name: str, **args) -> dict:
        monkeypatch.setitem(mcp_server._config, 'task_id', task_id)
        return json.loads(await mcp_server.handle_tool_call(name, args))

    return call


async def _store(client, name: str) -> dict:
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200, r.text
    return r.json()


async def _mailbox(client, email: str, store: dict | None) -> dict:
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
    acct = r.json()
    if store:
        r = await client.post(
            f'/api/stores/{store["id"]}/emails',
            json={'email_account_id': acct['id']},
        )
        assert r.status_code == 200, r.text
    return acct


async def _task(override_async_session, admin_user, store) -> str:
    task_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    async with override_async_session() as db:
        db.add(
            Task(
                id=task_id,
                title='Mail task',
                store_id=store['id'] if store else None,
                created_by=admin_user.id,
                status=TaskStatus.RUNNING,
                created_at=now,
                updated_at=now,
                started_at=now,
            )
        )
        await db.commit()
    return task_id


async def _set(override_async_session, account_id: str, **values) -> None:
    async with override_async_session() as db:
        await db.execute(
            update(EmailAccount)
            .where(EmailAccount.id == account_id)
            .values(**values)
        )
        await db.commit()


@pytest.fixture
async def shops(admin_client, override_async_session, admin_user):
    """Store A with a mailbox in every state, store B with one of its
    own, store C with none; a task on A, one on C, and one with none."""
    a = await _store(admin_client, 'Shop A')
    b = await _store(admin_client, 'Shop B')
    c = await _store(admin_client, 'Shop C')
    ok = await _mailbox(admin_client, 'ok@a.example', a)
    off = await _mailbox(admin_client, 'off@a.example', a)
    r = await admin_client.put(
        f'/api/email-accounts/{off["id"]}/send-permission',
        json={'allow_send': False},
    )
    assert r.status_code == 200, r.text
    nosmtp = await _mailbox(admin_client, 'nosmtp@a.example', a)
    await _set(override_async_session, nosmtp['id'], smtp_host=None)
    broken = await _mailbox(admin_client, 'broken@a.example', a)
    await _set(
        override_async_session,
        broken['id'],
        sync_error='LOGIN failed',
        synced_at='2026-11-20T08:00:00+00:00',
    )
    other = await _mailbox(admin_client, 'other@b.example', b)
    return {
        'a': a,
        'b': b,
        'ok': ok,
        'broken': broken,
        'other': other,
        'task_a': await _task(override_async_session, admin_user, a),
        'task_c': await _task(override_async_session, admin_user, c),
        'task_none': await _task(override_async_session, admin_user, None),
    }


def _by_email(info: dict) -> dict:
    return {acct['email']: acct for acct in info['accounts']}


def _send(to_from: str) -> dict:
    return {
        'account_email': to_from,
        'to': 'buyer@example.com',
        'subject': 'Order update',
        'body': 'It shipped.',
    }


class TestWhatEachMailboxCanDo:
    async def test_store_task_sees_its_own_mailboxes_and_their_state(
        self, shops, tool
    ):
        info = await tool(shops['task_a'], 'vibe_seller_email_info')
        boxes = _by_email(info)
        assert set(boxes) == {
            'ok@a.example',
            'off@a.example',
            'nosmtp@a.example',
            'broken@a.example',
        }
        state = {e: (b['can_read'], b['can_send']) for e, b in boxes.items()}
        assert state == {
            'ok@a.example': (True, True),
            'off@a.example': (True, False),  # read only: sending off
            'nosmtp@a.example': (True, False),  # read only: no SMTP
            'broken@a.example': (False, True),  # send only: sync failing
        }
        assert (
            '"Email sending permission"'
            in (boxes['off@a.example']['send_problem'])
        )
        assert (
            'no outgoing (SMTP) server'
            in (boxes['nosmtp@a.example']['send_problem'])
        )
        assert 'LOGIN failed' in boxes['broken@a.example']['read_problem']
        assert all(b['db_path'] for b in boxes.values())

    async def test_store_without_mailbox_says_so(self, shops, tool):
        info = await tool(shops['task_c'], 'vibe_seller_email_info')
        assert info['accounts'] == []
        assert info['note'] == NO_STORE_MAILBOX

    async def test_no_store_task_sees_every_mailbox_and_its_stores(
        self, shops, tool
    ):
        info = await tool(shops['task_none'], 'vibe_seller_email_info')
        boxes = _by_email(info)
        assert 'other@b.example' in boxes
        assert boxes['other@b.example']['stores'] == ['Shop B']
        assert boxes['ok@a.example']['stores'] == ['Shop A']


class TestReadingTheDatabaseItGives:
    """The agent queries the ``db_path`` ``email_info`` gives it with
    sqlite3 (its description, the prompt line, ``noon-shared`` § 1). The
    tool-call guard of that task's session lets exactly those files
    through. Observed on a real mailbox: it refused the path the tool had
    just given, and the task could not read its own inbox."""

    @pytest.fixture
    def readable(self, monkeypatch, override_async_session):
        # Where the paths really are: inside the data dir the guard
        # protects (only path strings; init_email_db is stubbed above).
        monkeypatch.setattr(
            'app.email.access.db_path_for_account',
            lambda account_id: EMAIL_DBS_DIR / f'email_{account_id}.db',
        )
        monkeypatch.setattr(
            'app.ai.tool_call_guards.async_session', override_async_session
        )

        async def refusal(task_id: str, tool_name: str, tool_input: dict):
            """What the PreToolUse hook of *task_id*'s agent answers."""
            session = SimpleNamespace(task_id=task_id, mode='auto')
            return await tool_call_refusal(
                session, {'tool_name': tool_name, 'tool_input': tool_input}
            )

        return refusal

    @staticmethod
    def _query(db_path: str) -> dict:
        return {
            'command': f'sqlite3 "{db_path}" "SELECT subject FROM emails '
            "WHERE folder='INBOX' ORDER BY received_epoch DESC LIMIT 1\""
        }

    async def test_its_own_mailboxes_read_and_another_stores_do_not(
        self, shops, tool, readable
    ):
        own = _by_email(await tool(shops['task_a'], 'vibe_seller_email_info'))
        theirs = _by_email(
            await tool(shops['task_none'], 'vibe_seller_email_info')
        )['other@b.example']
        for box in own.values():
            path = box['db_path']
            assert (
                await readable(shops['task_a'], 'Bash', self._query(path))
                is None
            )
            assert (
                await readable(shops['task_a'], 'Read', {'file_path': path})
                is None
            )
        deny = await readable(
            shops['task_a'], 'Bash', self._query(theirs['db_path'])
        )
        assert deny and "vibe-seller's own data" in deny

    async def test_no_store_task_reads_every_mailbox(
        self, shops, tool, readable
    ):
        info = await tool(shops['task_none'], 'vibe_seller_email_info')
        for box in info['accounts']:
            query = self._query(box['db_path'])
            assert await readable(shops['task_none'], 'Bash', query) is None

    async def test_store_without_mailbox_reads_none(
        self, shops, tool, readable
    ):
        any_box = (await tool(shops['task_none'], 'vibe_seller_email_info'))[
            'accounts'
        ][0]
        deny = await readable(
            shops['task_c'], 'Bash', self._query(any_box['db_path'])
        )
        assert deny and "vibe-seller's own data" in deny

    async def test_the_app_database_stays_refused(self, shops, readable):
        deny = await readable(
            shops['task_a'],
            'Bash',
            {
                'command': f'sqlite3 {EMAIL_DBS_DIR.parent}/vibe_seller.db .tables'
            },
        )
        assert deny and "vibe-seller's own data" in deny


class TestTheSkillsCallShape:
    """``vibe_seller_email_info(store_id='<store-id>')`` is what the
    synced skills (``noon-shared`` § 1) call, on every release."""

    async def test_own_store_by_id_or_slug(self, shops, tool):
        a = shops['a']
        for named in (a['id'], a['slug']):
            info = await tool(
                shops['task_a'], 'vibe_seller_email_info', store_id=named
            )
            assert 'ok@a.example' in _by_email(info), named

    async def test_store_task_cannot_read_another_store(self, shops, tool):
        info = await tool(
            shops['task_a'], 'vibe_seller_email_info', store_id=shops['b']['id']
        )
        assert "only its own store's mailboxes" in info['detail']
        assert 'accounts' not in info

    async def test_no_store_task_narrows_to_one_store(self, shops, tool):
        info = await tool(
            shops['task_none'],
            'vibe_seller_email_info',
            store_id=shops['b']['id'],
        )
        assert set(_by_email(info)) == {'other@b.example'}


class TestSending:
    async def test_configured_mailbox_sends(self, shops, tool, smtp_spy):
        result = await tool(
            shops['task_a'], 'vibe_seller_send_email', **_send('ok@a.example')
        )
        assert result['ok'] is True
        smtp_spy[0].return_value.sendmail.assert_called_once()

    async def test_sending_off_is_refused_with_why(self, shops, tool, smtp_spy):
        result = await tool(
            shops['task_a'], 'vibe_seller_send_email', **_send('off@a.example')
        )
        assert 'turned sending off' in result['detail']
        assert 'draft' in result['detail']
        assert _smtp_untouched(smtp_spy)

    async def test_no_outgoing_server_is_refused_with_why(
        self, shops, tool, smtp_spy
    ):
        result = await tool(
            shops['task_a'],
            'vibe_seller_send_email',
            **_send('nosmtp@a.example'),
        )
        assert 'no outgoing (SMTP) server' in result['detail']
        assert 'Settings → Email' in result['detail']
        assert _smtp_untouched(smtp_spy)

    async def test_send_only_mailbox_still_sends(self, shops, tool, smtp_spy):
        """A failing inbox sync does not stop the outgoing side."""
        result = await tool(
            shops['task_a'],
            'vibe_seller_send_email',
            **_send('broken@a.example'),
        )
        assert result['ok'] is True

    async def test_another_stores_mailbox_is_refused(
        self, shops, tool, smtp_spy
    ):
        result = await tool(
            shops['task_a'],
            'vibe_seller_send_email',
            **_send('other@b.example'),
        )
        detail = result['detail']
        assert "not one of this store's mailboxes" in detail
        assert 'ok@a.example' in detail
        assert 'other@b.example' not in detail.split('Use one of:')[1]
        assert _smtp_untouched(smtp_spy)

    async def test_no_store_task_sends_from_any_store(
        self, shops, tool, smtp_spy
    ):
        result = await tool(
            shops['task_none'],
            'vibe_seller_send_email',
            **_send('other@b.example'),
        )
        assert result['ok'] is True

    async def test_store_without_mailbox_cannot_send(
        self, shops, tool, smtp_spy
    ):
        result = await tool(
            shops['task_c'], 'vibe_seller_send_email', **_send('ok@a.example')
        )
        assert result['detail'] == NO_STORE_MAILBOX
        assert _smtp_untouched(smtp_spy)


class TestNoMailboxAnywhere:
    async def test_no_store_task_is_told_nothing_can_send(
        self, admin_client, override_async_session, admin_user, tool, smtp_spy
    ):
        task_id = await _task(override_async_session, admin_user, None)
        info = await tool(task_id, 'vibe_seller_email_info')
        assert info['note'] == NO_MAILBOX
        result = await tool(
            task_id, 'vibe_seller_send_email', **_send('x@example.com')
        )
        assert result['detail'] == NO_MAILBOX
        assert _smtp_untouched(smtp_spy)


class TestSyncing:
    @pytest.fixture(autouse=True)
    def _no_real_imap_state(self, monkeypatch):
        monkeypatch.setattr(
            'app.scheduler.email_sync.maybe_seed_sync_state',
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            'app.scheduler.email_sync.get_sync_state', lambda *a, **k: {}
        )

    async def test_another_stores_mailbox_is_refused(
        self, shops, tool, monkeypatch
    ):
        imap = AsyncMock(return_value=1)
        monkeypatch.setattr(
            'app.scheduler.email_sync.sync_account_emails', imap
        )
        result = await tool(
            shops['task_a'],
            'vibe_seller_sync_email_now',
            account_email='other@b.example',
        )
        assert "not one of this store's mailboxes" in result['detail']
        imap.assert_not_called()

    async def test_failure_is_recorded_and_success_clears_it(
        self, shops, tool, monkeypatch
    ):
        monkeypatch.setattr(
            'app.scheduler.email_sync.sync_account_emails',
            AsyncMock(side_effect=OSError('IMAP connection refused')),
        )
        result = await tool(
            shops['task_a'],
            'vibe_seller_sync_email_now',
            account_email='ok@a.example',
        )
        assert 'IMAP connection refused' in result['detail']
        assert 'Settings → Email' in result['detail']
        box = _by_email(await tool(shops['task_a'], 'vibe_seller_email_info'))
        assert box['ok@a.example']['can_read'] is False
        assert 'IMAP connection refused' in box['ok@a.example']['read_problem']

        monkeypatch.setattr(
            'app.scheduler.email_sync.sync_account_emails',
            AsyncMock(return_value=2),
        )
        result = await tool(
            shops['task_a'],
            'vibe_seller_sync_email_now',
            account_email='ok@a.example',
        )
        assert result['new_emails'] == 2
        box = _by_email(await tool(shops['task_a'], 'vibe_seller_email_info'))
        assert box['ok@a.example']['can_read'] is True

    async def test_fixing_the_settings_clears_the_recorded_error(
        self, shops, tool, admin_client
    ):
        r = await admin_client.put(
            f'/api/email-accounts/{shops["broken"]["id"]}',
            json={'password': 'the-right-one'},
        )
        assert r.status_code == 200, r.text
        box = _by_email(await tool(shops['task_a'], 'vibe_seller_email_info'))
        assert box['broken@a.example']['can_read'] is True

    async def test_scheduled_sync_records_each_mailbox_outcome(
        self, shops, override_async_session, monkeypatch
    ):
        """The 5-minute job goes through the same recorder as "sync now":
        a failing mailbox gets its error, a working one is cleared."""

        async def imap(account, password):
            if account.email == 'ok@a.example':
                raise OSError('LOGIN failed')
            return 0

        monkeypatch.setattr(
            'app.scheduler.email_sync.sync_account_emails', imap
        )
        await sync_all_email_accounts()
        async with override_async_session() as db:
            ok = await db.get(EmailAccount, shops['ok']['id'])
            broken = await db.get(EmailAccount, shops['broken']['id'])
        assert ok.sync_error == 'LOGIN failed'
        assert broken.sync_error is None  # its own sync worked this time
        assert broken.synced_at > '2026-11-20T08:00:00+00:00'
