"""Which ads path a task is told it is on.

Skill routing decides what a store task CAN load; the prompt says which
path it is ON. The case that needs it: an all-stores fanout whose plan
was written once, by a planner with no store, and is handed verbatim to
every store — the API ones and the console ones alike. So the planner
must see which stores are bound, and each store task must hear its own
path even when the plan says otherwise.

Nothing is said until some store is bound: an installation that never
used the ads service gets exactly the prompt it had before.
"""

import os

os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-testing-only')

import sys

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.ads_routing import API_SKILL, BROWSER_SKILL, ORCHESTRATOR_NOTE
from app.models.base import Base
from app.models.store import Store
from app.models.task import Task
from app.task_runner import TaskHeader, build_system_extra

pytestmark = pytest.mark.unit

CONSOLE_PLAN = '1. Open the seller ad console\n2. Read spend and sales'


@pytest_asyncio.fixture
async def db(monkeypatch):
    """An in-memory database every ``async_session`` binding points at."""
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
    for name, module in list(sys.modules.items()):
        if name.startswith('app.') and getattr(module, 'async_session', None):
            monkeypatch.setattr(f'{name}.async_session', maker)
    yield maker
    await engine.dispose()


def _store(store_id: str, name: str, authorized: bool) -> Store:
    return Store(
        id=store_id,
        name=name,
        browser_backend='chrome',
        browser_config='{"headless": true}',
        platforms='["amazon"]',
        countries='["SA"]',
        ads_authorized=authorized,
    )


@pytest.fixture
def api_store():
    return _store('store-api', 'shop-api', True)


@pytest.fixture
def console_store():
    return _store('store-console', 'shop-console', False)


async def _seed(db, *stores):
    async with db() as session:
        session.add_all(stores)
        await session.commit()


def _fanout_child(store: Store) -> Task:
    """A fanout child: the schedule's shared plan, on one store."""
    return Task(
        id=f'task-{store.id}',
        title='Ad spend, every store',
        store_id=store.id,
        created_by='user-1',
        status='planned',
        plan_mode=True,
        plan=CONSOLE_PLAN,
    )


def _planner() -> Task:
    return Task(
        id='task-plan',
        title='Plan: ad spend, every store',
        store_id=None,
        created_by='user-1',
        status='pending',
        plan_mode=True,
        is_plan_only=True,
    )


async def _prompt(task: Task, store: Store | None) -> str:
    header = TaskHeader.EXECUTE if store else TaskHeader.DESIGN
    bundle = await build_system_extra(task, store, header=header)
    return bundle.system_extra


class TestNothingBound:
    @pytest.mark.asyncio
    async def test_a_store_task_hears_nothing_about_ads(
        self, db, console_store
    ):
        await _seed(db, console_store)
        body = await _prompt(_fanout_child(console_store), console_store)
        assert 'Ads API:' not in body

    @pytest.mark.asyncio
    async def test_the_planner_sees_no_ads_labels(self, db, console_store):
        await _seed(db, console_store)
        body = await _prompt(_planner(), None)
        assert 'ads: ' not in body
        assert ORCHESTRATOR_NOTE not in body


class TestSomeBound:
    @pytest.mark.asyncio
    async def test_the_planner_sees_which_store_takes_which_path(
        self, db, api_store, console_store
    ):
        await _seed(db, api_store, console_store)
        body = await _prompt(_planner(), None)
        assert '"shop-api" (id: store-api)' in body
        api_line = next(ln for ln in body.splitlines() if '"shop-api"' in ln)
        console_line = next(
            ln for ln in body.splitlines() if '"shop-console"' in ln
        )
        assert api_line.endswith('— ads: API')
        assert console_line.endswith('— ads: not authorized')
        assert ORCHESTRATOR_NOTE in body

    @pytest.mark.asyncio
    async def test_an_api_store_is_told_its_path_over_the_plan(
        self, db, api_store, console_store
    ):
        await _seed(db, api_store, console_store)
        body = await _prompt(_fanout_child(api_store), api_store)
        assert 'this store is authorized with the ads service' in body
        assert f'`{API_SKILL}`' in body
        assert 'even where the task or its plan mentions the ad console' in body
        # The shared plan still reaches it; the note is what overrides it,
        # so it comes after the plan — the last word. Placed before it,
        # glm-4.7 followed the plan's console steps on an API store.
        assert 'is not authorized' not in body
        assert body.rindex('Ads API:') > body.index(CONSOLE_PLAN)

    @pytest.mark.asyncio
    async def test_a_console_store_is_told_the_api_refuses_it(
        self, db, api_store, console_store
    ):
        await _seed(db, api_store, console_store)
        body = await _prompt(_fanout_child(console_store), console_store)
        assert 'this store is not authorized with the ads service' in body
        assert 'this store is authorized with' not in body

    @pytest.mark.asyncio
    async def test_an_unbound_store_is_not_steered_to_amazon_ad_work(
        self, db, api_store, console_store
    ):
        """Its line is a fact, not a procedure. Naming the Amazon browser
        skill told every unbound store — noon ones, ones with no platform
        recorded — "this is Amazon ad work"; in CI a console review then
        loaded that skill and ran its full audit procedure, 20 minutes
        against 8 for the same request without the line."""
        await _seed(db, api_store, console_store)
        for task, store in (
            (_fanout_child(console_store), console_store),
            (_planner(), None),
        ):
            body = await _prompt(task, store)
            assert f'`{BROWSER_SKILL}`' not in body
            assert 'Amazon advertising' not in body

    @pytest.mark.asyncio
    async def test_the_line_follows_the_store_not_the_installation(
        self, db, api_store, console_store
    ):
        """Revoking the last store takes the lines away again."""
        await _seed(db, api_store, console_store)
        async with db() as session:
            row = await session.get(Store, api_store.id)
            row.ads_authorized = False
            await session.commit()
        body = await _prompt(_fanout_child(console_store), console_store)
        assert 'Ads API:' not in body
