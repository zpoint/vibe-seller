"""Terminal telemetry is structural: every committed transition of a
Task into COMPLETED or FAILED emits exactly one terminal event (or
task_cancelled for user stops); a rolled-back transition emits none.

The emitter lives in the Task.status set-listener (app/models/task.py)
+ the session after_commit hook (app/telemetry_tasks.py) — the same
chokepoint as the parked-time accounting — so new terminal writers
cannot miss it.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import telemetry
from app.models.base import Base
from app.models.schedule import Schedule
from app.models.task import Task
from app.scheduler.plan_reaper import reap_stuck_planning_tasks
from app.scheduler.stall_reaper import reap_stalled_running_tasks
from app.task_states import TaskStatus
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


class TestTerminalEvents:
    async def test_auto_completion_emits_exactly_once(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            'app.telemetry.send',
            lambda event, props=None: calls.append((event, props)),
        )
        r = await admin_client.post(
            '/api/stores', json={'name': 'Terminal Store'}
        )
        store_id = r.json()['id']
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'auto terminal ok',
                'store_id': store_id,
                'plan_mode': False,
            },
        )
        task_id = r.json()['id']
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        done = [p for e, p in calls if e == 'task_completed']
        assert len(done) == 1, f'expected one task_completed, got {calls}'
        assert done[0]['task_id'] == task_id

    async def test_auto_failure_emits_exactly_once(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            'app.telemetry.send',
            lambda event, props=None: calls.append((event, props)),
        )
        install_fake_agent.default_scenario = FakeAgentScenario(
            error_result='API Error: 500',
        )
        r = await admin_client.post(
            '/api/stores', json={'name': 'Terminal Fail Store'}
        )
        store_id = r.json()['id']
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'auto terminal fail',
                'store_id': store_id,
                'plan_mode': False,
            },
        )
        task_id = r.json()['id']
        data = await wait_for_task(admin_client, task_id, target='failed')
        assert data['status'] == 'failed'
        failed = [p for e, p in calls if e == 'task_failed']
        assert len(failed) == 1, f'expected one task_failed, got {calls}'
        assert failed[0]['task_id'] == task_id

    async def test_planned_executor_completion_emits_once(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            'app.telemetry.send',
            lambda event, props=None: calls.append((event, props)),
        )
        r = await admin_client.post(
            '/api/stores', json={'name': 'Terminal Planned Store'}
        )
        store_id = r.json()['id']
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'planned terminal ok',
                'store_id': store_id,
                'plan_mode': True,
                'description': 'planned run',
            },
        )
        task_id = r.json()['id']
        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['status'] == 'planned'
        await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        done = [p for e, p in calls if e == 'task_completed']
        assert len(done) == 1, f'expected one task_completed, got {calls}'
        assert done[0]['task_id'] == task_id

    async def test_followup_emits_once_per_run(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            'app.telemetry.send',
            lambda event, props=None: calls.append((event, props)),
        )
        r = await admin_client.post(
            '/api/stores', json={'name': 'Terminal Followup Store'}
        )
        store_id = r.json()['id']
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'followup terminal',
                'store_id': store_id,
                'plan_mode': False,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)
        before = len([1 for e, _ in calls if e == 'task_completed'])
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'and one more thing'},
        )
        assert r.status_code in (200, 201), r.text
        await wait_for_task(admin_client, task_id)
        after = len([1 for e, _ in calls if e == 'task_completed'])
        assert after - before == 1, (
            f'follow-up run must emit exactly once, delta={after - before}'
        )

    async def test_agent_stop_emits_one_cancelled_no_failed(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            'app.telemetry.send',
            lambda event, props=None: calls.append((event, props)),
        )
        install_fake_agent.default_scenario = FakeAgentScenario(
            error_result='Stopped by user',
        )
        r = await admin_client.post(
            '/api/stores', json={'name': 'Terminal Stop Store'}
        )
        store_id = r.json()['id']
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'stop terminal',
                'store_id': store_id,
                'plan_mode': False,
            },
        )
        task_id = r.json()['id']
        # stop while it is still in flight; fall back to stopping
        # whatever state it reaches first
        for _ in range(30):
            st = (await admin_client.get(f'/api/tasks/{task_id}')).json()[
                'status'
            ]
            if st in ('running', 'designing', 'waiting', 'planned'):
                break
        await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        await wait_for_task(admin_client, task_id, target='failed')
        cancelled = [p for e, p in calls if e == 'task_cancelled']
        failed = [p for e, p in calls if e == 'task_failed']
        assert len(cancelled) == 1, f'want one task_cancelled: {calls}'
        assert cancelled[0]['task_id'] == task_id
        assert failed == [], f'stops are cancellations, not failures: {calls}'


    async def test_stall_reaper_emits_once(
        self, admin_client, override_async_session, monkeypatch
    ):
        """finalize_terminal_state's sink is covered by the auto and
        follow-up tests; the reapers write terminal states directly."""
        calls = []
        monkeypatch.setattr(
            'app.telemetry.send',
            lambda event, props=None: calls.append((event, props)),
        )
        stale = (
            datetime.now(UTC) - timedelta(minutes=30)
        ).isoformat()
        async with override_async_session() as db:
            t = Task(
                id='t-stall-1',
                title='stalled',
                created_by='u-1',
                status=TaskStatus.RUNNING,
                source='ui',
                started_at=stale,
                updated_at=stale,
            )
            db.add(t)
            await db.commit()
        await reap_stalled_running_tasks()
        await asyncio.sleep(0.2)
        failed = [p for e, p in calls if e == 'task_failed']
        assert len(failed) == 1, f'want one task_failed, got {calls}'
        assert failed[0]['task_id'] == 't-stall-1'

    async def test_plan_reaper_emits_once(
        self, admin_client, override_async_session, monkeypatch
    ):
        calls = []
        monkeypatch.setattr(
            'app.telemetry.send',
            lambda event, props=None: calls.append((event, props)),
        )
        stale = (
            datetime.now(UTC) - timedelta(hours=2)
        ).isoformat()
        async with override_async_session() as db:
            db.add(
                Schedule(
                    id='sched-plan-1',
                    title='reaped plan',
                    schedule_type='days',
                    schedule_time='09:05',
                    plan_mode=True,
                    plan_status='planning',
                    current_planning_task_id='t-planreap-1',
                    created_by='u-1',
                    phase_mode='fanout',
                    timezone='UTC',
                )
            )
            db.add(
                Task(
                    id='t-planreap-1',
                    title='Plan: reaped plan',
                    created_by='u-1',
                    status=TaskStatus.DESIGNING,
                    source='schedule',
                    is_plan_only=True,
                    schedule_id='sched-plan-1',
                    started_at=stale,
                    updated_at=stale,
                )
            )
            await db.commit()
        await reap_stuck_planning_tasks()
        await asyncio.sleep(0.2)
        failed = [p for e, p in calls if e == 'task_failed']
        assert len(failed) == 1, f'want one task_failed, got {calls}'
        assert failed[0]['task_id'] == 't-planreap-1'


# ── Structural unit tests (listener + commit hooks) ───────────


@pytest_asyncio.fixture
async def term_env(async_engine, monkeypatch):
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr('app.telemetry_tasks.async_session', maker)
    calls = []
    monkeypatch.setattr(
        telemetry,
        'send',
        lambda event, props=None: calls.append((event, props)),
    )
    async with maker() as db:
        task = Task(
            id='t-term-1',
            title='terminal env',
            created_by='u-1',
            status='running',
            source='ui',
        )
        db.add(task)
        await db.commit()
    yield maker, calls


class TestTerminalStructural:
    async def test_rollback_emits_nothing(self, term_env):
        maker, calls = term_env
        async with maker() as db:
            t = await db.get(Task, 't-term-1')
            t.status = TaskStatus.COMPLETED
            await db.rollback()
        await asyncio.sleep(0.05)
        assert [e for e, _ in calls if e.startswith('task_')] == [], calls

    async def test_commit_emits_once(self, term_env):
        maker, calls = term_env
        async with maker() as db:
            t = await db.get(Task, 't-term-1')
            t.status = TaskStatus.COMPLETED
            t.completed_at = datetime.now(UTC).isoformat()
            await db.commit()
        await asyncio.sleep(0.1)
        done = [p for e, p in calls if e == 'task_completed']
        assert len(done) == 1, calls
        assert done[0]['task_id'] == 't-term-1'

    async def test_same_value_reassign_does_not_double(self, term_env):
        maker, calls = term_env
        async with maker() as db:
            t = await db.get(Task, 't-term-1')
            t.status = TaskStatus.COMPLETED
            t.completed_at = datetime.now(UTC).isoformat()
            await db.commit()
        await asyncio.sleep(0.1)
        async with maker() as db:
            t = await db.get(Task, 't-term-1')
            t.status = TaskStatus.COMPLETED
            await db.commit()
        await asyncio.sleep(0.1)
        done = [p for e, p in calls if e == 'task_completed']
        assert len(done) == 1, f're-assignment must not re-emit: {calls}'

    async def test_stopped_by_user_is_a_cancellation(self, term_env):
        maker, calls = term_env
        async with maker() as db:
            t = await db.get(Task, 't-term-1')
            t.status = TaskStatus.FAILED
            t.error_category = 'stopped_by_user'
            await db.commit()
        await asyncio.sleep(0.1)
        assert [e for e, _ in calls] == ['task_cancelled'], calls

    def test_hooks_registered(self):
        """Negative control: disabling the listeners must fail here."""
        from app import telemetry_tasks
        from app.models import task as task_model

        assert event.contains(
            Task.status, 'set', task_model._park_clock
        ), 'Task.status set listener missing'
        assert event.contains(
            telemetry_tasks._SyncSession,
            'after_commit',
            telemetry_tasks._emit_terminal_on_commit,
        ), 'after_commit terminal hook missing'
        assert event.contains(
            telemetry_tasks._SyncSession,
            'after_rollback',
            telemetry_tasks._drop_terminal_on_rollback,
        ), 'after_rollback hook missing'
