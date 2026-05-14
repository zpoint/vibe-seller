"""Workflow tests for the task create → design → execute → complete lifecycle.

This is the most critical test file — it exercises auto_run_task, the core
pipeline that uses async_session (not get_db) to drive task state transitions.
"""

import pytest

from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _create_store(client, name='Lifecycle Test Store'):
    """Helper: create a store and return its id."""
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200
    return r.json()['id']


class TestTaskAutoRun:
    async def test_create_task_auto_runs_pipeline(
        self, admin_client, install_fake_agent
    ):
        """POST /api/tasks → background pipeline → completed."""
        store_id = await _create_store(admin_client)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Auto-run test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] is not None

    async def test_create_task_without_store(
        self, admin_client, install_fake_agent
    ):
        """Storeless task forces plan_mode → pauses at planned."""
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'No store task',
                'store_id': None,
            },
        )
        assert r.status_code == 200
        assert r.json()['plan_mode'] is True
        data = await wait_for_task(
            admin_client, r.json()['id'], target='planned'
        )
        assert data['status'] == 'planned'

    async def test_design_failure_marks_failed(
        self, admin_client, install_fake_agent
    ):
        """When FakeAgent fails design → task.status=failed."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True, fail_at_phase='design'
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Fail design',
                'plan_mode': True,
            },
        )
        assert r.status_code == 200
        data = await wait_for_task(
            admin_client,
            r.json()['id'],
            target='failed',
        )
        assert data['status'] == 'failed'
        assert data['error'] is not None

    async def test_execution_saves_result(
        self, admin_client, install_fake_agent
    ):
        store_id = await _create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Custom result text'
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Result test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        data = await wait_for_task(admin_client, r.json()['id'])
        assert data['status'] == 'completed'
        assert data['result'] == 'Custom result text'

    async def test_execution_saves_todos(
        self, admin_client, install_fake_agent
    ):
        store_id = await _create_store(admin_client)
        todos = [
            {'content': 'Step 1', 'status': 'completed'},
            {'content': 'Step 2', 'status': 'completed'},
        ]
        install_fake_agent.default_scenario = FakeAgentScenario(todos=todos)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Todos test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        data = await wait_for_task(admin_client, r.json()['id'])
        assert data['status'] == 'completed'
        assert data['todos'] is not None

    async def test_incomplete_todos_with_result_marks_completed(
        self, admin_client, install_fake_agent
    ):
        """Agent exits with incomplete todos but has result → COMPLETED.

        The result indicates the agent finished the task, even if it
        forgot to mark all todos as completed.
        """
        store_id = await _create_store(admin_client)
        todos = [
            {'content': 'Query emails', 'status': 'completed'},
            {'content': 'Confirm with user', 'status': 'in_progress'},
            {'content': 'Create tasks', 'status': 'pending'},
        ]
        install_fake_agent.default_scenario = FakeAgentScenario(
            todos=todos,
            result='Task finished successfully',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Incomplete todos with result',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        data = await wait_for_task(
            admin_client,
            r.json()['id'],
            target='completed',
        )
        assert data['status'] == 'completed'
        # Todos are preserved for observability
        assert data['todos'] is not None

    async def test_incomplete_todos_no_result_marks_waiting(
        self, admin_client, install_fake_agent
    ):
        """Agent exits with incomplete todos and no result → WAITING.

        No result indicates the agent was interrupted mid-task.
        """
        store_id = await _create_store(admin_client)
        todos = [
            {'content': 'Query emails', 'status': 'completed'},
            {'content': 'Confirm with user', 'status': 'in_progress'},
            {'content': 'Create tasks', 'status': 'pending'},
        ]
        install_fake_agent.default_scenario = FakeAgentScenario(
            todos=todos,
            result=None,
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Incomplete todos no result',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        data = await wait_for_task(
            admin_client,
            r.json()['id'],
            target='waiting',
        )
        assert data['status'] == 'waiting'
        assert data['wait_condition'] is not None

    async def test_all_todos_completed_marks_completed(
        self, admin_client, install_fake_agent
    ):
        """Agent exits with all todos completed → COMPLETED."""
        store_id = await _create_store(admin_client)
        todos = [
            {'content': 'Step 1', 'status': 'completed'},
            {'content': 'Step 2', 'status': 'completed'},
        ]
        install_fake_agent.default_scenario = FakeAgentScenario(
            todos=todos,
            result='All done',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Complete todos',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        data = await wait_for_task(admin_client, r.json()['id'])
        assert data['status'] == 'completed'

    async def test_plan_persisted_between_phases(
        self, admin_client, install_fake_agent
    ):
        """Single plan_then_execute session saves plan to DB."""
        plan = '## Custom Plan\n1. Do X\n2. Do Y'
        install_fake_agent.default_scenario = FakeAgentScenario(plan=plan)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Plan persist',
                'plan_mode': True,
            },
        )
        task_id = r.json()['id']
        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['plan'] == plan

        # Execute the plan
        await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'

        # Verify single plan_then_execute call was made
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        pte_calls = [c for c in run_calls if c.mode == 'plan_then_execute']
        assert len(pte_calls) == 1

    async def test_schedule_or_run_fallback_when_scheduler_down(
        self, admin_client, install_fake_agent
    ):
        """Tasks launch directly when queue scheduler is not running.

        The test fixtures don't start the full task queue scheduler,
        so this verifies the schedule_or_run() fallback path works.
        """
        store_id = await _create_store(admin_client)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Scheduler fallback test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        assert r.status_code == 200
        data = await wait_for_task(admin_client, r.json()['id'])
        assert data['status'] == 'completed'


class TestRetry:
    async def test_retry_clears_all_data(
        self, admin_client, install_fake_agent
    ):
        """POST retry → plan/result/todos/error cleared, pipeline restarts."""
        # First run: complete successfully (plan_mode → execute-plan)
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Retry test',
                'plan_mode': True,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='planned')
        await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['plan'] is not None

        # Create a new task that fails so we can test retry
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True, fail_at_phase='design'
        )
        r2 = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Retry fail test',
                'plan_mode': True,
            },
        )
        t2_id = r2.json()['id']
        data2 = await wait_for_task(admin_client, t2_id, target='failed')
        assert data2['status'] == 'failed'

        # Now retry with a good scenario (plan_mode → planned)
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='New plan', result='New result'
        )
        r3 = await admin_client.post(f'/api/tasks/{t2_id}/retry')
        assert r3.status_code == 200

        data3 = await wait_for_task(admin_client, t2_id, target='planned')
        assert data3['plan'] == 'New plan'
        await admin_client.post(f'/api/tasks/{t2_id}/execute-plan')
        data3 = await wait_for_task(admin_client, t2_id)
        assert data3['status'] == 'completed'
        assert data3['result'] == 'New result'
        assert data3['error'] is None


class TestTaskListFilters:
    async def test_task_list_filters_by_store(self, admin_client):
        # Create a store
        r = await admin_client.post(
            '/api/stores', json={'name': 'Filter Store'}
        )
        store_id = r.json()['id']

        # Create tasks with and without store
        await admin_client.post(
            '/api/tasks',
            json={'title': 'Store task', 'store_id': store_id},
        )
        await admin_client.post('/api/tasks', json={'title': 'No store task'})

        # Filter by store
        r = await admin_client.get(f'/api/tasks?store_id={store_id}')
        assert r.status_code == 200
        tasks = r.json()
        assert all(t['store_id'] == store_id for t in tasks)
        assert any(t['title'] == 'Store task' for t in tasks)

    async def test_task_list_none_store(self, admin_client):
        # Create storeless task
        await admin_client.post('/api/tasks', json={'title': 'Storeless'})
        r = await admin_client.get('/api/tasks?store_id=__none__')
        assert r.status_code == 200
        tasks = r.json()
        assert all(t['store_id'] is None for t in tasks)

    async def test_task_status_transitions_via_pipeline(
        self, admin_client, install_fake_agent
    ):
        """Verify task goes through designing → running → completed."""
        store_id = await _create_store(admin_client)
        install_fake_agent.default_scenario = FakeAgentScenario(
            complete_delay=0.01
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Transition test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']

        # Wait for completion
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'

        # Verify auto mode was used (explicit plan_mode=False)
        calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        modes = [c.mode for c in calls]
        assert 'auto' in modes


class TestStopRetryRace:
    """Regression: stop then immediate retry must complete."""

    async def test_stop_retry_completes(self, admin_client, install_fake_agent):
        """Stop a running task, retry immediately → completed.

        Regression for a race where the first pipeline's post-loop
        handler didn't detect the retry and set the task back to
        FAILED.
        """
        store_id = await _create_store(admin_client, 'StopRetry Store')
        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=2.0,  # slow enough to stop mid-execution
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Stop-retry race test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']

        # Wait for RUNNING
        data = await wait_for_task(
            admin_client, task_id, target='running', timeout=5
        )
        assert data['status'] == 'running'

        # Stop → task goes to FAILED
        r = await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        assert r.status_code == 200

        # Immediately retry with a fast scenario
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Retry succeeded',
        )
        r = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r.status_code == 200

        # Must complete (not stuck at failed).
        # 30s timeout because under self-hosted CI runner contention
        # the ~8-await dispatch chain in auto_run_task (db.get +
        # write_browser_config_for_store + get_store_emails +
        # build_system_extra + refresh_token_if_needed + agent.run
        # with _on_start → PENDING→RUNNING, then FakeAgent finishes)
        # can starve under event-loop pressure; locally it finishes
        # in <2s.
        data = await wait_for_task(
            admin_client, task_id, target='completed', timeout=30
        )
        assert data['status'] == 'completed', (
            f'Expected completed after retry, got {data["status"]}'
        )


class TestPlanModeFollowupSkipPlan:
    async def test_followup_skip_plan_reaches_completed(
        self, admin_client, install_fake_agent
    ):
        """Regression: follow-up that skips ExitPlanMode → COMPLETED.

        Full lifecycle:
        1. Plan-mode task → agent plans → approved → executes → COMPLETED
        2. Follow-up message → COMPLETED → DESIGNING → agent re-enters
        3. Agent writes result without ExitPlanMode (skip_plan_on_followup)
        4. Task must transition DESIGNING → COMPLETED (not stuck)
        """
        scenario = FakeAgentScenario(
            plan='## Phase 1 Plan\nAudit ads',
            result='Phase 1 result',
            skip_plan_on_followup=True,
        )
        install_fake_agent.default_scenario = scenario

        # Phase 1: create plan-mode task
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Follow-up skip test', 'plan_mode': True},
        )
        task_id = r.json()['id']

        # Wait for planned
        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['plan'] == scenario.plan

        # Execute plan → completed
        await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        data = await wait_for_task(admin_client, task_id, target='completed')
        assert data['status'] == 'completed'
        assert data['result'] == 'Phase 1 result'

        # Phase 2: send follow-up → agent skips plan → completed.
        # Use a distinct result to prove the follow-up path overwrote.
        scenario.result = 'Phase 2 execution result'
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'execute the plan'},
        )
        assert r.status_code == 200

        data = await wait_for_task(admin_client, task_id, target='completed')
        assert data['status'] == 'completed'
        assert data['result'] == 'Phase 2 execution result'
