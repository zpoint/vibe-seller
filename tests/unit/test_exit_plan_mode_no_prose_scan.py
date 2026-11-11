"""ExitPlanMode does not read what the plan SAYS.

A fanout schedule's plan must not orchestrate sub-tasks — the scheduler
already fires one task per store, so an orchestrator step inside the plan
would fan out recursively. That rule used to be enforced here, by
scanning the plan text for ``vibe_seller_create_task`` /
``parent_task_id`` and denying ``ExitPlanMode``.

It refused the COMPLIANT plan: the prompt (``app/task_runner.py``) tells
the agent "Do NOT call `vibe_seller_create_task`, do NOT reference
parent_task_id", so every plan that repeats the instruction back contains
both needles. And the deny had no round limit — a guess from prose has
nothing to name as "still missing", so the same text was denied forever.

The rule is now enforced where it can bite: ``POST /api/tasks`` refuses a
sub-task whose parent is a fanout-schedule task
(``_validate_parent_task``, covered by
``tests/workflow/test_wf_fanout_subtask_guard.py``). What these tests pin
is the other half — that a plan's WORDING is not evidence, on the exact
task shape the old check fired for (``is_plan_only`` + a fanout schedule).
"""

from unittest.mock import AsyncMock
import uuid

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ai.claude_backend import AgentSession
import app.ai.claude_backend_utils as utils
import app.database as db_module
from app.models.base import Base
from app.models.schedule import Schedule
from app.models.task import Task

pytestmark = pytest.mark.unit

# The plan an obedient agent writes: it quotes the prompt's own
# prohibition, which is exactly what the deleted substring check punished.
COMPLIANT_PLAN = """## Plan

1. Read this store's L3 catalog.
2. Audit the store's campaigns and write AD_AUDIT.md.

Do NOT call `vibe_seller_create_task` and do NOT reference
parent_task_id — this fire is already bound to one store.
"""

NEUTRAL_PLAN = """## Plan

1. Read this store's L3 catalog.
2. Audit the store's campaigns and write AD_AUDIT.md.
"""


@pytest_asyncio.fixture
async def fanout_plan_task(monkeypatch):
    """A real ``is_plan_only`` task under a ``phase_mode='fanout'`` schedule.

    The shape the deleted validator loaded from the DB before scanning any
    text — without these rows it failed open, which is why an earlier
    version of this test proved nothing.
    """
    engine = create_async_engine(
        'sqlite+aiosqlite://',
        connect_args={'check_same_thread': False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(db_module, 'async_session', maker)
    # `raising=False`: the fix deletes this module-level name along with
    # the validator, and the test must still run on the fixed code.
    monkeypatch.setattr(utils, 'async_session', maker, raising=False)

    schedule_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    async with maker() as session:
        session.add(
            Schedule(
                id=schedule_id,
                title='fanout sched',
                schedule_type='days',
                schedule_time='09:00',
                plan_mode=True,
                phase_mode='fanout',
                created_by='u',
                timezone='UTC',
            )
        )
        session.add(
            Task(
                id=task_id,
                title='Plan: fanout sched',
                status='designing',
                plan_mode=True,
                is_plan_only=True,
                schedule_id=schedule_id,
                created_by='u',
            )
        )
        await session.commit()
    yield task_id
    await engine.dispose()


def _session(task_id: str) -> AgentSession:
    """An AgentSession with its I/O edges stubbed.

    Only the routing of ``_handle_exit_plan_mode_can_use_tool`` is under
    test: the plan save and the approve/deny responses are recorded, never
    written to a DB or to the CLI's stdin.
    """
    session = AgentSession(
        task_id=task_id,
        prompt='test',
        mode='plan_then_execute',
        auto_approve_plan=True,
    )
    session._save_design_plan = AsyncMock()
    session._approve_plan_request = AsyncMock()
    sent: list[tuple[str, str, str]] = []

    async def _record(request_id: str, behavior: str, **kwargs):
        sent.append((request_id, behavior, str(kwargs.get('message') or '')))

    session._send_control_response = _record
    session._sent = sent  # type: ignore[attr-defined]
    return session


class TestPlanTextIsNotScanned:
    @pytest.mark.parametrize(
        'plan',
        [COMPLIANT_PLAN, NEUTRAL_PLAN],
        ids=['names-the-tools', 'silent'],
    )
    async def test_any_plan_wording_takes_the_same_path(
        self, fanout_plan_task, plan
    ):
        session = _session(fanout_plan_task)
        tool_input = {'plan': plan}

        await session._handle_exit_plan_mode_can_use_tool('req-1', tool_input)

        # Not one deny: nothing on this path reads the plan's wording,
        # not even for the plan that names the forbidden tools while
        # saying it will not call them.
        assert session._sent == []
        # Saved, then approved, exactly as a plain plan would be.
        session._save_design_plan.assert_awaited_once_with(plan)
        session._approve_plan_request.assert_awaited_once_with(
            'req-1', tool_input
        )
