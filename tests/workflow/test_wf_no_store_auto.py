"""A no-store task follows the SAME plan-mode rule as a store task.

Pinned here, in the flow the rule exists to serve (All-Stores
orchestration):

* mode resolution is uniform — explicit wins, else the user default;
* the footer Auto/Review switch works for a no-store task;
* a sub-task must have a store_id, and a sub-task cannot spawn —
  depth is capped at 1, so recursion is impossible by construction;
* a sub-task with no explicit plan_mode runs Auto — the parent's mode
  already decided review.
"""

import asyncio

import pytest

from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _store(admin_client, name):
    r = await admin_client.post('/api/stores', json={'name': name})
    assert r.status_code == 200, r.text
    return r.json()['id']


async def _set_default(admin_client, value: bool) -> None:
    """Flip the user's DEFAULT execution mode (Settings → General)."""
    r = await admin_client.patch(
        '/api/auth/me/profile', json={'plan_mode_default': value}
    )
    assert r.status_code == 200, r.text


async def _task_ids(admin_client):
    tasks = (await admin_client.get('/api/tasks')).json()
    return {t['id'] for t in tasks}


async def _create(admin_client, body):
    r = await admin_client.post('/api/tasks', json=body)
    return r


class TestNoStoreFollowsUserDefault:
    async def test_auto_default_no_store_runs_to_completion(
        self, admin_client, install_fake_agent
    ):
        """W1: no plan_mode + Auto default → Auto, never a plan phase."""
        assert (await admin_client.get('/api/auth/me')).json()[
            'plan_mode_default'
        ] is False
        r = await _create(admin_client, {'title': 'All-stores auto'})
        assert r.status_code == 200, r.text
        task_id = r.json()['id']
        assert r.json()['plan_mode'] is False

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['plan'] is None
        # The agent was launched in auto mode and ONLY in auto mode —
        # no design/plan phase ran at all.
        runs = install_fake_agent.get_calls(task_id, action='run')
        assert [c.mode for c in runs] == ['auto']

    async def test_review_default_no_store_parks_at_planned(
        self, admin_client, install_fake_agent
    ):
        """W2a: no plan_mode + Review default → plan mode, parks."""
        await _set_default(admin_client, True)
        r = await _create(admin_client, {'title': 'All-stores review'})
        assert r.status_code == 200, r.text
        assert r.json()['plan_mode'] is True
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['status'] == 'planned'

    async def test_explicit_auto_overrides_review_default(
        self, admin_client, install_fake_agent
    ):
        """W2b: explicit plan_mode=false wins over a Review default."""
        await _set_default(admin_client, True)
        r = await _create(
            admin_client,
            {'title': 'All-stores explicit auto', 'plan_mode': False},
        )
        assert r.status_code == 200, r.text
        assert r.json()['plan_mode'] is False
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'

    async def test_explicit_plan_overrides_auto_default(
        self, admin_client, install_fake_agent
    ):
        """W2c: explicit plan_mode=true wins over an Auto default."""
        r = await _create(
            admin_client,
            {'title': 'All-stores explicit plan', 'plan_mode': True},
        )
        assert r.status_code == 200, r.text
        assert r.json()['plan_mode'] is True
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['status'] == 'planned'


class TestReviewPlanSwitchWorksForNoStore:
    async def test_toggle_off_plan_mode_on_no_store_task(
        self, admin_client, install_fake_agent
    ):
        """W3: the footer switch turns review OFF for a no-store task.

        Same endpoint, same behaviour as for a store task (which
        already toggles both ways): a planned no-store task can be
        switched to auto.
        """
        store_id = await _store(admin_client, 'Toggle parity store')
        r_store = await _create(
            admin_client,
            {
                'title': 'Store task to plan',
                'store_id': store_id,
                'plan_mode': True,
            },
        )
        await wait_for_task(admin_client, r_store.json()['id'], 'planned')
        r2 = await admin_client.patch(
            f'/api/tasks/{r_store.json()["id"]}/review-plan',
            json={'plan_mode': False},
        )
        assert r2.status_code == 200, r2.text  # the parity baseline

        r = await _create(
            admin_client, {'title': 'Planned all-stores', 'plan_mode': True}
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='planned')

        r3 = await admin_client.patch(
            f'/api/tasks/{task_id}/review-plan', json={'plan_mode': False}
        )
        assert r3.status_code == 200, r3.text
        assert r3.json()['plan_mode'] is False
        got = await admin_client.get(f'/api/tasks/{task_id}')
        assert got.json()['plan_mode'] is False


class TestOrchestratorFlow:
    async def test_auto_parent_with_auto_children(
        self, admin_client, install_fake_agent
    ):
        """W4: no-store parent + two store children, user default Review.

        A parent created Auto has already had its review decided by
        the user, so the children it spawns run Auto with NO explicit
        plan_mode — even though the same user defaults new tasks to
        Review. The parent goes WAITING while children run and
        auto-completes with the aggregated result.
        """
        await _set_default(admin_client, True)
        store_a = await _store(admin_client, 'store-a')
        store_b = await _store(admin_client, 'store-b')

        r = await _create(
            admin_client, {'title': 'Split per store', 'plan_mode': False}
        )
        parent_id = r.json()['id']
        assert r.json()['plan_mode'] is False
        await wait_for_task(admin_client, parent_id)

        # Hold both children at the gate so the WAITING observation
        # cannot race the children's completion.
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(gate=gate)

        before = await _task_ids(admin_client)
        children = []
        for store_id in (store_a, store_b):
            rc = await _create(
                admin_client,
                {
                    'title': 'store work',
                    'store_id': store_id,
                    'parent_task_id': parent_id,
                },
            )
            assert rc.status_code == 200, rc.text
            # No explicit plan_mode: the sub-task runs Auto by rule.
            assert rc.json()['plan_mode'] is False
            children.append(rc.json()['id'])
        assert len(await _task_ids(admin_client) - before) == 2

        resp = await admin_client.get(f'/api/tasks/{parent_id}')
        assert resp.json()['status'] == 'waiting'

        gate.set()
        for child_id in children:
            await wait_for_task(admin_client, child_id)

        parent = await wait_for_task(admin_client, parent_id)
        assert parent['status'] == 'completed'
        assert '✓' in (parent['result'] or '')
        # Children really ran the work, in auto mode.
        for child_id in children:
            runs = install_fake_agent.get_calls(child_id, action='run')
            assert [c.mode for c in runs] == ['auto']


class TestSubtaskSpawnRefusals:
    async def test_no_store_subtask_refused(self, admin_client):
        """W5: a sub-task without a store_id is refused, no row created.

        The orchestrator prompt says every sub-task MUST have a
        store_id; the ACTION now refuses it, so a model that ignores
        the prose cannot park an unrunnable child.
        """
        r = await _create(
            admin_client,
            {'title': 'top', 'plan_mode': False, 'defer_start': True},
        )
        assert r.status_code == 200, r.text
        parent_id = r.json()['id']

        before = await _task_ids(admin_client)
        bad = await _create(
            admin_client,
            {
                'title': 'child without store',
                'parent_task_id': parent_id,
                'plan_mode': False,
                'defer_start': True,
            },
        )
        assert bad.status_code == 400, bad.text
        detail = bad.json()['detail']
        # Actionable: names the way out, not just the rule.
        assert 'store_id' in detail
        assert 'inside' in detail.lower() or 'itself' in detail.lower()
        assert await _task_ids(admin_client) == before

    async def test_grandchild_refused(self, admin_client):
        """W6: a sub-task cannot spawn; depth is at most 1."""
        store_id = await _store(admin_client, 'depth store')
        parent = (
            await _create(
                admin_client,
                {'title': 'top', 'plan_mode': False, 'defer_start': True},
            )
        ).json()['id']
        child = (
            await _create(
                admin_client,
                {
                    'title': 'child',
                    'store_id': store_id,
                    'parent_task_id': parent,
                    'plan_mode': False,
                    'defer_start': True,
                },
            )
        ).json()['id']

        before = await _task_ids(admin_client)
        bad = await _create(
            admin_client,
            {
                'title': 'grandchild',
                'store_id': store_id,
                'parent_task_id': child,
                'plan_mode': False,
                'defer_start': True,
            },
        )
        assert bad.status_code == 400, bad.text
        assert 'sub-task' in bad.json()['detail']
        assert await _task_ids(admin_client) == before

    async def test_top_level_store_task_still_spawnable(self, admin_client):
        """The refusals stay narrow: a legit sub-task is untouched."""
        store_id = await _store(admin_client, 'legit spawn store')
        parent = (
            await _create(
                admin_client,
                {'title': 'top', 'plan_mode': False, 'defer_start': True},
            )
        ).json()['id']
        r = await _create(
            admin_client,
            {
                'title': 'legit child',
                'store_id': store_id,
                'parent_task_id': parent,
                'defer_start': True,
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()['plan_mode'] is False
