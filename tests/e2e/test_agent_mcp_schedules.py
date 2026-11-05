"""E2E: the hosted MCP endpoint's operator surface — guards, deletes,
schedules and lifecycle guidance.

Companion to ``test_agent_mcp.py``; same mock-CLI lane (no model
credits) and the same skip guard. A SEPARATE FILE on purpose: the
e2e-mock-cli CI job names its test files explicitly.

What these pin: token-mints-token → 403; delete_task; plan-mode
create_schedule → wait_task → review_plan approve → plan_status=ready;
prompt edit → stale → replan; run_schedule refused while not ready;
next_actions per state.
"""

import logging
import os
import time

import httpx
import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import create_store
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
def store_id(api_client) -> str:
    return create_store(api_client, 'MCP A1 E2E Store')['id']


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

    A plan-only task authors the plan and finishes: the plan is
    committed onto the schedule when it completes. It does not park in
    ``planned`` for a second review — the human checkpoint for a
    schedule's plan is the schedule's own plan panel.
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


class TestScheduleLifecycle:
    """The plan-mode schedule lifecycle, as the server actually runs it.

    A schedule never fires before its plan is frozen. Creating one
    spawns a plan-only task; when that task finishes, the plan it wrote
    is committed onto the schedule (``plan_status=ready``) and the
    schedule becomes eligible to fire. Editing the prompt later makes
    the plan ``stale`` — the schedule stops firing until it is
    re-planned and approved again.
    """

    def test_plan_mode_schedule_from_create_to_ready(self, mcp, store_id):
        created = mcp.call_tool(
            'create_schedule',
            title='MCP e2e weekly report',
            description=(
                'Pull the weekly campaign report and summarise the movers.'
            ),
            store_id=store_id,
            schedule_type='weekly',
            schedule_time='03:00',
            # ARMED on purpose: this test drives the fire path. The
            # tool's default is paused (the human checkpoint) — see
            # test_create_is_paused_by_default below.
            paused=False,
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

            # Follow the plan-only task. It authors the plan; when it
            # finishes, the plan is frozen onto the schedule.
            _wait_for_plan_task(mcp, plan_task_id)

            listed = _schedule(mcp, schedule_id)
            assert listed['plan_status'] == 'ready', listed
            assert listed['fires'] is True, listed
            assert listed['plan_task_id'] is None, listed
            assert 'run_schedule' in (listed.get('next_actions') or []), listed

            # A ready schedule fires on demand.
            fired = mcp.call_tool('run_schedule', schedule_id=schedule_id)
            assert fired['ok'], fired['text']
            if fired['data'].get('task_id'):
                fired_tasks.append(fired['data']['task_id'])

            # ── prompt edit → stale → the schedule stops firing ──
            edited = mcp.call_tool(
                'update_schedule',
                schedule_id=schedule_id,
                description=(
                    'Pull the weekly report for ALL marketplaces and '
                    'summarise the movers.'
                ),
            )
            assert edited['ok'], edited['text']
            assert edited['data']['plan_status'] == 'stale', edited['data']
            assert edited['data']['fires'] is False, edited['data']

            refused = mcp.call_tool('run_schedule', schedule_id=schedule_id)
            assert not refused['ok'], 'a stale schedule must not fire'
            assert 'plan_status' in refused['text'], refused['text']

            # ── replan → a new plan task → ready again ──
            replanned = mcp.call_tool(
                'update_schedule', schedule_id=schedule_id, replan=True
            )
            assert replanned['ok'], replanned['text']
            assert replanned['data']['plan_status'] == 'planning', replanned[
                'data'
            ]
            new_plan_task = replanned['data']['plan_task_id']
            assert new_plan_task, replanned['data']
            assert new_plan_task != plan_task_id

            _wait_for_plan_task(mcp, new_plan_task)
            listed = _schedule(mcp, schedule_id)
            assert listed['plan_status'] == 'ready', listed
            assert listed['fires'] is True, listed
        finally:
            removed = mcp.call_tool('delete_schedule', schedule_id=schedule_id)
            assert removed['ok'], removed['text']
            for task_id in fired_tasks:
                mcp.call_tool('delete_task', task_id=task_id)


class TestCreateIsPausedByDefault:
    """The MCP create path arms nothing by default.

    The web UI creates active schedules; the hosted MCP tool creates
    them PAUSED, because the server accepts a schedule's plan without
    anyone reading it — an armed schedule fires on its own the moment
    the plan freezes. The agent resumes it after showing the plan to
    its user.
    """

    def test_create_is_paused_and_says_so(self, mcp, store_id):
        created = mcp.call_tool(
            'create_schedule',
            title='MCP e2e paused report',
            description='Pull the weekly campaign report.',
            store_id=store_id,
            schedule_type='weekly',
            schedule_time='03:20',
        )
        assert created['ok'], created['text']
        data = created['data']
        schedule_id = data['id']
        try:
            assert data['paused'] is True, data
            assert data['fires'] is False, data
            # The note tells the agent what to do next.
            assert 'resume' in (data.get('note') or '').lower(), data
        finally:
            removed = mcp.call_tool('delete_schedule', schedule_id=schedule_id)
            assert removed['ok'], removed['text']


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
        )
        assert created['ok'], created['text']
        task_id = created['data']['task_id']
        try:
            # A no-store task always plans; the result says which path
            # it is.
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
