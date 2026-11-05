"""Workflow tests for the schedule tools on the hosted MCP endpoint.

Real HTTP → real DB → the real route handlers (``app/routers/
schedules.py``); the planner is the FakeAgent. What these pin:

* ``create_schedule`` spawns the plan-only task and reports
  ``plan_status`` / ``plan_task_id`` / ``fires``; following the task
  with ``wait_task`` ends with the plan frozen onto the schedule
  (``ready``) and the planning task completed WITHOUT executing;
* ``run_schedule`` while the plan is not ready is refused with the
  route's reason VERBATIM, and fires the frozen plan (not a re-plan)
  once ready;
* a prompt edit makes the plan ``stale``; ``replan`` re-authors it;
* ``paused`` maps to the /pause and /resume routes, including the
  resume-refused-while-not-ready case;
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


async def create_ready_schedule(client, *, store_id=None):
    """create_schedule → wait for the plan to freeze (ready)."""
    result, is_error = await call_tool(
        client,
        'create_schedule',
        {
            'title': 'Weekly link check',
            'description': 'Check product links for anomalies',
            'schedule_type': 'weekly',
            'schedule_time': '09:05',
            'store_id': store_id,
            # These tests exercise the ready/fire lifecycle, which
            # needs an ARMED schedule — the tool's default is paused.
            'paused': False,
        },
    )
    assert not is_error, result
    schedule_id = result['schedule_id']
    await wait_plan_status(schedule_id, 'ready')
    return result, schedule_id


class TestCreateSchedule:
    async def test_create_reports_planning_then_freezes(
        self, admin_client, install_fake_agent
    ):
        """create → planning + plan_task_id → wait_task follows the
        planner → the plan freezes onto the schedule and the planner
        executes nothing."""
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        result, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Weekly link check',
                'description': 'Check product links for anomalies',
                'schedule_type': 'weekly',
                'schedule_time': '09:05',
                # Active on purpose: this test pins the ACTIVE-created
                # guidance (pause named first). The paused default has
                # its own test.
                'paused': False,
            },
        )
        assert not is_error, result
        assert result['plan_status'] == 'planning'
        assert result['plan_task_id'], 'planning task id must be reported'
        assert result['fires'] is False
        assert 'cannot fire' in result['plan_status_meaning']
        assert result['next_actions'] == [
            'update_schedule',
            'wait_task',
            'get_task',
        ]
        assert 'plan' in result['note'].lower()

        # Follow the planner with the EXISTING wait_task tool. It may
        # catch the momentary `planned` (needs_operator) or only the
        # terminal state — both are "the planner finished presenting";
        # the freeze is what matters.
        waited, is_error = await call_tool(
            admin_client, 'wait_task', {'task_id': result['plan_task_id']}
        )
        assert not is_error, waited
        assert waited['reason'] in {'needs_operator', 'terminal'}

        schedule = await wait_plan_status(result['schedule_id'], 'ready')
        assert schedule.plan == PLAN_TEXT
        assert schedule.plan_version == 1

        listed = await mcp_schedule(admin_client, result['schedule_id'])
        assert listed['plan_status'] == 'ready'
        assert listed['fires'] is True
        assert listed['plan_task_id'] is None
        assert 'run_schedule' in listed['next_actions']

        # The planning task authored the plan and terminated — it
        # must not have executed anything (no result was produced).
        task, is_error = await call_tool(
            admin_client, 'get_task', {'task_id': result['plan_task_id']}
        )
        assert not is_error, task
        assert task['status'] == 'completed'
        assert task['result'] is None

    async def test_create_paused_holds_the_checkpoint(
        self, admin_client, install_fake_agent
    ):
        """create(paused=true) → paused from the start → the plan
        freezes → still paused (fires=false) → resume → it fires.

        The REST create path has no "create it paused" (ScheduleCreate
        carries no is_active; the route always creates it active), so
        the tool creates and then pauses inside the SAME call. While
        the plan is still PLANNING the fire gate refuses it, so there
        is no window in which the schedule could have fired.
        """
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        created, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Paused weekly check',
                'description': 'Check product links for anomalies',
                'schedule_type': 'weekly',
                'schedule_time': '09:05',
                'paused': True,
            },
        )
        assert not is_error, created
        schedule_id = created['schedule_id']
        assert created['paused'] is True, 'the schedule is paused'
        assert created['plan_status'] == 'planning'
        assert created['fires'] is False
        # Paused mid-planning: the pause is a no-op and the resume is
        # refused, so only the planner is left to follow.
        assert created['next_actions'] == ['wait_task', 'get_task']

        # The plan freezes — and the schedule is STILL paused.
        await wait_plan_status(schedule_id, 'ready')
        frozen, is_error = await call_tool(admin_client, 'list_schedules', {})
        assert not is_error, frozen
        entry = next(s for s in frozen['schedules'] if s['id'] == schedule_id)
        assert entry['plan_status'] == 'ready'
        assert entry['paused'] is True
        assert entry['fires'] is False, (
            'a paused schedule must not report that it will fire'
        )
        assert entry['plan'] == PLAN_TEXT

        # The user has seen the plan → resume → armed.
        resumed, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'paused': False},
        )
        assert not is_error, resumed
        assert resumed['paused'] is False
        assert resumed['fires'] is True
        assert 'run_schedule' in resumed['next_actions']

    async def test_create_defaults_to_paused_until_opt_in(
        self, admin_client, install_fake_agent
    ):
        """The tool's default is PAUSED — a deliberate divergence
        from the web UI (which creates active schedules), documented
        in the tool description like manage_profile's set_default: a
        plan the server auto-accepts must not arm itself unless the
        caller explicitly opted in with ``paused: false``.
        """
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
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
        assert created['paused'] is True, 'paused is the default'
        assert created['fires'] is False
        # Paused mid-planning: no update named, the planner is the
        # thing to follow.
        assert created['next_actions'] == ['wait_task', 'get_task']

        # The opt-in is explicit and quiet: paused=false creates the
        # schedule ACTIVE, like the UI.
        armed, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Armed weekly check',
                'description': 'Check product links for anomalies',
                'schedule_type': 'weekly',
                'schedule_time': '09:05',
                'paused': False,
            },
        )
        assert not is_error, armed
        assert armed['paused'] is False
        assert armed['fires'] is False  # the plan is not ready yet
        # And the guidance names the pause — the call that preserves
        # the checkpoint before the plan freezes.
        assert armed['next_actions'][0] == 'update_schedule'
        await wait_planner_settled(armed['schedule_id'])
        await wait_planner_settled(created['schedule_id'])

    async def test_create_pause_show_approve_is_a_real_checkpoint(
        self, admin_client, install_fake_agent
    ):
        """The human checkpoint, end to end.

        The server accepts a schedule's plan by itself, so the tool
        creates the schedule PAUSED (its default): the plan freezes,
        the agent shows the plan text, and only then resumes. Until
        the resume, `fires` is false — the schedule cannot run
        anything the user has not seen.
        """
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        created, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Weekly link check',
                'description': 'Check product links for anomalies',
                'schedule_type': 'weekly',
                'schedule_time': '09:05',
            },
        )
        assert not is_error, created
        schedule_id = created['schedule_id']

        # 1. Pause it right away — the plan is not ready yet, so the
        #    schedule could not have fired anyway.
        paused, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'paused': True},
        )
        assert not is_error, paused
        assert paused['paused'] is True
        assert paused['fires'] is False

        # 2. The plan freezes onto the schedule — still paused, so the
        #    schedule still cannot fire.
        await wait_plan_status(schedule_id, 'ready')
        frozen, is_error = await call_tool(admin_client, 'list_schedules', {})
        assert not is_error, frozen
        entry = next(s for s in frozen['schedules'] if s['id'] == schedule_id)
        assert entry['plan_status'] == 'ready'
        assert entry['paused'] is True
        assert entry['fires'] is False, (
            'a paused schedule must not report that it will fire'
        )

        # 3. The plan text is in the result — the agent can show it.
        assert entry['plan'] == PLAN_TEXT
        assert entry['plan_truncated'] is False

        # 4. The user objects → the plan goes stale and the schedule
        #    stops firing; a re-plan re-arms it (paused).
        objected, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {
                'schedule_id': schedule_id,
                'description': 'Check product links AND pricing',
            },
        )
        assert not is_error, objected
        assert objected['plan_status'] == 'stale'
        assert objected['fires'] is False

        # 5. The user approves the re-plan → resume → armed.
        replanned, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'replan': True},
        )
        assert not is_error, replanned
        await wait_plan_status(schedule_id, 'ready')
        resumed, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'paused': False},
        )
        assert not is_error, resumed
        assert resumed['paused'] is False
        assert resumed['fires'] is True, 'the approved schedule is armed'
        assert 'automatically' in resumed['plan_status_meaning']

    async def test_schedule_results_expose_the_frozen_plan(
        self, admin_client, install_fake_agent
    ):
        """Every schedule result carries the plan text.

        The agent has to show the plan to its user before the
        schedule runs it, so the text has to travel with the result —
        not just its status.
        """
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
        result, _ = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Daily audit',
                'description': 'Audit yesterday',
                'schedule_type': 'days',
                'schedule_time': '08:00',
                'paused': False,
            },
        )
        listed, is_error = await call_tool(admin_client, 'list_schedules', {})
        assert not is_error, listed
        entry = next(
            s for s in listed['schedules'] if s['id'] == result['schedule_id']
        )
        assert entry['paused'] is False
        assert entry['plan_mode'] is True
        assert entry['schedule_type'] == 'days'
        assert entry['schedule_time'] == '08:00'
        assert entry['timezone']
        assert entry['plan_status'] in {'planning', 'ready'}
        assert 'fire' in entry['plan_status_meaning']


class TestRunSchedule:
    async def test_refused_while_planning_reason_verbatim(
        self, admin_client, install_fake_agent
    ):
        """The route's refusal reaches the agent word for word."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan=PLAN_TEXT,
            design_delay=1.0,  # hold the planner in DESIGNING
        )
        result, is_error = await call_tool(
            admin_client,
            'create_schedule',
            {
                'title': 'Held planning',
                'description': 'Held',
                'schedule_type': 'days',
                'schedule_time': '09:00',
            },
        )
        assert not is_error, result
        assert result['plan_status'] == 'planning'

        refused, is_error = await call_tool(
            admin_client, 'run_schedule', {'schedule_id': result['id']}
        )
        assert is_error, refused
        assert refused['error'] == (
            "Schedule cannot fire with plan_status='planning'. "
            'Call /replan first.'
        )

        # Let the planner finish before the test ends — a background
        # task outliving the test corrupts the next one's teardown.
        await wait_planner_settled(result['id'])

    async def test_fires_the_frozen_plan_not_a_replan(
        self, admin_client, install_fake_agent
    ):
        """run_schedule on a ready store-bound schedule returns the
        created task id, and that task carries the frozen plan
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

        task, is_error = await call_tool(
            admin_client, 'get_task', {'task_id': run['task_id']}
        )
        assert not is_error, task
        assert task['plan'] == PLAN_TEXT, 'fire copies the frozen plan'
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
    async def test_prompt_edit_makes_stale_then_replan_heals(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
        _, schedule_id = await create_ready_schedule(admin_client)

        # Optimistic lock: a stale plan_version is refused.
        locked, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'plan_version': 999},
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
        assert edited['plan_status'] == 'stale'
        assert edited['fires'] is False
        assert edited['updated_fields'] == ['description']
        assert 'cannot fire' in edited['plan_status_meaning']

        # While stale, a manual fire is refused with the reason.
        refused, is_error = await call_tool(
            admin_client, 'run_schedule', {'schedule_id': schedule_id}
        )
        assert is_error, refused
        assert refused['error'] == (
            "Schedule cannot fire with plan_status='stale'. Call /replan first."
        )

        replanned, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'replan': True},
        )
        assert not is_error, replanned
        assert replanned['replanned'] is True
        assert replanned['plan_status'] == 'planning'
        assert replanned['plan_task_id'], 'replan must report the new task'

        schedule = await wait_plan_status(schedule_id, 'ready')
        assert schedule.plan_version == 2

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

    async def test_resume_refused_while_stale_reason_verbatim(
        self, admin_client, install_fake_agent
    ):
        """Paused + stale: resume hits the route's gate; replan is the
        way back, and after it the resume goes through."""
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

        refused, is_error = await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'paused': False},
        )
        assert is_error, refused
        assert refused['error'] == (
            "Cannot resume schedule with plan_status='stale'. "
            'Call /replan first.'
        )

        await call_tool(
            admin_client,
            'update_schedule',
            {'schedule_id': schedule_id, 'replan': True},
        )
        await wait_plan_status(schedule_id, 'ready')
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
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
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
        install_fake_agent.default_scenario = FakeAgentScenario(plan=PLAN_TEXT)
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
        # Let the planner finish first: a schedule deleted mid-planning
        # leaves the planner writing to a row that is gone, and its
        # background task would outlive the test.
        await wait_plan_status(schedule_id, 'ready')

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
