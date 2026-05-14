"""Workflow tests: HTTP endpoints respect the status gates.

For each gated endpoint, parametrize over every TaskStatus value
and confirm only the declared gate set is accepted:

- `/retry`           → `RETRIABLE`, others 400
- `/start`           → `STARTABLE`, others 400
- `/execute-plan`    → `{PLANNED}`, others 400
- `/messages` (wake) → `WAKEABLE`, others fall through to the
  normal chat path; only WAITING woke-handling behaviour is
  exercised here
- `/agent/stop`      → returns 200 for every status. For
  `STOPPABLE` ones it transitions the task to FAILED; for the
  rest it's a no-op. The gate is enforced by the side-effect,
  not the status code.

Also: `GET /api/tasks/{id}` must round-trip every TaskStatus
value unchanged — the UI reads `status` directly and any
server-side transformation would silently break badge rendering
/ button gates.
"""

from datetime import UTC, datetime
import itertools

import pytest
from sqlalchemy import select

from app.models.task import Task
from app.task_states import (
    RETRIABLE,
    STARTABLE,
    STOPPABLE,
    WAKEABLE,
    TaskStatus,
)

pytestmark = pytest.mark.workflow


async def _seed_task(
    db_maker,
    *,
    status: TaskStatus,
    store_id: str | None = None,
    plan_mode: bool = False,
    plan: str | None = None,
) -> str:
    """Insert a task directly into the DB with the target status.

    Bypasses the normal create → auto-run pipeline so we can
    exercise every status in isolation. Returns the task_id.
    """
    async with db_maker() as db:
        # Every workflow test seeds a default admin via the
        # admin_client fixture; reuse its id.
        result = await db.execute(select(Task).limit(1))
        _ = result.scalars().first()  # ensure table exists
        task = Task(
            title=f'gating-{status}',
            created_by='00000000-0000-0000-0000-000000000001',
            status=status,
            priority=1,
            plan_mode=plan_mode,
            plan=plan,
            store_id=store_id,
            created_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(),
        )
        db.add(task)
        await db.commit()
        return task.id


class TestTaskResponseRoundTrip:
    """Every TaskStatus must round-trip through GET /api/tasks/{id}
    unchanged. A regression here would break the frontend badge and
    every button gate that reads `task.status`.
    """

    @pytest.mark.parametrize('status', list(TaskStatus))
    async def test_get_returns_status_verbatim(
        self, admin_client, override_async_session, status
    ):
        task_id = await _seed_task(override_async_session, status=status)
        r = await admin_client.get(f'/api/tasks/{task_id}')
        assert r.status_code == 200
        assert r.json()['status'] == str(status)


class TestRetryGate:
    """POST /retry accepts RETRIABLE states, rejects the rest."""

    @pytest.mark.parametrize('status', list(TaskStatus))
    async def test_retry_gate(
        self, admin_client, override_async_session, status
    ):
        task_id = await _seed_task(override_async_session, status=status)
        r = await admin_client.post(f'/api/tasks/{task_id}/retry')
        if status in RETRIABLE:
            # Accepted → returns PENDING (may schedule background run,
            # but in test env without browser/agent just transitions).
            assert r.status_code == 200, (
                f'/retry must accept {status}; got {r.status_code}'
            )
            assert r.json()['status'] == 'pending'
        else:
            assert r.status_code == 400, (
                f'/retry must reject {status}; got {r.status_code}'
            )


class TestStopGate:
    """POST /agent/stop accepts STOPPABLE; otherwise best-effort 200."""

    @pytest.mark.parametrize('status', list(TaskStatus))
    async def test_stop_gate(
        self, admin_client, override_async_session, status
    ):
        task_id = await _seed_task(override_async_session, status=status)
        r = await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        # The stop handler returns 200 even for non-stoppable —
        # it's a no-op fast-path. What matters is that STOPPABLE
        # tasks transition to FAILED, and non-STOPPABLE tasks
        # preserve their status.
        assert r.status_code == 200
        # Refetch to confirm status changed only for STOPPABLE.
        got = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        if status in STOPPABLE:
            assert got['status'] == 'failed', (
                f'STOPPABLE status {status} must transition to failed; '
                f'got {got["status"]}'
            )
            assert got['error'], 'stop should record an error message'
        else:
            assert got['status'] == str(status), (
                f'Non-STOPPABLE status {status} must be preserved; '
                f'got {got["status"]}'
            )


class TestStartGate:
    """POST /start accepts STARTABLE (store required), rejects rest."""

    @pytest.mark.parametrize('status', list(TaskStatus))
    async def test_start_gate_without_store(
        self, admin_client, override_async_session, status
    ):
        """No store_id — /start always rejects with 400 (require store)."""
        task_id = await _seed_task(override_async_session, status=status)
        r = await admin_client.post(f'/api/tasks/{task_id}/start')
        if status in STARTABLE:
            # Gate passes but then rejects on missing store.
            assert r.status_code == 400
            assert 'store' in r.json().get('detail', '').lower()
        else:
            assert r.status_code == 400


class TestExecutePlanGate:
    """POST /execute-plan requires status == PLANNED."""

    @pytest.mark.parametrize('status', list(TaskStatus))
    async def test_execute_plan_gate(
        self, admin_client, override_async_session, status
    ):
        # Only include plan text for PLANNED status (others are
        # invalid for this endpoint anyway).
        task_id = await _seed_task(
            override_async_session,
            status=status,
            plan_mode=True,
            plan='Test plan' if status == TaskStatus.PLANNED else None,
        )
        r = await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        if status == TaskStatus.PLANNED:
            # Must pass the gate and return a valid response shape.
            # 500 is NOT acceptable — it would mean a handler crash,
            # which the gate test shouldn't silently absorb.
            assert r.status_code in (200, 400), (
                f'PLANNED must pass the gate; got {r.status_code}: '
                f'{r.text[:200]}'
            )
            # If 400 it should be from a downstream missing-store
            # check, not from the gate itself.
            if r.status_code == 400:
                detail = (r.json().get('detail') or '').lower()
                assert 'cannot' not in detail or 'store' in detail, (
                    f'Unexpected 400 from PLANNED: {detail}'
                )
        else:
            assert r.status_code == 400, (
                f'/execute-plan must reject {status}; got {r.status_code}'
            )


class TestWakeGate:
    """POST /messages on a task in WAKEABLE wakes it by transitioning
    to QUEUED (with the message becoming the wake trigger).  Non-
    WAKEABLE statuses fall through to the normal chat path and never
    transition the task.
    """

    @pytest.mark.parametrize('status', list(TaskStatus))
    async def test_wake_gate(
        self, admin_client, override_async_session, status
    ):
        task_id = await _seed_task(override_async_session, status=status)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'wake up'},
        )
        assert r.status_code == 200, (
            f'/messages must always accept valid input (got '
            f'{r.status_code} for status={status})'
        )
        got = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        if status in WAKEABLE:
            assert r.json().get('woken') is True, (
                f'WAKEABLE status {status} must be flagged as woken; '
                f'response: {r.json()}'
            )
            # Wake transitions to QUEUED (or RUNNING if dispatched
            # synchronously under test infra). Either way, not the
            # original WAITING.
            assert got['status'] != str(status), (
                f'WAKEABLE status {status} should transition on wake; '
                f'stayed at {got["status"]}'
            )
        else:
            assert r.json().get('woken') is not True, (
                f'Non-WAKEABLE status {status} must not produce a '
                f'wake; response: {r.json()}'
            )


class TestGateInvariantConsistency:
    """Each set is non-empty and disjoint in the expected way."""

    def test_retriable_is_terminal_subset(self):
        assert RETRIABLE <= {
            TaskStatus.PENDING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        }

    def test_stoppable_is_active_or_paused(self):
        assert STOPPABLE == {
            TaskStatus.DESIGNING,
            TaskStatus.PLANNED,
            TaskStatus.RUNNING,
            TaskStatus.WAITING,
        }

    def test_retriable_and_stoppable_disjoint(self):
        # A task cannot be both retryable (terminal-ish) AND
        # stoppable (active) at the same time. PENDING is the
        # one overlap candidate — excluded from STOPPABLE.
        overlap = RETRIABLE & STOPPABLE
        assert overlap == set(), f'Unexpected overlap: {overlap}'

    def test_all_statuses_reachable_by_at_least_one_gate(self):
        """Sanity: every TaskStatus is either retriable, stoppable,
        wakeable, startable, or an intermediate/terminal state we
        don't directly expose. Left out would mean a dead state
        with no way to progress.
        """
        covered = RETRIABLE | STOPPABLE | WAKEABLE | STARTABLE
        # Special cases:
        # - PLANNED → `/execute-plan`
        # - COMPLETED → terminal happy path (retriable via RETRIABLE)
        # - QUEUED → transient dispatch state; not user-actionable
        #   but reached from schedule_or_run / task_queue_scheduler.
        covered |= {
            TaskStatus.PLANNED,
            TaskStatus.COMPLETED,
            TaskStatus.QUEUED,
        }
        uncovered = set(TaskStatus) - covered
        assert uncovered == set(), (
            f'Statuses with no gate: {uncovered}. Every status '
            f'must be actionable by at least one endpoint.'
        )

    def test_product_of_statuses_is_full_enum(self):
        """Documentation: enumerate all possible status ordered
        pairs for completeness — same count as the backend's
        exhaustive transition matrix. Pure sanity check.
        """
        pairs = list(itertools.product(TaskStatus, TaskStatus))
        assert len(pairs) == 8 * 8
