"""Workflow: the one-time knowledge cleanup rides the catalog sync.

After an upgrade that leaves notes describing old platform mechanics
(app/knowledge_contract.py), each catalog task whose scope owes the
cleanup gets one extra instruction and runs even if its catalog is
current. Pinned here, through the real ``auto_run_task``:

- it runs ONCE: after a completed run the scope is recorded and the
  next catalog sync neither re-runs nor re-instructs it;
- a failed run (e.g. the provider is out of credits) records nothing,
  so the next catalog sync runs it again — until one succeeds;
- scopes are independent: one store failing does not re-run the others;
- while a scope owes it, the task's note writes may only delete lines.
"""

import asyncio
import uuid

import pytest
import pytest_asyncio

from app import knowledge_contract
from app.browser.manager import store_slug
from app.config import VIBE_SELLER_DIR
from app.models.app_settings import AppSettings
from app.models.schedule import Schedule
from app.models.schedule_constants import (
    SYSTEM_CATALOG_SYNC_ID,
    PhaseMode,
    StalenessCheck,
)
from app.models.store import Store
from app.models.task import Task
from app.prompts import CATALOG_DESC_L3, CATALOG_RESTRICTION_PROMPT_L3
from app.task_runner_auto import auto_run_task
from app.task_states import TaskStatus
from tests.workflow.conftest import real_sleep, wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

UP_TO_DATE = 'L3 catalog already up-to-date'
CLEANUP_LINE = 'after a one-time cleanup of the notes it lists'
# The everyday restriction forbids writing notes; a cleanup run must
# not carry it next to the cleanup's permission (no contradiction).
FORBID_LINE = 'write any file other than the catalog'
assert FORBID_LINE in CATALOG_RESTRICTION_PROMPT_L3


@pytest_asyncio.fixture
async def catalog_schedule(override_async_session, admin_user):
    async with override_async_session() as db:
        db.add(
            Schedule(
                id=SYSTEM_CATALOG_SYNC_ID,
                title='Update Knowledge Catalogs',
                schedule_type='days',
                schedule_time='03:00',
                interval_value=1,
                is_active=True,
                is_system=True,
                phase_mode=PhaseMode.TWO_PHASE,
                staleness_check=StalenessCheck.CATALOG,
                created_by=admin_user.id,
            )
        )
        await db.commit()


@pytest_asyncio.fixture
async def upgraded(override_async_session):
    """An install that existed before the cleanup: floor 1 (what
    init_db records for it)."""
    async with override_async_session() as db:
        db.add(AppSettings(key=knowledge_contract.FLOOR_KEY, value='1'))
        await db.commit()


@pytest_asyncio.fixture
async def two_stores(admin_client):
    out = []
    for name in ('CleanStoreA', 'CleanStoreB'):
        r = await admin_client.post('/api/stores', json={'name': name})
        assert r.status_code == 200
        out.append(r.json())
    return out


async def _fire(session_maker, stores, admin_user_id) -> dict[str, str]:
    """One catalog sync's per-store tasks, dispatched as the queue does."""
    task_map: dict[str, str] = {}
    for s in stores:
        tid = str(uuid.uuid4())
        async with session_maker() as db:
            db.add(
                Task(
                    id=tid,
                    store_id=s['id'],
                    schedule_id=SYSTEM_CATALOG_SYNC_ID,
                    created_by=admin_user_id,
                    title='Update Knowledge Catalogs',
                    description=CATALOG_DESC_L3.replace(
                        '<slug>', store_slug(s['name'])
                    ),
                    status=TaskStatus.PENDING,
                )
            )
            await db.commit()
        task_map[s['id']] = tid
    for s in stores:
        async with session_maker() as db:
            store = await db.get(Store, s['id'])
        asyncio.create_task(auto_run_task(task_map[s['id']], store))
        await real_sleep(0.15)
    return task_map


def _ran_with_cleanup(fake, task_id) -> bool | None:
    """None when the agent never ran for the task."""
    calls = fake.get_calls(task_id=task_id, action='run')
    if not calls:
        return None
    return any(CLEANUP_LINE in c.system_extra for c in calls)


async def _owes(session_maker, store_id) -> bool:
    async with session_maker() as db:
        return await knowledge_contract.needs_cleanup(
            db, knowledge_contract.store_scope(store_id)
        )


async def test_runs_once_then_never_again(
    admin_client,
    install_fake_agent,
    override_async_session,
    mock_knowledge_sync,
    catalog_schedule,
    upgraded,
    two_stores,
    admin_user,
):
    # Catalogs are current: without the cleanup these tasks would skip.
    install_fake_agent.default_scenario = FakeAgentScenario(
        result='Cleaned and cataloged', skip_plan=True
    )
    first = await _fire(override_async_session, two_stores, admin_user.id)
    for s in two_stores:
        data = await wait_for_task(admin_client, first[s['id']], timeout=10)
        assert data['status'] == 'completed'
        assert _ran_with_cleanup(install_fake_agent, first[s['id']]) is True
        assert not await _owes(override_async_session, s['id'])
        prompt = install_fake_agent.get_calls(
            task_id=first[s['id']], action='run'
        )[0].system_extra
        assert FORBID_LINE not in prompt
        assert f'stores/{store_slug(s["name"])}/' in prompt
        assert 'stores/<slug>/' not in prompt

    second = await _fire(override_async_session, two_stores, admin_user.id)
    for s in two_stores:
        data = await wait_for_task(admin_client, second[s['id']], timeout=10)
        assert data.get('result') == UP_TO_DATE
        assert _ran_with_cleanup(install_fake_agent, second[s['id']]) is None


async def test_a_failed_run_repeats_until_one_succeeds(
    admin_client,
    install_fake_agent,
    override_async_session,
    mock_knowledge_sync,
    catalog_schedule,
    upgraded,
    two_stores,
    admin_user,
):
    store = two_stores[:1]
    sid = store[0]['id']
    install_fake_agent.default_scenario = FakeAgentScenario(
        skip_plan=True,
        error_result='API Error: 429 quota exhausted',
    )
    for _ in range(2):  # two nightly fires, both out of credits
        t = await _fire(override_async_session, store, admin_user.id)
        data = await wait_for_task(admin_client, t[sid], timeout=10)
        assert data['status'] == 'failed'
        assert _ran_with_cleanup(install_fake_agent, t[sid]) is True
        assert await _owes(override_async_session, sid)

    install_fake_agent.default_scenario = FakeAgentScenario(
        result='Cleaned and cataloged', skip_plan=True
    )
    t = await _fire(override_async_session, store, admin_user.id)
    data = await wait_for_task(admin_client, t[sid], timeout=10)
    assert data['status'] == 'completed'
    assert _ran_with_cleanup(install_fake_agent, t[sid]) is True
    assert not await _owes(override_async_session, sid)

    t = await _fire(override_async_session, store, admin_user.id)
    data = await wait_for_task(admin_client, t[sid], timeout=10)
    assert data.get('result') == UP_TO_DATE


async def test_one_store_failing_does_not_rerun_the_other(
    admin_client,
    install_fake_agent,
    override_async_session,
    mock_knowledge_sync,
    catalog_schedule,
    upgraded,
    two_stores,
    admin_user,
):
    ok, bad = two_stores
    install_fake_agent.default_scenario = FakeAgentScenario(
        result='Cleaned and cataloged', skip_plan=True
    )
    first = await _fire(override_async_session, [ok], admin_user.id)
    await wait_for_task(admin_client, first[ok['id']], timeout=10)
    install_fake_agent.default_scenario = FakeAgentScenario(
        skip_plan=True, error_result='API Error: 429 quota exhausted'
    )
    first = await _fire(override_async_session, [bad], admin_user.id)
    await wait_for_task(admin_client, first[bad['id']], timeout=10)

    install_fake_agent.default_scenario = FakeAgentScenario(
        result='Cleaned and cataloged', skip_plan=True
    )
    second = await _fire(override_async_session, two_stores, admin_user.id)
    ok_data = await wait_for_task(admin_client, second[ok['id']], timeout=10)
    bad_data = await wait_for_task(admin_client, second[bad['id']], timeout=10)
    assert ok_data.get('result') == UP_TO_DATE
    assert _ran_with_cleanup(install_fake_agent, second[bad['id']]) is True
    assert bad_data['status'] == 'completed'


async def test_no_floor_owes_nothing(
    admin_client,
    install_fake_agent,
    override_async_session,
    mock_knowledge_sync,
    catalog_schedule,
    two_stores,
    admin_user,
):
    """No recorded floor (nothing upgraded): catalog sync is unchanged."""
    t = await _fire(override_async_session, two_stores, admin_user.id)
    for s in two_stores:
        data = await wait_for_task(admin_client, t[s['id']], timeout=10)
        assert data.get('result') == UP_TO_DATE
    assert install_fake_agent.get_calls(action='run') == []


class TestCleanupWritesOnlyDelete:
    NOTE = (
        '# Notes\n\n'
        'margin: 40\n'
        'The first submit is refused by the reviewer; that is normal.\n'
        'Seller Central reports live under Reports > Business Reports.\n'
    )

    async def _cleanup_task(self, session_maker, admin_client, admin_user):
        r = await admin_client.post('/api/stores', json={'name': 'WriteStore'})
        store = r.json()
        tid = str(uuid.uuid4())
        async with session_maker() as db:
            db.add(
                Task(
                    id=tid,
                    store_id=store['id'],
                    schedule_id=SYSTEM_CATALOG_SYNC_ID,
                    created_by=admin_user.id,
                    title='Update Knowledge Catalogs',
                    status=TaskStatus.RUNNING,
                )
            )
            await db.commit()
        path = f'stores/{store_slug(store["name"])}/notes.md'
        r = await admin_client.put(
            f'/api/workspace/file?path={path}', json={'content': self.NOTE}
        )
        assert r.status_code == 200, r.text
        return tid, path

    async def test_deleting_the_outdated_passage_lands(
        self,
        admin_client,
        override_async_session,
        mock_workspace,
        catalog_schedule,
        upgraded,
        admin_user,
    ):
        tid, path = await self._cleanup_task(
            override_async_session, admin_client, admin_user
        )
        cleaned = self.NOTE.replace(
            'The first submit is refused by the reviewer; that is normal.\n',
            '',
        )
        r = await admin_client.put(
            f'/api/workspace/file?path={path}&task_id={tid}',
            json={'content': cleaned},
        )
        assert r.status_code == 200, r.text

    async def test_adding_or_rewording_is_refused(
        self,
        admin_client,
        override_async_session,
        mock_workspace,
        catalog_schedule,
        upgraded,
        admin_user,
    ):
        tid, path = await self._cleanup_task(
            override_async_session, admin_client, admin_user
        )
        reworded = self.NOTE.replace('margin: 40', 'margin: 30')
        r = await admin_client.put(
            f'/api/workspace/file?path={path}&task_id={tid}',
            json={'content': reworded},
        )
        assert r.status_code == 400
        assert 'margin: 30' in r.text

    async def test_a_store_run_cannot_edit_shared_knowledge(
        self,
        admin_client,
        override_async_session,
        mock_workspace,
        catalog_schedule,
        upgraded,
        admin_user,
    ):
        tid, _path = await self._cleanup_task(
            override_async_session, admin_client, admin_user
        )
        shared = 'knowledge/notes.md'
        r = await admin_client.put(
            f'/api/workspace/file?path={shared}', json={'content': self.NOTE}
        )
        assert r.status_code == 200, r.text
        r = await admin_client.put(
            f'/api/workspace/file?path={shared}&task_id={tid}',
            json={'content': '# Notes\n'},
        )
        assert r.status_code == 400
        assert 'another tier' in r.text

    async def test_other_writers_are_unaffected(
        self,
        admin_client,
        override_async_session,
        mock_workspace,
        catalog_schedule,
        upgraded,
        admin_user,
    ):
        _tid, path = await self._cleanup_task(
            override_async_session, admin_client, admin_user
        )
        r = await admin_client.put(
            f'/api/workspace/file?path={path}',
            json={'content': self.NOTE + 'A new fact.\n'},
        )
        assert r.status_code == 200, r.text


async def test_an_edit_made_around_the_tool_is_put_back(
    admin_client,
    install_fake_agent,
    override_async_session,
    mock_knowledge_sync,
    catalog_schedule,
    upgraded,
    admin_user,
):
    """Observed live: a run rewrote a note with a shell script instead of
    the write tool, so the endpoint's check never saw it, and it left
    half-sentences behind. The run's outcome is checked: the note is put
    back and the store stays owing, so the next catalog sync retries."""
    r = await admin_client.post('/api/stores', json={'name': 'AroundStore'})
    store = r.json()
    notes = VIBE_SELLER_DIR / 'stores' / store_slug(store['name']) / 'notes.md'
    notes.parent.mkdir(parents=True, exist_ok=True)
    original = TestCleanupWritesOnlyDelete.NOTE
    notes.write_text(original, encoding='utf-8')

    install_fake_agent.default_scenario = FakeAgentScenario(
        result='Cleaned and cataloged', skip_plan=True, complete_delay=1.0
    )
    t = await _fire(override_async_session, [store], admin_user.id)
    await real_sleep(0.3)  # the run is in progress
    notes.write_text(
        original.replace('margin: 40', 'margin: 4'), encoding='utf-8'
    )
    data = await wait_for_task(admin_client, t[store['id']], timeout=10)
    assert data['status'] == 'completed'
    assert notes.read_text(encoding='utf-8') == original
    assert await _owes(override_async_session, store['id'])
