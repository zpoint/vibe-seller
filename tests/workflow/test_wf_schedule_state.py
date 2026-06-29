"""Workflow tests for the agent-controlled schedule_state tools.

Covers the task-scoped endpoints that back the
`vibe_seller_get_schedule_state` and `vibe_seller_set_schedule_state`
MCP tools. Verifies that two runs of the same schedule can hand off
a watermark value without either knowing the schedule_id.
"""

from datetime import UTC, datetime
import uuid

import pytest
import pytest_asyncio

from app.models.schedule import Schedule
from app.models.schedule_state import NO_STORE_SCOPE, ScheduleState
from app.models.store import Store
from app.models.task import Task
from app.task_states import TaskStatus

pytestmark = pytest.mark.workflow


@pytest_asyncio.fixture
async def schedule(override_async_session, admin_user):
    async with override_async_session() as db:
        sched = Schedule(
            id=str(uuid.uuid4()),
            title='Daily email sweep',
            schedule_type='days',
            schedule_time='09:00',
            interval_value=1,
            created_by=admin_user.id,
        )
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
        return sched


def _recent_epoch(seconds_ago: int = 60) -> str:
    """Epoch string a few seconds in the past — well inside the
    server-side sane-window guard for `email_watermark`. Computed at
    call time so these tests don't time-bomb if the window narrows
    or if CI runs months after a hardcoded value was authored.
    """
    return str(int(datetime.now(UTC).timestamp()) - seconds_ago)


async def _make_running_task(
    override_async_session,
    admin_user,
    schedule_id: str | None,
    store_id: str | None = None,
) -> str:
    """Insert a task directly in RUNNING so the endpoints accept it
    without going through the auto-run pipeline.

    *store_id* is optional — pass it for fanout sub-task scenarios
    where each sibling task carries its own store.
    """
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


async def _make_store(override_async_session, name: str) -> str:
    """Insert a Store row and return its id — used by per-store
    fanout scoping tests."""
    now = datetime.now(UTC).isoformat()
    store_id = str(uuid.uuid4())
    async with override_async_session() as db:
        db.add(
            Store(
                id=store_id,
                name=name,
                browser_backend='chrome',
                browser_config='{"headless": true}',
                platforms='["amazon"]',
                countries='["US"]',
                created_at=now,
                updated_at=now,
            )
        )
        await db.commit()
    return store_id


class TestScheduleStateEndpoints:
    async def test_first_run_returns_null(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        task_id = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        r = await admin_client.get(
            f'/api/tasks/{task_id}/schedule-state/email_watermark'
        )
        assert r.status_code == 200
        body = r.json()
        assert body['value'] is None
        assert body['key'] == 'email_watermark'
        # schedule_id must NOT leak back to the agent — it should
        # stay strictly server-side.
        assert 'schedule_id' not in body
        # True first-run → no other keys exist either. Empty list
        # tells the agent "nothing persisted yet, this really is
        # run 1" (distinguishing from "you hallucinated the key").
        assert body['other_known_keys'] == []

    async def test_null_lookup_surfaces_other_known_keys(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """Regression guard for the schedule-state key hallucination
        bug observed in MiniMax-M2.7 (issue #142):

          run 1 SET key='email_watermark'
          run 2 GET key='last_email_watermark'  ← hallucinated
          → agent treated run 2 as 'first run' and re-processed
            already-seen emails.

        Fix: when the requested key has no value but OTHER keys DO,
        return them in `other_known_keys` so the agent can
        self-correct on the next call instead of silently thinking
        it's a first run."""
        task_id = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        # Seed the canonical key (run 1 behavior).
        await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': _recent_epoch()},
        )
        # Agent hallucinates the prefix (run 2 behavior).
        r = await admin_client.get(
            f'/api/tasks/{task_id}/schedule-state/last_email_watermark'
        )
        assert r.status_code == 200
        body = r.json()
        assert body['value'] is None
        # Agent sees the canonical key in the hint list and can
        # retry with the right name.
        assert body['other_known_keys'] == ['email_watermark']

    async def test_set_then_get_roundtrip(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        task_id = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        # email_watermark is a typed key: must be a unix epoch
        # seconds integer string AND must land within the
        # server-side sane window. See _TYPED_VALUE_PATTERNS and
        # _EPOCH_TYPED_KEYS in app/routers/tasks_schedule_state.py.
        epoch = _recent_epoch()
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': epoch},
        )
        assert r.status_code == 200
        put_body = r.json()
        assert put_body['value'] == epoch
        assert 'schedule_id' not in put_body

        r = await admin_client.get(
            f'/api/tasks/{task_id}/schedule-state/email_watermark'
        )
        assert r.status_code == 200
        body = r.json()
        assert body['value'] == epoch
        assert body['updated_by_task_id'] == task_id
        assert 'schedule_id' not in body
        # GET surfaces a `now_epoch` reference so the agent can
        # sanity-check candidate values before persisting.
        assert isinstance(body['now_epoch'], int)
        assert body['now_epoch'] > int(epoch)

    async def test_second_run_reads_first_runs_watermark(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """The whole point: task B sees what task A wrote, even
        though they share only a schedule_id (never exchanged)."""
        task_a = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        task_b = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        epoch = _recent_epoch()

        r = await admin_client.put(
            f'/api/tasks/{task_a}/schedule-state/email_watermark',
            json={'value': epoch},
        )
        assert r.status_code == 200

        r = await admin_client.get(
            f'/api/tasks/{task_b}/schedule-state/email_watermark'
        )
        assert r.status_code == 200
        body = r.json()
        assert body['value'] == epoch
        assert body['updated_by_task_id'] == task_a

    async def test_upsert_overwrites(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        task_id = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/k',
            json={'value': 'first'},
        )
        await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/k',
            json={'value': 'second'},
        )
        r = await admin_client.get(f'/api/tasks/{task_id}/schedule-state/k')
        assert r.json()['value'] == 'second'

        async with override_async_session() as db:
            rows = await db.execute(
                ScheduleState.__table__.select().where(
                    ScheduleState.schedule_id == schedule.id
                )
            )
            assert len(rows.all()) == 1

    async def test_non_scheduled_task_rejected(
        self,
        admin_client,
        override_async_session,
        admin_user,
    ):
        """Tasks without a schedule_id get 400, not silently scoped."""
        task_id = await _make_running_task(
            override_async_session, admin_user, None
        )
        r = await admin_client.get(
            f'/api/tasks/{task_id}/schedule-state/email_watermark'
        )
        assert r.status_code == 400

        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': 'x'},
        )
        assert r.status_code == 400

    async def test_missing_task_returns_404(self, admin_client):
        r = await admin_client.get('/api/tasks/does-not-exist/schedule-state/k')
        assert r.status_code == 404

    async def test_null_value_rejected(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """Weak models sometimes call set_schedule_state with
        value=null / "" / "   " which sends them into a circuit-
        breaker loop. The endpoint rejects it at the pydantic
        boundary so callers are forced to supply something
        concrete (non-empty, non-whitespace)."""
        task_id = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        # Use last_order_id (non-typed) so we're exercising the
        # boundary pydantic check, not the email_watermark regex.
        for bad in (None, '', '   ', '\t\n'):
            r = await admin_client.put(
                f'/api/tasks/{task_id}/schedule-state/last_order_id',
                json={'value': bad},
            )
            assert r.status_code == 422, f'{bad!r} → {r.status_code}'
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/last_order_id',
            json={},
        )
        assert r.status_code == 422

    async def test_value_whitespace_stripped(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """Surrounding whitespace is stripped before validation +
        storage, so a padded epoch persists as the trimmed integer
        and still satisfies typed-key regexes like
        `email_watermark`'s epoch-seconds pattern."""
        task_id = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        epoch = _recent_epoch()
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': f'  {epoch}  '},
        )
        assert r.status_code == 200, r.text
        assert r.json()['value'] == epoch
        r = await admin_client.get(
            f'/api/tasks/{task_id}/schedule-state/email_watermark'
        )
        assert r.json()['value'] == epoch

    async def test_invalid_key_rejected(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """Keys must match [A-Za-z0-9_.-]{1,64} — anything that
        could be misrouted by URL semantics is rejected."""
        task_id = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/has%20space',
            json={'value': 'x'},
        )
        assert r.status_code == 400
        r = await admin_client.get(
            f'/api/tasks/{task_id}/schedule-state/' + ('x' * 65)
        )
        assert r.status_code == 400

    async def test_keys_are_independent(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        task_id = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        epoch = _recent_epoch()
        await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': epoch},
        )
        await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/last_order_id',
            json={'value': 'ORD-42019'},
        )
        r1 = await admin_client.get(
            f'/api/tasks/{task_id}/schedule-state/email_watermark'
        )
        r2 = await admin_client.get(
            f'/api/tasks/{task_id}/schedule-state/last_order_id'
        )
        assert r1.json()['value'] == epoch
        assert r2.json()['value'] == 'ORD-42019'

    async def test_email_watermark_requires_epoch_int(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """The `email_watermark` key is a typed slot — the server
        rejects anything that is not a unix-epoch integer string.

        Guards against the "agent truncates ISO watermark" failure
        mode: `'2026-04-17T13:50:57.009101+00:00'` can be trimmed to
        `'2026-04-17T13:50:57'` when the agent writes SQL, letting
        the same email re-appear on the next run.
        """
        task_id = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        # ISO timestamps no longer accepted.
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': '2026-04-16T09:02:11+00:00'},
        )
        assert r.status_code == 400, r.text
        # Plain strings, mixed alphanumerics, negative numbers,
        # decimals, internal whitespace, etc. (Leading/trailing
        # whitespace is stripped by the pydantic layer before this
        # regex runs — see test_value_whitespace_stripped.)
        for bad in ('not-a-number', '-1', '1.5', '0x1f', '1 2 3'):
            r = await admin_client.put(
                f'/api/tasks/{task_id}/schedule-state/email_watermark',
                json={'value': bad},
            )
            assert r.status_code == 400, f'{bad!r} → {r.status_code}'
        # Well-formed, recent epoch is accepted.
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': _recent_epoch()},
        )
        assert r.status_code == 200
        # Non-typed keys keep accepting any non-empty string.
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/last_order_id',
            json={'value': 'not-a-number-but-fine-here'},
        )
        assert r.status_code == 200

    async def test_email_watermark_rejects_year_off_epoch(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """Regression for the e2e flake where MiniMax-M2.7 produced
        a 2025 epoch for a 2026-dated email by translating the ISO
        date in its head and getting the year off by one. The
        wire format check (``_EPOCH_SECONDS_RE``) accepts the 10
        digit string fine — the sanity-window guard is what catches
        it. See ``_EPOCH_TYPED_KEYS`` in
        ``app/routers/tasks_schedule_state.py``.
        """
        task_id = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        now_epoch = int(datetime.now(UTC).timestamp())

        # 1 year ago → outside the 90-day past window. This is the
        # exact failure shape from the e2e log: a real-looking
        # 10-digit epoch that is silently a year wrong.
        year_off = str(now_epoch - 365 * 86400)
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': year_off},
        )
        assert r.status_code == 400, r.text
        # The error tells the agent how to compute the value
        # correctly (SQL-side via strftime), not just "rejected".
        assert 'strftime' in r.text or 'epoch' in r.text.lower()

        # 1 year in the future → outside the 1-hour future window.
        future = str(now_epoch + 365 * 86400)
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': future},
        )
        assert r.status_code == 400, r.text

        # Boundary: 89 days ago is inside the window (window is 90d).
        edge_in = str(now_epoch - 89 * 86400)
        r = await admin_client.put(
            f'/api/tasks/{task_id}/schedule-state/email_watermark',
            json={'value': edge_in},
        )
        assert r.status_code == 200, r.text

    async def test_first_run_get_includes_now_epoch(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """The GET response carries a ``now_epoch`` reference so the
        agent can sanity-check candidate cursors before persisting —
        belt-and-suspenders alongside the server-side window guard.
        """
        task_id = await _make_running_task(
            override_async_session, admin_user, schedule.id
        )
        before = int(datetime.now(UTC).timestamp())
        r = await admin_client.get(
            f'/api/tasks/{task_id}/schedule-state/email_watermark'
        )
        after = int(datetime.now(UTC).timestamp())
        assert r.status_code == 200
        body = r.json()
        assert body['value'] is None
        assert before <= body['now_epoch'] <= after


class TestPerStoreScoping:
    """Fanout-schedule sibling tasks must NOT see each other's cursor.

    Real incident: schedule_state had PK
    (schedule_id, key) only. A fanout schedule's demo-northshore
    sub-task wrote ``last_report_date=2026-04-30`` and
    demo-meadowbrook's sibling task on the same schedule then read
    that value and skipped its own download with "已下载". The new
    PK is (schedule_id, store_id, key) — these tests pin that
    contract.
    """

    async def test_two_stores_keep_independent_cursors(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """Two fanout sibling tasks on the same schedule + same key
        must each persist and read back THEIR OWN value."""
        store_northshore = await _make_store(
            override_async_session, 'demo-northshore'
        )
        store_meadowbrook = await _make_store(
            override_async_session, 'demo-meadowbrook'
        )
        task_northshore = await _make_running_task(
            override_async_session,
            admin_user,
            schedule.id,
            store_id=store_northshore,
        )
        task_meadowbrook = await _make_running_task(
            override_async_session,
            admin_user,
            schedule.id,
            store_id=store_meadowbrook,
        )

        await admin_client.put(
            f'/api/tasks/{task_northshore}/schedule-state/last_report_date',
            json={'value': '2026-04-30'},
        )
        await admin_client.put(
            f'/api/tasks/{task_meadowbrook}/schedule-state/last_report_date',
            json={'value': '2026-03-15'},
        )

        r_northshore = await admin_client.get(
            f'/api/tasks/{task_northshore}/schedule-state/last_report_date'
        )
        r_meadowbrook = await admin_client.get(
            f'/api/tasks/{task_meadowbrook}/schedule-state/last_report_date'
        )
        assert r_northshore.json()['value'] == '2026-04-30'
        assert r_meadowbrook.json()['value'] == '2026-03-15'

    async def test_sibling_does_not_see_other_store_in_known_keys(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """`other_known_keys` must be scoped to the calling store.
        If it leaked across stores the agent would think a key it
        never touched was 'already populated' and treat its run as
        a resume rather than a first run."""
        store_a = await _make_store(override_async_session, 'store-a')
        store_b = await _make_store(override_async_session, 'store-b')
        task_a = await _make_running_task(
            override_async_session, admin_user, schedule.id, store_id=store_a
        )
        task_b = await _make_running_task(
            override_async_session, admin_user, schedule.id, store_id=store_b
        )

        # Store A seeds a cursor.
        await admin_client.put(
            f'/api/tasks/{task_a}/schedule-state/last_report_date',
            json={'value': '2026-04-30'},
        )

        # Store B asks for a key it has never written. The hint
        # list MUST be empty — store A's cursor is irrelevant to it.
        r = await admin_client.get(
            f'/api/tasks/{task_b}/schedule-state/something_else'
        )
        assert r.status_code == 200
        body = r.json()
        assert body['value'] is None
        assert body['other_known_keys'] == []

    async def test_no_store_task_uses_schedule_level_scope(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """Tasks without a store_id (e.g. email-sweep schedules) keep
        the legacy schedule-level cursor semantics by writing under
        the empty-string sentinel — independent from any store-bound
        sibling that happens to share the schedule."""
        task_no_store = await _make_running_task(
            override_async_session, admin_user, schedule.id, store_id=None
        )
        store_id = await _make_store(override_async_session, 'paired-store')
        task_with_store = await _make_running_task(
            override_async_session,
            admin_user,
            schedule.id,
            store_id=store_id,
        )

        await admin_client.put(
            f'/api/tasks/{task_no_store}/schedule-state/last_report_date',
            json={'value': '2026-01-01'},
        )
        await admin_client.put(
            f'/api/tasks/{task_with_store}/schedule-state/last_report_date',
            json={'value': '2026-12-31'},
        )

        r_ns = await admin_client.get(
            f'/api/tasks/{task_no_store}/schedule-state/last_report_date'
        )
        r_ws = await admin_client.get(
            f'/api/tasks/{task_with_store}/schedule-state/last_report_date'
        )
        assert r_ns.json()['value'] == '2026-01-01'
        assert r_ws.json()['value'] == '2026-12-31'

    async def test_db_row_carries_store_id(
        self,
        admin_client,
        override_async_session,
        admin_user,
        schedule,
    ):
        """Defensive check on the storage layer: the row a fanout
        sub-task writes must land with its own store_id, and a
        no-store task must land with NO_STORE_SCOPE — so the PK
        actually distinguishes the two writers."""
        store_id = await _make_store(override_async_session, 'fan-out-store')
        task_with_store = await _make_running_task(
            override_async_session,
            admin_user,
            schedule.id,
            store_id=store_id,
        )
        task_no_store = await _make_running_task(
            override_async_session, admin_user, schedule.id, store_id=None
        )
        await admin_client.put(
            f'/api/tasks/{task_with_store}/schedule-state/cursor_a',
            json={'value': 'A'},
        )
        await admin_client.put(
            f'/api/tasks/{task_no_store}/schedule-state/cursor_b',
            json={'value': 'B'},
        )

        async with override_async_session() as db:
            row_a = await db.get(
                ScheduleState, (schedule.id, store_id, 'cursor_a')
            )
            row_b = await db.get(
                ScheduleState,
                (schedule.id, NO_STORE_SCOPE, 'cursor_b'),
            )
            assert row_a is not None and row_a.value == 'A'
            assert row_b is not None and row_b.value == 'B'
