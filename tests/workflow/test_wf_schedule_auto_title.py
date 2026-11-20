"""Auto schedule titles: the schedule dialogs are one prompt box too.

Pins the same contract as tasks (``app/task_title.py``) through the
real schedule routes:

- A blank title on create is derived from the prompt, then upgraded by
  the AI title (compare-and-set on the row + ``schedule_title`` SSE).
- An explicit title (old REST clients, MCP ``create_schedule``) is
  kept and costs no model call.
- On edit, a blank title re-derives from the edited prompt; an edit
  that leaves ``title`` out keeps it (old clients, MCP).
- A fire names its task from the schedule ROW, so the AI title that
  lands after the job was registered still reaches the fired tasks.
"""

import asyncio
import json

import pytest
from sqlalchemy import select

import app.database as _db
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.task import Task
import app.scheduler.cron as cron
from app.scheduler.fanout import run_single_job
import app.task_title as task_title

pytestmark = pytest.mark.workflow

LONG = (
    'Download yesterday orders for widget-006 every morning. '
    'Then summarize the returns by reason.'
)
PLACEHOLDER = 'Download yesterday orders for widget-006 every mo…'
AI_TITLE = 'Daily widget-006 orders'


def _drain(q):
    out = []
    while not q.empty():
        out.append(json.loads(q.get_nowait()))
    return out


async def _settle():
    pending = list(task_title._pending)
    if pending:
        await asyncio.gather(*pending)


@pytest.fixture
def fake_generate(monkeypatch):
    calls = []

    async def gen(text, profile_id):
        calls.append((text, profile_id))
        return AI_TITLE

    monkeypatch.setattr(task_title, 'generate_title', gen)
    return calls


async def _create(client, **body):
    r = await client.post(
        '/api/schedules',
        json={'schedule_type': 'days', 'schedule_time': '09:05', **body},
    )
    return r


class TestCreate:
    async def test_blank_title_gets_placeholder_then_ai_title(
        self, admin_client, install_fake_agent, fake_generate
    ):
        q = event_bus.subscribe()
        try:
            r = await _create(admin_client, description=LONG)
            assert r.status_code == 201, r.text
            body = r.json()
            assert body['title'] == PLACEHOLDER
            assert body['description'] == LONG
            await _settle()
            events = _drain(q)
        finally:
            event_bus.unsubscribe(q)

        assert [text for text, _ in fake_generate] == [LONG]
        assert [e for e in events if e['type'] == 'schedule_title'] == [
            {
                'type': 'schedule_title',
                'schedule_id': body['id'],
                'title': AI_TITLE,
            }
        ]
        r = await admin_client.get(f'/api/schedules/{body["id"]}')
        assert r.json()['title'] == AI_TITLE

    async def test_ai_title_uses_the_profile_the_fires_use(
        self, admin_client, install_fake_agent, fake_generate
    ):
        r = await _create(
            admin_client, description=LONG, ai_profile_id='pinned-x'
        )
        assert r.status_code == 201
        await _settle()
        assert fake_generate == [(LONG, 'pinned-x')]

    async def test_rename_before_ai_title_is_kept(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        gate = asyncio.Event()

        async def slow_gen(text, profile_id):
            await gate.wait()
            return AI_TITLE

        monkeypatch.setattr(task_title, 'generate_title', slow_gen)
        r = await _create(admin_client, description=LONG)
        sid = r.json()['id']
        r = await admin_client.put(
            f'/api/schedules/{sid}', json={'title': 'My own name'}
        )
        assert r.status_code == 200
        gate.set()
        await _settle()
        r = await admin_client.get(f'/api/schedules/{sid}')
        assert r.json()['title'] == 'My own name'

    async def test_short_prompt_is_its_own_title(
        self, admin_client, install_fake_agent, fake_generate
    ):
        r = await _create(admin_client, description='Check the ads')
        assert r.json()['title'] == 'Check the ads'
        await _settle()
        assert fake_generate == []

    async def test_explicit_title_is_untouched(
        self, admin_client, install_fake_agent, fake_generate
    ):
        r = await _create(admin_client, title='Explicit', description=LONG)
        assert r.json()['title'] == 'Explicit'
        await _settle()
        assert fake_generate == []

    async def test_neither_title_nor_prompt_is_rejected(
        self, admin_client, install_fake_agent
    ):
        r = await _create(admin_client, title=' ', description='')
        assert r.status_code == 422


class TestEdit:
    async def test_blank_title_rederives_from_the_edited_prompt(
        self, admin_client, install_fake_agent, fake_generate
    ):
        r = await _create(admin_client, title='Old', description='Old job')
        sid = r.json()['id']
        r = await admin_client.put(
            f'/api/schedules/{sid}', json={'title': '', 'description': LONG}
        )
        assert r.status_code == 200, r.text
        assert r.json()['title'] == PLACEHOLDER
        await _settle()
        assert [text for text, _ in fake_generate] == [LONG]
        r = await admin_client.get(f'/api/schedules/{sid}')
        assert r.json()['title'] == AI_TITLE
        assert r.json()['description'] == LONG

    async def test_prompt_edit_without_title_keeps_the_title(
        self, admin_client, install_fake_agent, fake_generate
    ):
        r = await _create(admin_client, title='Kept', description='Old job')
        sid = r.json()['id']
        r = await admin_client.put(
            f'/api/schedules/{sid}', json={'description': LONG}
        )
        assert r.json()['title'] == 'Kept'
        await _settle()
        assert fake_generate == []


class TestFiresUseTheRowTitle:
    async def test_store_fire_names_its_task_from_the_row(
        self, admin_client, install_fake_agent, fake_generate
    ):
        store = await admin_client.post('/api/stores', json={'name': 'S'})
        r = await _create(
            admin_client, description=LONG, store_id=store.json()['id']
        )
        sid = r.json()['id']
        await _settle()
        # APScheduler's kwargs still hold the placeholder it was
        # registered with; the fire must not use them.
        await cron._run_task_job(
            task_title=PLACEHOLDER,
            store_id=store.json()['id'],
            schedule_id=sid,
            description=LONG,
        )
        await self._assert_fired_title(sid)

    async def test_single_fire_names_its_task_from_the_row(
        self, admin_client, install_fake_agent, fake_generate
    ):
        r = await _create(admin_client, description=LONG, phase_mode='single')
        sid = r.json()['id']
        await _settle()
        await run_single_job(
            schedule_id=sid, task_title=PLACEHOLDER, description=LONG
        )
        await self._assert_fired_title(sid)

    @staticmethod
    async def _assert_fired_title(sid):
        async with _db.async_session() as db:
            assert (await db.get(Schedule, sid)).title == AI_TITLE
            fired = (
                (await db.execute(select(Task).where(Task.schedule_id == sid)))
                .scalars()
                .all()
            )
        assert [t.title for t in fired] == [AI_TITLE]
        assert fired[0].description == LONG
