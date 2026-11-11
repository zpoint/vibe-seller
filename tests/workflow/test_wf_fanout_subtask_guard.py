"""A fanout schedule's task cannot spawn sub-tasks — refused on the ACTION.

The invariant: ``phase_mode='fanout'`` schedules already create one
per-store Task per fire, so a task from one that spawns its own children
spawns them once per store, per fire, recursively.

That used to be enforced by scanning the PLAN TEXT for the strings
``vibe_seller_create_task`` and ``parent_task_id`` at ExitPlanMode — which
refused the plan that obeys, because the prompt instructs the agent not to
call those tools and the plan quoting that instruction contains both
strings. It also had no round limit (a shape guess has nothing to name as
"still missing"), so a planning session could be denied forever.

Now the plan may say anything, and the sub-task itself is what POST
/api/tasks refuses. These tests pin both halves: the obligation still
holds, and wording is no longer evidence.
"""

import uuid

import pytest

from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


def _schedule_payload(**overrides):
    body = {
        'title': 'Periodic check',
        'schedule_type': 'days',
        'schedule_time': '09:00',
        'interval_value': 1,
    }
    body.update(overrides)
    return body


async def _store(admin_client, name):
    r = await admin_client.post('/api/stores', json={'name': name})
    assert r.status_code == 200, r.text
    return r.json()['id']


async def _scheduled_task(admin_client, store_id, schedule_id, title='fire'):
    """A task bound to ``schedule_id``, created but NOT started.

    ``defer_start`` keeps the agent out of it: the rule under test is
    about which rows may be created, not about what a running agent does.
    """
    r = await admin_client.post(
        '/api/tasks',
        json={
            'title': title,
            'store_id': store_id,
            'schedule_id': schedule_id,
            'plan_mode': False,
            'defer_start': True,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()['id']


async def _schedule(admin_client, name, **overrides):
    r = await admin_client.post(
        '/api/schedules', json=_schedule_payload(**overrides)
    )
    assert r.status_code == 201, r.text
    return r.json()['id']


class TestFanoutChildCannotSpawn:
    async def test_subtask_under_a_fanout_task_is_refused(self, admin_client):
        """THE OBLIGATION, still enforced."""
        store_id = await _store(admin_client, 'Fanout Guard Store')
        sched_id = await _schedule(admin_client, 'fanout')
        fire_id = await _scheduled_task(admin_client, store_id, sched_id)

        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'spawned child',
                'store_id': store_id,
                'parent_task_id': fire_id,
                'plan_mode': False,
                'defer_start': True,
            },
        )
        assert r.status_code == 400, r.text
        detail = r.json()['detail']
        assert 'fanout' in detail.lower()
        # The refusal names the way out, not just the rule.
        assert 'Do the store-bound work inside this task' in detail

        # And no row was created for the refused spawn.
        children = (
            await admin_client.get(f'/api/tasks?parent_task_id={fire_id}')
        ).json()
        assert children == []

    async def test_subtask_under_a_single_mode_task_is_allowed(
        self, admin_client
    ):
        """Scope: only the fanout shape recurses. A ``single`` schedule
        fires exactly one task, so orchestrating from it stays legal."""
        store_id = await _store(admin_client, 'Single Mode Store')
        sched_id = await _schedule(admin_client, 'single', phase_mode='single')
        fire_id = await _scheduled_task(admin_client, store_id, sched_id)

        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'legit child',
                'store_id': store_id,
                'parent_task_id': fire_id,
                'plan_mode': False,
                'defer_start': True,
            },
        )
        assert r.status_code == 200, r.text

    async def test_subtask_under_an_interactive_task_is_allowed(
        self, admin_client
    ):
        """The documented orchestrator flow (a no-store task delegating
        per-store work to sub-tasks) is untouched."""
        store_id = await _store(admin_client, 'Orchestrator Store')
        r = await admin_client.post(
            '/api/tasks', json={'title': 'orchestrator'}
        )
        assert r.status_code == 200, r.text
        parent_id = r.json()['id']

        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'per-store child',
                'store_id': store_id,
                'parent_task_id': parent_id,
                'plan_mode': False,
                'defer_start': True,
            },
        )
        assert r.status_code == 200, r.text


class TestParentIdIsWhatIsChecked:
    async def test_unknown_parent_is_refused(self, admin_client):
        """``parent_task_id`` is agent-supplied and used to be trusted:
        a typo silently orphaned the child and ``reopen_parent_if_child_active``
        quietly did nothing."""
        store_id = await _store(admin_client, 'Bogus Parent Store')
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'orphan',
                'store_id': store_id,
                'parent_task_id': str(uuid.uuid4()),
                'plan_mode': False,
                'defer_start': True,
            },
        )
        assert r.status_code == 400, r.text
        assert 'names no task' in r.json()['detail']

    async def test_words_in_the_task_title_are_not_evidence(
        self, admin_client, install_fake_agent
    ):
        """Naming the forbidden tool in prose changes nothing here.

        The refused spawn above was refused because its PARENT is a
        fanout fire; a task whose title says
        ``don't call vibe_seller_create_task`` (the prompt's own wording)
        is created like any other.
        """
        install_fake_agent.default_scenario = FakeAgentScenario(result='ok')
        store_id = await _store(admin_client, 'Prose Store')
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Do NOT call vibe_seller_create_task; no '
                'parent_task_id',
                'store_id': store_id,
                'plan_mode': False,
            },
        )
        assert r.status_code == 200, r.text
