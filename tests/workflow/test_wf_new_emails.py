"""Workflow tests for the server-side email-watermark sweep.

Backs the ``vibe_seller_get_new_emails`` MCP tool
(``GET /api/tasks/{task_id}/new-emails``). This is the design fix for
the watermark leak that ``tests/e2e/test_email_watermark_e2e.py`` pins
with a live agent: the agent's "let me look at the inbox" reflex used
to run an unfiltered ``SELECT`` that dragged already-processed email
bodies into run 2's transcript. The contract now lives in code — the
server filters by the cursor and never returns old rows — so this test
guards that invariant deterministically, without an LLM.
"""

from datetime import UTC, datetime, timedelta
import uuid

import pytest
import pytest_asyncio

from app.email.db import db_path_for_account, init_email_db, store_emails
from app.models.email_account import EmailAccount
from app.models.schedule import Schedule
from app.models.schedule_state import ScheduleState
from app.models.store import Store
from app.models.store_email_link import StoreEmailLink
from app.models.task import Task
from app.task_states import TaskStatus

pytestmark = pytest.mark.workflow

SECRET_1 = 'sekret-alpha-42'
SECRET_2 = 'sekret-bravo-99'


def _iso_hours_ago(hours: float) -> str:
    return (datetime.now(UTC) - timedelta(hours=hours)).isoformat()


def _epoch_of(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def _iso_from_epoch(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat()


@pytest_asyncio.fixture
async def email_env(override_async_session, admin_user):
    """Schedule + store + linked email account, seeded with SECRET_1.

    Yields the ids plus the run-1 email's ISO date. The per-account
    SQLite file is removed on teardown so repeat runs start clean.
    """
    sched_id = str(uuid.uuid4())
    store_id = str(uuid.uuid4())
    acct_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    async with override_async_session() as db:
        db.add(
            Schedule(
                id=sched_id,
                title='Daily email sweep',
                schedule_type='days',
                schedule_time='09:00',
                interval_value=1,
                created_by=admin_user.id,
            )
        )
        db.add(
            Store(
                id=store_id,
                name=f'mail-store-{store_id[:8]}',
                browser_backend='chrome',
                browser_config='{"headless": true}',
                platforms='["amazon"]',
                countries='["US"]',
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            EmailAccount(
                id=acct_id,
                email=f'sweep-{acct_id[:8]}@example.test',
                encrypted_password='x',
                imap_host='imap.invalid',
                created_by=admin_user.id,
            )
        )
        db.add(
            StoreEmailLink(
                id=str(uuid.uuid4()),
                store_id=store_id,
                email_account_id=acct_id,
            )
        )
        await db.commit()

    init_email_db(acct_id)
    date_1 = _iso_hours_ago(2)
    store_emails(
        acct_id,
        [
            {
                'message_id': f'msg-1-{acct_id}@example.test',
                'folder': 'INBOX',
                'subject': 'Run1 inbound',
                'sender': 'partner@example.test',
                'date': date_1,
                # Pin fetched_at (the cursor axis) explicitly. Left to
                # default it would be `now()` for both emails and, in a
                # fast test, collide at 1-second epoch granularity —
                # run-2 would then filter SECRET_2 out. Seeding it equal
                # to `date` keeps the two emails distinctly ordered.
                'fetched_at': date_1,
                'body_text': f'The value is {SECRET_1}. Please confirm.',
            }
        ],
    )

    yield {
        'schedule_id': sched_id,
        'store_id': store_id,
        'account_id': acct_id,
        'date_1': date_1,
    }

    db_path_for_account(acct_id).unlink(missing_ok=True)


async def _make_running_task(
    override_async_session, admin_user, schedule_id, store_id
) -> str:
    now = datetime.now(UTC).isoformat()
    task_id = str(uuid.uuid4())
    async with override_async_session() as db:
        db.add(
            Task(
                id=task_id,
                title='Scheduled run',
                schedule_id=schedule_id,
                store_id=store_id,
                created_by=admin_user.id,
                status=TaskStatus.RUNNING,
                created_at=now,
                updated_at=now,
                started_at=now,
            )
        )
        await db.commit()
    return task_id


def _all_emails(body: dict) -> list[dict]:
    return [e for acct in body['accounts'] for e in acct['new_emails']]


class TestNewEmailsSweep:
    async def test_first_run_returns_recent_then_cursor_filters(
        self, admin_client, override_async_session, admin_user, email_env
    ):
        """The core anti-leak contract across two runs.

        Run 1 (no cursor) returns SECRET_1 within the lookback window.
        After the cursor advances, run 2 returns ONLY SECRET_2 — the
        already-processed SECRET_1 never comes back, so it can never
        re-enter the agent's context.
        """
        task_id = await _make_running_task(
            override_async_session,
            admin_user,
            email_env['schedule_id'],
            email_env['store_id'],
        )

        # ── Run 1: no cursor → 24h lookback returns SECRET_1 ──
        r1 = await admin_client.get(f'/api/tasks/{task_id}/new-emails')
        assert r1.status_code == 200
        b1 = r1.json()
        assert b1['first_run'] is True
        assert b1['count'] == 1
        emails1 = _all_emails(b1)
        assert SECRET_1 in emails1[0]['body_text']
        # next_watermark is a ready-to-persist epoch string at/after
        # the email we just saw.
        epoch_1 = _epoch_of(email_env['date_1'])
        assert int(b1['next_watermark']) >= epoch_1

        # Persist the cursor exactly as an agent would (goes through
        # the typed-value epoch validation on the way in).
        put = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': b1['next_watermark']},
        )
        assert put.status_code == 200

        # ── SECRET_2 arrives, strictly newer than the cursor ──
        date_2 = _iso_hours_ago(0.5)
        store_emails(
            email_env['account_id'],
            [
                {
                    'message_id': f'msg-2-{uuid.uuid4()}@example.test',
                    'folder': 'INBOX',
                    'subject': 'Run2 inbound',
                    'sender': 'partner@example.test',
                    'date': date_2,
                    'fetched_at': date_2,
                    'body_text': f'Follow-up: {SECRET_2}. Thanks.',
                }
            ],
        )

        # ── Run 2: cursor set → only SECRET_2, never SECRET_1 ──
        r2 = await admin_client.get(f'/api/tasks/{task_id}/new-emails')
        assert r2.status_code == 200
        b2 = r2.json()
        assert b2['first_run'] is False
        assert b2['count'] == 1
        emails2 = _all_emails(b2)
        body2 = emails2[0]['body_text']
        assert SECRET_2 in body2
        # The whole point: the old secret cannot reappear.
        blob2 = str(b2)
        assert SECRET_1 not in blob2
        assert int(b2['next_watermark']) >= _epoch_of(date_2)

    async def test_backdated_but_newly_fetched_is_returned(
        self, admin_client, override_async_session, admin_user, email_env
    ):
        """Cursor axis is fetch time, not the sender's Date header.

        Pins the bug class the live-agent e2e caught on MiniMax: run 1
        left the watermark at ~wall-clock ``now`` (a fumbled sweep that
        fell back to raw sqlite), then a genuinely-new email arrived
        carrying a ``Date`` header *older* than that watermark — a
        late-delivered / backdated / clock-skewed message. Filtering by
        ``date`` dropped it forever; filtering by ``fetched_at`` returns
        it because it arrived (was stored) after the cursor. This test
        would fail on the old date-based query, so it guards the fix.
        """
        task_id = await _make_running_task(
            override_async_session,
            admin_user,
            email_env['schedule_id'],
            email_env['store_id'],
        )

        # Watermark parked at "now". Seed it directly in the DB rather
        # than via the PUT endpoint: for an email-linked store that
        # endpoint now refuses a watermark that didn't come from
        # get_new_emails (the cursor-authority guard tested separately
        # in TestWatermarkFloorAuthority). This test is about the READ
        # axis (fetched_at, not the Date header), so we bypass the write
        # guard and pin the cursor directly.
        now_epoch = int(datetime.now(UTC).timestamp())
        async with override_async_session() as db:
            db.add(
                ScheduleState(
                    schedule_id=email_env['schedule_id'],
                    store_id=email_env['store_id'],
                    key='email_watermark',
                    value=str(now_epoch),
                    updated_at=datetime.now(UTC).isoformat(),
                    updated_by_task_id=task_id,
                )
            )
            await db.commit()

        # A new email arrives AFTER the cursor (fetched_at ahead of it)
        # but with a Date header from an hour BEFORE it.
        secret_late = 'sekret-late-77'
        store_emails(
            email_env['account_id'],
            [
                {
                    'message_id': f'msg-late-{uuid.uuid4()}@example.test',
                    'folder': 'INBOX',
                    'subject': 'Backdated but freshly delivered',
                    'sender': 'partner@example.test',
                    'date': _iso_from_epoch(now_epoch - 3600),
                    'fetched_at': _iso_from_epoch(now_epoch + 60),
                    'body_text': f'Delayed delivery: {secret_late}.',
                }
            ],
        )

        r = await admin_client.get(f'/api/tasks/{task_id}/new-emails')
        assert r.status_code == 200
        body = r.json()
        assert body['count'] == 1, (
            'a newly-fetched email with a past Date header must be swept'
            ' — the cursor keys off fetch time, not the sender clock'
        )
        assert secret_late in _all_emails(body)[0]['body_text']

    async def test_non_scheduled_task_rejected(
        self, admin_client, override_async_session, admin_user, email_env
    ):
        """Non-scheduled tasks have no cursor scope → 400, same as the
        other schedule-state endpoints."""
        task_id = await _make_running_task(
            override_async_session,
            admin_user,
            None,
            email_env['store_id'],
        )
        r = await admin_client.get(f'/api/tasks/{task_id}/new-emails')
        assert r.status_code == 400


class TestWatermarkFloorAuthority:
    """get_new_emails is the sole authority for the email watermark.

    Pins the design fix for the run-1→run-2 leak: a store with linked
    email accounts can only advance ``email_watermark`` through the
    ``next_watermark`` get_new_emails emitted. A hand-derived value —
    the Date-header epoch a fumbled sweep persisted in the incident —
    is below that floor and is rejected at the write boundary, so the
    already-processed email can never be re-swept.
    """

    async def test_write_rejected_before_get_new_emails(
        self, admin_client, override_async_session, admin_user, email_env
    ):
        """No floor yet + store has email accounts → write is refused."""
        task_id = await _make_running_task(
            override_async_session,
            admin_user,
            email_env['schedule_id'],
            email_env['store_id'],
        )
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': str(int(datetime.now(UTC).timestamp()) - 60)},
        )
        assert r.status_code == 400
        assert 'get_new_emails' in r.json()['detail']

    async def test_write_below_floor_rejected(
        self, admin_client, override_async_session, admin_user, email_env
    ):
        """The incident: a value below what get_new_emails showed is
        rejected, so run 2 cannot re-sweep the processed email."""
        task_id = await _make_running_task(
            override_async_session,
            admin_user,
            email_env['schedule_id'],
            email_env['store_id'],
        )
        # Establish the floor via the sanctioned read path.
        r1 = await admin_client.get(f'/api/tasks/{task_id}/new-emails')
        assert r1.status_code == 200
        floor = int(r1.json()['next_watermark'])

        # A Date-header-derived value below the floor (the fetched_at
        # axis) — exactly what leaked in the e2e — is refused.
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': str(floor - 1)},
        )
        assert r.status_code == 400
        assert 'floor' in r.json()['detail']

        # Persisting the next_watermark verbatim (== floor) is accepted.
        ok = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': str(floor)},
        )
        assert ok.status_code == 200

    async def test_reserved_floor_key_rejected_read_and_write(
        self, admin_client, override_async_session, admin_user, email_env
    ):
        """Agents can neither write nor read the server-managed floor."""
        task_id = await _make_running_task(
            override_async_session,
            admin_user,
            email_env['schedule_id'],
            email_env['store_id'],
        )
        w = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/_email_watermark_floor',
            json={'value': '0'},
        )
        assert w.status_code == 400
        assert 'reserved' in w.json()['detail']

        r = await admin_client.get(
            f'/api/tasks/{task_id}/schedule-state/_email_watermark_floor'
        )
        assert r.status_code == 400
        assert 'reserved' in r.json()['detail']

    async def test_floor_hidden_from_other_known_keys(
        self, admin_client, override_async_session, admin_user, email_env
    ):
        """The internal floor never surfaces to the agent's key list."""
        task_id = await _make_running_task(
            override_async_session,
            admin_user,
            email_env['schedule_id'],
            email_env['store_id'],
        )
        # get_new_emails writes the floor; then persist the watermark.
        r1 = await admin_client.get(f'/api/tasks/{task_id}/new-emails')
        assert r1.status_code == 200
        put = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': r1.json()['next_watermark']},
        )
        assert put.status_code == 200
        # A miss on an unrelated key lists other keys — the floor
        # (a `_`-prefixed reserved key) must not appear.
        miss = await admin_client.get(
            f'/api/tasks/{task_id}/schedule-state/nope'
        )
        known = miss.json()['other_known_keys']
        assert '_email_watermark_floor' not in known
        assert 'email_watermark' in known
