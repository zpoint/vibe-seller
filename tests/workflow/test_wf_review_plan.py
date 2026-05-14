"""Workflow tests for plan_mode toggle and execute-plan endpoint.

Covers:
- Task pauses at 'planned' when plan_mode=True (interactive)
- execute-plan endpoint triggers execution from 'planned'
- plan_mode toggle endpoint updates task and user default
- User default propagation to new tasks
"""

import pytest

from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


class TestPlanModePause:
    async def test_plan_mode_pauses_at_planned(
        self, admin_client, install_fake_agent
    ):
        """Task with plan_mode=True stops after design."""
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Review me', 'plan_mode': True},
        )
        assert r.status_code == 200
        task_id = r.json()['id']
        assert r.json()['plan_mode'] is True

        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['status'] == 'planned'
        assert data['plan'] is not None
        assert data['result'] is None

    async def test_auto_mode_runs_to_completion(
        self, admin_client, install_fake_agent
    ):
        """Store task with plan_mode=False goes straight to completed."""
        store_r = await admin_client.post(
            '/api/stores', json={'name': 'Auto Store'}
        )
        assert store_r.status_code == 200
        store_id = store_r.json()['id']

        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'No review',
                'store_id': store_id,
                'plan_mode': False,
            },
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] is not None


class TestExecutePlan:
    async def test_execute_plan_from_planned(
        self, admin_client, install_fake_agent
    ):
        """POST execute-plan on a 'planned' task runs execution."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Do stuff',
            result='Done!',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Execute after review',
                'plan_mode': True,
            },
        )
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['status'] == 'planned'

        r2 = await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        assert r2.status_code == 200

        data2 = await wait_for_task(admin_client, task_id)
        assert data2['status'] == 'completed'
        assert data2['result'] == 'Done!'

    async def test_execute_plan_rejects_non_planned(
        self, admin_client, install_fake_agent
    ):
        """Cannot execute-plan on a task not in 'planned' status."""
        store_r = await admin_client.post(
            '/api/stores', json={'name': 'Reject Store'}
        )
        assert store_r.status_code == 200
        store_id = store_r.json()['id']

        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Will complete',
                'store_id': store_id,
                'plan_mode': False,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        r2 = await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        assert r2.status_code == 400

    async def test_execute_plan_not_found(self, admin_client):
        r = await admin_client.post('/api/tasks/nonexistent-id/execute-plan')
        assert r.status_code == 404


class TestPlanModeToggle:
    async def test_toggle_plan_mode(self, admin_client, install_fake_agent):
        """PATCH review-plan toggles plan_mode on a store task."""
        store_r = await admin_client.post(
            '/api/stores', json={'name': 'Toggle Store'}
        )
        assert store_r.status_code == 200
        store_id = store_r.json()['id']

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Toggle test', 'store_id': store_id},
        )
        task_id = r.json()['id']

        # Toggle plan_mode on
        r2 = await admin_client.patch(
            f'/api/tasks/{task_id}/review-plan',
            json={'plan_mode': True},
        )
        assert r2.status_code == 200
        assert r2.json()['plan_mode'] is True

        # Toggle plan_mode off
        r3 = await admin_client.patch(
            f'/api/tasks/{task_id}/review-plan',
            json={'plan_mode': False},
        )
        assert r3.status_code == 200
        assert r3.json()['plan_mode'] is False

    async def test_toggle_updates_user_default(
        self, admin_client, install_fake_agent
    ):
        """Toggling plan_mode also updates user's default."""
        store_r = await admin_client.post(
            '/api/stores', json={'name': 'Default Store'}
        )
        assert store_r.status_code == 200
        store_id = store_r.json()['id']

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Default test', 'store_id': store_id},
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        await admin_client.patch(
            f'/api/tasks/{task_id}/review-plan',
            json={'plan_mode': True},
        )
        me = await admin_client.get('/api/auth/me')
        assert me.json()['plan_mode_default'] is True

    async def test_user_default_propagates_to_new_task(
        self, admin_client, install_fake_agent
    ):
        """New store tasks inherit plan_mode from user default."""
        store_r = await admin_client.post(
            '/api/stores', json={'name': 'Propagate Store'}
        )
        assert store_r.status_code == 200
        store_id = store_r.json()['id']

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Seed task', 'store_id': store_id},
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        await admin_client.patch(
            f'/api/tasks/{task_id}/review-plan',
            json={'plan_mode': True},
        )

        r2 = await admin_client.post(
            '/api/tasks',
            json={'title': 'Inherits default', 'store_id': store_id},
        )
        assert r2.json()['plan_mode'] is True


class TestNonStoreTaskPlanModeDefault:
    async def test_non_store_task_defaults_to_plan_mode(
        self, admin_client, install_fake_agent
    ):
        """Non-store tasks force plan_mode=True."""
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'No store plan mode', 'store_id': None},
        )
        assert r.status_code == 200
        assert r.json()['plan_mode'] is True
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['status'] == 'planned'

    async def test_store_task_defaults_to_user_preference(
        self, admin_client, install_fake_agent
    ):
        """Store tasks inherit user default (plan_mode=False for admin)."""
        store_resp = await admin_client.post(
            '/api/stores', json={'name': 'Plan Mode Store'}
        )
        assert store_resp.status_code == 200
        store_id = store_resp.json()['id']

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Store auto mode', 'store_id': store_id},
        )
        assert r.status_code == 200
        assert r.json()['plan_mode'] is False
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'

    async def test_explicit_plan_mode_false_on_non_store_still_forces_plan(
        self, admin_client, install_fake_agent
    ):
        """Non-store tasks always force plan_mode=True."""
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Explicit auto no store',
                'store_id': None,
                'plan_mode': False,
            },
        )
        assert r.status_code == 200
        assert r.json()['plan_mode'] is True
