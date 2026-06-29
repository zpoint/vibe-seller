"""Workflow gate for the fanout → finalize (parent-reduce) capability.

The scenario the feature exists for: a fanout schedule with a
``finalize_description`` fans out to N stores; some succeed, some
fail; once ALL children are terminal, ONE parent "finalize" task runs,
is handed every child's result + workspace, and produces a combined
output. The framework guarantees only fire-once-after-all-terminal +
the batch_results.json hand-off; the parent's behaviour (gather +
summarize here) is driven by the prompt — stubbed deterministically
via ``FakeAgentScenario.result_fn`` so this gate has no LLM flakiness.

The load-bearing assertion: the summary the parent writes says
"2 succeeded, 2 failed" — NOT 4/0. That can only be right if the
framework reported each child's REAL terminal status in
batch_results.json. A bug that assumed all-success, or dropped the
failed children, fails here.
"""

import asyncio
import json
from pathlib import Path
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.browser.manager import store_slug
from app.models.schedule import Schedule
from app.models.store import Store
from app.models.task import Task
from app.scheduler.finalize_reaper import reap_finalized_batches
from app.task_runner_auto import auto_run_task
from app.task_states import TaskStatus
from tests.workflow.conftest import real_sleep, wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


# ── Fixtures ────────────────────────────────────────────


@pytest_asyncio.fixture
async def finalize_schedule(override_async_session, admin_user):
    """A fanout schedule that registers a parent finalize step."""
    async with override_async_session() as db:
        sched = Schedule(
            id=str(uuid.uuid4()),
            title='Collect everything',
            schedule_type='days',
            schedule_time='03:00',
            interval_value=3,
            is_active=True,
            plan_mode=False,
            finalize_description=(
                'Read the batch results and write one combined summary.'
            ),
            created_by=admin_user.id,
        )
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
        return sched


@pytest_asyncio.fixture
async def four_stores(admin_client):
    stores = []
    for name in ('FinStoreA', 'FinStoreB', 'FinStoreC', 'FinStoreD'):
        r = await admin_client.post('/api/stores', json={'name': name})
        assert r.status_code == 200
        stores.append(r.json())
    return stores


# ── Helpers ─────────────────────────────────────────────


def _child_out_path(root: Path, task_id: str) -> Path:
    return root / 'tasks' / task_id / 'out.txt'


def _make_success_fn(root: Path):
    """result_fn for a succeeding child: write its own output file."""

    def _fn(task_id: str) -> str:
        out = _child_out_path(root, task_id)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(f'OUTPUT::{task_id}', encoding='utf-8')
        return f'collected for {task_id}'

    return _fn


def _make_finalize_fn(root: Path):
    """result_fn for the parent: do the real reduce from batch_results.

    Reads ./batch_results.json (written by the reaper into the
    finalize task dir), gathers every COMPLETED child's out.txt into a
    single destination file, and returns a summary string with the
    true success/fail counts. This is the deterministic stand-in for
    the AI prompt — the framework contract (accurate batch_results) is
    what's actually under test.
    """

    def _fn(task_id: str) -> str:
        results_path = root / 'tasks' / task_id / 'batch_results.json'
        data = json.loads(results_path.read_text(encoding='utf-8'))
        children = data['children']
        ok = [c for c in children if c['status'] == TaskStatus.COMPLETED]
        bad = [c for c in children if c['status'] == TaskStatus.FAILED]

        # Gather each successful child's own output file (proves the
        # parent can reach into each child's workspace via task_dir).
        gathered = []
        for c in ok:
            out = Path(c['task_dir']) / 'out.txt'
            gathered.append(out.read_text(encoding='utf-8'))
        dest = root / 'tasks' / task_id / 'gathered.txt'
        dest.write_text('\n'.join(gathered), encoding='utf-8')

        failed_slugs = ', '.join(sorted(c['store_slug'] for c in bad))
        return (
            f'{len(ok)} succeeded, {len(bad)} failed. '
            f'Failed stores: {failed_slugs}.'
        )

    return _fn


async def _dispatch_children(
    session_maker,
    schedule_id: str,
    admin_user_id: str,
    stores: list[dict],
    fake_agent,
    workspace_root: Path,
    *,
    succeed: set[str],
    gate: asyncio.Event | None = None,
    gate_store_id: str | None = None,
) -> tuple[str, dict[str, str]]:
    """Create + dispatch one child task per store, sharing a batch_id.

    Stores in *succeed* run a success scenario (write out.txt); the
    rest fail. If *gate* is given, the *gate_store_id* child holds
    until the gate is set (lets the test probe the reaper mid-flight).
    Returns ``(batch_id, {store_id: task_id})``.
    """
    batch_id = str(uuid.uuid4())
    task_map: dict[str, str] = {}
    for s in stores:
        task_id = str(uuid.uuid4())
        task_map[s['id']] = task_id
        if s['id'] in succeed:
            fake_agent.scenarios[task_id] = FakeAgentScenario(
                result_fn=_make_success_fn(workspace_root),
                complete_delay=0.02,
                gate=gate if s['id'] == gate_store_id else None,
            )
        else:
            fake_agent.scenarios[task_id] = FakeAgentScenario(
                should_fail=True,
                complete_delay=0.02,
            )
        async with session_maker() as db:
            db.add(
                Task(
                    id=task_id,
                    store_id=s['id'],
                    schedule_id=schedule_id,
                    created_by=admin_user_id,
                    title='Collect everything',
                    description='collect',
                    status=TaskStatus.PENDING,
                    batch_id=batch_id,
                )
            )
            await db.commit()

    for s in stores:
        async with session_maker() as db:
            store = await db.get(Store, s['id'])
        asyncio.create_task(auto_run_task(task_map[s['id']], store))
        await real_sleep(0.15)
    return batch_id, task_map


async def _find_finalize(session_maker, batch_id: str) -> list[Task]:
    async with session_maker() as db:
        return list(
            (
                await db.execute(
                    select(Task).where(
                        Task.batch_id == batch_id,
                        Task.is_finalize.is_(True),
                    )
                )
            )
            .scalars()
            .all()
        )


# ── The gate ────────────────────────────────────────────


class TestFanoutFinalize:
    async def test_two_fail_two_succeed_parent_reduces(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        mock_workspace,
        finalize_schedule,
        four_stores,
        admin_user,
    ):
        root = mock_workspace.root
        succeed = {four_stores[0]['id'], four_stores[1]['id']}  # A, B ok
        # C, D fail.

        batch_id, task_map = await _dispatch_children(
            override_async_session,
            finalize_schedule.id,
            admin_user.id,
            four_stores,
            install_fake_agent,
            root,
            succeed=succeed,
        )

        # Wait for all four children terminal (2 completed, 2 failed).
        for s in four_stores:
            await wait_for_task(
                admin_client,
                task_map[s['id']],
                target='completed',
                timeout=10,
            )
        async with override_async_session() as db:
            statuses = {
                s['id']: (await db.get(Task, task_map[s['id']])).status
                for s in four_stores
            }
        assert statuses[four_stores[0]['id']] == TaskStatus.COMPLETED
        assert statuses[four_stores[1]['id']] == TaskStatus.COMPLETED
        assert statuses[four_stores[2]['id']] == TaskStatus.FAILED
        assert statuses[four_stores[3]['id']] == TaskStatus.FAILED

        # ── Fire the reaper: exactly one finalize task ──
        await reap_finalized_batches()
        finals = await _find_finalize(override_async_session, batch_id)
        assert len(finals) == 1, f'expected 1 finalize task, got {len(finals)}'
        finalize_id = finals[0].id

        # ── Idempotent: a second tick must NOT create a second ──
        await reap_finalized_batches()
        finals2 = await _find_finalize(override_async_session, batch_id)
        assert len(finals2) == 1, 'finalize reaper double-fired'

        # ── batch_results.json gate: real per-child status (2/2) ──
        results_path = root / 'tasks' / finalize_id / 'batch_results.json'
        payload = json.loads(results_path.read_text(encoding='utf-8'))
        assert payload['completed'] == 2 and payload['failed'] == 2
        by_slug = {c['store_slug']: c for c in payload['children']}
        assert (
            by_slug[store_slug(four_stores[0]['name'], four_stores[0]['id'])][
                'status'
            ]
            == TaskStatus.COMPLETED
        )
        assert (
            by_slug[store_slug(four_stores[2]['name'], four_stores[2]['id'])][
                'status'
            ]
            == TaskStatus.FAILED
        )

        # ── Run the parent reduce, then assert its output ──
        install_fake_agent.scenarios[finalize_id] = FakeAgentScenario(
            result_fn=_make_finalize_fn(root),
            complete_delay=0.02,
        )
        await auto_run_task(finalize_id, None)
        data = await wait_for_task(
            admin_client, finalize_id, target='completed', timeout=10
        )
        assert data['status'] == 'completed'

        summary = data['result']
        # THE gate: real counts, not 4 succeeded / 0 failed.
        assert '2 succeeded, 2 failed' in summary
        assert '4 succeeded' not in summary
        # Failed stores named.
        assert (
            store_slug(four_stores[2]['name'], four_stores[2]['id']) in summary
        )
        assert (
            store_slug(four_stores[3]['name'], four_stores[3]['id']) in summary
        )

        # Gathered destination holds BOTH successes' output, neither failure.
        gathered = (root / 'tasks' / finalize_id / 'gathered.txt').read_text()
        assert f'OUTPUT::{task_map[four_stores[0]["id"]]}' in gathered
        assert f'OUTPUT::{task_map[four_stores[1]["id"]]}' in gathered
        assert task_map[four_stores[2]['id']] not in gathered

    async def test_no_fire_while_a_child_still_running(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        mock_workspace,
        finalize_schedule,
        four_stores,
        admin_user,
    ):
        """Negative gate: finalize must NOT fire before all terminal."""
        root = mock_workspace.root
        succeed = {s['id'] for s in four_stores}
        gate = asyncio.Event()  # hold store A until we open it
        batch_id, task_map = await _dispatch_children(
            override_async_session,
            finalize_schedule.id,
            admin_user.id,
            four_stores,
            install_fake_agent,
            root,
            succeed=succeed,
            gate=gate,
            gate_store_id=four_stores[0]['id'],
        )

        # Let the other three finish; A is parked on the gate.
        for s in four_stores[1:]:
            await wait_for_task(
                admin_client, task_map[s['id']], target='completed', timeout=10
            )
        await reap_finalized_batches()
        assert await _find_finalize(override_async_session, batch_id) == []

        # Release A → all terminal → now it fires.
        gate.set()
        await wait_for_task(
            admin_client, task_map[four_stores[0]['id']], target='completed'
        )
        await reap_finalized_batches()
        assert len(await _find_finalize(override_async_session, batch_id)) == 1

    async def test_no_finalize_description_is_noop(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        mock_workspace,
        four_stores,
        admin_user,
    ):
        """A plain fanout schedule (no finalize_description) never fires."""
        async with override_async_session() as db:
            sched = Schedule(
                id=str(uuid.uuid4()),
                title='Plain fanout',
                schedule_type='days',
                schedule_time='03:00',
                interval_value=1,
                is_active=True,
                plan_mode=False,
                created_by=admin_user.id,
            )
            db.add(sched)
            await db.commit()
            await db.refresh(sched)

        batch_id, task_map = await _dispatch_children(
            override_async_session,
            sched.id,
            admin_user.id,
            four_stores,
            install_fake_agent,
            mock_workspace.root,
            succeed={s['id'] for s in four_stores},
        )
        for s in four_stores:
            await wait_for_task(
                admin_client, task_map[s['id']], target='completed', timeout=10
            )
        await reap_finalized_batches()
        assert await _find_finalize(override_async_session, batch_id) == []


class TestRegisterFinalizeEndpoint:
    """The agent-facing register-finalize surface (vibe_seller_register_finalize)."""

    async def _task_for(self, session_maker, schedule_id, user_id):
        tid = str(uuid.uuid4())
        async with session_maker() as db:
            db.add(
                Task(
                    id=tid,
                    schedule_id=schedule_id,
                    created_by=user_id,
                    title='plan',
                    description='plan',
                    status=TaskStatus.RUNNING,
                    is_plan_only=True,
                )
            )
            await db.commit()
        return tid

    async def test_register_sets_schedule_finalize_description(
        self,
        admin_client,
        override_async_session,
        finalize_schedule,
        admin_user,
    ):
        # finalize_schedule is an all-stores (store_id=None) fanout sched.
        # Clear it first so we prove the endpoint sets it.
        async with override_async_session() as db:
            s = await db.get(Schedule, finalize_schedule.id)
            s.finalize_description = None
            await db.commit()
        tid = await self._task_for(
            override_async_session, finalize_schedule.id, admin_user.id
        )
        r = await admin_client.post(
            f'/api/tasks/{tid}/register-finalize',
            json={'description': 'Combine all stores into ONE PR + WeCom.'},
        )
        assert r.status_code == 200, r.text
        async with override_async_session() as db:
            s = await db.get(Schedule, finalize_schedule.id)
            assert s.finalize_description == (
                'Combine all stores into ONE PR + WeCom.'
            )

    async def test_register_rejected_for_single_store_schedule(
        self, admin_client, override_async_session, four_stores, admin_user
    ):
        async with override_async_session() as db:
            sched = Schedule(
                id=str(uuid.uuid4()),
                store_id=four_stores[0]['id'],  # store-bound → not a batch
                title='Single store',
                schedule_type='days',
                schedule_time='03:00',
                interval_value=1,
                is_active=True,
                plan_mode=False,
                created_by=admin_user.id,
            )
            db.add(sched)
            await db.commit()
        tid = await self._task_for(
            override_async_session, sched.id, admin_user.id
        )
        r = await admin_client.post(
            f'/api/tasks/{tid}/register-finalize',
            json={'description': 'x'},
        )
        assert r.status_code == 400

    async def test_register_rejected_empty_description(
        self,
        admin_client,
        override_async_session,
        finalize_schedule,
        admin_user,
    ):
        tid = await self._task_for(
            override_async_session, finalize_schedule.id, admin_user.id
        )
        r = await admin_client.post(
            f'/api/tasks/{tid}/register-finalize',
            json={'description': '   '},
        )
        assert r.status_code == 422


class TestFinalizeDescriptionScheduleApi:
    """Create/update reject finalize_description on non-fanout schedules."""

    async def test_create_allstores_fanout_accepts(self, admin_client):
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'Fanout w/ finalize',
                'schedule_type': 'days',
                'store_id': None,
                'phase_mode': 'fanout',
                'finalize_description': 'combine all stores into one PR',
            },
        )
        assert r.status_code in (200, 201), r.text
        assert r.json()['finalize_description'] == (
            'combine all stores into one PR'
        )

    async def test_create_store_bound_rejects(self, admin_client, four_stores):
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'Store sched w/ finalize',
                'schedule_type': 'days',
                'store_id': four_stores[0]['id'],
                'finalize_description': 'combine',
            },
        )
        assert r.status_code == 400
