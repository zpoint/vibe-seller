"""Unit tests for the plan reaper (review-schedule planners).

The reaper decides by whether anything can still advance a planner,
never by how long it has waited: a planner waiting for a slot, for an
answer or for its launcher is left alone; one whose agent is gone is
failed; a plan that was written is never lost.
"""

from datetime import UTC, datetime, timedelta
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

import app.ai.claude_backend_manager as _cb
import app.database as _db
from app.models.base import Base
from app.models.schedule import Schedule
from app.models.task import Task
import app.scheduler.plan_reaper as _reaper
import app.scheduler.stall_reaper as _stall

pytestmark = pytest.mark.unit


class _Session:
    running = True


class _FakeManager:
    """Substitute for agent_manager — liveness knobs + stop() record."""

    def __init__(self):
        self.stopped: list[str] = []
        self.alive: set[str] = set()
        self.launching: set[str] = set()

    async def stop(self, task_id: str) -> bool:
        self.stopped.append(task_id)
        return True

    def is_launching(self, task_id: str) -> bool:
        return task_id in self.launching

    def is_process_alive(self, task_id: str) -> bool:
        return task_id in self.alive

    def get_session(self, task_id: str):
        return _Session() if task_id in self.alive else None

    def get_pending_questions(self, task_id: str):
        return None


@pytest_asyncio.fixture
async def reaper_env(monkeypatch):
    """In-memory DB + fake agent_manager for reaper tests."""
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(_db, 'async_session', maker)
    monkeypatch.setattr(_reaper, 'async_session', maker)
    fake = _FakeManager()
    monkeypatch.setattr(_reaper, 'agent_manager', fake)
    monkeypatch.setattr(_stall, 'agent_manager', fake)
    monkeypatch.setattr(_cb, 'agent_manager', fake)
    # A live agent is judged by the CLI's stream-silence bound; pin it
    # so the test does not depend on the profile env.
    monkeypatch.setattr(
        _stall, '_stream_silence_bound', lambda _t: timedelta(minutes=13)
    )
    yield maker, fake


async def _mk(maker, *, plan_status, task_status, minutes_ago, plan=None):
    """Seed one Schedule + pointed-to Task."""
    sched_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    stale_ts = (datetime.now(UTC) - timedelta(minutes=minutes_ago)).isoformat()
    async with maker() as db:
        t = Task(
            id=task_id,
            created_by='u',
            title='plan',
            status=task_status,
            plan_mode=True,
            is_plan_only=True,
            updated_at=stale_ts,
            schedule_id=sched_id,
            plan=plan,
        )
        s = Schedule(
            id=sched_id,
            title='t',
            schedule_type='days',
            schedule_time='09:05',
            plan_mode=True,
            plan_status=plan_status,
            current_planning_task_id=task_id,
            created_by='u',
            phase_mode='fanout',
            timezone='UTC',
        )
        db.add(s)
        db.add(t)
        await db.commit()
    return sched_id, task_id


async def _state(maker, sched_id, task_id):
    async with maker() as db:
        return await db.get(Schedule, sched_id), await db.get(Task, task_id)


class TestWaitingIsNotStuck:
    """Each of these was failed by the old 30-minute rule."""

    @pytest.mark.parametrize('status', ['pending', 'queued'])
    async def test_planner_waiting_for_a_slot_is_never_reaped(
        self, reaper_env, status
    ):
        maker, fake = reaper_env
        sched_id, task_id = await _mk(
            maker, plan_status='planning', task_status=status, minutes_ago=600
        )
        await _reaper.reap_stuck_planning_tasks()
        s, t = await _state(maker, sched_id, task_id)
        assert s.plan_status == 'planning'
        assert t.status == status
        assert task_id not in fake.stopped

    async def test_waiting_task_not_reaped(self, reaper_env):
        """WAITING is legitimate — user is being asked a question."""
        maker, fake = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='waiting',
            minutes_ago=600,
        )
        await _reaper.reap_stuck_planning_tasks()
        s, t = await _state(maker, sched_id, task_id)
        assert s.plan_status == 'planning'
        assert t.status == 'waiting'
        assert task_id not in fake.stopped

    async def test_planner_still_launching_is_not_reaped(self, reaper_env):
        """Between run() and the process spawn (slot wait, skills deps
        wait — minutes on a first boot) no process exists yet."""
        maker, fake = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='designing',
            minutes_ago=60,
        )
        fake.launching.add(task_id)
        await _reaper.reap_stuck_planning_tasks()
        s, t = await _state(maker, sched_id, task_id)
        assert s.plan_status == 'planning'
        assert t.status == 'designing'

    async def test_live_agent_inside_its_silence_bound_not_reaped(
        self, reaper_env
    ):
        maker, fake = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='designing',
            minutes_ago=10,
        )
        fake.alive.add(task_id)
        await _reaper.reap_stuck_planning_tasks()
        s, _t = await _state(maker, sched_id, task_id)
        assert s.plan_status == 'planning'

    async def test_pending_approval_is_out_of_scope(self, reaper_env):
        """A plan waiting for its owner is not planning — never touched,
        however old."""
        maker, fake = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='pending_approval',
            task_status='completed',
            minutes_ago=60 * 24 * 30,
            plan='P',
        )
        await _reaper.reap_stuck_planning_tasks()
        s, _t = await _state(maker, sched_id, task_id)
        assert s.plan_status == 'pending_approval'
        assert fake.stopped == []


class TestDeadPlanner:
    async def test_dead_designing_planner_fails(self, reaper_env):
        maker, fake = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='designing',
            minutes_ago=5,
        )
        await _reaper.reap_stuck_planning_tasks()
        s, t = await _state(maker, sched_id, task_id)
        assert s.plan_status == 'failed'
        assert s.plan_error == _reaper.PLANNER_DIED_MESSAGE
        assert s.plan_error_code == 'planner_died'
        assert s.current_planning_task_id is None
        assert t.status == 'failed'
        assert t.error_category == 'planner_died'
        assert task_id in fake.stopped

    async def test_dead_planner_gets_the_handover_grace(self, reaper_env):
        """A row younger than the stall threshold is a launch hand-over,
        not a death."""
        maker, _ = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='designing',
            minutes_ago=1,
        )
        await _reaper.reap_stuck_planning_tasks()
        s, _t = await _state(maker, sched_id, task_id)
        assert s.plan_status == 'planning'

    async def test_live_agent_silent_past_its_bound_is_reaped(self, reaper_env):
        """The same verdict as stall_reaper: a live process whose stream
        has been silent past the CLI's own watchdog bound is dead."""
        maker, fake = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='designing',
            minutes_ago=20,
        )
        fake.alive.add(task_id)
        await _reaper.reap_stuck_planning_tasks()
        s, _t = await _state(maker, sched_id, task_id)
        assert s.plan_status == 'failed'

    async def test_written_plan_of_a_dead_planner_becomes_the_proposal(
        self, reaper_env
    ):
        """The server died between saving the plan (PLANNED) and the
        hand-over: the plan is proposed, never lost and never run."""
        maker, _ = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='planned',
            minutes_ago=5,
            plan='the plan',
        )
        await _reaper.reap_stuck_planning_tasks()
        s, t = await _state(maker, sched_id, task_id)
        assert s.plan_status == 'pending_approval'
        assert s.proposed_plan == 'the plan'
        assert s.plan is None
        assert t.status == 'completed'


class TestTerminalReconcile:
    async def test_completed_task_proposes_never_approves(self, reaper_env):
        """Defensive: if the hook failed to hand over, the reaper does —
        as a PROPOSAL. Nothing but a human makes a plan ready."""
        maker, _ = reaper_env
        sched_id, task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='completed',
            minutes_ago=1,
            plan='P',
        )
        await _reaper.reap_stuck_planning_tasks()
        s, t = await _state(maker, sched_id, task_id)
        assert s.plan_status == 'pending_approval'
        assert s.proposed_plan == 'P'
        assert s.plan is None
        assert s.current_planning_task_id is None
        assert t.status == 'completed'

    async def test_a_replan_never_promotes_the_old_plan(self, reaper_env):
        """If the planning task completes without writing a new plan, the
        schedule's earlier text must not come back as a proposal."""
        maker, _ = reaper_env
        sched_id, _task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='completed',
            minutes_ago=1,
        )
        async with maker() as db:
            s = await db.get(Schedule, sched_id)
            s.plan = 'old plan'
            await db.commit()

        await _reaper.reap_stuck_planning_tasks()
        async with maker() as db:
            s = await db.get(Schedule, sched_id)
            assert s.plan_status == 'failed'
            assert s.proposed_plan is None

    async def test_completed_task_with_empty_plan_flips_failed(
        self, reaper_env
    ):
        """Regression guard: if the agent skipped ExitPlanMode, the
        planning task completes with no plan. The reaper must flip the
        schedule to FAILED with a clear error instead of leaving it
        stuck at plan_status='planning'."""
        maker, _ = reaper_env
        sched_id, _task_id = await _mk(
            maker,
            plan_status='planning',
            task_status='completed',
            minutes_ago=1,
        )
        await _reaper.reap_stuck_planning_tasks()
        async with maker() as db:
            s = await db.get(Schedule, sched_id)
            assert s.plan_status == 'failed'
            assert s.current_planning_task_id is None
            assert s.plan_error and 'ExitPlanMode' in s.plan_error
            assert s.plan_error_code == 'no_plan'

    async def test_missing_pointer_cleaned_up(self, reaper_env):
        """Schedule in planning with no pointer → failed (repair)."""
        maker, _ = reaper_env
        sched_id = str(uuid.uuid4())
        async with maker() as db:
            s = Schedule(
                id=sched_id,
                title='t',
                schedule_type='days',
                schedule_time='09:05',
                plan_mode=True,
                plan_status='planning',
                current_planning_task_id=None,
                created_by='u',
                phase_mode='fanout',
                timezone='UTC',
            )
            db.add(s)
            await db.commit()

        await _reaper.reap_stuck_planning_tasks()
        async with maker() as db:
            s = await db.get(Schedule, sched_id)
            assert s.plan_status == 'failed'


class TestFailureCodes:
    """The UI says why a plan failed from the code, in the user's language."""

    @pytest.mark.parametrize(
        ('category', 'code'),
        [
            ('server_restart', 'server_restart'),
            ('stopped_by_user', 'stopped'),
            ('planning_launch_failed', 'start_failed'),
            ('cli_error', 'other'),
            (None, 'other'),
        ],
    )
    async def test_failed_planner_maps_to_a_code(
        self, reaper_env, category, code
    ):
        maker, _ = reaper_env
        sched_id, task_id = await _mk(
            maker, plan_status='planning', task_status='failed', minutes_ago=1
        )
        async with maker() as db:
            t = await db.get(Task, task_id)
            t.error = 'technical detail'
            t.error_category = category
            await db.commit()
        await _reaper.reap_stuck_planning_tasks()
        s, _t = await _state(maker, sched_id, task_id)
        assert s.plan_status == 'failed'
        assert s.plan_error_code == code
        assert s.plan_error == 'technical detail'

    async def test_a_new_planning_round_clears_the_code(self, reaper_env):
        from app.plan_states import (  # noqa: PLC0415
            PlanFailure,
            clear_plan_failure,
            fail_plan,
        )

        sched = Schedule(plan_status='planning')
        fail_plan(sched, PlanFailure.SERVER_RESTART, 'x')
        assert (sched.plan_status, sched.plan_error_code) == (
            'failed',
            'server_restart',
        )
        clear_plan_failure(sched)
        assert sched.plan_error_code is None and sched.plan_error is None
