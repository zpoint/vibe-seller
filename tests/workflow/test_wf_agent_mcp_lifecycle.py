"""Workflow tests for the lifecycle guidance in every MCP tool result.

The lifecycle guidance is a PROMISE to the agent:
``status_meaning`` says what the status means and ``next_actions``
says what it may do next. These tests check both halves:

* the block is present on every task-bearing result;
* every tool the block names is actually ACCEPTED by the server in
  the state it was named for.

The second half is the one that matters. A ``next_actions`` list that
names a tool the route would refuse is worse than no guidance at all:
the agent spends a turn on a call that comes back "this task cannot
be retried", and learns nothing about the state it was trying to act
on. So each state is REACHED, its guidance read, and each named tool
CALLED — on a fresh task in the same state, because an action may
change the task (retry, approve, delete).

The states cover the whole ``TaskStatus`` enum, plus the pending
question (which is a flag on a running task, not a status).
"""

import asyncio
import json

import pytest

from app.models.task import Task
from app.task_states import TaskStatus
from tests.workflow.conftest import real_sleep, wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario
from tests.workflow.test_wf_agent_mcp import (
    MCP_PATH,
    call_tool,
    make_store,
    settle_task,
)

pytestmark = pytest.mark.workflow

LIFECYCLE_STATES = (
    'pending',
    'queued',
    'designing',
    'planned',
    'running',
    'waiting',
    'completed',
    'failed',
    'question',
)

# The status each state presents. A pending question is a FLAG on a
# running task, not a status of its own.
_EXPECTED_STATUS = {
    'pending': 'pending',
    'queued': 'queued',
    'designing': 'designing',
    'planned': 'planned',
    'running': 'running',
    'waiting': 'waiting',
    'completed': 'completed',
    'failed': 'failed',
    'question': 'running',
}

_GUIDANCE_QUESTION = {
    'request_id': 'req-guidance',
    'questions': [
        {
            'question': 'Which marketplace should I use?',
            'header': 'Marketplace',
            'options': [{'label': 'US'}, {'label': 'AE'}],
        }
    ],
}


async def _wait_for_status(client, task_id, status, timeout=10.0):
    """Poll GET /api/tasks/{id} until the task reaches *status*."""
    data: dict = {}
    for _ in range(int(timeout / 0.05)):
        await real_sleep(0.05)
        data = (await client.get(f'/api/tasks/{task_id}')).json()
        if data.get('status') == status:
            return data
    raise AssertionError(
        f'task {task_id} never reached {status} '
        f'(last status: {data.get("status")})'
    )


async def _task_in_state(client, fake, state, store_id, session_maker=None):
    """Create a task and drive it into *state*; return its id.

    Every state is reached the way the SERVER reaches it: the fake
    agent plans, runs, completes, and is stopped. One is not — a task
    parked on a condition. Parking is the scheduler's job and has no
    route of its own, so that row is written directly: the row IS
    the state.
    """
    if state == 'pending':
        r = await client.post(
            '/api/tasks',
            json={
                'title': 'Pending',
                'description': 'x',
                'defer_start': True,
            },
        )
        assert r.status_code == 200, r.text
        return r.json()['id']

    if state == 'waiting':
        r = await client.post(
            '/api/tasks',
            json={
                'title': 'Waiting',
                'description': 'x',
                'store_id': store_id,
                'defer_start': True,
            },
        )
        assert r.status_code == 200, r.text
        task_id = r.json()['id']
        assert session_maker is not None
        async with session_maker() as db:
            task = await db.get(Task, task_id)
            task.status = TaskStatus.WAITING
            task.wait_condition = json.dumps({
                'reason': 'Waiting for the store to come back',
                'check_strategy': 'manual',
            })
            await db.commit()
        return task_id

    if state == 'queued':
        # The queue's park: the task is waiting for a free run slot.
        # The queue scheduler does not start without the app
        # lifespan, so a queued task stays queued — which is exactly
        # the state this test wants.
        r = await client.post(
            '/api/tasks',
            json={
                'title': 'Queued',
                'description': 'x',
                'store_id': store_id,
                'defer_start': True,
            },
        )
        assert r.status_code == 200, r.text
        task_id = r.json()['id']
        assert session_maker is not None
        async with session_maker() as db:
            task = await db.get(Task, task_id)
            task.status = TaskStatus.QUEUED
            await db.commit()
        return task_id

    if state == 'designing':
        # A long design delay keeps the task in DESIGNING while the
        # test reads its guidance.
        fake.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Step', result='done', design_delay=30.0
        )
        r = await client.post(
            '/api/tasks',
            json={
                'title': 'Designing',
                'description': 'x',
                'store_id': store_id,
                'plan_mode': True,
            },
        )
        assert r.status_code == 200, r.text
        task_id = r.json()['id']
        await _wait_for_status(client, task_id, 'designing')
        return task_id

    if state == 'planned':
        fake.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Step', result='done'
        )
        r = await client.post(
            '/api/tasks',
            json={
                'title': 'Planned',
                'description': 'x',
                'plan_mode': True,
            },
        )
        assert r.status_code == 200, r.text
        task_id = r.json()['id']
        await _wait_for_status(client, task_id, 'planned')
        return task_id

    if state in ('running', 'question'):
        gate = asyncio.Event()
        fake.default_scenario = FakeAgentScenario(
            result='done',
            gate=gate,
            pending_question=(
                _GUIDANCE_QUESTION if state == 'question' else None
            ),
        )
        r = await client.post(
            '/api/tasks',
            json={
                'title': state.title(),
                'description': 'x',
                'store_id': store_id,
            },
        )
        assert r.status_code == 200, r.text
        task_id = r.json()['id']
        await fake.wait_started(task_id)
        await _wait_for_status(client, task_id, 'running')
        return task_id

    if state == 'completed':
        fake.default_scenario = FakeAgentScenario(result='done')
        r = await client.post(
            '/api/tasks',
            json={
                'title': 'Completed',
                'description': 'x',
                'store_id': store_id,
            },
        )
        assert r.status_code == 200, r.text
        task_id = r.json()['id']
        await _wait_for_status(client, task_id, 'completed')
        return task_id

    if state == 'failed':
        # A stopped run is the real way a task FAILS.
        gate = asyncio.Event()
        fake.default_scenario = FakeAgentScenario(result='done', gate=gate)
        r = await client.post(
            '/api/tasks',
            json={
                'title': 'Failed',
                'description': 'x',
                'store_id': store_id,
            },
        )
        assert r.status_code == 200, r.text
        task_id = r.json()['id']
        await fake.wait_started(task_id)
        await _wait_for_status(client, task_id, 'running')
        await client.post(f'/api/tasks/{task_id}/agent/stop')
        await _wait_for_status(client, task_id, 'failed')
        return task_id

    raise AssertionError(f'unknown state: {state}')


async def _settle(client, task_id: str, action: str | None = None) -> None:
    """Leave a task finished, with no writer still running behind it.

    ``send_message``, ``review_plan`` and ``retry_task`` start a run
    OFF the request path: the tool returns while the agent is still
    going. Waiting that run out is what keeps its writes inside the
    test; stopping it instead sends the pipeline down its revert path,
    which writes after the test has returned.
    """
    if action in ('send_message', 'review_plan', 'retry_task'):
        await wait_for_task(client, task_id, target='completed')
    # settle_task drains: it waits for the app's background work to
    # unwind (the shared drain in tests/workflow/conftest.py).
    await settle_task(client, task_id)


def _action_arguments(action: str, task_id: str) -> dict:
    """The arguments for calling *action* on *task_id*."""
    if action == 'wait_task':
        return {'task_id': task_id, 'timeout_seconds': 1}
    if action == 'review_plan':
        return {'task_id': task_id, 'approve': True}
    if action == 'send_message':
        return {'task_id': task_id, 'message': 'carry on'}
    if action == 'answer_question':
        return {
            'task_id': task_id,
            'answers': {'Which marketplace should I use?': 'US'},
        }
    return {'task_id': task_id}


class TestLifecycleGuidance:
    @pytest.mark.parametrize('state', LIFECYCLE_STATES)
    async def test_guidance_is_present_and_names_accepted_tools(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        state,
    ):
        store_id = await make_store(admin_client, f'State {state}')
        task_id = await _task_in_state(
            admin_client,
            install_fake_agent,
            state,
            store_id,
            override_async_session,
        )
        try:
            guidance, _ = await call_tool(
                admin_client, 'get_task', {'task_id': task_id}
            )
            assert guidance['status'] == _EXPECTED_STATUS[state], state
            assert guidance['status_meaning'], state
            assert guidance['next_actions'], state

            for action in guidance['next_actions']:
                # A FRESH task in the same state: an action may change
                # the task (retry, approve, delete), so each action is
                # tried on a task of its own — in the state the
                # guidance named it for.
                fresh = await _task_in_state(
                    admin_client,
                    install_fake_agent,
                    state,
                    store_id,
                    override_async_session,
                )
                try:
                    _, envelope = await call_tool(
                        admin_client,
                        action,
                        _action_arguments(action, fresh),
                    )
                    assert envelope.get('isError') is not True, (
                        f'{action} refused in {state}: '
                        f'{envelope["content"][0]["text"]}'
                    )
                finally:
                    await _settle(admin_client, fresh, action)

            if 'wait_task' not in guidance['next_actions']:
                # Where the table does NOT name wait_task, the long
                # poll must return INSTANTLY — advising a wait that
                # returns at once is a busy-loop dressed as advice.
                # (Where it IS named, it may block; that is the
                # point of it.)
                fresh = await _task_in_state(
                    admin_client,
                    install_fake_agent,
                    state,
                    store_id,
                    override_async_session,
                )
                try:
                    waited, _ = await call_tool(
                        admin_client,
                        'wait_task',
                        {'task_id': fresh, 'timeout_seconds': 5},
                    )
                    assert waited['timed_out'] is False, (
                        f'wait_task blocked in {state} but was not named: '
                        f'{waited}'
                    )
                    assert waited['reason'] != 'timeout', state
                finally:
                    await _settle(admin_client, fresh)
        finally:
            await _settle(admin_client, task_id)

    async def test_delete_is_not_offered_while_a_child_runs(
        self, admin_client, install_fake_agent
    ):
        """The delete guard is about the SUBTREE, not the row.

        A parent that failed while a child still runs cannot be
        deleted — the route refuses with 409 "stop them first" — so
        the guidance must NOT name delete_task, and calling it must
        be refused. Once the child settles, the delete is named AND
        accepted.
        """
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='done', gate=gate
        )
        store_id = await make_store(admin_client, 'Delete Guard Store')

        # The parent runs, so it can be stopped into 'failed'.
        parent, _ = await call_tool(
            admin_client,
            'create_task',
            {'description': 'Parent task', 'store_id': store_id},
        )
        parent_id = parent['task_id']
        await install_fake_agent.wait_started(parent_id)

        # A child under it, running too.
        child = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Child task',
                'description': 'x',
                'store_id': store_id,
                'parent_task_id': parent_id,
            },
        )
        assert child.status_code == 200, child.text
        child_id = child.json()['id']
        await install_fake_agent.wait_started(child_id)

        # The parent fails while the child keeps running.
        await call_tool(admin_client, 'stop_task', {'task_id': parent_id})
        try:
            guidance, _ = await call_tool(
                admin_client, 'get_task', {'task_id': parent_id}
            )
            assert guidance['status'] == 'failed'
            assert 'delete_task' not in guidance['next_actions'], (
                'delete_task named while a child is still running'
            )

            # And the call the guidance did not name is refused.
            _, envelope = await call_tool(
                admin_client, 'delete_task', {'task_id': parent_id}
            )
            assert envelope['isError'] is True
            text = envelope['content'][0]['text']
            assert 'stop them first' in text, text

            # Once the child settles, the delete is offered...
            await call_tool(admin_client, 'stop_task', {'task_id': child_id})
            guidance, _ = await call_tool(
                admin_client, 'get_task', {'task_id': parent_id}
            )
            assert 'delete_task' in guidance['next_actions']

            # ...and accepted.
            _, envelope = await call_tool(
                admin_client, 'delete_task', {'task_id': parent_id}
            )
            assert envelope.get('isError') is not True, envelope['content'][0][
                'text'
            ]
        finally:
            await _settle(admin_client, parent_id)
            await _settle(admin_client, child_id)

    async def test_initialize_names_every_task_status(
        self, admin_client, install_fake_agent
    ):
        """The instructions the SERVER serves name the whole vocabulary.

        A client that shows the instructions to its model gives it the
        status words up front — so every one of them must be there,
        not just the ones a task happened to reach.
        """
        r = await admin_client.post(
            MCP_PATH,
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {
                    'protocolVersion': '2025-06-18',
                    'capabilities': {},
                    'clientInfo': {'name': 'test', 'version': '1'},
                },
            },
        )
        assert r.status_code == 200, r.text
        instructions = r.json()['result']['instructions']
        for member in TaskStatus:
            assert member.value in instructions, f'{member} missing'

    async def test_a_pending_question_names_the_answer_tools(
        self, admin_client, install_fake_agent
    ):
        """The question tools are named when — and only when — one is
        outstanding: answer_question 409s without a pending question,
        so naming it unconditionally would be a lie."""
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='done',
            gate=gate,
            pending_question=_GUIDANCE_QUESTION,
        )
        store_id = await make_store(admin_client, 'Question Store')
        task_id = await _task_in_state(
            admin_client,
            install_fake_agent,
            'question',
            store_id,
        )
        try:
            guidance, _ = await call_tool(
                admin_client, 'get_task', {'task_id': task_id}
            )
            assert 'answer_question' in guidance['next_actions']
            assert 'get_pending_questions' in guidance['next_actions']
        finally:
            await _settle(admin_client, task_id)

    async def test_every_task_result_carries_the_guidance(
        self, admin_client, install_fake_agent
    ):
        """Every tool that reports a task reports it WITH the block."""
        store_id = await make_store(admin_client, 'Guidance Store')

        # create_task
        created, _ = await call_tool(
            admin_client,
            'create_task',
            {'description': 'Guidance please', 'store_id': store_id},
        )
        task_id = created['task_id']
        assert created['status_meaning']
        assert created['next_actions']

        # get_task
        detail, _ = await call_tool(
            admin_client, 'get_task', {'task_id': task_id}
        )
        assert detail['status_meaning']
        assert detail['next_actions']

        # wait_task
        waited, _ = await call_tool(
            admin_client,
            'wait_task',
            {'task_id': task_id, 'timeout_seconds': 1},
        )
        assert waited['status_meaning']
        assert waited['next_actions']

        # list_tasks — every ITEM carries it
        listed, _ = await call_tool(
            admin_client, 'list_tasks', {'store_id': store_id}
        )
        assert listed['tasks']
        for item in listed['tasks']:
            assert item['status_meaning'], item
            assert 'next_actions' in item, item

        # send_message
        messaged, _ = await call_tool(
            admin_client,
            'send_message',
            {'task_id': task_id, 'message': 'and again'},
        )
        assert messaged['status_meaning']
        assert messaged['next_actions']
        # A follow-up runs off the request path — wait it out (see
        # _settle).
        await _settle(admin_client, task_id, 'send_message')

        # stop_task
        stopped, _ = await call_tool(
            admin_client, 'stop_task', {'task_id': task_id}
        )
        assert stopped['status_meaning']
        assert stopped['next_actions']

        # retry_task
        retried, _ = await call_tool(
            admin_client, 'retry_task', {'task_id': task_id}
        )
        assert retried['status_meaning']
        assert retried['next_actions']
        # The retry launches the task off the request path — wait the
        # run out so nothing is still writing when the test returns.
        await _settle(admin_client, task_id, 'retry_task')

    async def test_answer_question_result_carries_the_guidance(
        self, admin_client, install_fake_agent
    ):
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='done',
            gate=gate,
            pending_question=_GUIDANCE_QUESTION,
        )
        store_id = await make_store(admin_client, 'Answer Store')
        created, _ = await call_tool(
            admin_client,
            'create_task',
            {'description': 'Ask me', 'store_id': store_id},
        )
        task_id = created['task_id']
        await install_fake_agent.wait_started(task_id)
        try:
            answered, _ = await call_tool(
                admin_client,
                'answer_question',
                {
                    'task_id': task_id,
                    'answers': {'Which marketplace should I use?': 'US'},
                },
            )
            assert answered['ok'] is True
            assert answered['status_meaning']
            assert answered['next_actions']
        finally:
            await _settle(admin_client, task_id)

    async def test_review_plan_result_carries_the_guidance(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Step', result='done'
        )
        created, _ = await call_tool(
            admin_client,
            'create_task',
            {'description': 'Plan me', 'plan_mode': True},
        )
        task_id = created['task_id']
        await _wait_for_status(admin_client, task_id, 'planned')
        try:
            approved, _ = await call_tool(
                admin_client,
                'review_plan',
                {'task_id': task_id, 'approve': True},
            )
            assert approved['action'] == 'approve'
            assert approved['status_meaning']
            assert approved['next_actions']
        finally:
            await _settle(admin_client, task_id)
