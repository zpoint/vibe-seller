"""A wait that reaches its deadline stops on time, cleanly, once.

Observed on a parked question (``max_wait_days: 7``, the card counting
"7 days remaining" down to 0):

* the checker compared whole days (``(now - since).days > 7``), so the
  task stopped only on day 8, a day after the card said 0;
* its FAILED write skipped the parent re-check every other end of a run
  makes, so an orchestrator waiting on a timed-out sub-task waited
  forever (its children-wait has no clock of its own);
* it wrote FAILED over whatever the row had become since the checker
  read it: an answer that landed in between was thrown away.

Driven through the real routers, the FakeAgent park path (the product's
own bounded question wait) and the real checker, ``check_waiting_tasks``.
"""

from datetime import UTC, datetime, timedelta
import json

import pytest

from app.models.task import Task
from app.scheduler import waiting
from tests.workflow.fake_agent import FakeAgentScenario
from tests.workflow.test_wf_question_park import (
    _answer,
    _asking,
    _create,
    _park,
    _pending,
    _store,
    _task,
    _until,
    _wait_question,
    _wait_status,
)

pytestmark = pytest.mark.workflow

DAY_H = 24.0
ONE_MINUTE_H = 1 / 60


@pytest.fixture(autouse=True)
def short_question_timeout(monkeypatch):
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '0.3')


async def _edit(maker, task_id, **fields) -> None:
    async with maker() as db:
        task = await db.get(Task, task_id)
        for k, v in fields.items():
            setattr(task, k, v)
        await db.commit()


async def _waited(maker, task_id, hours: float) -> dict:
    """Make the row's wait have started *hours* ago; return its condition."""
    async with maker() as db:
        task = await db.get(Task, task_id)
        cond = json.loads(task.wait_condition)
        cond['waiting_since'] = (
            datetime.now(UTC) - timedelta(hours=hours)
        ).isoformat()
        task.wait_condition = json.dumps(cond)
        await db.commit()
    return cond


async def _snapshot(maker, task_id) -> Task:
    """The row as the checker's own query would have read it."""
    async with maker() as db:
        task = await db.get(Task, task_id)
        db.expunge(task)
    return task


async def _parent_waiting_on(client, maker, *child_ids) -> str:
    r = await client.post(
        '/api/tasks',
        json={
            'title': 'Orchestrate',
            'plan_mode': False,
            'defer_start': True,
        },
    )
    assert r.status_code == 200, r.text
    parent_id = r.json()['id']
    await _edit(
        maker,
        parent_id,
        status='waiting',
        wait_condition=json.dumps({'strategy': 'children'}),
    )
    for child_id in child_ids:
        await _edit(maker, child_id, parent_task_id=parent_id)
    return parent_id


class TestDeadline:
    async def test_parked_question_stops_on_the_day_the_card_shows(
        self, admin_client, install_fake_agent, override_async_session
    ):
        maker = override_async_session
        _, task_id, _ = await _park(admin_client, install_fake_agent)

        await _waited(maker, task_id, 7 * DAY_H - ONE_MINUTE_H)
        await waiting.check_waiting_tasks()
        still = await _task(admin_client, task_id)
        assert still['status'] == 'waiting', still

        await _waited(maker, task_id, 7 * DAY_H + ONE_MINUTE_H)
        await waiting.check_waiting_tasks()
        stopped = await _task(admin_client, task_id)
        assert stopped['status'] == 'failed', stopped
        assert stopped['error_category'] == 'wait_timeout'
        assert stopped['error'].startswith('Nobody replied within 7 days')
        assert 'Send a message below to continue' in stopped['error']
        # Nothing to answer any more: the question went with the wait.
        assert (await _pending(admin_client, task_id))['pending'] is False

    async def test_a_message_continues_the_stopped_task(
        self, admin_client, install_fake_agent, override_async_session
    ):
        maker = override_async_session
        fake = install_fake_agent
        _, task_id, _ = await _park(admin_client, fake)
        await _edit(maker, task_id, session_id='sess-parked')
        await _waited(maker, task_id, 7 * DAY_H + ONE_MINUTE_H)
        await waiting.check_waiting_tasks()
        assert (await _task(admin_client, task_id))['status'] == 'failed'

        fake.scenarios[task_id] = FakeAgentScenario(result='Shipped Express')
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages', json={'content': 'Express'}
        )
        assert r.status_code == 200, r.text
        done = await _wait_status(admin_client, task_id, 'completed')
        assert done['result'] == 'Shipped Express'
        assert done['error'] is None and done['error_category'] is None
        # Continued in the parked session, not started over.
        assert fake._sessions[task_id].resume_session_id == 'sess-parked'


class TestParentOfATimedOutChild:
    async def test_timed_out_subtask_completes_its_parent(
        self, admin_client, install_fake_agent, override_async_session
    ):
        maker = override_async_session
        _, child_id, _ = await _park(admin_client, install_fake_agent)
        parent_id = await _parent_waiting_on(admin_client, maker, child_id)
        await _waited(maker, child_id, 8 * DAY_H)

        await waiting.check_waiting_tasks()

        assert (await _task(admin_client, child_id))['status'] == 'failed'
        parent = await _task(admin_client, parent_id)
        assert parent['status'] == 'completed', parent
        assert parent['wait_condition'] is None

    async def test_parent_whose_children_ended_elsewhere_completes(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """The checker owns every WAITING row: a parent whose last child
        was ended by a path that skipped the parent re-check still
        completes at the next check, never waits forever."""
        maker = override_async_session
        _, child_id, _ = await _park(admin_client, install_fake_agent)
        parent_id = await _parent_waiting_on(admin_client, maker, child_id)
        await _edit(maker, child_id, status='failed', error='ended elsewhere')

        await waiting.check_waiting_tasks()

        parent = await _task(admin_client, parent_id)
        assert parent['status'] == 'completed', parent

    async def test_parent_with_a_child_still_waiting_keeps_waiting(
        self, admin_client, install_fake_agent, override_async_session
    ):
        maker = override_async_session
        _, child_id, _ = await _park(admin_client, install_fake_agent)
        parent_id = await _parent_waiting_on(admin_client, maker, child_id)

        await waiting.check_waiting_tasks()

        assert (await _task(admin_client, parent_id))['status'] == 'waiting'
        assert (await _task(admin_client, child_id))['status'] == 'waiting'


class TestWhatLandedSinceTheCheckRead:
    async def test_answer_after_the_read_is_not_overwritten(
        self, admin_client, install_fake_agent, override_async_session
    ):
        maker = override_async_session
        _, task_id, _ = await _park(admin_client, install_fake_agent)
        await _waited(maker, task_id, 8 * DAY_H)
        read = await _snapshot(maker, task_id)

        r = await _answer(admin_client, task_id)
        assert r.status_code == 200, r.text
        await _wait_status(admin_client, task_id, 'completed')

        await waiting._check_one_task(read)

        after = await _task(admin_client, task_id)
        assert after['status'] == 'completed', after
        assert after['result'] == 'Chose: Express'
        assert after['error'] is None

    async def test_a_new_wait_is_not_ended_by_the_old_deadline(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """Answered, resumed, asked again and parked again: the row is
        WAITING once more, but on a wait that started just now."""
        maker = override_async_session
        fake = install_fake_agent
        store_id = await _store(admin_client)
        task_id = await _create(
            admin_client,
            fake,
            store_id,
            'Ship ORD-1002',
            _asking(ask_again_after_answer=True),
        )
        await _wait_status(admin_client, task_id, 'waiting')
        await _waited(maker, task_id, 8 * DAY_H)
        read = await _snapshot(maker, task_id)

        assert (await _answer(admin_client, task_id)).status_code == 200

        async def parked_again():
            t = await _task(admin_client, task_id)
            return t['status'] == 'waiting' and t['wait_condition'] != (
                read.wait_condition
            )

        await _until(parked_again, timeout=10.0)

        await waiting._check_one_task(read)

        again = await _task(admin_client, task_id)
        assert again['status'] == 'waiting', again
        assert (await _pending(admin_client, task_id))['pending'] is True


class TestAttention:
    """GET /api/tasks/attention — what the top banner lists."""

    async def _items(self, client) -> list[dict]:
        r = await client.get('/api/tasks/attention')
        assert r.status_code == 200, r.text
        return r.json()['waiting_tasks']

    async def test_parked_question_is_listed_until_answered(
        self, admin_client, install_fake_agent, override_async_session
    ):
        maker = override_async_session
        _, task_id, _ = await _park(admin_client, install_fake_agent)
        cond = await _waited(maker, task_id, 2 * DAY_H)

        [item] = await self._items(admin_client)
        assert item['task_id'] == task_id
        assert item['kind'] == 'question'
        assert item['since'] == cond['waiting_since']
        deadline = datetime.fromisoformat(item['deadline'])
        assert deadline == datetime.fromisoformat(
            cond['waiting_since']
        ) + timedelta(days=7)

        assert (await _answer(admin_client, task_id)).status_code == 200
        await _wait_status(admin_client, task_id, 'completed')
        assert await self._items(admin_client) == []

    async def test_only_waits_a_person_ends_are_listed_oldest_first(
        self, admin_client, install_fake_agent, override_async_session
    ):
        maker = override_async_session
        fake = install_fake_agent
        store_id = await _store(admin_client)

        async def parked(title):
            task_id = await _create(
                admin_client, fake, store_id, title, _asking()
            )
            await _wait_status(admin_client, task_id, 'waiting')
            return task_id

        question_id = await parked('Question')
        await _waited(maker, question_id, 1.0)
        reply_id = await parked('Reply')
        email_id = await parked('Email')
        planner_id = await parked('Plan')
        parent_id = await _parent_waiting_on(admin_client, maker)
        now = datetime.now(UTC)
        await _edit(
            maker,
            reply_id,
            wait_condition=json.dumps({
                'reason': 'Agent exited with incomplete steps',
                'check_strategy': 'manual',
                'waiting_since': (now - timedelta(hours=3)).isoformat(),
                'max_wait_days': 30,
            }),
        )
        await _edit(
            maker,
            email_id,
            wait_condition=json.dumps({
                'reason': 'Waiting for the case reply',
                'check_strategy': 'email',
                'keywords': ['case'],
                'waiting_since': (now - timedelta(hours=5)).isoformat(),
                'max_wait_days': 30,
            }),
        )
        await _edit(maker, planner_id, is_plan_only=True)

        items = await self._items(admin_client)

        assert [(i['task_id'], i['kind']) for i in items] == [
            (reply_id, 'reply'),
            (question_id, 'question'),
        ]
        listed = {i['task_id'] for i in items}
        assert not listed & {email_id, planner_id, parent_id}

    async def test_every_kind_of_task_a_person_moves_is_listed(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """No-store or store, sub-task or not, auto or plan mode: each
        is listed by what it waits for; a parent waiting on its children
        and a scheduled fire queued as PLANNED are not."""
        maker = override_async_session
        fake = install_fake_agent
        store_id = await _store(admin_client)

        async def parked(title, store):
            task_id = await _create(
                admin_client, fake, store, title, _asking(), plan_mode=False
            )
            await _wait_status(admin_client, task_id, 'waiting')
            return task_id

        async def planned(title):
            task_id = await _create(
                admin_client,
                fake,
                store_id,
                title,
                FakeAgentScenario(plan='## Plan\n1. Ship', result='Shipped'),
                plan_mode=True,
            )
            await _wait_status(admin_client, task_id, 'planned')
            return task_id

        no_store = await parked('No-store asks', None)
        store_task = await parked('Store task asks', store_id)
        sub_task = await parked('Sub-task asks', store_id)
        parent_id = await _parent_waiting_on(admin_client, maker, sub_task)
        plan_task = await planned('Plan first')
        fire = await planned('Scheduled fire')
        r = await admin_client.post(
            '/api/schedules',
            json={
                'title': 'Nightly check',
                'description': 'Check the orders.',
                'store_id': store_id,
                'schedule_type': 'days',
                'schedule_time': '03:00',
                'plan_mode': False,
            },
        )
        assert r.status_code == 201, r.text
        await _edit(maker, fire, schedule_id=r.json()['id'])

        kinds = {
            i['task_id']: i['kind'] for i in await self._items(admin_client)
        }

        assert kinds == {
            no_store: 'question',
            store_task: 'question',
            sub_task: 'question',
            plan_task: 'plan',
        }
        assert parent_id not in kinds and fire not in kinds

        # Approving the plan takes it off the list.
        r = await admin_client.post(f'/api/tasks/{plan_task}/execute-plan')
        assert r.status_code == 200, r.text
        await _wait_status(admin_client, plan_task, 'completed')
        kinds = {
            i['task_id']: i['kind'] for i in await self._items(admin_client)
        }
        assert plan_task not in kinds

    async def test_live_question_is_listed_while_the_agent_waits(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '30')
        fake = install_fake_agent
        store_id = await _store(admin_client)
        task_id = await _create(
            admin_client, fake, store_id, 'Ship live', _asking()
        )
        await _wait_question(admin_client, task_id)
        items = await self._items(admin_client)
        assert [(i['task_id'], i['kind']) for i in items] == [
            (task_id, 'question')
        ]
        assert items[0]['deadline'] is None
        assert (await _answer(admin_client, task_id)).status_code == 200
        await _wait_status(admin_client, task_id, 'completed')
