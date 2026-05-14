"""Snapshot tests for build_system_extra prompt assembly.

Each test calls the real builder with test fixtures and asserts
expected substrings are present/absent in the assembled prompt.
"""

import os

os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-testing-only')

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.schedule import Schedule
from app.models.schedule_constants import (
    SYSTEM_CATALOG_SYNC_ID,
    PhaseMode,
    StalenessCheck,
)
from app.models.store import Store
from app.models.task import Task
from app.prompts import (
    CATALOG_RESTRICTION_PROMPT_L2,
)
from app.task_runner import (
    PromptBundle,
    TaskHeader,
    build_system_extra,
)

pytestmark = pytest.mark.unit

# ── Fixtures ─────────────────────────────────────────────────

TEST_EMAILS = ['seller@example.com', 'support@test.com']


@pytest_asyncio.fixture
async def _patch_async_session(monkeypatch):
    """Patch async_session in all modules that use it."""
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

    # Seed the catalog-sync Schedule row so the staleness_check
    # gate in build_system_extra resolves correctly for tasks
    # that reference it.
    async with maker() as db:
        db.add(
            Schedule(
                id=SYSTEM_CATALOG_SYNC_ID,
                title='Update Knowledge Catalogs',
                schedule_type='days',
                schedule_time='03:00',
                is_active=True,
                is_system=True,
                staleness_check=StalenessCheck.CATALOG,
                created_by='user-1',
            )
        )
        await db.commit()

    for mod in [
        'app.database',
        'app.routers.tasks',
        'app.task_runner',
    ]:
        try:
            monkeypatch.setattr(f'{mod}.async_session', maker)
        except AttributeError:
            pass
    yield maker
    await engine.dispose()


@pytest.fixture
def store():
    return Store(
        id='store-1',
        name='Test Store Alpha',
        browser_backend='chrome',
        browser_config='{"headless": true}',
        platforms='["amazon"]',
        countries='["SA"]',
    )


@pytest.fixture
def task(store):
    return Task(
        id='task-1',
        title='Check inventory levels',
        description='Check SA inventory for all SKUs',
        store_id=store.id,
        created_by='user-1',
        status='pending',
        plan_mode=False,
        schedule_id=None,
    )


@pytest.fixture
def plan_task(store):
    return Task(
        id='task-2',
        title='Setup logistics',
        description='Configure SA logistics settings',
        store_id=store.id,
        created_by='user-1',
        status='pending',
        plan_mode=True,
        schedule_id=None,
    )


@pytest.fixture
def catalog_task(store):
    return Task(
        id='task-3',
        title='Update Knowledge Catalogs',
        description='Regenerate catalogs',
        store_id=store.id,
        created_by='user-1',
        status='pending',
        plan_mode=True,
        schedule_id=SYSTEM_CATALOG_SYNC_ID,
    )


@pytest.fixture
def catalog_l2_task():
    return Task(
        id='task-l2',
        title='Update Knowledge Catalogs',
        description='Regenerate L2 catalog',
        store_id=None,
        created_by='user-1',
        status='pending',
        plan_mode=True,
        schedule_id=SYSTEM_CATALOG_SYNC_ID,
    )


@pytest.fixture
def execute_task(store):
    return Task(
        id='task-4',
        title='Ship orders',
        description='Process pending SA shipments',
        store_id=store.id,
        created_by='user-1',
        status='planned',
        plan_mode=True,
        plan='1. Open shipments page\n2. Process each order',
    )


@pytest.fixture
def woken_task(store):
    return Task(
        id='task-5',
        title='Wait for case response',
        description='Follow up on Amazon case',
        store_id=store.id,
        created_by='user-1',
        status='running',
        plan_mode=False,
        plan='1. Check case status\n2. Reply if needed',
        result='Sent case FBAXXX, waiting for response',
    )


@pytest.fixture
def no_store_task():
    return Task(
        id='task-6',
        title='Cross-store report',
        description='Generate inventory report across all stores',
        store_id=None,
        created_by='user-1',
        status='pending',
        plan_mode=False,
    )


# ── Tests ────────────────────────────────────────────────────


class TestAutoStoreTask:
    @pytest.mark.asyncio
    async def test_no_workspace_guidance_for_regular_task(
        self, _patch_async_session, task, store
    ):
        bundle = await build_system_extra(
            task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        assert isinstance(bundle, PromptBundle)
        # Regular tasks get empty {workspace_guidance} — no
        # skill/knowledge creation during execution
        assert '{workspace_guidance}' not in bundle.system_extra
        # Store-bound tasks must NOT carry the no-store write
        # policy — they have their own store dir as a home and
        # the policy would contradict that.
        assert 'Write policy (no-store task)' not in bundle.system_extra

    @pytest.mark.asyncio
    async def test_contains_reflection_reminder(
        self,
        _patch_async_session,
        task,
        store,
    ):
        bundle = await build_system_extra(
            task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        # Full REFLECTION_PROMPT is now delivered via Stop hook,
        # only a one-liner reminder is in the system prompt.
        assert 'reflect and update knowledge' in bundle.system_extra
        assert 'After completing the main task' not in bundle.system_extra

    @pytest.mark.asyncio
    async def test_contains_store_context(
        self, _patch_async_session, task, store
    ):
        bundle = await build_system_extra(
            task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        assert 'Test Store Alpha' in bundle.system_extra

    @pytest.mark.asyncio
    async def test_slug_resolved(self, _patch_async_session, task, store):
        bundle = await build_system_extra(
            task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        assert '<slug>' not in bundle.system_extra
        assert 'test-store-alpha' in bundle.system_extra

    @pytest.mark.asyncio
    async def test_mode_is_auto(self, _patch_async_session, task, store):
        bundle = await build_system_extra(
            task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        assert bundle.mode == 'auto'

    @pytest.mark.asyncio
    async def test_prompt_format(self, _patch_async_session, task, store):
        bundle = await build_system_extra(
            task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        assert bundle.prompt.startswith('Check inventory levels')
        assert 'Details:' in bundle.prompt

    @pytest.mark.asyncio
    async def test_contains_waiting_instruction(
        self, _patch_async_session, task, store
    ):
        bundle = await build_system_extra(
            task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        assert 'wait-condition' in bundle.system_extra


class TestAutoNoStoreTask:
    @pytest.mark.asyncio
    async def test_no_store_context(self, _patch_async_session, no_store_task):
        bundle = await build_system_extra(
            no_store_task,
            None,
            header=TaskHeader.AUTO,
        )
        # No store context, no workspace guidance, but reflection reminder present
        assert '{workspace_guidance}' not in bundle.system_extra
        assert 'reflect and update knowledge' in bundle.system_extra

    @pytest.mark.asyncio
    async def test_no_store_write_policy_present_no_stores(
        self, _patch_async_session, no_store_task
    ):
        """Fresh install / zero stores: the no-store task must STILL
        get the write policy. Early return on empty `stores` would
        otherwise let an orchestrator task squat in `stores/<slug>/`
        as soon as the first store is created.
        """
        bundle = await build_system_extra(
            no_store_task,
            None,
            header=TaskHeader.AUTO,
        )
        body = bundle.system_extra
        assert 'Write policy (no-store task)' in body
        assert 'No stores are configured yet' in body

    @pytest.mark.asyncio
    async def test_no_store_write_policy_present(
        self, _patch_async_session, no_store_task, store
    ):
        """Regression guard: no-store tasks must be told to write
        artifacts to the task workspace root, not to stores/<any>/.

        Without this, an orchestrator / all-stores scheduled task
        picks an arbitrary store dir for its output (observed in the
        wild: a cross-store email report landed in one store's
        knowledge dir, polluting that store's L3 catalog).
        """
        # Seed at least one store — build_all_stores_context()
        # returns '' when the DB has zero stores, which would skip
        # the write-policy block entirely.
        async with _patch_async_session() as db:
            db.add(store)
            await db.commit()

        bundle = await build_system_extra(
            no_store_task,
            None,
            header=TaskHeader.AUTO,
        )
        body = bundle.system_extra
        assert 'Write policy (no-store task)' in body
        assert 'task workspace root' in body
        assert 'Do NOT write to `stores/<name>/' in body


class TestDesignTask:
    @pytest.mark.asyncio
    async def test_plan_mode_sections(
        self, _patch_async_session, plan_task, store
    ):
        bundle = await build_system_extra(
            plan_task,
            store,
            header=TaskHeader.DESIGN,
            store_emails=TEST_EMAILS,
        )
        assert 'Phase 5' in bundle.system_extra
        assert 'ExitPlanMode' in bundle.system_extra

    @pytest.mark.asyncio
    async def test_mode_is_plan(self, _patch_async_session, plan_task, store):
        bundle = await build_system_extra(
            plan_task,
            store,
            header=TaskHeader.DESIGN,
            store_emails=TEST_EMAILS,
        )
        assert bundle.mode == 'plan_then_execute'

    @pytest.mark.asyncio
    async def test_prompt_format(self, _patch_async_session, plan_task, store):
        bundle = await build_system_extra(
            plan_task,
            store,
            header=TaskHeader.DESIGN,
            store_emails=TEST_EMAILS,
        )
        assert 'Design an execution plan' in bundle.prompt


class TestExecuteWithPlan:
    @pytest.mark.asyncio
    async def test_plan_in_extra(
        self, _patch_async_session, execute_task, store
    ):
        bundle = await build_system_extra(
            execute_task,
            store,
            header=TaskHeader.EXECUTE,
            store_emails=TEST_EMAILS,
        )
        assert 'Execute the following plan' in bundle.system_extra
        assert 'Open shipments page' in bundle.system_extra

    @pytest.mark.asyncio
    async def test_mode_is_execute(
        self, _patch_async_session, execute_task, store
    ):
        bundle = await build_system_extra(
            execute_task,
            store,
            header=TaskHeader.EXECUTE,
            store_emails=TEST_EMAILS,
        )
        assert bundle.mode == 'execute'

    @pytest.mark.asyncio
    async def test_no_plan_mode_sections(
        self, _patch_async_session, execute_task, store
    ):
        """Execute mode must not include Phase 5 / ExitPlanMode."""
        bundle = await build_system_extra(
            execute_task,
            store,
            header=TaskHeader.EXECUTE,
            store_emails=TEST_EMAILS,
        )
        assert 'ExitPlanMode' not in bundle.system_extra
        assert '## Phase 5' not in bundle.system_extra


class TestWokenTask:
    @pytest.mark.asyncio
    async def test_plan_in_extra(self, _patch_async_session, woken_task, store):
        bundle = await build_system_extra(
            woken_task,
            store,
            header=TaskHeader.WOKEN,
            store_emails=TEST_EMAILS,
        )
        assert 'Continue executing the task' in bundle.system_extra
        assert 'Check case status' in bundle.system_extra

    @pytest.mark.asyncio
    async def test_mode_is_auto(self, _patch_async_session, woken_task, store):
        """Woken task with plan_mode=False gets auto mode."""
        bundle = await build_system_extra(
            woken_task,
            store,
            header=TaskHeader.WOKEN,
            store_emails=TEST_EMAILS,
        )
        assert bundle.mode == 'auto'


class TestChatTask:
    @pytest.mark.asyncio
    async def test_extra_context_appended(
        self, _patch_async_session, task, store
    ):
        bundle = await build_system_extra(
            task,
            store,
            header=TaskHeader.CHAT,
            store_emails=TEST_EMAILS,
            extra_context='## Prior Conversation\nUser asked about X',
        )
        assert 'Prior Conversation' in bundle.system_extra


class TestCatalogSync:
    @pytest.mark.asyncio
    async def test_catalog_restriction(
        self, _patch_async_session, catalog_task, store
    ):
        bundle = await build_system_extra(
            catalog_task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        # L3 prompt contains <slug> which gets rendered, so
        # check for a substring that survives render_prompt()
        assert 'regenerating ONLY the L3 store catalog' in bundle.system_extra

    @pytest.mark.asyncio
    async def test_no_reflection_reminder(
        self, _patch_async_session, catalog_task, store
    ):
        bundle = await build_system_extra(
            catalog_task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        assert 'reflect and update knowledge' not in bundle.system_extra

    @pytest.mark.asyncio
    async def test_no_skill_creation_instructions(
        self, _patch_async_session, catalog_task, store
    ):
        """Catalog sync must not tell agent to create skills."""
        bundle = await build_system_extra(
            catalog_task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        sys = bundle.system_extra.lower()
        assert 'create a skill' not in sys
        assert 'write to stores/' not in sys


class TestCatalogSyncL2:
    """L2 (no-store) catalog sync uses the L2 restriction prompt."""

    @pytest.mark.asyncio
    async def test_l2_restriction(self, _patch_async_session, catalog_l2_task):
        bundle = await build_system_extra(
            catalog_l2_task,
            None,
            header=TaskHeader.AUTO,
        )
        assert CATALOG_RESTRICTION_PROMPT_L2 in bundle.system_extra

    @pytest.mark.asyncio
    async def test_no_reflection_reminder(
        self,
        _patch_async_session,
        catalog_l2_task,
    ):
        bundle = await build_system_extra(
            catalog_l2_task,
            None,
            header=TaskHeader.AUTO,
        )
        assert 'reflect and update knowledge' not in bundle.system_extra


class TestPromptOrdering:
    @pytest.mark.asyncio
    async def test_canonical_order(self, _patch_async_session, task, store):
        """Prompt parts appear in canonical order."""
        bundle = await build_system_extra(
            task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        sys = bundle.system_extra

        # Base prompt (design_system) comes first
        base_pos = sys.find('task design agent')
        # Waiting instruction comes after base
        wait_pos = sys.find('Waiting for External Responses')
        # Store context (store name in browser section)
        store_pos = sys.find('Test Store Alpha')
        # System context
        ctx_pos = sys.find('## System Context')
        # Reflection reminder (one-liner, after system context)
        refl_pos = sys.find('reflect and update knowledge')

        assert 0 <= base_pos < wait_pos, f'base={base_pos} < wait={wait_pos}'
        assert wait_pos < store_pos, f'wait={wait_pos} < store={store_pos}'
        assert store_pos < ctx_pos, f'store={store_pos} < ctx={ctx_pos}'
        assert ctx_pos < refl_pos, f'ctx={ctx_pos} < refl={refl_pos}'


# ── Scheduled-task state block ──────────────────────────────


@pytest.fixture
def scheduled_task(store):
    """A regular store task with a schedule_id set — the pre-task
    block should be injected."""
    return Task(
        id='task-sched-1',
        title='Daily inbox sweep',
        description='Analyze new emails since last run',
        store_id=store.id,
        created_by='user-1',
        status='pending',
        plan_mode=False,
        schedule_id='sched-abc-123',
    )


class TestScheduledPretaskBlock:
    """Pre-task cross-run state hint is injected iff schedule_id set
    AND the task is not a catalog-sync run."""

    @pytest.mark.asyncio
    async def test_injected_for_scheduled_task(
        self,
        _patch_async_session,
        scheduled_task,
        store,
    ):
        bundle = await build_system_extra(
            scheduled_task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        assert 'vibe_seller_get_schedule_state' in bundle.system_extra
        assert 'Scheduled task — cross-run state' in bundle.system_extra

    @pytest.mark.asyncio
    async def test_absent_for_non_scheduled_task(
        self,
        _patch_async_session,
        task,
        store,
    ):
        bundle = await build_system_extra(
            task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        assert 'vibe_seller_get_schedule_state' not in bundle.system_extra

    @pytest.mark.asyncio
    async def test_absent_for_catalog_sync_task(
        self,
        _patch_async_session,
        catalog_task,
        store,
    ):
        """Catalog sync is scheduled but has no cursor to resume."""
        bundle = await build_system_extra(
            catalog_task,
            store,
            header=TaskHeader.AUTO,
            store_emails=TEST_EMAILS,
        )
        assert 'vibe_seller_get_schedule_state' not in bundle.system_extra


class TestPlanOnlyBlock:
    """is_plan_only=True injects the planner-authoring block and
    suppresses the scheduled-task pretask block (it's a creation-time
    planner, not a scheduled run)."""

    @pytest.mark.asyncio
    async def test_plan_only_block_injected(
        self, _patch_async_session, scheduled_task, store
    ):
        scheduled_task.is_plan_only = True
        scheduled_task.plan_mode = True
        bundle = await build_system_extra(
            scheduled_task,
            None,  # plan-only tasks are store-agnostic
            header=TaskHeader.DESIGN,
        )
        assert 'reusable plan for a schedule' in bundle.system_extra
        assert 'AskUserQuestion' in bundle.system_extra

    @pytest.mark.asyncio
    async def test_scheduled_pretask_suppressed_for_plan_only(
        self, _patch_async_session, scheduled_task
    ):
        scheduled_task.is_plan_only = True
        scheduled_task.plan_mode = True
        bundle = await build_system_extra(
            scheduled_task,
            None,
            header=TaskHeader.DESIGN,
        )
        # Pretask state-cursor block is for actual fires, not planners.
        assert 'vibe_seller_get_schedule_state' not in bundle.system_extra

    @pytest.mark.asyncio
    async def test_plan_language_lock_is_language_agnostic(
        self, _patch_async_session, scheduled_task
    ):
        """The plan-only block tells the agent to match the user's
        language without naming it. This scales to any language
        the user writes in (Chinese, English, Spanish, …) without
        a maintained enum in our prompt."""
        scheduled_task.is_plan_only = True
        scheduled_task.plan_mode = True
        bundle = await build_system_extra(
            scheduled_task,
            None,
            header=TaskHeader.DESIGN,
        )
        assert (
            'Write the entire plan in the same language' in bundle.system_extra
        )
        assert 'this rule is hard' in bundle.system_extra
        # No hardcoded language names in this bullet (intentionally).
        # The generic detect_language_hint above provides the soft
        # "respond in Chinese/English" nudge; this bullet is the hard
        # lock and trusts the LLM to detect language from task text.

    @pytest.mark.asyncio
    async def test_requires_exit_plan_mode_bullet(
        self, _patch_async_session, scheduled_task
    ):
        """Plan-only prompt must require ExitPlanMode explicitly —
        without this nudge, some models decide "nothing to plan"
        and return a chat reply, leaving Schedule.plan empty and
        the schedule stuck in planning."""
        scheduled_task.is_plan_only = True
        scheduled_task.plan_mode = True
        bundle = await build_system_extra(
            scheduled_task,
            None,
            header=TaskHeader.DESIGN,
        )
        assert 'MUST call ExitPlanMode' in bundle.system_extra
        # The nudge must explicitly address the "seems trivial" case
        # — that's the exact reasoning LLMs use to skip.
        assert 'trivial' in bundle.system_extra.lower()

    @pytest.mark.asyncio
    async def test_fanout_restriction_injected(
        self, _patch_async_session, scheduled_task
    ):
        """Plan-only on a fanout schedule gets the extra bullet
        forbidding orchestrator spawning. The hook validator enforces
        the same rule at ExitPlanMode time."""
        scheduled_task.is_plan_only = True
        scheduled_task.plan_mode = True
        fanout_sched = Schedule(
            id='fanout-sched',
            title='Fanout',
            schedule_type='days',
            schedule_time='09:00',
            plan_mode=True,
            phase_mode=PhaseMode.FANOUT.value,
            created_by='user-1',
        )
        bundle = await build_system_extra(
            scheduled_task,
            None,
            header=TaskHeader.DESIGN,
            schedule=fanout_sched,
        )
        # The extra bullet must appear; the hook validator enforces
        # the same rule, but the prompt nudges the agent first.
        assert 'vibe_seller_create_task' in bundle.system_extra
        assert 'FANOUT mode' in bundle.system_extra

    @pytest.mark.asyncio
    async def test_fanout_restriction_absent_for_single_mode(
        self, _patch_async_session, scheduled_task
    ):
        scheduled_task.is_plan_only = True
        scheduled_task.plan_mode = True
        single_sched = Schedule(
            id='single-sched',
            title='Single',
            schedule_type='days',
            schedule_time='09:00',
            plan_mode=True,
            phase_mode=PhaseMode.SINGLE.value,
            created_by='user-1',
        )
        bundle = await build_system_extra(
            scheduled_task,
            None,
            header=TaskHeader.DESIGN,
            schedule=single_sched,
        )
        # Single-mode plans may legitimately orchestrate — no bullet.
        assert 'FANOUT mode' not in bundle.system_extra
