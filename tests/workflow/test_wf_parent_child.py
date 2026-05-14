"""Workflow tests for parent-child task lifecycle.

Covers:
- Parent waits for children (WAITING with strategy=children)
- Parent auto-completes when all children are terminal
- Parent reopens (WAITING) when a completed child gets follow-up
- Parent reopens when a failed child is retried
- Mixed child statuses (some complete, some fail)
"""

import pytest

from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _create_store(client, name='Parent-Child Store'):
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200
    return r.json()['id']


async def _create_parent_and_execute(client):
    """Create a non-store parent task, confirm plan, wait for complete."""
    r = await client.post(
        '/api/tasks',
        json={'title': 'Parent task'},
    )
    assert r.status_code == 200
    parent_id = r.json()['id']
    # Non-store tasks force plan_mode=True → goes to PLANNED
    data = await wait_for_task(client, parent_id, target='planned', timeout=10)
    assert data['status'] == 'planned'
    # Execute the plan
    r = await client.post(f'/api/tasks/{parent_id}/execute-plan')
    assert r.status_code == 200
    # Wait for completion
    data = await wait_for_task(
        client, parent_id, target='completed', timeout=10
    )
    assert data['status'] == 'completed'
    return parent_id


class TestParentWaitsForChildren:
    """Parent stays WAITING until all children are terminal."""

    async def test_parent_waits_then_completes(
        self, admin_client, install_fake_agent
    ):
        """Parent completed → create children → parent WAITING →
        children complete → parent auto-completes."""
        store_id = await _create_store(admin_client)
        parent_id = await _create_parent_and_execute(admin_client)

        # Create two children linked to parent
        r1 = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Child 1',
                'store_id': store_id,
                'parent_task_id': parent_id,
                'plan_mode': False,
            },
        )
        child1_id = r1.json()['id']
        r2 = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Child 2',
                'store_id': store_id,
                'parent_task_id': parent_id,
                'plan_mode': False,
            },
        )
        child2_id = r2.json()['id']

        # Parent should revert to WAITING (children are non-terminal)
        resp = await admin_client.get(f'/api/tasks/{parent_id}')
        assert resp.json()['status'] == 'waiting'

        # Wait for children to complete
        await wait_for_task(admin_client, child1_id, timeout=10)
        await wait_for_task(admin_client, child2_id, timeout=10)

        # Parent should auto-complete with aggregated results
        parent = await wait_for_task(
            admin_client, parent_id, target='completed', timeout=10
        )
        assert parent['result'] is not None

    async def test_parent_waits_mixed_success_fail(
        self, admin_client, install_fake_agent
    ):
        """Parent completes when all children are terminal,
        even if some failed."""
        store_id = await _create_store(admin_client, 'Mixed Store')
        parent_id = await _create_parent_and_execute(admin_client)

        # Child 1 succeeds (default scenario) — wait for it first
        r1 = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Success child',
                'store_id': store_id,
                'parent_task_id': parent_id,
                'plan_mode': False,
            },
        )
        child1_id = r1.json()['id']
        await wait_for_task(admin_client, child1_id, timeout=10)

        # Now change scenario for Child 2 to fail
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True
        )
        r2 = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Fail child',
                'store_id': store_id,
                'parent_task_id': parent_id,
                'plan_mode': False,
            },
        )
        child2_id = r2.json()['id']
        await wait_for_task(
            admin_client, child2_id, target='failed', timeout=10
        )

        # Parent should complete (all children terminal)
        parent = await wait_for_task(
            admin_client, parent_id, target='completed', timeout=10
        )
        assert '\u2713' in parent['result']  # checkmark
        assert '\u2717' in parent['result']  # cross


class TestParentReopensOnChildResume:
    """Parent reverts to WAITING when a child resumes."""

    async def test_child_followup_reopens_parent(
        self, admin_client, install_fake_agent
    ):
        """Child completes → parent completes → child gets
        follow-up → parent goes back to WAITING."""
        store_id = await _create_store(admin_client, 'Followup Store')
        parent_id = await _create_parent_and_execute(admin_client)

        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Followup child',
                'store_id': store_id,
                'parent_task_id': parent_id,
                'plan_mode': False,
            },
        )
        child_id = r.json()['id']
        await wait_for_task(admin_client, child_id, timeout=10)

        # Parent should be completed
        resp = await admin_client.get(f'/api/tasks/{parent_id}')
        assert resp.json()['status'] == 'completed'

        # Send follow-up to child → child goes RUNNING
        await admin_client.post(
            f'/api/tasks/{child_id}/messages',
            json={'content': 'Do more work'},
        )

        # Parent should revert to WAITING
        resp = await admin_client.get(f'/api/tasks/{parent_id}')
        assert resp.json()['status'] == 'waiting'

        # Child completes again
        await wait_for_task(admin_client, child_id, timeout=10)

        # Parent should auto-complete again
        await wait_for_task(
            admin_client, parent_id, target='completed', timeout=10
        )

    async def test_child_retry_reopens_parent(
        self, admin_client, install_fake_agent
    ):
        """Child fails → parent completes → child retried →
        parent goes back to WAITING."""
        store_id = await _create_store(admin_client, 'Retry Store')
        parent_id = await _create_parent_and_execute(admin_client)

        # Child fails
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Retry child',
                'store_id': store_id,
                'parent_task_id': parent_id,
                'plan_mode': False,
            },
        )
        child_id = r.json()['id']
        await wait_for_task(admin_client, child_id, target='failed', timeout=10)

        # Parent completed (all children terminal)
        resp = await admin_client.get(f'/api/tasks/{parent_id}')
        assert resp.json()['status'] == 'completed'

        # Retry the failed child — now succeeds
        install_fake_agent.default_scenario = FakeAgentScenario()
        await admin_client.post(f'/api/tasks/{child_id}/retry')

        # Parent should revert to WAITING
        resp = await admin_client.get(f'/api/tasks/{parent_id}')
        assert resp.json()['status'] == 'waiting'

        # Child completes
        await wait_for_task(admin_client, child_id, timeout=10)

        # Parent auto-completes
        await wait_for_task(
            admin_client, parent_id, target='completed', timeout=10
        )
