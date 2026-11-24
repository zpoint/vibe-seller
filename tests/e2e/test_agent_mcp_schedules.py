"""E2E: the hosted MCP endpoint's operator surface — guards, deletes,
schedules and lifecycle guidance.

Companion to ``test_agent_mcp.py``; same mock-CLI lane (no model
credits) and the same skip guard. A SEPARATE FILE on purpose: the
e2e-mock-cli CI job names its test files explicitly.

What these pin: token-mints-token → 403; delete_task; a blank agent's
two schedule flows — auto (create → it fires its prompt) and review
(create → wait_task → get_attention → review_schedule_plan approve →
ready → fires; request changes → re-plan; prompt edit → re-plan for
approval; run_schedule refused without an approved plan); next_actions
per state.
"""

import logging
import os
import time

import httpx
import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import create_store, run_tag
from tests.e2e.test_agent_mcp import McpClient, wait_until

logger = logging.getLogger(__name__)

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — skipping MCP e2e tests. '
        'Run with: MOCK_CLI=tests/e2e/mock_cli.py ./start.sh 7777',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]


# ── Fixtures ────────────────────────────────────────


@pytest.fixture(scope='module')
def api_token(api_client):
    """A minted token, with auth ON for the module (mirrors test_agent_mcp)."""
    resp = api_client.put(
        f'{BASE_URL}/api/settings', json={'auth_required': 'true'}
    )
    resp.raise_for_status()

    created = api_client.post(
        f'{BASE_URL}/api/auth/tokens',
        json={'name': 'e2e-mcp-a1', 'expires_in_days': 1},
    )
    created.raise_for_status()
    body = created.json()
    try:
        yield {'token': body['token'], 'id': body['id']}
    finally:
        api_client.delete(f'{BASE_URL}/api/auth/tokens/{body["id"]}')
        api_client.put(
            f'{BASE_URL}/api/settings', json={'auth_required': 'false'}
        )


@pytest.fixture(scope='module')
def mcp(api_token) -> McpClient:
    client = McpClient(token=api_token['token'])
    yield client
    client.close()


@pytest.fixture(scope='module')
def store_id(api_client):
    # A unique name and a delete on teardown: a second run on the same
    # server must not collide with the first one's store.
    store = create_store(api_client, f'mcp-a1-e2e-{run_tag()}')
    yield store['id']
    api_client.delete(f'{BASE_URL}/api/stores/{store["id"]}')


def _schedule(mcp: McpClient, schedule_id: str) -> dict:
    """One schedule out of ``list_schedules``."""
    listed = mcp.call_tool('list_schedules')
    assert listed['ok'], listed['text']
    for schedule in listed['data']['schedules']:
        if schedule['id'] == schedule_id:
            return schedule
    raise AssertionError(f'schedule {schedule_id} is gone')


def _wait_for_plan_task(mcp: McpClient, task_id: str) -> None:
    """Follow a plan-only task until it has written its plan.

    A plan-only task writes the plan and finishes: the plan becomes the
    schedule's PROPOSAL (``pending_approval``). The decision is made on
    the schedule (``review_schedule_plan``), never on this task.
    """
    wait_until(
        mcp,
        task_id,
        lambda w: w['status'] in ('completed', 'failed', 'planned'),
        'the plan task to finish',
    )


def _approve_plan_task(mcp: McpClient, task_id: str) -> None:
    """Approve a plan-only task and wait for it to complete."""
    approved = mcp.call_tool('review_plan', task_id=task_id, approve=True)
    assert approved['ok'], approved['text']
    deadline = time.time() + 60
    while time.time() < deadline:
        detail = mcp.call_tool('get_task', task_id=task_id)
        assert detail['ok'], detail['text']
        if detail['data']['status'] == 'completed':
            return
        time.sleep(1)
    raise AssertionError(f'plan task {task_id} never completed')


# ── The token cannot mint tokens ────────────────────


class TestTokenCannotMintTokens:
    def test_token_authenticated_mint_is_refused(self, api_token):
        """A vst_ token must not be able to mint another vst_ token.

        Otherwise a leaked token replicates itself forever and revoking
        the original cuts nothing.
        """
        client = httpx.Client(timeout=30)
        try:
            resp = client.post(
                f'{BASE_URL}/api/auth/tokens',
                json={'name': 'should-not-exist', 'expires_in_days': 1},
                headers={'Authorization': f'Bearer {api_token["token"]}'},
            )
            assert resp.status_code == 403, resp.text
        finally:
            client.close()

    def test_session_mint_still_works(self, api_client, api_token):
        """The same mint from a logged-in session is fine (201/200)."""
        resp = api_client.post(
            f'{BASE_URL}/api/auth/tokens',
            json={'name': 'e2e-mcp-a1-session', 'expires_in_days': 1},
        )
        assert resp.status_code in (200, 201), resp.text
        api_client.delete(f'{BASE_URL}/api/auth/tokens/{resp.json()["id"]}')


# ── delete_task ─────────────────────────────────────


class TestDeleteTask:
    def test_completed_task_deletes_and_then_is_gone(self, mcp, store_id):
        created = mcp.call_tool(
            'create_task',
            description='Write one line to a scratch file.',
            store_id=store_id,
            plan_mode=False,
        )
        assert created['ok'], created['text']
        task_id = created['data']['task_id']

        waited = wait_until(
            mcp,
            task_id,
            lambda w: w['status'] in ('completed', 'failed'),
            'a terminal state',
        )
        assert waited['status'] == 'completed', waited

        deleted = mcp.call_tool('delete_task', task_id=task_id)
        assert deleted['ok'], deleted['text']

        gone = mcp.call_tool('get_task', task_id=task_id)
        assert not gone['ok']
        assert 'not found' in gone['text'].lower()


# ── The schedule lifecycle ──────────────────────────


def _approve_pending(mcp: McpClient, schedule_id: str) -> dict:
    """What a blank agent does after its user said yes."""
    attention = mcp.call_tool('get_attention')
    assert attention['ok'], attention['text']
    item = next(
        a
        for a in attention['data']['schedule_plan_approvals']
        if a['schedule_id'] == schedule_id
    )
    assert item['proposed_plan'], item
    decided = mcp.call_tool(
        'review_schedule_plan',
        schedule_id=schedule_id,
        approve=True,
        plan_version=item['plan_version'],
    )
    assert decided['ok'], decided['text']
    return decided['data']


class TestAutoSchedule:
    """The default: an auto schedule fires its prompt — nothing to plan,
    nothing to approve."""

    def test_auto_schedule_fires_its_prompt(self, mcp, store_id):
        created = mcp.call_tool(
            'create_schedule',
            title='MCP e2e auto report',
            description='Write one line to a scratch file.',
            store_id=store_id,
            schedule_type='weekly',
            schedule_time='03:10',
        )
        assert created['ok'], created['text']
        data = created['data']
        schedule_id = data['id']
        fired_tasks: list[str] = []
        try:
            assert data['review_plan'] is False, data
            assert data['paused'] is False, data
            assert data['plan_status'] == 'none', data
            assert data['fires'] is True, data
            assert data['plan_task_id'] is None, data

            fired = mcp.call_tool('run_schedule', schedule_id=schedule_id)
            assert fired['ok'], fired['text']
            task_id = fired['data']['task_id']
            fired_tasks.append(task_id)
            waited = wait_until(
                mcp,
                task_id,
                lambda w: w['status'] in ('completed', 'failed'),
                'the auto fire to finish',
            )
            assert waited['status'] == 'completed', waited
        finally:
            removed = mcp.call_tool('delete_schedule', schedule_id=schedule_id)
            assert removed['ok'], removed['text']
            for task_id in fired_tasks:
                mcp.call_tool('delete_task', task_id=task_id)


class TestReviewSchedule:
    """A review schedule, as the server actually runs it.

    It never fires a plan nobody approved. Creating one spawns a
    plan-only task; when it finishes, the plan waits for approval
    (``pending_approval``) for as long as it takes. Approval makes it
    ``ready`` and the schedule fires it. Editing the prompt later drops
    the approved plan and re-plans for approval.
    """

    def test_review_schedule_from_create_to_fire(self, mcp, store_id):
        created = mcp.call_tool(
            'create_schedule',
            title='MCP e2e weekly report',
            description=(
                'Pull the weekly campaign report and summarise the movers.'
            ),
            store_id=store_id,
            schedule_type='weekly',
            schedule_time='03:00',
            review_plan=True,
        )
        assert created['ok'], created['text']
        data = created['data']
        schedule_id = data['id']
        fired_tasks: list[str] = []
        try:
            # Creating arms nothing: the schedule cannot fire yet, and
            # the result says so.
            assert data['plan_status'] == 'planning', data
            assert data['fires'] is False, data
            assert data['plan_status_meaning'], data
            plan_task_id = data['plan_task_id']
            assert plan_task_id, f'no plan task id: {data}'
            assert 'wait_task' in (data.get('next_actions') or []), data

            _wait_for_plan_task(mcp, plan_task_id)
            listed = _schedule(mcp, schedule_id)
            assert listed['plan_status'] == 'pending_approval', listed
            assert listed['fires'] is False, listed
            assert listed['proposed_plan'], listed
            assert listed['plan'] is None, 'the server never approves'

            refused = mcp.call_tool('run_schedule', schedule_id=schedule_id)
            assert not refused['ok'], 'an unapproved plan must not fire'
            assert 'approv' in refused['text'], refused['text']

            approved = _approve_pending(mcp, schedule_id)
            assert approved['plan_status'] == 'ready', approved
            assert approved['fires'] is True, approved

            fired = mcp.call_tool('run_schedule', schedule_id=schedule_id)
            assert fired['ok'], fired['text']
            fired_tasks.append(fired['data']['task_id'])
            waited = wait_until(
                mcp,
                fired['data']['task_id'],
                lambda w: w['status'] in ('completed', 'failed'),
                'the approved fire to finish',
            )
            assert waited['status'] == 'completed', waited

            # ── prompt edit → the approved plan is dropped, re-plan ──
            edited = mcp.call_tool(
                'update_schedule',
                schedule_id=schedule_id,
                description=(
                    'Pull the weekly report for ALL marketplaces and '
                    'summarise the movers.'
                ),
            )
            assert edited['ok'], edited['text']
            assert edited['data']['plan_status'] == 'planning', edited['data']
            assert edited['data']['fires'] is False, edited['data']
            new_plan_task = edited['data']['plan_task_id']
            assert new_plan_task and new_plan_task != plan_task_id

            _wait_for_plan_task(mcp, new_plan_task)
            # ── request changes → another planning round ──
            pending = _schedule(mcp, schedule_id)
            sent_back = mcp.call_tool(
                'review_schedule_plan',
                schedule_id=schedule_id,
                approve=False,
                feedback='Only campaigns spending more than 50 a day.',
                plan_version=pending['plan_version'],
            )
            assert sent_back['ok'], sent_back['text']
            third = sent_back['data']['plan_task_id']
            assert third and third != new_plan_task
            _wait_for_plan_task(mcp, third)
            approved = _approve_pending(mcp, schedule_id)
            assert approved['fires'] is True, approved
        finally:
            removed = mcp.call_tool('delete_schedule', schedule_id=schedule_id)
            assert removed['ok'], removed['text']
            for task_id in fired_tasks:
                mcp.call_tool('delete_task', task_id=task_id)


# ── Lifecycle guidance in results ───────────────────


class TestLifecycleGuidance:
    def test_next_actions_match_the_state(self, mcp):
        """Every task result says what to do next — and it is right.

        The guidance comes from the server's own lifecycle table, so
        the assertions here are EXACT sets, not membership: a tool that
        the server would refuse in that state (review_plan while the
        task is running, answer_question with no pending question)
        must not be offered.
        """
        created = mcp.call_tool(
            'create_task',
            description='Plan a small piece of work, then do it.',
            plan_mode=True,
        )
        assert created['ok'], created['text']
        task_id = created['data']['task_id']
        try:
            # Plan mode is what was asked for; the result says which
            # path it is.
            assert created['data']['plan_mode'] is True
            assert created['data']['status'] == 'pending', created['data']
            assert created['data'].get('status_meaning'), created['data']
            assert set(created['data']['next_actions']) == {
                'wait_task',
                'retry_task',
                'delete_task',
            }, created['data']

            _wait_for_plan_task(mcp, task_id)
            planned = mcp.call_tool('get_task', task_id=task_id)
            assert planned['ok'], planned['text']
            # Plan ready: review it. No pending question, so
            # answer_question is not on offer.
            assert set(planned['data']['next_actions']) == {
                'review_plan',
                'stop_task',
                'delete_task',
            }, planned['data']
            assert planned['data'].get('status_meaning'), planned['data']

            _approve_plan_task(mcp, task_id)
            done = mcp.call_tool('get_task', task_id=task_id)
            assert done['data']['status'] == 'completed', done['data']
            # Done: continue it (send_message) or start over
            # (retry_task) — but there is no plan left to review.
            assert set(done['data']['next_actions']) == {
                'send_message',
                'retry_task',
                'delete_task',
            }, done['data']
        finally:
            # NEVER leave a task parked on a plan: an unapproved plan
            # holds one of the server's agent slots (the stdout reader
            # is blocked in the approval hook, so the agent's exit is
            # never noticed and the slot is never released). Stop it.
            mcp.call_tool('stop_task', task_id=task_id)
            mcp.call_tool('delete_task', task_id=task_id)
