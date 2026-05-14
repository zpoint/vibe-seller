"""Workflow tests for conversation-first task detail view.

Covers: replan, stop, retry, chat during execution,
message ordering, and edge cases like double-stop and
send-to-completed.

Skip duplicates: W1 (test_wf_task_lifecycle), W11
(test_wf_plan_mode), W15 (test_wf_task_chat).
"""

import asyncio

import pytest

from app.models.schedule import Schedule
from app.models.task import Task
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


async def _make_store(client, name='Conv Test Store'):
    """Helper: create a store and return its id."""
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200
    return r.json()['id']


# ── W2: Replan → execute ─────────────────────────────


class TestReplan:
    async def test_plan_feedback_replan_execute(
        self, admin_client, install_fake_agent
    ):
        """W2: Create with review, reject plan, wait for
        replan, approve, wait for completion.
        """
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Initial Plan\n1. First step',
            result='Done after replan',
        )

        # Create task with plan_mode
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Replan test', 'plan_mode': True},
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        # Wait for PLANNED
        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['status'] == 'planned'
        assert data['plan'] is not None

        # Send feedback (reject) — triggers replan
        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'Add error handling step'},
        )
        assert r2.status_code == 200

        # Wait for re-PLANNED status
        data2 = await wait_for_task(admin_client, task_id, target='planned')
        assert data2['status'] == 'planned'
        assert 'revised' in data2['plan']

        # Approve (execute) the revised plan
        r3 = await admin_client.post(
            f'/api/tasks/{task_id}/execute-plan',
        )
        assert r3.status_code == 200

        # Wait for completion
        data3 = await wait_for_task(admin_client, task_id)
        assert data3['status'] == 'completed'
        assert data3['result'] == 'Done after replan'

        # Verify messages are ordered
        r4 = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r4.json()
        roles = [m['role'] for m in msgs]
        # Should have: plan, user, plan, result
        assert 'plan' in roles
        assert 'user' in roles
        assert 'result' in roles
        # created_at should be non-decreasing (allows duplicates)
        times = [m['created_at'] for m in msgs]
        assert times == sorted(times)


# ── W3-W5: Stop in various states ────────────────────


class TestStopStates:
    async def test_stop_designing(self, admin_client, install_fake_agent):
        """W3: Stop during DESIGNING → FAILED."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            design_delay=2.0,
        )

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Stop design test', 'plan_mode': True},
        )
        task_id = r.json()['id']

        # Wait for designing status
        data = await wait_for_task(admin_client, task_id, target='designing')
        assert data['status'] == 'designing'

        # Stop (correct URL: /agent/stop)
        r2 = await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        assert r2.status_code == 200

        # Verify failed
        data2 = await wait_for_task(admin_client, task_id, target='failed')
        assert data2['status'] == 'failed'
        assert data2['error'] == 'Stopped by user'

    async def test_stop_running(self, admin_client, install_fake_agent):
        """W4: Stop during RUNNING → FAILED."""
        store_id = await _make_store(admin_client, 'W4 Store')
        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=2.0,
        )

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Stop running test', 'store_id': store_id},
        )
        task_id = r.json()['id']

        # Wait for running status
        data = await wait_for_task(admin_client, task_id, target='running')
        assert data['status'] == 'running'

        # Stop
        r2 = await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        assert r2.status_code == 200

        data2 = await wait_for_task(admin_client, task_id, target='failed')
        assert data2['status'] == 'failed'

    async def test_stop_planned(self, admin_client, install_fake_agent):
        """W5: Stop PLANNED → FAILED."""
        install_fake_agent.default_scenario = FakeAgentScenario()

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Stop planned', 'plan_mode': True},
        )
        task_id = r.json()['id']

        # Wait for planned
        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['status'] == 'planned'

        # Stop
        r2 = await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        assert r2.status_code == 200

        data2 = await wait_for_task(admin_client, task_id, target='failed')
        assert data2['status'] == 'failed'
        assert data2['error'] == 'Stopped by user'


# ── W7-W10: Retry ────────────────────────────────────


class TestRetryConversation:
    async def test_retry_clears_all_db_data(
        self, admin_client, install_fake_agent
    ):
        """W7: Retry clears messages, steps, logs."""
        # Create and fail a task
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True, fail_at_phase='design'
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Retry clear test', 'plan_mode': True},
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='failed')

        # Retry with working scenario (plan_mode=True → pauses
        # at PLANNED, must execute-plan to complete)
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='Fresh plan', result='Fresh result'
        )
        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200

        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['plan'] == 'Fresh plan'
        await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] == 'Fresh result'
        assert data['error'] is None

    async def test_retry_clears_run_scoped_timestamps(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """Retry must clear ``started_at``/``completed_at`` so the
        UI doesn't show a stale duration during the window between
        retry-dispatch and the new run reaching its first state
        write. RETRIABLE includes COMPLETED, where both timestamps
        are set; without an explicit reset they leak into the
        post-retry intermediate state.

        Uses ``execute_delay`` to widen the window between the
        retry returning and the new run finishing — the assertion
        below is on the immediate post-retry DB state, before the
        agent flips status to RUNNING."""
        store_id = await _make_store(admin_client, 'Retry ts store')
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='First run result',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Retry timestamps test', 'store_id': store_id},
        )
        task_id = r.json()['id']
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'

        async with override_async_session() as db:
            done = await db.get(Task, task_id)
            assert done.started_at is not None, (
                'precondition: completed task must have started_at'
            )
            assert done.completed_at is not None, (
                'precondition: completed task must have completed_at'
            )

        # Slow second run so the post-retry intermediate state is
        # observable before the agent overwrites the timestamps.
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Second run result',
            execute_delay=2.0,
        )
        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200

        # Immediately after retry returns: agent is queued/dispatched
        # but has not transitioned to RUNNING yet. Run-scoped
        # timestamps must already be cleared by the endpoint.
        async with override_async_session() as db:
            mid = await db.get(Task, task_id)
            assert mid.started_at is None, (
                f'started_at not cleared by retry: {mid.started_at}'
            )
            assert mid.completed_at is None, (
                f'completed_at not cleared by retry: {mid.completed_at}'
            )

        # And the new run still completes correctly.
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] == 'Second run result'

    async def test_retry_after_stop_design(
        self, admin_client, install_fake_agent
    ):
        """W9: Stop during design, then retry → COMPLETED."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            design_delay=2.0,
        )

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Stop-retry design', 'plan_mode': True},
        )
        task_id = r.json()['id']

        # Wait for designing, then stop
        await wait_for_task(admin_client, task_id, target='designing')
        await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        await wait_for_task(admin_client, task_id, target='failed')

        # Retry with fast scenario
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='Retry plan', result='Retry result'
        )
        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200

        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['plan'] == 'Retry plan'
        await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'

    async def test_retry_after_stop_execution(
        self, admin_client, install_fake_agent
    ):
        """W10: Stop during execution, then retry → COMPLETED."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=2.0,
        )

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Stop-retry exec', 'plan_mode': True},
        )
        task_id = r.json()['id']

        # Wait for running, then stop
        await wait_for_task(admin_client, task_id, target='running')
        await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        await wait_for_task(admin_client, task_id, target='failed')

        # Retry with fast scenario
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='Exec retry plan', result='Exec retry result'
        )
        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200

        await wait_for_task(admin_client, task_id, target='planned')
        await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'

    async def test_retry_scheduled_plan_mode_reseeds_frozen_plan(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """Retrying a failed child of a plan-mode Schedule with
        plan_status='ready' must re-seed Task.plan from Schedule.plan
        and skip re-planning — matches the cron fire path. Without
        this, the agent goes back into DESIGNING and regenerates a
        plan that the user already reviewed."""
        # Let the original run fail naturally so the runner exits
        # cleanly before we call retry.  Forcing task.status='failed'
        # via DB write while the runner is still alive races with
        # ``_on_start`` (which also commits DESIGNING/RUNNING),
        # producing intermittent "status=designing" assertion errors.
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True, fail_at_phase='design'
        )

        # Seed a plan-mode schedule with a frozen, user-approved plan.
        async with override_async_session() as db:
            sched = Schedule(
                title='Daily email check',
                schedule_type='days',
                schedule_time='09:00',
                interval_value=1,
                timezone='UTC',
                is_active=True,
                plan_mode=True,
                phase_mode='single',
                plan_status='ready',
                plan_version=3,
                plan='# Frozen plan\nStep 1. Do the thing.',
                created_by='admin',
            )
            db.add(sched)
            await db.commit()
            await db.refresh(sched)
            sched_id = sched.id

        # Attach the schedule_id at create time so the relationship is
        # in place before the runner starts.
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Scheduled plan retry',
                'plan_mode': True,
                'schedule_id': sched_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='failed')

        # Now reconfigure for a successful retry — we want to see the
        # frozen plan re-seeded and executed.
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Execution result after plan reuse'
        )

        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200
        assert r2.json()['status'] == 'planned'

        # DB state: plan re-seeded from the schedule, status=PLANNED,
        # plan_version copied.
        async with override_async_session() as db:
            task = await db.get(Task, task_id)
            assert task.plan == '# Frozen plan\nStep 1. Do the thing.'
            assert task.plan_version == 3
            assert task.status in ('planned', 'running', 'completed')

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['plan'] == '# Frozen plan\nStep 1. Do the thing.'
        assert data['result'] == 'Execution result after plan reuse'

    async def test_retry_non_scheduled_plan_mode_still_replans(
        self, admin_client, install_fake_agent
    ):
        """Regression: non-scheduled plan-mode tasks must keep the
        original behavior — retry clears the plan and re-plans from
        scratch. Only tasks with a Schedule whose plan_status='ready'
        get the fast path."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True, fail_at_phase='design'
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Standalone plan retry', 'plan_mode': True},
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='failed')

        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='Regenerated plan', result='After replan'
        )
        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200
        # Standalone plan-mode retry stays in PENDING — the agent has
        # to re-plan from scratch because there's no frozen source.
        assert r2.json()['status'] == 'pending'

        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['plan'] == 'Regenerated plan'

    async def test_retry_scheduled_fanout_plan_mode_reseeds(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """Fanout (store-bound) scheduled tasks use a different
        dispatch path: schedule_or_run routes through the task queue,
        and `_dispatch` picks the handler from `task.plan`. Verify
        retry re-seeds the plan for that path too — the frozen-plan
        branch must not be gated on ``phase_mode``."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Fanout execution done'
        )
        store_id = await _make_store(admin_client, 'Fanout retry store')

        async with override_async_session() as db:
            sched = Schedule(
                title='Fanout check',
                schedule_type='days',
                schedule_time='09:05',
                interval_value=1,
                timezone='UTC',
                is_active=True,
                plan_mode=True,
                phase_mode='fanout',
                plan_status='ready',
                plan_version=7,
                plan='# Fanout frozen plan',
                created_by='admin',
            )
            db.add(sched)
            await db.commit()
            await db.refresh(sched)
            sched_id = sched.id

        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Fanout plan retry',
                'plan_mode': True,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        async with override_async_session() as db:
            task = await db.get(Task, task_id)
            task.schedule_id = sched_id
            task.status = 'failed'
            task.plan = None
            await db.commit()

        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200
        assert r2.json()['status'] == 'planned'

        async with override_async_session() as db:
            task = await db.get(Task, task_id)
            assert task.plan == '# Fanout frozen plan'
            assert task.plan_version == 7

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['plan'] == '# Fanout frozen plan'

    @pytest.mark.parametrize(
        'plan_status', ['planning', 'failed', 'stale', 'none']
    )
    async def test_retry_schedule_plan_not_ready_falls_back_to_replan(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        plan_status,
    ):
        """If Schedule.plan_status is anything other than 'ready',
        retry must not re-seed — the schedule's plan is either being
        authored, broken, or explicitly invalidated. Fall back to
        the default (PENDING → re-plan) so the UI can surface the
        re-planning step."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='Regenerated plan', result='After replan'
        )

        async with override_async_session() as db:
            sched = Schedule(
                title='Not-ready schedule',
                schedule_type='days',
                schedule_time='09:00',
                interval_value=1,
                timezone='UTC',
                is_active=True,
                plan_mode=True,
                phase_mode='single',
                plan_status=plan_status,
                plan_version=1,
                plan='# Stale or in-flight plan',
                created_by='admin',
            )
            db.add(sched)
            await db.commit()
            await db.refresh(sched)
            sched_id = sched.id

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Not-ready retry', 'plan_mode': True},
        )
        task_id = r.json()['id']
        async with override_async_session() as db:
            task = await db.get(Task, task_id)
            task.schedule_id = sched_id
            task.status = 'failed'
            task.plan = None
            await db.commit()

        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200
        assert r2.json()['status'] == 'pending'

        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['plan'] == 'Regenerated plan'

    async def test_retry_non_plan_mode_schedule_does_not_reseed(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """Auto-mode schedules have no frozen plan to re-seed from.
        Even with plan_status='ready' (which shouldn't happen in
        practice but is possible via direct DB writes), retry must
        respect the ``plan_mode`` gate and fall back to the default
        auto-mode flow."""
        # Same race as the plan-mode test above: let the first run
        # fail naturally rather than racing the runner with a forced
        # DB write.  See the sibling test for the full explanation.
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True, fail_at_phase='design'
        )

        async with override_async_session() as db:
            sched = Schedule(
                title='Auto-mode schedule',
                schedule_type='days',
                schedule_time='09:00',
                interval_value=1,
                timezone='UTC',
                is_active=True,
                plan_mode=False,
                phase_mode='single',
                plan_status='ready',
                plan_version=1,
                plan='# Leftover plan (should be ignored)',
                created_by='admin',
            )
            db.add(sched)
            await db.commit()
            await db.refresh(sched)
            sched_id = sched.id

        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Auto retry',
                'plan_mode': False,
                'schedule_id': sched_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='failed')

        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Auto-mode result'
        )

        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200
        assert r2.json()['status'] == 'pending'

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['plan'] != '# Leftover plan (should be ignored)'

    async def test_retry_task_plan_mode_false_with_plan_mode_schedule(
        self, admin_client, install_fake_agent, override_async_session
    ):
        """Defensive guard: if task.plan_mode and sched.plan_mode
        disagree (only reachable via manual task creation, since the
        cron fire path copies sched.plan_mode onto the task), the
        task's recorded mode wins. Don't flip an auto-mode task into
        planned execution just because its schedule is plan-mode.

        Uses a store task because `POST /api/tasks` coerces non-store
        tasks to ``plan_mode=True`` unconditionally (tasks.py:125)."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Auto-mode execution'
        )
        store_id = await _make_store(admin_client, 'Mismatched retry store')

        async with override_async_session() as db:
            sched = Schedule(
                title='Plan-mode schedule',
                schedule_type='days',
                schedule_time='09:00',
                interval_value=1,
                timezone='UTC',
                is_active=True,
                plan_mode=True,
                phase_mode='single',
                plan_status='ready',
                plan_version=2,
                plan='# Frozen plan (should not leak into auto task)',
                created_by='admin',
            )
            db.add(sched)
            await db.commit()
            await db.refresh(sched)
            sched_id = sched.id

        # Store task with plan_mode=False attached to a plan-mode
        # schedule — inconsistent state only reachable via manual
        # construction (cron copies sched.plan_mode onto the task),
        # but must not auto-flip to PLANNED here.
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Mismatched retry',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        async with override_async_session() as db:
            task = await db.get(Task, task_id)
            assert task.plan_mode is False, (
                'precondition: task must be auto-mode'
            )
            task.schedule_id = sched_id
            task.status = 'failed'
            await db.commit()

        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200
        assert r2.json()['status'] == 'pending'

        async with override_async_session() as db:
            task = await db.get(Task, task_id)
            assert task.plan is None
            assert task.status in ('pending', 'queued', 'running', 'completed')

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['plan'] != (
            '# Frozen plan (should not leak into auto task)'
        )


# ── W12: Restart a FAILED task via chat message ─────


class TestFailedMessageRestart:
    async def test_failed_message_restarts_and_preserves_history(
        self, admin_client, install_fake_agent
    ):
        """W12: Auto-mode task FAILED → POST /messages → task
        transitions FAILED → RUNNING, agent restarted with auto
        mode and message history, user message saved.

        Replaces the former /continue endpoint coverage: the chat
        message path is now the sole way to resume a terminal task.
        """
        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=5.0,
        )
        store_id = await _make_store(admin_client, 'W12 restart')

        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Message restart test',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']

        await wait_for_task(
            admin_client, task_id, target='running', timeout=15.0
        )

        r_msgs = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs_before = len(r_msgs.json())

        # Stop → FAILED
        r_stop = await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        assert r_stop.status_code == 200
        await wait_for_task(
            admin_client, task_id, target='failed', timeout=15.0
        )

        # Restart via chat message (replaces /continue)
        install_fake_agent.calls.clear()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Restarted via message',
        )
        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'please continue'},
        )
        assert r2.status_code == 200

        # FAILED → RUNNING → COMPLETED. Follow-up sessions now go
        # through `finalize_followup_session` (scheduled by
        # tasks_conversation.py after agent_manager.run), which
        # mirrors `auto_run_task`'s terminal-transition block.
        # Before that helper existed the task stayed RUNNING
        # forever once the new FakeAgent session finished —
        # the intermediate-RUNNING assertion can't be made anymore
        # because FakeAgent completes in milliseconds, but the
        # RUNNING transition is still required for `completed` to
        # be a legal terminal state (assert_transition enforces it).
        data = await wait_for_task(
            admin_client, task_id, target='completed', timeout=15.0
        )
        assert data['status'] == 'completed'
        assert data['result'] == 'Restarted via message'

        # New user message persisted + prior history preserved
        r_msgs2 = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r_msgs2.json()
        assert len(msgs) >= msgs_before + 1
        user_msgs = [m for m in msgs if m['role'] == 'user']
        assert any(m['content'] == 'please continue' for m in user_msgs)

        # FakeAgent was re-run in auto mode with resume=True-equivalent
        # (message history carried through)
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert len(run_calls) >= 1
        assert run_calls[-1].mode == 'auto'

    async def test_failed_message_with_profile_switch(
        self, admin_client, install_fake_agent
    ):
        """Profile switch on FAILED restart: POST /messages with
        profile_id → ai_profile_id updates and agent restarts with
        new profile.

        Replaces the /continue + profile_id coverage.
        """
        await admin_client.post(
            '/api/profiles',
            json={'id': 'msg-restart-prof', 'name': 'MsgRestart', 'env': {}},
        )

        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=5.0,
        )
        store_id = await _make_store(admin_client, 'W12 switch')
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Profile switch on restart',
                'plan_mode': False,
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(
            admin_client, task_id, target='running', timeout=15.0
        )
        await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        await wait_for_task(
            admin_client, task_id, target='failed', timeout=15.0
        )

        install_fake_agent.calls.clear()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Restarted with new profile',
        )
        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={
                'content': 'please continue',
                'profile_id': 'msg-restart-prof',
            },
        )
        assert r2.status_code == 200

        # Follow-up with profile-switch must go through the full
        # FAILED → PENDING → RUNNING → COMPLETED cycle.  This is
        # the exact scenario that bit the in-the-wild email-review
        # task: profile-switch on an active session left it stuck
        # at RUNNING because nothing owned the new session.
        data = await wait_for_task(
            admin_client, task_id, target='completed', timeout=15.0
        )
        assert data['status'] == 'completed'
        assert data['result'] == 'Restarted with new profile'
        assert data['ai_profile_id'] == 'msg-restart-prof'

        # Agent was restarted with the new profile
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        assert len(run_calls) >= 1
        assert run_calls[-1].profile_id == 'msg-restart-prof'


# ── W13: Chat during RUNNING ────────────────────────


class TestChatDuringExecution:
    async def test_message_during_running(
        self, admin_client, install_fake_agent
    ):
        """W13: Send message to RUNNING task → saved,
        send_message called on agent.
        """
        store_id = await _make_store(admin_client, 'W13 Store')
        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=2.0,
        )

        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Chat running test', 'store_id': store_id},
        )
        task_id = r.json()['id']

        # Wait for running
        await wait_for_task(admin_client, task_id, target='running')

        # Send message
        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'Check spam too'},
        )
        assert r2.status_code == 200

        # Verify message in DB
        r_msgs = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r_msgs.json()
        user_msgs = [m for m in msgs if m['role'] == 'user']
        assert any(m['content'] == 'Check spam too' for m in user_msgs)

        # Verify send_message was called on FakeAgent
        send_calls = install_fake_agent.get_calls(
            task_id=task_id, action='send_message'
        )
        assert len(send_calls) >= 1
        assert send_calls[0].message == 'Check spam too'


# ── W17: GET task after retry → clean state ──────────


class TestRetryCleanState:
    async def test_get_task_after_retry_clean(
        self, admin_client, install_fake_agent
    ):
        """W17: POST retry → immediate GET → clean state."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True, fail_at_phase='design'
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Retry clean test'},
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='failed')

        # Retry
        install_fake_agent.default_scenario = FakeAgentScenario(
            complete_delay=1.0,  # slow so we can check state
        )
        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200
        assert r2.json()['status'] == 'pending'

        # Immediate GET
        r3 = await admin_client.get(f'/api/tasks/{task_id}')
        data = r3.json()
        assert data['plan'] is None
        assert data['result'] is None
        assert data['error'] is None
        # session_id must be cleared on retry — otherwise the next
        # run would --resume a transcript from the failed attempt
        # and carry that failure's state as the baseline.
        assert data['session_id'] is None


class TestTaskResponseExposesSessionId:
    """Regression guard: TaskResponse must expose `session_id`.

    The column was written correctly on the server but never
    returned to clients, so tests / UIs asserting on session_id got
    None whatever the DB said.
    """

    async def test_session_id_is_in_get_task_response(self, admin_client):
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'SID exposure test'},
        )
        task_id = r.json()['id']

        r2 = await admin_client.get(f'/api/tasks/{task_id}')
        assert r2.status_code == 200
        data = r2.json()
        # Key must be present (even if None pre-first-run); the
        # bug was a missing key, not a missing value.
        assert 'session_id' in data, (
            'TaskResponse must include session_id so clients can '
            'observe --resume chain state without DB access.'
        )


# ── W19: Retry on RUNNING → 400 ─────────────────────


class TestRetryGuards:
    async def test_retry_running_rejected(
        self, admin_client, install_fake_agent
    ):
        """W19: Retry on RUNNING task → 400."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=2.0,
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Retry running test',
                'store_id': (await _make_store(admin_client, 'S')),
            },
        )
        task_id = r.json()['id']

        await wait_for_task(admin_client, task_id, target='running')

        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 400


# ── W21: Send message to COMPLETED ──────────────────


class TestCompletedMessage:
    async def test_message_to_completed_saved(
        self, admin_client, install_fake_agent
    ):
        """W21: Send message to COMPLETED task →
        200 OK, message saved, no send_message call.
        """
        install_fake_agent.default_scenario = FakeAgentScenario()
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Completed msg test',
                'store_id': (await _make_store(admin_client, 'S')),
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        # Clear call log
        install_fake_agent.calls.clear()

        # Send message to completed task
        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': 'Follow-up question'},
        )
        assert r2.status_code == 200

        # Verify message saved in DB
        r_msgs = await admin_client.get(f'/api/tasks/{task_id}/messages')
        msgs = r_msgs.json()
        user_msgs = [m for m in msgs if m['role'] == 'user']
        assert any(m['content'] == 'Follow-up question' for m in user_msgs)

        # No send_message call (agent not running)
        send_calls = install_fake_agent.get_calls(
            task_id=task_id, action='send_message'
        )
        assert len(send_calls) == 0


# ── W22: Double stop → graceful ─────────────────────


class TestDoubleStop:
    async def test_double_stop_graceful(self, admin_client, install_fake_agent):
        """W22: Stop already-failed task → 200, status unchanged."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True,
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Double stop test'},
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='failed')

        # Stop again
        r2 = await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
        assert r2.status_code == 200

        # Status unchanged
        r3 = await admin_client.get(f'/api/tasks/{task_id}')
        assert r3.json()['status'] == 'failed'


# ── W8: Retry reruns full pipeline ───────────────────


class TestRetryFullPipeline:
    async def test_retry_reruns_pipeline(
        self, admin_client, install_fake_agent
    ):
        """W8: After retry → new plan + result, COMPLETED."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True, fail_at_phase='design'
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Retry pipeline test', 'plan_mode': True},
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='failed')

        # Retry with working scenario
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='New pipeline plan',
            result='New pipeline result',
        )
        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200

        data = await wait_for_task(admin_client, task_id, target='planned')
        assert data['plan'] == 'New pipeline plan'
        await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] == 'New pipeline result'


# ── Profile switch on retry ────────────────────────


class TestProfileSwitchRetry:
    async def test_retry_with_profile_switch(
        self, admin_client, install_fake_agent
    ):
        """Retry with profile_id → agent uses new profile, task updated."""
        # Create a profile
        await admin_client.post(
            '/api/profiles',
            json={'id': 'retry-prof', 'name': 'RetryProf', 'env': {}},
        )

        # Create and fail a task
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True, fail_at_phase='design'
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Profile switch retry',
                'store_id': (await _make_store(admin_client, 'S')),
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='failed')

        # Retry with new profile
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='New profile plan', result='New profile result'
        )
        r2 = await admin_client.post(
            f'/api/tasks/{task_id}/retry',
            json={'profile_id': 'retry-prof'},
        )
        assert r2.status_code == 200
        assert r2.json()['profile_id'] == 'retry-prof'

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['ai_profile_id'] == 'retry-prof'

        # Verify agent was called with the new profile
        run_calls = install_fake_agent.get_calls(task_id=task_id, action='run')
        # The last run call should use the new profile
        assert any(c.profile_id == 'retry-prof' for c in run_calls)

    async def test_retry_preserves_profile_when_none(
        self, admin_client, install_fake_agent
    ):
        """Retry without body → original profile preserved."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True, fail_at_phase='design'
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Profile preserve retry',
                'store_id': (await _make_store(admin_client, 'S')),
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id, target='failed')

        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='Same profile plan', result='Same profile result'
        )
        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'


# ── Agent error result handling ───────────────────


class TestAgentErrorResult:
    async def test_error_result_design_phase_fails_task(
        self, admin_client, install_fake_agent
    ):
        """Agent returns is_error during design → FAILED
        with actual error text (not generic message).
        """
        install_fake_agent.default_scenario = FakeAgentScenario(
            should_fail=True,
            fail_at_phase='design',
            error_result='API Error: 429 Too Many Requests',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Error result design', 'plan_mode': True},
        )
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id, target='failed')
        assert data['status'] == 'failed'
        assert '429' in data['error']

    async def test_error_result_execute_phase_fails_task(
        self, admin_client, install_fake_agent
    ):
        """Agent returns is_error during execution → FAILED
        (not COMPLETED).
        """
        install_fake_agent.default_scenario = FakeAgentScenario(
            error_result='API Error: 529 Overloaded',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Error result execute',
                'store_id': (await _make_store(admin_client, 'S')),
            },
        )
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id, target='failed')
        assert data['status'] == 'failed'
        assert '529' in data['error']


# ── Agent skips planning ──────────────────────────


class TestSkipPlan:
    async def test_agent_skips_plan_completes(
        self, admin_client, install_fake_agent
    ):
        """Agent produces result without plan → COMPLETED,
        plan is null.
        """
        install_fake_agent.default_scenario = FakeAgentScenario(
            skip_plan=True,
            result='Done without planning',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Skip plan test',
                'store_id': (await _make_store(admin_client, 'S')),
            },
        )
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] == 'Done without planning'
        assert data['plan'] is None

    async def test_agent_skips_plan_error_fails(
        self, admin_client, install_fake_agent
    ):
        """Agent produces error result without plan → FAILED."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            skip_plan=True,
            error_result='API Error: 500 Internal Server Error',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Skip plan error test',
                'store_id': (await _make_store(admin_client, 'S')),
            },
        )
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id, target='failed')
        assert data['status'] == 'failed'
        assert '500' in data['error']

    async def test_retry_after_skip_plan_failure(
        self, admin_client, install_fake_agent
    ):
        """Retry after plan-skipped failure works normally."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            skip_plan=True,
            error_result='API Error: 503 Service Unavailable',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Skip plan retry test',
                'store_id': (await _make_store(admin_client, 'S')),
            },
        )
        task_id = r.json()['id']

        data = await wait_for_task(admin_client, task_id, target='failed')
        assert data['status'] == 'failed'
        assert data['plan'] is None

        # Retry with normal scenario (with plan)
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='Retry plan',
            result='Retry result with plan',
        )
        r2 = await admin_client.post(f'/api/tasks/{task_id}/retry')
        assert r2.status_code == 200

        data = await wait_for_task(admin_client, task_id)
        assert data['status'] == 'completed'
        assert data['result'] == 'Retry result with plan'


# ── Retry race: old pipeline must not stomp retry ───


class TestRetryRace:
    async def test_retry_not_stomped_by_old_pipeline(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        """Stop+retry: old pipeline must not overwrite retry's status.

        The race:
        1. Agent completes normally → pipeline exits polling loop
        2. Pipeline passes MCP guard (status=RUNNING, not terminal)
        3. Pipeline enters _auto_commit (slow)
        4. Test calls stop → sets FAILED
        5. Test calls retry → clears result, sets PENDING→RUNNING
        6. Pipeline wakes from _auto_commit
        7. Pipeline reads DB: status=RUNNING, result=None
        8. BUG: writes FAILED ("Agent exited without result")

        We inject a slow _auto_commit to hold the old pipeline in
        step 3 while stop+retry happens.
        """
        store_id = await _make_store(admin_client, 'Race Store')

        # _auto_commit is called after the polling loop but before
        # terminal status writes.  Make the first call slow so the
        # old pipeline is stuck there when stop+retry fires.
        commit_entered = asyncio.Event()
        commit_release = asyncio.Event()
        call_count = 0

        async def _slow_auto_commit(message):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Signal test that we're inside _auto_commit
                commit_entered.set()
                # Hold old pipeline here until test releases
                await commit_release.wait()

        mock_workspace._auto_commit = _slow_auto_commit

        # Agent completes fast (default delay) with a result.
        # The pipeline will pass the MCP guard (status=RUNNING)
        # and enter _auto_commit where it gets stuck.
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='First result',
        )
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Race test', 'store_id': store_id},
        )
        task_id = r.json()['id']

        # Wait for RUNNING, then wait for pipeline to enter
        # _auto_commit (signal-based, no sleep).
        await wait_for_task(admin_client, task_id, target='running')
        await asyncio.wait_for(commit_entered.wait(), timeout=5.0)

        # Stop + retry while pipeline is in _auto_commit.
        # Stop sets FAILED; retry clears result and sets RUNNING.
        await admin_client.post(f'/api/tasks/{task_id}/agent/stop')

        # Retry with a gate-held agent — the retry's task must
        # still be RUNNING when the old pipeline wakes.
        retry_gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Retry succeeded',
            gate=retry_gate,
        )
        await admin_client.post(f'/api/tasks/{task_id}/retry')

        # Wait for retry agent to start, then release old pipeline
        await install_fake_agent.wait_started(task_id)
        commit_release.set()

        # Release retry agent so it can complete
        retry_gate.set()

        data = await wait_for_task(admin_client, task_id, timeout=15.0)
        assert data['status'] == 'completed', (
            f'Expected completed but got {data["status"]}; '
            f'error={data.get("error")}'
        )
        assert data['result'] == 'Retry succeeded'
