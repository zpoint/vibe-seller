"""Workflow tests for the schedule tools on the hosted MCP endpoint.

Real HTTP → real DB → the real route handlers (``app/routers/
schedules.py``, ``schedule_review.py``); the planner is the FakeAgent.
What these pin:

* ``create_schedule`` is AUTO and ACTIVE by default: no planner, it
  fires its prompt (``run_schedule`` creates a plain auto task);
* with ``review_plan=true`` it spawns the plan-only task; following it
  with ``wait_task`` ends at ``pending_approval`` with the plan in
  ``proposed_plan`` — never approved by the server — and the planner
  executed nothing; ``get_attention`` lists it with the full plan;
  ``review_schedule_plan`` approves (→ ``ready``, fires) or sends it
  back (→ a new planner sees the feedback);
* ``run_schedule`` without an approved plan is refused with the
  route's reason VERBATIM, and fires the approved plan (not a re-plan);
* a prompt edit on a review schedule drops the approved plan and
  re-plans for approval; ``paused`` maps to /pause and /resume;
* every schedule result says in plain words whether it can fire
  (``fires`` + ``plan_status_meaning`` from the lifecycle table).
"""

import asyncio
import json

import pytest
from sqlalchemy import select

import app.database as _db
from app.models.schedule import Schedule
from app.models.task import Task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

MCP_PATH = '/api/mcp'
PLAN_TEXT = '## Weekly check plan\n1. List products\n2. Report anomalies'


async def rpc(client, method, params=None, request_id=1):
    """One JSON-RPC call."""
    payload = {'jsonrpc': '2.0', 'id': request_id, 'method': method}
    if params is not None:
        payload['params'] = params
    return await client.post(MCP_PATH, json=payload)


async def call_tool(client, name, arguments=None):
    """Call one MCP tool; return (parsed result, isError)."""
    r = await rpc(
        client, 'tools/call', {'name': name, 'arguments': arguments or {}}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert 'error' not in body, body
    result = body['result']
    text = result['content'][0]['text']
    if result.get('isError'):
        return {'error': text}, True
    return (json.loads(text) if text else {}), False


async def mcp_schedule(client, schedule_id):
    """One schedule from list_schedules."""
    result, is_error = await call_tool(
        client, 'list_schedules', {'store_id': None}
    )
    assert not is_error, result
    for item in result['schedules']:
        if item['id'] == schedule_id:
            return item
    raise AssertionError(f'{schedule_id} not listed')


async def wait_plan_status(schedule_id, target, timeout=10.0):
    """Poll the DB until the schedule's plan_status reaches *target*.

    API-mediated polling races FakeAgent's background commit under
    StaticPool (see test_wf_schedule_plan_creation for the same
    workaround).
    """
    for _ in range(int(timeout / 0.05)):
        async with _db.async_session() as db:
            s = (
                await db.execute(
                    select(Schedule).where(Schedule.id == schedule_id)
                )
            ).scalar_one_or_none()
            if s is not None and s.plan_status == target:
                return s
        await asyncio.sleep(0.05)
    raise AssertionError(
        f'Schedule {schedule_id} plan_status did not reach {target}'
    )


async def wait_planner_settled(schedule_id, timeout=10.0):
    """Wait until the plan-only task has finished.

    A test that ends while the planner is still running leaks an
    asyncio task into the NEXT test's teardown, where the engine is
    disposed underneath it — which surfaces as a teardown error in an
    unrelated test. So every test that leaves a planner in flight waits
    for it here.
    """
    for _ in range(int(timeout / 0.05)):
        async with _db.async_session() as db:
            s = (
                await db.execute(
                    select(Schedule).where(Schedule.id == schedule_id)
                )
            ).scalar_one_or_none()
            if s is None:
                return
            task_id = s.current_planning_task_id
            if s.plan_status != 'planning':
                return
            if not task_id:
                return
            t = (
                await db.execute(select(Task).where(Task.id == task_id))
            ).scalar_one_or_none()
            if t is None or t.status in {'completed', 'failed'}:
                return
        await asyncio.sleep(0.05)


async def create_review_schedule(client, *, store_id=None, **extra):
    """create_schedule(review_plan=true) — returns the tool result."""
    result, is_error = await call_tool(
        client,
        'create_schedule',
        {
            'title': 'Weekly link check',
            'description': 'Check product links for anomalies',
            'schedule_type': 'weekly',
            'schedule_time': '09:05',
            'store_id': store_id,
            'review_plan': True,
            **extra,
        },
    )
    assert not is_error, result
    return result


async def approve_over_mcp(client, schedule_id):
    """What a blank agent does once its user said yes: read the pending
    plan's version from get_attention, then review_schedule_plan."""
    await wait_plan_status(schedule_id, 'pending_approval')
    attention, is_error = await call_tool(client, 'get_attention', {})
    assert not is_error, attention
    item = next(
        a
        for a in attention['schedule_plan_approvals']
        if a['schedule_id'] == schedule_id
    )
    decided, is_error = await call_tool(
        client,
        'review_schedule_plan',
        {
            'schedule_id': schedule_id,
            'approve': True,
            'plan_version': item['plan_version'],
        },
    )
    assert not is_error, decided
    return decided


async def create_ready_schedule(client, *, store_id=None):
    """A review schedule whose plan the user approved (ready)."""
    result = await create_review_schedule(client, store_id=store_id)
    schedule_id = result['schedule_id']
    await approve_over_mcp(client, schedule_id)
    return result, schedule_id


class TestCreateAuto:
    async def test_default_is_auto_active_and_fires(
        self, admin_client, install_fake_agent
    ):
        """The default a blank agent gets: an AUTO schedule, ACTIVE like
        the web UI, that fires its prompt — no planner, nothing to
        approve."""
        created, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Plain weekly check',
                'description': 'Check product links for anomalies',
                'schedule_type': 'weekly',
                'schedule_time': '09:05',
            },
        )
        assert not is_error, created
        assert created['review_plan'] is False
        assert created['paused'] is False
        assert created['plan_status'] == 'none'
        assert created['plan_task_id'] is None
        assert created['fires'] is True
        assert created['next_actions'] == ['run_schedule']
        assert 'AUTO' in created['note']

    async def test_run_now_fires_the_prompt(
        self, admin_client, install_fake_agent
    ):
        store_id = (
            await admin_client.post('/api/stores', json={'name': 'Auto Store'})
        ).json()['id']
        created, _ = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Auto store check',
                'description': 'Check the store dashboard',
                'schedule_type': 'days',
                'schedule_time': '08:00',
                'store_id': store_id,
            },
        )
        run, is_error = await call_tool(
            admin_client, 'run_schedule', {'schedule_id': created['id']}
        )
        assert not is_error, run
        assert run['task_id']
        assert 'prompt' in run['note']
        task = await _db_task(run['task_id'])
        assert task.plan_mode is False, 'an auto fire runs the prompt'
        assert task.plan is None
        assert task.description == 'Check the store dashboard'

    async def test_paused_true_creates_it_paused(
        self, admin_client, install_fake_agent
    ):
        created, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Set up only',
                'description': 'x',
                'schedule_type': 'days',
                'schedule_time': '08:00',
                'paused': True,
            },
        )
        assert not is_error, created
        assert created['paused'] is True
        assert created['fires'] is False
        assert created['next_actions'][:2] == [
            'update_schedule',
            'run_schedule',
        ]
        assert 'PAUSED' in created['note']


class TestReviewFlow:
    async def test_blank_agent_review_loop(
        self, admin_client, install_fake_agent
    ):
        """create(review) → wait_task on the planner → get_attention →
        review_schedule_plan(approve) → ready, fires. The planner
        executes nothing and the server approves nothing."""
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        result = await create_review_schedule(admin_client)
        assert result['review_plan'] is True
        assert result['plan_status'] == 'planning'
        assert result['plan_task_id'], 'planning task id must be reported'
        assert result['fires'] is False
        assert 'cannot fire' in result['plan_status_meaning']
        assert result['next_actions'] == ['wait_task', 'get_task']
        assert 'WAITS FOR APPROVAL' in result['note']

        waited, is_error = await call_tool(
            admin_client, 'wait_task', {'task_id': result['plan_task_id']}
        )
        assert not is_error, waited
        assert waited['reason'] in {'needs_operator', 'terminal'}

        schedule = await wait_plan_status(
            result['schedule_id'], 'pending_approval'
        )
        assert schedule.plan is None, 'the server never approves'
        assert schedule.proposed_plan == PLAN_TEXT

        listed = await mcp_schedule(admin_client, result['schedule_id'])
        assert listed['plan_status'] == 'pending_approval'
        assert listed['fires'] is False
        assert listed['proposed_plan'] == PLAN_TEXT
        assert listed['next_actions'][0] == 'review_schedule_plan'
        assert 'never expires' in listed['plan_status_meaning']

        attention, is_error = await call_tool(admin_client, 'get_attention', {})
        assert not is_error, attention
        assert attention['needs_attention'] is True
        item = next(
            a
            for a in attention['schedule_plan_approvals']
            if a['schedule_id'] == result['schedule_id']
        )
        assert item['proposed_plan'] == PLAN_TEXT
        assert 'review_schedule_plan' in item['next']

        decided, is_error = await call_tool(
            admin_client,
            'review_schedule_plan',
            {
                'schedule_id': result['schedule_id'],
                'approve': True,
                'plan_version': item['plan_version'],
            },
        )
        assert not is_error, decided
        assert decided['decision'] == 'approved'
        assert decided['plan_status'] == 'ready'
        assert decided['fires'] is True
        assert decided['plan'] == PLAN_TEXT
        assert 'run_schedule' in decided['next_actions']

        # The planning task wrote the plan and executed nothing.
        task, is_error = await call_tool(
            admin_client, 'get_task', {'task_id': result['plan_task_id']}
        )
        assert not is_error, task
        assert task['status'] == 'completed'
        assert task['result'] is None

        after, _ = await call_tool(admin_client, 'get_attention', {})
        assert all(
            a['schedule_id'] != result['schedule_id']
            for a in after['schedule_plan_approvals']
        )

    async def test_request_changes_replans_with_the_feedback(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        result = await create_review_schedule(admin_client)
        schedule_id = result['schedule_id']
        pending = await wait_plan_status(schedule_id, 'pending_approval')

        sent_back, is_error = await call_tool(
            admin_client,
            'review_schedule_plan',
            {
                'schedule_id': schedule_id,
                'approve': False,
                'feedback': 'Only check the top 10 products',
                'plan_version': pending.plan_version,
            },
        )
        assert not is_error, sent_back
        assert sent_back['decision'] == 'changes_requested'
        assert sent_back['plan_feedback'] == 'Only check the top 10 products'
        new_planner = sent_back['plan_task_id']
        assert new_planner and new_planner != result['plan_task_id']

        # The new planner was told what to change, with the old plan.
        for _ in range(100):
            calls = install_fake_agent.get_calls(
                task_id=new_planner, action='run'
            )
            if calls:
                break
            await asyncio.sleep(0.05)
        assert calls, 'the revising planner never launched'
        extra = calls[0].system_extra
        assert 'Only check the top 10 products' in extra
        assert PLAN_TEXT in extra

        # …and its plan comes back for another decision.
        await wait_plan_status(schedule_id, 'pending_approval')

    async def test_approve_needs_feedback_to_send_back(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        result = await create_review_schedule(admin_client)
        await wait_plan_status(result['schedule_id'], 'pending_approval')
        refused, is_error = await call_tool(
            admin_client,
            'review_schedule_plan',
            {'schedule_id': result['schedule_id'], 'approve': False},
        )
        assert is_error, refused
        assert 'feedback is required' in refused['error']

    async def test_approve_a_plan_that_changed_is_refused(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        result = await create_review_schedule(admin_client)
        pending = await wait_plan_status(
            result['schedule_id'], 'pending_approval'
        )
        refused, is_error = await call_tool(
            admin_client,
            'review_schedule_plan',
            {
                'schedule_id': result['schedule_id'],
                'approve': True,
                'plan_version': pending.plan_version - 1,
            },
        )
        assert is_error, refused
        assert 'changed since you read it' in refused['error']
        row = await wait_plan_status(result['schedule_id'], 'pending_approval')
        assert row.plan is None

    async def test_get_attention_when_nothing_waits(
        self, admin_client, install_fake_agent
    ):
        attention, is_error = await call_tool(admin_client, 'get_attention', {})
        assert not is_error, attention
        assert attention['needs_attention'] is False
        assert attention['schedule_plan_approvals'] == []
        assert 'Nothing is waiting' in attention['note']

    async def test_paused_review_schedule_stays_paused_after_approval(
        self, admin_client, install_fake_agent
    ):
        """create(review, paused) → approve → still paused (fires=false)
        → resume → it fires."""
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        created = await create_review_schedule(admin_client, paused=True)
        schedule_id = created['schedule_id']
        assert created['paused'] is True
        assert created['fires'] is False
        approved = await approve_over_mcp(admin_client, schedule_id)
        assert approved['plan_status'] == 'ready'
        assert approved['paused'] is True
        assert approved['fires'] is False, (
            'a paused schedule must not report that it will fire'
        )
        resumed, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'paused': False},
        )
        assert not is_error, resumed
        assert resumed['fires'] is True

    async def test_schedule_results_expose_the_approved_plan(
        self, admin_client, install_fake_agent
    ):
        """Every schedule result carries the plan text."""
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        _, schedule_id = await create_ready_schedule(admin_client)

        listed, is_error = await call_tool(admin_client, 'list_schedules', {})
        assert not is_error, listed
        entry = next(s for s in listed['schedules'] if s['id'] == schedule_id)
        assert entry['plan'] == PLAN_TEXT
        assert entry['plan_truncated'] is False
        assert entry['plan_full_text_where'] is None

        edited, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'title': 'Renamed'},
        )
        assert not is_error, edited
        assert edited['plan'] == PLAN_TEXT
        assert edited['plan_truncated'] is False

    async def test_list_schedules_carries_the_lifecycle(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        result = await create_review_schedule(
            admin_client,
            title='Daily audit',
            schedule_type='days',
            schedule_time='08:00',
        )
        listed, is_error = await call_tool(admin_client, 'list_schedules', {})
        assert not is_error, listed
        entry = next(
            s for s in listed['schedules'] if s['id'] == result['schedule_id']
        )
        assert entry['paused'] is False
        assert entry['review_plan'] is True
        assert entry['schedule_type'] == 'days'
        assert entry['schedule_time'] == '08:00'
        assert entry['timezone']
        assert entry['plan_status'] in {'planning', 'pending_approval'}
        assert 'fire' in entry['plan_status_meaning']
        await wait_planner_settled(result['schedule_id'])


class TestRunSchedule:
    async def test_refused_while_planning_reason_verbatim(
        self, admin_client, install_fake_agent
    ):
        """The route's refusal reaches the agent word for word."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan=PLAN_TEXT,
            design_delay=1.0,  # hold the planner in DESIGNING
        )
        result = await create_review_schedule(admin_client)
        assert result['plan_status'] == 'planning'

        refused, is_error = await call_tool(
            admin_client, 'run_schedule', {'schedule_id': result['id']}
        )
        assert is_error, refused
        assert refused['error'] == (
            'This schedule runs only an approved plan; its plan is being '
            'written now (plan_status=planning). Approve it once it is '
            'ready.'
        )

        # Let the planner finish before the test ends — a background
        # task outliving the test corrupts the next one's teardown.
        await wait_planner_settled(result['id'])

    async def test_refused_while_pending_approval(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        result = await create_review_schedule(admin_client)
        await wait_plan_status(result['schedule_id'], 'pending_approval')
        refused, is_error = await call_tool(
            admin_client, 'run_schedule', {'schedule_id': result['id']}
        )
        assert is_error, refused
        assert 'waiting for approval' in refused['error']

    async def test_fires_the_approved_plan_not_a_replan(
        self, admin_client, install_fake_agent
    ):
        """run_schedule on an approved store-bound schedule returns the
        created task id, and that task carries the approved plan
        verbatim (scheduled fires skip planning)."""
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        store_r = await admin_client.post(
            '/api/stores', json={'name': 'MCP Sched Store'}
        )
        store_id = store_r.json()['id']

        _, schedule_id = await create_ready_schedule(
            admin_client, store_id=store_id
        )
        run, is_error = await call_tool(
            admin_client, 'run_schedule', {'schedule_id': schedule_id}
        )
        assert not is_error, run
        assert run['ok'] is True
        assert run['task_id'], 'store-bound fire must report the task id'
        assert run['plan_status'] == 'ready'
        assert run['fires'] is True
        assert 'approved plan' in run['note']

        task, is_error = await call_tool(
            admin_client, 'get_task', {'task_id': run['task_id']}
        )
        assert not is_error, task
        assert task['plan'] == PLAN_TEXT, 'fire copies the approved plan'
        assert task['status'] in {'planned', 'queued'}

    async def test_all_stores_fanout_reports_mode(
        self, admin_client, install_fake_agent
    ):
        """No-store schedule: the route fans out in the background, so
        the result reports the mode and steers to list_tasks instead
        of inventing a task id."""
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        _, schedule_id = await create_ready_schedule(admin_client)
        run, is_error = await call_tool(
            admin_client, 'run_schedule', {'schedule_id': schedule_id}
        )
        assert not is_error, run
        assert run['mode'] == 'fanout'
        assert run['task_id'] is None
        assert 'list_tasks' in run['note']


class TestUpdateSchedule:
    async def test_prompt_edit_replans_for_approval(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan=PLAN_TEXT, design_delay=0.5
        )
        _, schedule_id = await create_ready_schedule(admin_client)

        # Optimistic lock: a stale plan_version is refused for an edit
        # that changes the plan.
        locked, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {
                'schedule_id': schedule_id,
                'description': 'Something else',
                'plan_version': 999,
            },
        )
        assert is_error, locked
        assert 'Stale plan_version' in locked['error']

        edited, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {
                'schedule_id': schedule_id,
                'description': 'Check product links AND pricing',
            },
        )
        assert not is_error, edited
        assert edited['plan_status'] == 'planning'
        assert edited['plan'] is None, 'the old approved plan is dropped'
        assert edited['fires'] is False
        assert edited['updated_fields'] == ['description']
        assert edited['plan_task_id'], 'the edit starts a new planner'

        # While no plan is approved, a manual fire is refused.
        refused, is_error = await call_tool(
            admin_client, 'run_schedule', {'schedule_id': schedule_id}
        )
        assert is_error, refused
        assert 'approved plan' in refused['error']

        approved = await approve_over_mcp(admin_client, schedule_id)
        assert approved['fires'] is True

    async def test_edit_without_prompt_change_keeps_plan(
        self, admin_client, install_fake_agent
    ):
        """Only the prompt (normalized) invalidates — a title edit on
        a ready schedule stays ready."""
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        _, schedule_id = await create_ready_schedule(admin_client)
        edited, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'title': 'Renamed audit'},
        )
        assert not is_error, edited
        assert edited['plan_status'] == 'ready'
        assert edited['fires'] is True
        assert edited['title'] == 'Renamed audit'

    async def test_review_plan_switch(self, admin_client, install_fake_agent):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan=PLAN_TEXT, design_delay=0.5
        )
        _, schedule_id = await create_ready_schedule(admin_client)
        off, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'review_plan': False},
        )
        assert not is_error, off
        assert off['review_plan'] is False
        assert off['plan_status'] == 'none'
        assert off['plan'] is None
        assert off['fires'] is True

        on, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'review_plan': True},
        )
        assert not is_error, on
        assert on['review_plan'] is True
        assert on['plan_status'] == 'planning'
        assert on['fires'] is False
        await wait_planner_settled(schedule_id)

    async def test_paused_maps_to_pause_and_resume_routes(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        _, schedule_id = await create_ready_schedule(admin_client)

        paused, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'paused': True},
        )
        assert not is_error, paused
        assert paused['paused'] is True
        assert paused['fires'] is False, 'a paused schedule never fires'

        resumed, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'paused': False},
        )
        assert not is_error, resumed
        assert resumed['paused'] is False
        assert resumed['fires'] is True

    async def test_resume_refused_without_an_approved_plan(
        self, admin_client, install_fake_agent
    ):
        """Paused + prompt edited (approved plan dropped): resume hits
        the route's gate; approving the new plan is the way back."""
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        _, schedule_id = await create_ready_schedule(admin_client)
        await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'paused': True},
        )
        await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'description': 'New intent'},
        )
        await wait_plan_status(schedule_id, 'pending_approval')

        refused, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'paused': False},
        )
        assert is_error, refused
        assert refused['error'] == (
            'This schedule runs only an approved plan, and its plan is '
            'waiting for approval (plan_status=pending_approval). '
            'Approve it first.'
        )

        await approve_over_mcp(admin_client, schedule_id)
        resumed, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'paused': False},
        )
        assert not is_error, resumed
        assert resumed['paused'] is False
        assert resumed['fires'] is True

    async def test_update_with_no_effect_is_a_tool_error(
        self, admin_client, install_fake_agent
    ):
        result, _ = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'No-op guard',
                'description': 'x',
                'schedule_type': 'days',
                'schedule_time': '09:00',
            },
        )
        noop, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': result['id']},
        )
        assert is_error, noop
        assert 'nothing to update' in noop['error']


class TestDeleteSchedule:
    async def test_delete_removes_and_second_delete_404s(
        self, admin_client, install_fake_agent
    ):
        result, _ = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Doomed',
                'description': 'x',
                'schedule_type': 'days',
                'schedule_time': '09:00',
            },
        )
        schedule_id = result['id']

        deleted, is_error = await call_tool(
            admin_client, 'delete_schedule', {'schedule_id': schedule_id}
        )
        assert not is_error, deleted
        assert deleted['ok'] is True

        listed, _ = await call_tool(admin_client, 'list_schedules', {})
        assert all(s['id'] != schedule_id for s in listed['schedules']), (
            'deleted schedule must not be listed'
        )

        again, is_error = await call_tool(
            admin_client, 'delete_schedule', {'schedule_id': schedule_id}
        )
        assert is_error, again
        assert again['error'] == 'Schedule not found'


async def _db_task(task_id):
    async with _db.async_session() as db:
        return await db.get(Task, task_id)
