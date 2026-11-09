"""E2E: the hosted MCP endpoint (``/api/mcp``) driven as an external agent.

Runs in the e2e-mock-cli CI job (server started with
``MOCK_CLI=tests/e2e/mock_cli.py``) — the mock CLI answers every task,
so **no model credits are spent**.

This is the whole external-agent story, end to end: mint a personal API
token over the REST API, then speak JSON-RPC to ``/api/mcp`` with that
token as a bearer credential — create a task, follow it to its result,
answer the question the task agent asked, review a plan, stop and retry
a task, read the knowledge base, and prove that a revoked token stops
working.

The endpoint is stateless (one JSON document per POST, no event
stream), so the client here is a few lines of httpx rather than a
dependency on the MCP SDK.
"""

import logging
import os
import time

import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import create_store
from tests.e2e.mcp_client import MCP_URL, McpClient  # noqa: F401

logger = logging.getLogger(__name__)

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — skipping MCP endpoint e2e tests. '
        'Run with: MOCK_CLI=tests/e2e/mock_cli.py ./start.sh 7777',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]


# The toolset the endpoint must expose. A literal, so a rename on the
# server fails this test loudly instead of silently passing on a subset.
EXPECTED_TOOLS = {
    'answer_question',
    'app_settings',
    'browser',
    'create_schedule',
    'create_task',
    'delete_schedule',
    'delete_task',
    'get_pending_questions',
    'get_task',
    'get_task_log',
    'image_request',
    'list_schedules',
    'list_stores',
    'list_tasks',
    'manage_chat_bot',
    'manage_email_account',
    'manage_profile',
    'manage_skill',
    'manage_store',
    'manage_ticktick',
    'manage_ziniao_account',
    'read_knowledge',
    'retry_task',
    'review_plan',
    'run_schedule',
    'send_message',
    'stop_task',
    'task_files',
    'update_schedule',
    'wait_task',
    'workspace_assistant',
    'workspace_file',
}

# The mock CLI asks exactly this question when a task carries the
# ``[[MOCK_ASK_FREE_TEXT]]`` marker (tests/e2e/mock_cli.py). The text
# carries the '(free-text-e2e)' tag, which the session-wide SSE
# auto-answer defers — so this test really does answer it.
ASK_QUESTION = 'Which marketplaces should I audit? (free-text-e2e)'


# ── Fixtures ────────────────────────────────────────


@pytest.fixture(scope='module')
def api_token(api_client):
    """Turn auth on, mint a token, hand it over — and clean up after.

    Auth is a server-wide switch and the token is a live credential, so
    both are undone when the module finishes: the other e2e modules in
    the same run expect the seeded admin to work without one.
    """
    resp = api_client.put(
        f'{BASE_URL}/api/settings', json={'auth_required': 'true'}
    )
    resp.raise_for_status()

    created = api_client.post(
        f'{BASE_URL}/api/auth/tokens',
        json={'name': 'e2e-mcp', 'expires_in_days': 1},
    )
    created.raise_for_status()
    body = created.json()
    token = body['token']
    assert token.startswith('vst_'), f'unexpected token format: {token!r}'

    try:
        yield {'token': token, 'id': body['id']}
    finally:
        api_client.delete(f'{BASE_URL}/api/auth/tokens/{body["id"]}')
        api_client.put(
            f'{BASE_URL}/api/settings', json={'auth_required': 'false'}
        )


@pytest.fixture(scope='module')
def mcp(api_token) -> McpClient:
    """An MCP client authenticated with the minted token."""
    client = McpClient(token=api_token['token'])
    yield client
    client.close()


@pytest.fixture(scope='module')
def store_id(api_client, api_token) -> str:
    """A store for the auto-mode task.

    A task with no store is always plan mode (the server forces it), so
    an auto-mode task needs a real store to act on.
    """
    return create_store(api_client, 'MCP E2E Store')['id']


def wait_until(
    mcp: McpClient,
    task_id: str,
    predicate,
    what: str,
    timeout: int = 120,
) -> dict:
    """Wait on ``wait_task`` until *predicate* holds.

    ``wait_task`` is the primary wait — it blocks until the task asks a
    question, offers a plan, or reaches a terminal state. It returns
    IMMEDIATELY when the task is parked waiting for the operator, so a
    task that is still ``planned`` a moment after an approval comes
    back at once. This loop pauses and asks again rather than reading a
    parked state as the answer.
    """
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        waited = mcp.call_tool('wait_task', task_id=task_id, timeout_seconds=30)
        assert waited['ok'], waited['text']
        last = waited['data']
        if predicate(last):
            return last
        time.sleep(1)
    raise AssertionError(
        f'task {task_id} never reached {what} (last wait={last!r})'
    )


def wait_for_terminal(mcp: McpClient, task_id: str, timeout: int = 120) -> dict:
    """Wait until the task is completed or failed."""
    return wait_until(
        mcp,
        task_id,
        lambda w: w['status'] in ('completed', 'failed'),
        'a terminal state',
        timeout=timeout,
    )


# ── Tests ───────────────────────────────────────────


class TestMcpEndpoint:
    def test_initialize_and_tool_listing(self, mcp):
        """The handshake a client performs before anything else."""
        result = mcp.rpc(
            'initialize',
            {
                'protocolVersion': '2025-06-18',
                'capabilities': {},
                'clientInfo': {'name': 'e2e', 'version': '1.0'},
            },
        )
        assert result['serverInfo']['name'] == 'vibe-seller'
        assert result['protocolVersion']
        assert result['capabilities']['tools'] == {'listChanged': False}

        # The instructions are rendered from the server's lifecycle
        # table: they must name the statuses and the wait_task reasons,
        # or a client that never shows them has no lifecycle at all.
        instructions = result['instructions']
        assert 'planned' in instructions
        assert 'review_plan' in instructions
        assert 'wait_task' in instructions
        assert 'question' in instructions

        # The notification carries no id, so the endpoint answers 202
        # with no body.
        assert mcp.notify('notifications/initialized') == 202

        listing = mcp.rpc('tools/list')
        tools = listing['tools']
        assert {t['name'] for t in tools} == EXPECTED_TOOLS
        for tool in tools:
            assert tool['description'], f'{tool["name"]} has no description'
            assert tool['inputSchema']['type'] == 'object'

    def test_ping(self, mcp):
        assert mcp.rpc('ping') == {}

    def test_unknown_method_and_unknown_tool_are_errors(self, mcp):
        """A typo must not look like a success."""
        resp = mcp.post({'jsonrpc': '2.0', 'id': 99, 'method': 'tools/nope'})
        assert resp.status_code == 200
        assert resp.json()['error']['code'] == -32601

        resp = mcp.post({
            'jsonrpc': '2.0',
            'id': 100,
            'method': 'tools/call',
            'params': {'name': 'no_such_tool', 'arguments': {}},
        })
        assert resp.status_code == 200
        assert resp.json()['error']['code'] == -32601

    def test_mcp_requires_a_credential(self, mcp):
        """Auth is on for this module: no token, no endpoint."""
        anonymous = McpClient()
        try:
            resp = anonymous.post({
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/list',
            })
            assert resp.status_code == 401
            assert resp.headers.get('WWW-Authenticate') == 'Bearer'
        finally:
            anonymous.close()


class TestMcpTaskLifecycle:
    def test_list_stores(self, mcp, store_id):
        result = mcp.call_tool('list_stores')
        assert result['ok'], result['text']
        stores = result['data']['stores']
        assert store_id in {s['id'] for s in stores}

    def test_auto_task_runs_to_completion(self, mcp, store_id):
        """create → wait → get: the whole happy path over MCP."""
        created = mcp.call_tool(
            'create_task',
            title='MCP e2e auto task',
            description='Summarise the store catalogue.',
            store_id=store_id,
            plan_mode=False,
        )
        assert created['ok'], created['text']
        task_id = created['data']['task_id']
        assert created['data']['plan_mode'] is False
        assert created['data']['store_id'] == store_id

        waited = wait_for_terminal(mcp, task_id)
        assert waited['status'] == 'completed', waited
        assert waited['reason'] == 'terminal'

        detail = mcp.call_tool('get_task', task_id=task_id)
        assert detail['ok'], detail['text']
        assert detail['data']['status'] == 'completed'
        assert detail['data']['result'], 'a completed task must carry a result'
        assert detail['data']['error'] is None

        listing = mcp.call_tool('list_tasks', store_id=store_id, limit=20)
        assert listing['ok'], listing['text']
        assert task_id in {t['id'] for t in listing['data']['tasks']}

    def test_plan_task_is_reviewed_then_approved(self, mcp):
        """A store-less task plans first; review_plan releases it.

        The mock CLI emits a plan and waits for the operator, so the
        task parks in ``planned`` until review_plan approves it.
        """
        created = mcp.call_tool(
            'create_task',
            title='MCP e2e plan task',
            description='Plan the work, then do it.',
        )
        assert created['ok'], created['text']
        task_id = created['data']['task_id']
        # A task with no store always plans first — the server forces
        # it, whatever the caller asked for.
        assert created['data']['plan_mode'] is True

        waited = wait_until(
            mcp, task_id, lambda w: w['status'] == 'planned', 'a plan'
        )
        assert waited['reason'] == 'needs_operator'

        detail = mcp.call_tool('get_task', task_id=task_id)
        assert detail['ok'], detail['text']
        assert detail['data']['plan'], 'a planned task must carry its plan'

        approved = mcp.call_tool('review_plan', task_id=task_id, approve=True)
        assert approved['ok'], approved['text']

        waited = wait_for_terminal(mcp, task_id)
        assert waited['status'] == 'completed', waited

        detail = mcp.call_tool('get_task', task_id=task_id)
        assert detail['data']['status'] == 'completed'
        assert detail['data']['result']

    def test_rejected_plan_comes_back_for_revision(self, mcp):
        """A rejection must reach the task agent, not vanish.

        The mock CLI answers a ``deny`` with a revised plan (the
        original plus '(revised based on feedback)'), so the revised
        text in the task proves the feedback was delivered.
        """
        created = mcp.call_tool(
            'create_task',
            title='MCP e2e plan rejection',
            description='Plan the work, then do it.',
        )
        assert created['ok'], created['text']
        task_id = created['data']['task_id']

        waited = wait_until(
            mcp, task_id, lambda w: w['status'] == 'planned', 'a plan'
        )
        assert waited['reason'] == 'needs_operator'

        rejected = mcp.call_tool(
            'review_plan',
            task_id=task_id,
            approve=False,
            feedback='Cover every marketplace, not just one.',
        )
        assert rejected['ok'], rejected['text']

        # The agent re-plans: the task goes back to designing and then
        # parks in 'planned' again with the revised plan.
        deadline = time.time() + 60
        plan = ''
        while time.time() < deadline:
            detail = mcp.call_tool('get_task', task_id=task_id)
            assert detail['ok'], detail['text']
            plan = detail['data']['plan'] or ''
            if '(revised based on feedback)' in plan:
                break
            time.sleep(1)
        assert '(revised based on feedback)' in plan, (
            f'the rejection never reached the agent (plan={plan!r})'
        )

        # Rejecting without feedback is refused — a silent rejection
        # would produce the same plan again.
        empty = mcp.call_tool('review_plan', task_id=task_id, approve=False)
        assert not empty['ok']
        assert 'feedback' in empty['text']

        # The task is still parked on its revised plan, with the agent
        # holding one of the server's agent slots (there are only two
        # by default). Stop it, or the next test in this module waits
        # for a slot that is not coming.
        stopped = mcp.call_tool('stop_task', task_id=task_id)
        assert stopped['ok'], stopped['text']

    def test_agent_question_is_answered_by_key(self, mcp):
        """AskUserQuestion → get_pending_questions → answer_question.

        The answer is keyed by the FULL question text. The mock CLI
        echoes back what it received ('<question> => <answer>'), so the
        echo in the task result proves the key matched.
        """
        created = mcp.call_tool(
            'create_task',
            title='MCP e2e question',
            description=(
                '[[MOCK_ASK_FREE_TEXT]] audit the marketplaces and tell '
                'me what you found.'
            ),
        )
        assert created['ok'], created['text']
        task_id = created['data']['task_id']

        waited = wait_until(
            mcp, task_id, lambda w: w['reason'] == 'question', 'a question'
        )
        assert waited['pending_question'], waited

        pending = mcp.call_tool('get_pending_questions', task_id=task_id)
        assert pending['ok'], pending['text']
        assert pending['data']['pending'] is True
        questions = pending['data']['questions']
        assert questions, 'the agent asked nothing'
        assert ASK_QUESTION in {q.get('question') for q in questions}

        answered = mcp.call_tool(
            'answer_question',
            task_id=task_id,
            answers={ASK_QUESTION: 'US only'},
        )
        assert answered['ok'], answered['text']

        waited = wait_for_terminal(mcp, task_id)
        assert waited['status'] == 'completed', waited

        detail = mcp.call_tool('get_task', task_id=task_id)
        assert detail['ok'], detail['text']
        result = detail['data']['result'] or ''
        assert '=> US only' in result, (
            f'the answer never reached the agent (result={result!r})'
        )

    def test_stop_and_retry(self, mcp):
        """An external agent can stop a parked task and start it again."""
        created = mcp.call_tool(
            'create_task',
            title='MCP e2e stop and retry',
            description='Plan the work, then do it.',
        )
        assert created['ok'], created['text']
        task_id = created['data']['task_id']

        waited = wait_until(
            mcp, task_id, lambda w: w['status'] == 'planned', 'a plan'
        )
        assert waited['reason'] == 'needs_operator'

        stopped = mcp.call_tool('stop_task', task_id=task_id)
        assert stopped['ok'], stopped['text']

        deadline = time.time() + 60
        status = ''
        while time.time() < deadline:
            detail = mcp.call_tool('get_task', task_id=task_id)
            assert detail['ok'], detail['text']
            status = detail['data']['status']
            if status == 'failed':
                break
            time.sleep(1)
        assert status == 'failed', f'the task kept running (status={status})'

        retried = mcp.call_tool('retry_task', task_id=task_id)
        assert retried['ok'], retried['text']

        # The retry starts the task over: it re-plans, so it parks in
        # 'planned' again rather than jumping straight to a result.
        deadline = time.time() + 90
        status = ''
        while time.time() < deadline:
            detail = mcp.call_tool('get_task', task_id=task_id)
            status = detail['data']['status']
            if status in ('planned', 'completed', 'failed'):
                break
            time.sleep(1)
        assert status in ('planned', 'completed'), (
            f'the retry did not restart the task (status={status})'
        )

        if status == 'planned':
            approved = mcp.call_tool(
                'review_plan', task_id=task_id, approve=True
            )
            assert approved['ok'], approved['text']
            waited = wait_for_terminal(mcp, task_id)
            assert waited['status'] == 'completed', waited

    def test_read_knowledge_is_read_only(self, mcp):
        """The knowledge tool lists, and refuses to write."""
        listed = mcp.call_tool('read_knowledge', section='knowledge')
        assert listed['ok'], listed['text']
        assert 'files' in listed['data']
        assert isinstance(listed['data']['total'], int)

        # A path outside the knowledge base is refused — the tool is a
        # reader, not a second way into the workspace.
        refused = mcp.call_tool('read_knowledge', path='app/main.py')
        assert not refused['ok']
        assert 'knowledge/' in refused['text']


class TestMcpTokenLifecycle:
    def test_revoked_token_is_refused(self, mcp, api_client):
        """Revoking a token really cuts it off — even with auth on."""
        created = api_client.post(
            f'{BASE_URL}/api/auth/tokens',
            json={'name': 'e2e-mcp-to-revoke', 'expires_in_days': 1},
        )
        created.raise_for_status()
        body = created.json()

        doomed = McpClient(token=body['token'])
        try:
            result = doomed.call_tool('list_stores')
            assert result['ok'], result['text']

            revoked = api_client.delete(
                f'{BASE_URL}/api/auth/tokens/{body["id"]}'
            )
            revoked.raise_for_status()

            resp = doomed.post({
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/list',
            })
            assert resp.status_code == 401, resp.text
            assert resp.headers.get('WWW-Authenticate') == 'Bearer'
        finally:
            doomed.close()

    def test_unknown_token_is_refused(self, mcp):
        """A token that was never minted is refused, not ignored."""
        bogus = McpClient(token='vst_not_a_real_token_at_all')
        try:
            resp = bogus.post({
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/list',
            })
            assert resp.status_code == 401, resp.text
        finally:
            bogus.close()
