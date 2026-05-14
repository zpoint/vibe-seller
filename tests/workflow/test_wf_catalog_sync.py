"""Workflow: catalog sync fanout only runs agent for stale stores.

When the system catalog-sync schedule fans out to per-store (L3)
tasks, only the store whose L3 catalog actually changed should
launch the agent. The rest must complete immediately with
``'L3 catalog already up-to-date'``.

L2 staleness is handled by a separate no-store task in Phase 1 of
the fanout (see ``app/scheduler/fanout.py``). Per-store L3 tasks
only check their own L3 catalog staleness.

Regression guard for the bug where ``rotate_catalogs`` unconditionally
deleted the global L2 catalog, causing every subsequent store task to
see L2 as stale and needlessly run the agent.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio

from app.browser.manager import store_slug
from app.models.schedule import Schedule
from app.models.schedule_constants import (
    SYSTEM_CATALOG_SYNC_ID,
    PhaseMode,
    StalenessCheck,
)
from app.models.store import Store
from app.models.task import Task
from app.prompts import CATALOG_DESC_L3
from app.task_runner_auto import auto_run_task
from app.task_states import TaskStatus
from tests.workflow.conftest import real_sleep, wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


# ── Fixtures ────────────────────────────────────────────


@pytest_asyncio.fixture
async def catalog_schedule(override_async_session, admin_user):
    """The system catalog-sync all-stores schedule."""
    async with override_async_session() as db:
        sched = Schedule(
            id=SYSTEM_CATALOG_SYNC_ID,
            title='Update Knowledge Catalogs',
            schedule_type='days',
            schedule_time='03:00',
            interval_value=1,
            is_active=True,
            is_system=True,
            phase_mode=PhaseMode.TWO_PHASE,
            staleness_check=StalenessCheck.CATALOG,
            skip_reflection=True,
            created_by=admin_user.id,
        )
        db.add(sched)
        await db.commit()
        await db.refresh(sched)
        return sched


@pytest_asyncio.fixture
async def three_stores(admin_client):
    """Create three stores via the API."""
    stores = []
    for name in ('CatStoreA', 'CatStoreB', 'CatStoreC'):
        r = await admin_client.post(
            '/api/stores',
            json={'name': name},
        )
        assert r.status_code == 200
        stores.append(r.json())
    return stores


async def _create_and_dispatch_catalog_tasks(
    session_maker,
    stores: list[dict],
    admin_user_id: str,
) -> dict[str, str]:
    """Simulate fanout: create one catalog-sync task per store.

    Creates tasks in the DB then dispatches each via
    ``auto_run_task`` — the same code path the real queue
    scheduler uses.  Returns ``{store_id: task_id}``.

    Tasks are dispatched sequentially with a small yield
    between them so the StaticPool single-connection
    doesn't contend under aiosqlite.
    """
    batch_id = str(uuid.uuid4())
    task_map: dict[str, str] = {}

    for s in stores:
        task_id = str(uuid.uuid4())
        slug = store_slug(s['name'])
        desc = CATALOG_DESC_L3.replace('<slug>', slug)
        async with session_maker() as db:
            task = Task(
                id=task_id,
                store_id=s['id'],
                schedule_id=SYSTEM_CATALOG_SYNC_ID,
                created_by=admin_user_id,
                title='Update Knowledge Catalogs',
                description=desc,
                status=TaskStatus.PENDING,
                batch_id=batch_id,
            )
            db.add(task)
            await db.commit()
        task_map[s['id']] = task_id

    # Dispatch tasks sequentially — yield to the event loop
    # between each so DB sessions from earlier tasks close
    # before the next starts (StaticPool single-connection).
    for s in stores:
        async with session_maker() as db:
            store = await db.get(Store, s['id'])
        asyncio.create_task(
            auto_run_task(task_map[s['id']], store),
        )
        await real_sleep(0.15)

    return task_map


# ── Tests ───────────────────────────────────────────────


class TestCatalogSyncFanout:
    """Fan-out creates per-store tasks; only stale ones run agent."""

    async def test_only_stale_store_runs_agent(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        mock_knowledge_sync,
        catalog_schedule,
        three_stores,
        admin_user,
    ):
        """Modify one store → only that task launches the agent.

        The other two must short-circuit with
        'Catalogs already up-to-date'.
        """
        stale_store = three_stores[0]  # CatDemoNorthshore
        stale_slug = store_slug(stale_store['name'])

        # Mark only CatDemoNorthshore as L3-stale (L2 up-to-date)
        mock_knowledge_sync.stale_overrides[stale_slug] = (
            False,
            True,
        )
        # Other stores default to (False, False) → not stale

        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Catalog regenerated',
            skip_plan=True,
            complete_delay=0.05,
        )

        # Simulate fanout + dispatch
        task_map = await _create_and_dispatch_catalog_tasks(
            override_async_session,
            three_stores,
            admin_user.id,
        )

        # Wait for all tasks to reach terminal state
        results = {}
        for sid, tid in task_map.items():
            results[sid] = await wait_for_task(
                admin_client,
                tid,
                timeout=10,
            )

        # All three must complete
        for sid, data in results.items():
            assert data['status'] == 'completed', (
                f'Store {sid}: expected completed, got {data["status"]}'
            )

        # Stale store ran the agent → custom result
        stale_data = results[stale_store['id']]
        assert 'regenerated' in stale_data.get('result', '')

        # Non-stale stores skipped → "L3 catalog already up-to-date"
        for s in three_stores[1:]:
            data = results[s['id']]
            assert data.get('result') == ('L3 catalog already up-to-date'), (
                f'Store {s["name"]}: expected skip, '
                f'got result={data.get("result")!r}'
            )

        # Agent was called only for the stale store's task
        stale_tid = task_map[stale_store['id']]
        agent_runs = install_fake_agent.get_calls(action='run')
        agent_task_ids = {c.task_id for c in agent_runs}
        assert stale_tid in agent_task_ids
        for s in three_stores[1:]:
            assert task_map[s['id']] not in agent_task_ids

    async def test_l3_stale_triggers_all_stores(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        mock_knowledge_sync,
        catalog_schedule,
        three_stores,
        admin_user,
    ):
        """When L3 (per-store) is stale for all stores, ALL tasks
        must run the agent."""
        # Mark ALL stores as L3-stale
        for s in three_stores:
            slug = store_slug(s['name'])
            mock_knowledge_sync.stale_overrides[slug] = (
                False,
                True,
            )

        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Catalog regenerated',
            skip_plan=True,
            complete_delay=0.05,
        )

        task_map = await _create_and_dispatch_catalog_tasks(
            override_async_session,
            three_stores,
            admin_user.id,
        )

        for tid in task_map.values():
            data = await wait_for_task(admin_client, tid, timeout=10)
            assert data['status'] == 'completed'

        # All three should have triggered the agent
        agent_runs = install_fake_agent.get_calls(action='run')
        assert len(agent_runs) == 3

    async def test_nothing_stale_skips_all(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        mock_knowledge_sync,
        catalog_schedule,
        three_stores,
        admin_user,
    ):
        """All catalogs up-to-date → zero agent runs."""
        # stale_overrides empty → default (False, False) for all

        task_map = await _create_and_dispatch_catalog_tasks(
            override_async_session,
            three_stores,
            admin_user.id,
        )

        for tid in task_map.values():
            data = await wait_for_task(
                admin_client,
                tid,
                timeout=10,
            )
            assert data['status'] == 'completed'
            assert data.get('result') == ('L3 catalog already up-to-date')

        # No agent calls at all
        assert install_fake_agent.get_calls(action='run') == []

    async def test_task_description_has_real_slug(
        self,
        admin_client,
        override_async_session,
        mock_knowledge_sync,
        catalog_schedule,
        three_stores,
        admin_user,
    ):
        """Task description must contain the real store slug,
        not the literal ``<slug>`` placeholder."""
        task_map = await _create_and_dispatch_catalog_tasks(
            override_async_session,
            three_stores,
            admin_user.id,
        )

        for s in three_stores:
            tid = task_map[s['id']]
            async with override_async_session() as db:
                task = await db.get(Task, tid)
            slug = store_slug(s['name'])
            assert '<slug>' not in task.description, (
                f'Task for {s["name"]} has unresolved <slug>'
            )
            assert f'stores/{slug}/' in task.description
