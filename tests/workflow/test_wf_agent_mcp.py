"""Workflow tests for the hosted MCP endpoint (``POST /api/mcp``).

Real HTTP → real DB → the real route handlers; the agent is the
FakeAgent (``tests/workflow/fake_agent.py``), never a mock of
``_auto_run_task``. What these pin:

* the MCP handshake (``initialize`` → ``tools/list`` → ``tools/call``)
  and the exact tool set an external agent sees;
* that a task created over MCP runs to completion like a task created
  in the UI;
* that ``answer_question`` maps an answer onto the FULL question text
  the agent reads (keyed by the header it would otherwise miss);
* that ``review_plan`` approves and rejects the way the UI does;
* that the profile semantics match the UI: create falls back to the
  account's default profile, retry keeps the profile it replaces;
* that an unauthenticated call is a 401 with ``WWW-Authenticate``.
"""

import asyncio
import json

import pytest

from app.agent_mcp.support import McpContext
from app.agent_mcp.tools_tasks import wait_task
from app.models.user import User
from tests.workflow.conftest import drain_background_tasks, wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

MCP_PATH = '/api/mcp'

EXPECTED_TOOLS = [
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
]


async def rpc(client, method, params=None, request_id=1):
    """One JSON-RPC call."""
    payload = {'jsonrpc': '2.0', 'id': request_id, 'method': method}
    if params is not None:
        payload['params'] = params
    return await client.post(MCP_PATH, json=payload)


async def call_tool(client, name, arguments=None):
    """Call one MCP tool; return (parsed result, raw envelope).

    A tool that failed answers with ``isError`` and a plain-text
    reason, so the parsed value is the text itself in that case.
    """
    r = await rpc(
        client,
        'tools/call',
        {'name': name, 'arguments': arguments or {}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert 'error' not in body, body
    result = body['result']
    text = result['content'][0]['text']
    if result.get('isError'):
        return {'error': text}, result
    return (json.loads(text) if text else {}), result


async def settle_task(client, task_id):
    """Leave the task in a terminal state before the test returns.

    A task that is still planning or running keeps a live agent session
    and a background pipeline. The fixture then tears the database
    engine down underneath them, and the resulting connection error
    surfaces in whatever test runs NEXT — a failure in a file that has
    nothing to do with this one. So stop it (or let it finish) first.
    """
    try:
        r = await client.get(f'/api/tasks/{task_id}')
        if r.status_code == 404:
            # Deleted (delete_task): nothing runs.
            return 'deleted'
        status = r.json().get('status')
        if status in ('completed', 'failed', 'pending', 'queued'):
            # Terminal, never launched (defer_start), or queued with no
            # dispatcher running (the queue scheduler is not started
            # without the app lifespan, so a queued task never runs).
            return status
        await client.post(f'/api/tasks/{task_id}/agent/stop')
        await wait_for_task(client, task_id, target='failed', timeout=5.0)
        return 'failed'
    finally:
        # A run's pipeline keeps writing after the status flips — on
        # EVERY path out of here, including the ones where the task
        # was already terminal. Wait for it to unwind before the test
        # ends. The implementation is the fixture's drain
        # (tests/workflow/conftest.py), so the teardown and the
        # mid-test settling cannot disagree about what "drained"
        # means.
        await drain_background_tasks()


async def make_store(client, name='MCP Store'):
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code == 200
    return r.json()['id']


class TestHandshake:
    async def test_initialize_reports_tools_capability(
        self, admin_client, install_fake_agent
    ):
        r = await rpc(
            admin_client,
            'initialize',
            {
                'protocolVersion': '2025-06-18',
                'capabilities': {},
                'clientInfo': {'name': 'test', 'version': '0'},
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body['jsonrpc'] == '2.0'
        assert body['id'] == 1
        result = body['result']
        # The server answers with the revision the client asked for
        # when it speaks it.
        assert result['protocolVersion'] == '2025-06-18'
        assert result['capabilities']['tools'] == {'listChanged': False}
        assert result['serverInfo']['name'] == 'vibe-seller'

    async def test_initialize_negotiates_unknown_version(
        self, admin_client, install_fake_agent
    ):
        r = await rpc(
            admin_client,
            'initialize',
            {'protocolVersion': '1999-01-01'},
        )
        assert r.json()['result']['protocolVersion'] == '2025-11-25'

    async def test_tools_list_is_the_documented_toolset(
        self, admin_client, install_fake_agent
    ):
        r = await rpc(admin_client, 'tools/list')
        body = r.json()
        tools = body['result']['tools']
        assert [tool['name'] for tool in tools] == EXPECTED_TOOLS
        for tool in tools:
            assert tool['description']
            assert tool['inputSchema']['type'] == 'object'

    async def test_ping(self, admin_client, install_fake_agent):
        r = await rpc(admin_client, 'ping')
        assert r.json()['result'] == {}

    async def test_notification_gets_no_body(
        self, admin_client, install_fake_agent
    ):
        r = await admin_client.post(
            MCP_PATH,
            json={'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        )
        assert r.status_code == 202
        assert r.content == b''

    async def test_unknown_method_is_a_jsonrpc_error(
        self, admin_client, install_fake_agent
    ):
        r = await rpc(admin_client, 'does/not/exist')
        assert r.json()['error']['code'] == -32601

    async def test_unknown_tool_is_a_jsonrpc_error(
        self, admin_client, install_fake_agent
    ):
        r = await rpc(
            admin_client, 'tools/call', {'name': 'nope', 'arguments': {}}
        )
        assert r.json()['error']['code'] == -32601

    async def test_unknown_argument_is_rejected(
        self, admin_client, install_fake_agent
    ):
        """A silently ignored argument is the worst failure for an agent."""
        r = await rpc(
            admin_client,
            'tools/call',
            {'name': 'list_tasks', 'arguments': {'stort_id': 'typo'}},
        )
        body = r.json()
        assert body['error']['code'] == -32602
        assert 'stort_id' in body['error']['message']

    async def test_missing_required_argument_is_rejected(
        self, admin_client, install_fake_agent
    ):
        r = await rpc(
            admin_client,
            'tools/call',
            {'name': 'create_task', 'arguments': {'title': 'no description'}},
        )
        body = r.json()
        assert body['error']['code'] == -32602
        assert 'description' in body['error']['message']

    async def test_malformed_body_is_rejected(
        self, admin_client, install_fake_agent
    ):
        r = await admin_client.post(
            MCP_PATH,
            content=b'{not json',
            headers={'Content-Type': 'application/json'},
        )
        assert r.status_code == 400
        assert r.json()['error']['code'] == -32700

    async def test_get_is_405_from_the_endpoint(
        self, admin_client, install_fake_agent
    ):
        """GET is answered by the MCP route, not by the SPA fallback.

        The SPA is mounted at ``/`` after the routers; without a route
        of its own, ``GET /api/mcp`` would fall through to it.
        """
        r = await admin_client.get(MCP_PATH)
        assert r.status_code == 405
        assert r.headers.get('allow') == 'POST'


class TestAuth:
    async def test_unauthenticated_call_is_401_with_challenge(
        self, unauthed_client, admin_user
    ):
        """Auth required and missing → 401 + WWW-Authenticate: Bearer."""
        r = await unauthed_client.post(
            MCP_PATH, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}
        )
        assert r.status_code == 401
        assert r.headers.get('www-authenticate') == 'Bearer'

    async def test_unauthenticated_tool_call_is_401(
        self, unauthed_client, admin_user
    ):
        r = await unauthed_client.post(
            MCP_PATH,
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {'name': 'list_tasks', 'arguments': {}},
            },
        )
        assert r.status_code == 401
        assert r.headers.get('www-authenticate') == 'Bearer'


class TestTokenAuth:
    """A ``vst_`` personal API token is the credential an agent uses."""

    async def _mint(self, admin_client, name='agent'):
        minted = await admin_client.post(
            '/api/auth/tokens', json={'name': name}
        )
        assert minted.status_code == 200, minted.text
        body = minted.json()
        assert body['token'].startswith('vst_')
        return body

    async def test_vst_bearer_authenticates_the_endpoint(
        self, admin_client, unauthed_client, admin_user, install_fake_agent
    ):
        minted = await self._mint(admin_client)
        # The client has NO cookie: only the bearer can authenticate it.
        r = await unauthed_client.post(
            MCP_PATH,
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            headers={'Authorization': f'Bearer {minted["token"]}'},
        )
        assert r.status_code == 200, r.text
        tools = r.json()['result']['tools']
        assert [tool['name'] for tool in tools] == EXPECTED_TOOLS

    async def test_vst_bearer_can_call_a_tool(
        self, admin_client, unauthed_client, admin_user, install_fake_agent
    ):
        minted = await self._mint(admin_client)
        r = await unauthed_client.post(
            MCP_PATH,
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {'name': 'list_tasks', 'arguments': {}},
            },
            headers={'Authorization': f'Bearer {minted["token"]}'},
        )
        assert r.status_code == 200, r.text
        assert r.json()['result']['isError'] is False

    async def test_revoked_vst_bearer_is_401(
        self, admin_client, unauthed_client, admin_user, install_fake_agent
    ):
        minted = await self._mint(admin_client)
        revoked = await admin_client.delete(f'/api/auth/tokens/{minted["id"]}')
        assert revoked.status_code == 200
        r = await unauthed_client.post(
            MCP_PATH,
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            headers={'Authorization': f'Bearer {minted["token"]}'},
        )
        assert r.status_code == 401
        assert r.headers.get('www-authenticate') == 'Bearer'

    async def test_unknown_vst_bearer_is_401(
        self, unauthed_client, admin_user, install_fake_agent
    ):
        r = await unauthed_client.post(
            MCP_PATH,
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
            headers={'Authorization': 'Bearer vst_not-a-real-token'},
        )
        assert r.status_code == 401


class TestReadTools:
    async def test_list_stores(self, admin_client, install_fake_agent):
        store_id = await make_store(admin_client, 'Listed Store')
        data, _ = await call_tool(admin_client, 'list_stores')
        names = [store['id'] for store in data['stores']]
        assert store_id in names
        listed = next(
            store for store in data['stores'] if store['id'] == store_id
        )
        assert listed['name'] == 'Listed Store'

    async def test_list_tasks_filters(self, admin_client, install_fake_agent):
        store_id = await make_store(admin_client, 'Filter Store')
        created = await admin_client.post(
            '/api/tasks',
            json={'title': 'Filterable', 'store_id': store_id},
        )
        await wait_for_task(admin_client, created.json()['id'])
        data, _ = await call_tool(
            admin_client, 'list_tasks', {'store_id': store_id}
        )
        assert [task['title'] for task in data['tasks']] == ['Filterable']

        none, _ = await call_tool(
            admin_client, 'list_tasks', {'store_id': '__none__'}
        )
        assert none['tasks'] == []

    async def test_get_task_returns_detail(
        self, admin_client, install_fake_agent
    ):
        store_id = await make_store(admin_client, 'Detail Store')
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Detail me', 'store_id': store_id},
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)
        data, _ = await call_tool(
            admin_client, 'get_task', {'task_id': task_id}
        )
        assert data['id'] == task_id
        assert data['title'] == 'Detail me'
        assert 'steps' in data
        assert 'pending_question' in data

    async def test_get_task_missing_is_a_tool_error(
        self, admin_client, install_fake_agent
    ):
        _, envelope = await call_tool(
            admin_client, 'get_task', {'task_id': 'nope'}
        )
        assert envelope['isError'] is True
        assert 'not found' in envelope['content'][0]['text'].lower()

    async def test_read_knowledge_lists_and_reads(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        (mock_workspace.root / 'knowledge').mkdir(parents=True, exist_ok=True)
        (mock_workspace.root / 'knowledge' / 'catalog.md').write_text(
            '# Catalog\nShared platform notes.'
        )
        listed, _ = await call_tool(admin_client, 'read_knowledge')
        paths = [item['path'] for item in listed['files']]
        assert 'knowledge/catalog.md' in paths

        read, _ = await call_tool(
            admin_client, 'read_knowledge', {'path': 'knowledge/catalog.md'}
        )
        assert 'Shared platform notes.' in read['content']

    async def test_read_knowledge_refuses_other_paths(
        self, admin_client, install_fake_agent
    ):
        _, envelope = await call_tool(
            admin_client,
            'read_knowledge',
            {'path': '.claude/settings.json'},
        )
        assert envelope['isError'] is True

    async def test_read_knowledge_rejects_traversal(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        """A path that climbs out of knowledge/ reads nothing.

        ``knowledge/../x`` passes a naive prefix check, and the
        workspace manager's own guard only confines to the workspace
        ROOT — so without validation this would read any workspace
        file. The tool must refuse before anything is resolved.
        """
        secret = mock_workspace.root / 'CLAUDE.md'
        secret.write_text('workspace instructions — not knowledge')

        for bad in (
            'knowledge/../CLAUDE.md',
            'stores/../CLAUDE.md',
            'knowledge/./../CLAUDE.md',
            'knowledge/sub/../../CLAUDE.md',
            '/knowledge/../CLAUDE.md',
            'knowledge/a/../b.md',
            # Backslash is a separator on native Windows: these would
            # slip past a '/'-only '..' check and climb out there.
            'knowledge/..\\CLAUDE.md',
            'knowledge\\..\\CLAUDE.md',
            '\\knowledge\\catalog.md',
        ):
            data, envelope = await call_tool(
                admin_client, 'read_knowledge', {'path': bad}
            )
            assert envelope['isError'] is True, bad
            text = envelope['content'][0]['text']
            # Refused for the confinement rule, and nothing leaked.
            assert 'knowledge/ or stores/' in text, bad
            assert 'workspace instructions' not in text, bad

    async def test_read_knowledge_normalizes_safe_paths(
        self, admin_client, install_fake_agent, mock_workspace
    ):
        """Redundant separators are collapsed, not punished."""
        (mock_workspace.root / 'knowledge').mkdir(parents=True, exist_ok=True)
        (mock_workspace.root / 'knowledge' / 'catalog.md').write_text(
            'the notes'
        )
        for fine in (
            'knowledge/catalog.md',
            'knowledge/./catalog.md',
            'knowledge//catalog.md',
        ):
            data, envelope = await call_tool(
                admin_client, 'read_knowledge', {'path': fine}
            )
            assert envelope['isError'] is False, fine
            assert data['path'] == 'knowledge/catalog.md', fine
            assert 'the notes' in data['content'], fine


class TestTaskLifecycle:
    async def test_create_task_runs_to_completion(
        self, admin_client, install_fake_agent
    ):
        store_id = await make_store(admin_client, 'Run Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'title': 'MCP created task',
                'description': 'Summarise the storefront',
                'store_id': store_id,
            },
        )
        task_id = data['task_id']
        assert data['plan_mode'] is False
        await wait_for_task(admin_client, data['task_id'])

        waited, _ = await call_tool(
            admin_client,
            'wait_task',
            {'task_id': task_id, 'timeout_seconds': 20},
        )
        assert waited['status'] == 'completed'

        detail, _ = await call_tool(
            admin_client, 'get_task', {'task_id': task_id}
        )
        assert detail['result'] == 'Task completed successfully'

    async def test_wait_task_times_out_without_a_change(
        self, admin_client, install_fake_agent
    ):
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='later', gate=gate
        )
        store_id = await make_store(admin_client, 'Wait Store')
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Still running', 'store_id': store_id},
        )
        task_id = r.json()['id']
        await install_fake_agent.wait_started(task_id)
        waited, _ = await call_tool(
            admin_client,
            'wait_task',
            {'task_id': task_id, 'timeout_seconds': 1},
        )
        assert waited['status'] in ('pending', 'queued', 'running')
        assert waited['timed_out'] is True
        assert waited['reason'] == 'timeout'
        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_create_task_defaults_to_the_users_profile(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        admin_user,
    ):
        """No profile_id → the account's default profile (as the UI)."""
        async with override_async_session() as db:
            user = await db.get(User, admin_user.id)
            user.default_profile_id = 'user-default'
            await db.commit()

        store_id = await make_store(admin_client, 'Profile Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'title': 'Profile default',
                'description': 'x',
                'store_id': store_id,
            },
        )
        assert data['ai_profile_id'] == 'user-default'
        await wait_for_task(admin_client, data['task_id'])

    async def test_stop_task(self, admin_client, install_fake_agent):
        store_id = await make_store(admin_client, 'Stop Store')
        install_fake_agent.default_scenario = FakeAgentScenario(
            execute_delay=5.0, result='never'
        )
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'title': 'Stop me',
                'description': 'x',
                'store_id': store_id,
            },
        )
        task_id = data['task_id']
        await install_fake_agent.wait_started(task_id)
        await call_tool(admin_client, 'stop_task', {'task_id': task_id})
        stopped = await wait_for_task(admin_client, task_id, target='failed')
        assert stopped['status'] == 'failed'
        assert stopped['error'] == 'Stopped by user'

    async def test_retry_omitted_profile_uses_the_callers_default(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        admin_user,
    ):
        """Omitted profile_id mirrors the UI: the picker's initial
        value — the caller's default profile — not the task's last."""
        async with override_async_session() as db:
            user = await db.get(User, admin_user.id)
            user.default_profile_id = 'user-default'
            await db.commit()

        store_id = await make_store(admin_client, 'Retry Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'description': 'Retry me',
                'store_id': store_id,
                'profile_id': 'alpha',
            },
        )
        task_id = data['task_id']
        assert data['ai_profile_id'] == 'alpha'
        await wait_for_task(admin_client, task_id)

        retried, _ = await call_tool(
            admin_client, 'retry_task', {'task_id': task_id}
        )
        assert retried['profile_id'] == 'user-default'

        switched, _ = await call_tool(
            admin_client,
            'retry_task',
            {'task_id': task_id, 'profile_id': 'beta'},
        )
        assert switched['profile_id'] == 'beta'
        await wait_for_task(admin_client, task_id)

    async def test_send_message_omitted_profile_uses_the_callers_default(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        admin_user,
    ):
        """The composer always sends the picker's profile — so does
        the tool when the caller names none."""
        async with override_async_session() as db:
            user = await db.get(User, admin_user.id)
            user.default_profile_id = 'user-default'
            await db.commit()

        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='later', gate=gate
        )
        store_id = await make_store(admin_client, 'Msg Profile Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'description': 'Msg me',
                'store_id': store_id,
                'profile_id': 'alpha',
            },
        )
        task_id = data['task_id']
        await install_fake_agent.wait_started(task_id)

        sent, _ = await call_tool(
            admin_client,
            'send_message',
            {'task_id': task_id, 'message': 'go on'},
        )
        assert sent['profile_id'] == 'user-default'
        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_send_message_reaches_the_agent(
        self, admin_client, install_fake_agent
    ):
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='later', gate=gate
        )
        store_id = await make_store(admin_client, 'Message Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'title': 'Message me',
                'description': 'x',
                'store_id': store_id,
            },
        )
        task_id = data['task_id']
        await install_fake_agent.wait_started(task_id)
        await call_tool(
            admin_client,
            'send_message',
            {'task_id': task_id, 'message': 'Also check the returns'},
        )
        sent = install_fake_agent.get_calls(task_id, action='send_message')
        assert sent
        assert 'returns' in sent[-1].message
        gate.set()
        await wait_for_task(admin_client, task_id)


class TestPlanReview:
    async def test_review_plan_approve(self, admin_client, install_fake_agent):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Do it', result='Approved and done'
        )
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'title': 'Approve me',
                'description': 'x',
                'plan_mode': True,
            },
        )
        task_id = data['task_id']
        planned = await wait_for_task(admin_client, task_id, target='planned')
        assert planned['status'] == 'planned'

        approved, _ = await call_tool(
            admin_client,
            'review_plan',
            {'task_id': task_id, 'approve': True},
        )
        assert approved['action'] == 'approve'

        done = await wait_for_task(admin_client, task_id)
        assert done['status'] == 'completed'
        assert done['result'] == 'Approved and done'

    async def test_review_plan_reject_replans(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Do it', result='Revised and done'
        )
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'title': 'Reject me',
                'description': 'x',
                'plan_mode': True,
            },
        )
        task_id = data['task_id']
        await wait_for_task(admin_client, task_id, target='planned')

        rejected, _ = await call_tool(
            admin_client,
            'review_plan',
            {
                'task_id': task_id,
                'approve': False,
                'feedback': 'Add a rollback step',
            },
        )
        assert rejected['action'] == 'reject'

        revised = await wait_for_task(admin_client, task_id, target='planned')
        assert 'revised' in revised['plan']
        # The revised plan is still waiting for review — stop it so
        # the task does not outlive the test.
        await settle_task(admin_client, task_id)

    async def test_review_plan_reject_needs_feedback(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario()
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'title': 'Reject without feedback',
                'description': 'x',
                'plan_mode': True,
            },
        )
        task_id = data['task_id']
        await wait_for_task(admin_client, task_id, target='planned')
        _, envelope = await call_tool(
            admin_client,
            'review_plan',
            {'task_id': task_id, 'approve': False},
        )
        assert envelope['isError'] is True
        # The plan is still waiting for review — stop it so the task
        # does not outlive the test.
        await settle_task(admin_client, task_id)

    async def test_review_plan_accepts_the_approve_flag(
        self, admin_client, install_fake_agent
    ):
        """``approve: true`` is the documented spelling; ``action`` also
        works."""
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Do it', result='Approved by flag'
        )
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'description': 'Plan me with a flag',
                'plan_mode': True,
            },
        )
        task_id = data['task_id']
        await wait_for_task(admin_client, task_id, target='planned')
        approved, _ = await call_tool(
            admin_client,
            'review_plan',
            {'task_id': task_id, 'approve': True},
        )
        assert approved['action'] == 'approve'
        done = await wait_for_task(admin_client, task_id)
        assert done['status'] == 'completed'

    async def test_create_task_derives_a_title(
        self, admin_client, install_fake_agent
    ):
        """The description is the instruction; the title is optional."""
        store_id = await make_store(admin_client, 'Derived Title Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'description': 'Check the returns for last week',
                'store_id': store_id,
            },
        )
        detail, _ = await call_tool(
            admin_client, 'get_task', {'task_id': data['task_id']}
        )
        assert detail['title'] == 'Check the returns for last week'
        await wait_for_task(admin_client, data['task_id'])


class TestQuestions:
    async def test_answer_question_is_keyed_by_full_question_text(
        self, admin_client, install_fake_agent
    ):
        """The agent reads answers BY question text, not by header."""
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='Answered and done',
            gate=gate,
            pending_question={
                'request_id': 'req-1',
                'questions': [
                    {
                        'question': 'Which marketplace should I use?',
                        'header': 'Marketplace',
                        'options': [
                            {'label': 'US'},
                            {'label': 'AE'},
                        ],
                    }
                ],
            },
        )
        store_id = await make_store(admin_client, 'Question Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'title': 'Ask me',
                'description': 'x',
                'store_id': store_id,
            },
        )
        task_id = data['task_id']
        await install_fake_agent.wait_started(task_id)

        pending, _ = await call_tool(
            admin_client, 'get_pending_questions', {'task_id': task_id}
        )
        assert pending['pending'] is True
        assert (
            pending['questions'][0]['question']
            == 'Which marketplace should I use?'
        )

        # Answered by HEADER — the server maps it onto the full text.
        answered, _ = await call_tool(
            admin_client,
            'answer_question',
            {'task_id': task_id, 'answers': {'Marketplace': 'US'}},
        )
        assert answered['ok'] is True

        calls = install_fake_agent.get_calls(task_id, action='submit_answer')
        assert calls, 'the agent never received the answer'
        recorded = json.loads(calls[-1].message)
        assert recorded == {'Which marketplace should I use?': 'US'}

        gate.set()
        done = await wait_for_task(admin_client, task_id)
        assert done['status'] == 'completed'

    async def test_answer_question_rejects_an_unknown_key(
        self, admin_client, install_fake_agent
    ):
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            gate=gate,
            pending_question={
                'request_id': 'req-2',
                'questions': [
                    {'question': 'First question?', 'header': 'One'},
                    {'question': 'Second question?', 'header': 'Two'},
                ],
            },
        )
        store_id = await make_store(admin_client, 'Ambiguous Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'title': 'Ambiguous',
                'description': 'x',
                'store_id': store_id,
            },
        )
        task_id = data['task_id']
        await install_fake_agent.wait_started(task_id)

        _, envelope = await call_tool(
            admin_client,
            'answer_question',
            {'task_id': task_id, 'answers': {'nonsense': 'yes'}},
        )
        assert envelope['isError'] is True
        assert 'nonsense' in envelope['content'][0]['text']
        gate.set()
        await wait_for_task(admin_client, task_id)

    async def test_answer_without_a_pending_question_is_an_error(
        self, admin_client, install_fake_agent
    ):
        store_id = await make_store(admin_client, 'No Question Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'title': 'No question',
                'description': 'x',
                'store_id': store_id,
            },
        )
        task_id = data['task_id']
        _, envelope = await call_tool(
            admin_client,
            'answer_question',
            {'task_id': task_id, 'answers': {'anything': 'yes'}},
        )
        assert envelope['isError'] is True
        await wait_for_task(admin_client, task_id)


class TestTransportGuard:
    """C1: the endpoint must not be a CORS-simple-request write surface.

    On an auth-off server (the product default) a webpage could POST
    ``text/plain`` JSON-RPC at /api/mcp and create tasks as the
    default admin — a simple request, so no preflight ever happens.
    """

    async def test_non_json_content_type_is_415(
        self, admin_client, install_fake_agent
    ):
        body = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'tools/call',
            'params': {
                'name': 'create_task',
                'arguments': {'description': 'do evil'},
            },
        }
        r = await admin_client.post(
            MCP_PATH,
            content=json.dumps(body),
            headers={'Content-Type': 'text/plain'},
        )
        assert r.status_code == 415
        assert r.json()['error']['code'] == -32600
        # …and nothing ran: no task was created.
        listed = await admin_client.get('/api/tasks')
        assert listed.json() == []

    async def test_json_content_type_with_params_is_accepted(
        self, admin_client, install_fake_agent
    ):
        r = await admin_client.post(
            MCP_PATH,
            content='{"jsonrpc":"2.0","id":1,"method":"ping"}',
            headers={'Content-Type': 'application/json; charset=utf-8'},
        )
        assert r.status_code == 200
        assert r.json()['result'] == {}

    async def test_cross_site_sec_fetch_is_403(
        self, admin_client, install_fake_agent
    ):
        r = await admin_client.post(
            MCP_PATH,
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
            headers={'Sec-Fetch-Site': 'cross-site'},
        )
        assert r.status_code == 403
        assert r.json()['error']['code'] == -32002

    async def test_foreign_origin_is_403(
        self, admin_client, install_fake_agent
    ):
        r = await admin_client.post(
            MCP_PATH,
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
            headers={'Origin': 'https://evil.example'},
        )
        assert r.status_code == 403
        assert 'evil.example' in r.json()['error']['message']

    async def test_same_origin_and_cors_listed_origins_pass(
        self, admin_client, install_fake_agent
    ):
        # Same-origin: the Origin's host:port equals the request Host.
        r = await admin_client.post(
            MCP_PATH,
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
            headers={'Origin': 'http://test'},
        )
        assert r.status_code == 200
        # An origin from the app's CORS allow-list (a dev frontend).
        r2 = await admin_client.post(
            MCP_PATH,
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
            headers={'Origin': 'http://localhost:5173'},
        )
        assert r2.status_code == 200


class TestListTasksDefaultLimit:
    """S1: a bare list_tasks must not return the whole history."""

    async def test_bare_list_is_bounded_at_50(
        self, admin_client, install_fake_agent
    ):
        # defer_start keeps them PENDING — no agent runs, just rows.
        for i in range(55):
            r = await admin_client.post(
                '/api/tasks',
                json={
                    'title': f'bulk {i}',
                    'description': 'x',
                    'defer_start': True,
                },
            )
            assert r.status_code == 200
        data, _ = await call_tool(admin_client, 'list_tasks', {})
        assert len(data['tasks']) == 50
        smaller, _ = await call_tool(admin_client, 'list_tasks', {'limit': 10})
        assert len(smaller['tasks']) == 10


class TestProtocolEdges:
    """M1: malformed JSON-RPC gets an envelope, never a 500."""

    async def test_params_as_list_is_32602(
        self, admin_client, install_fake_agent
    ):
        r = await admin_client.post(
            MCP_PATH,
            json={
                'jsonrpc': '2.0',
                'id': 5,
                'method': 'tools/list',
                'params': [1, 2],
            },
        )
        assert r.status_code == 200
        assert r.json()['error']['code'] == -32602

    async def test_params_as_string_is_32602(
        self, admin_client, install_fake_agent
    ):
        r = await admin_client.post(
            MCP_PATH,
            json={'jsonrpc': '2.0', 'id': 6, 'method': 'ping', 'params': 'x'},
        )
        assert r.status_code == 200
        assert r.json()['error']['code'] == -32602


class TestBatchAndEnvelope:
    """M3: batch handling, the 401 body shape, cookie auth."""

    async def test_batch_mixes_calls_and_notifications(
        self, admin_client, install_fake_agent
    ):
        batch = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'ping'},
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'ping'},
        ]
        r = await admin_client.post(MCP_PATH, json=batch)
        assert r.status_code == 200
        body = r.json()
        # Notifications get no entry; requests are answered in order.
        assert [entry['id'] for entry in body] == [1, 2]
        assert all(entry['result'] == {} for entry in body)

    async def test_batch_of_only_notifications_is_202(
        self, admin_client, install_fake_agent
    ):
        batch = [
            {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
        ]
        r = await admin_client.post(MCP_PATH, json=batch)
        assert r.status_code == 202
        assert r.content == b''

    async def test_401_body_is_a_jsonrpc_envelope(
        self, unauthed_client, admin_user
    ):
        """If this regresses to FastAPI's {"detail": …} default, MCP
        clients cannot tell auth failure from server error."""
        r = await unauthed_client.post(
            MCP_PATH, json={'jsonrpc': '2.0', 'id': 1, 'method': 'ping'}
        )
        assert r.status_code == 401
        body = r.json()
        assert body['jsonrpc'] == '2.0'
        assert body['error']['code'] == -32001
        assert 'detail' not in body

    async def test_cookie_authenticated_call(
        self, admin_client, install_fake_agent
    ):
        """A browser session drives MCP exactly like it drives REST."""
        r = await rpc(admin_client, 'ping')
        assert r.status_code == 200
        assert r.json()['result'] == {}


class TestWaitTaskDisconnect:
    """M2: a dropped client ends the wait instead of polling on."""

    async def test_wait_task_returns_when_client_disconnects(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        admin_user,
    ):
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Gone', 'description': 'x', 'defer_start': True},
        )
        task_id = r.json()['id']

        class _Gone:
            async def is_disconnected(self):
                return True

        async with override_async_session() as db:
            ctx = McpContext(user=admin_user, db=db, request=_Gone())
            result = await wait_task(ctx, {'task_id': task_id})
        # The reason stays inside the five the contract defines; the
        # flag says the client was the reason.
        assert result['reason'] in (
            'question',
            'needs_operator',
            'waiting',
            'terminal',
            'timeout',
        )
        assert result['client_disconnected'] is True
        # Even this result carries the lifecycle block: the wait may
        # end before the first poll, and a result without the
        # guidance is the one shape every other path never produces.
        assert result['status'] == 'pending'
        assert result['status_meaning']
        assert result['next_actions']


class TestDeleteTask:
    """delete_task goes through the UI's delete route, rules and all."""

    async def test_delete_a_finished_task(
        self, admin_client, install_fake_agent
    ):
        store_id = await make_store(admin_client, 'Delete Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'description': 'Delete me when done',
                'store_id': store_id,
            },
        )
        task_id = data['task_id']
        await wait_for_task(admin_client, task_id)

        deleted, _ = await call_tool(
            admin_client, 'delete_task', {'task_id': task_id}
        )
        assert deleted['ok'] is True

        # The row is gone.
        r = await admin_client.get(f'/api/tasks/{task_id}')
        assert r.status_code == 404

    async def test_delete_refuses_a_running_task(
        self, admin_client, install_fake_agent
    ):
        """The route's own rule: an active task must be stopped first."""
        gate = asyncio.Event()
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='later', gate=gate
        )
        store_id = await make_store(admin_client, 'Delete Running Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'description': 'Do not delete me yet',
                'store_id': store_id,
            },
        )
        task_id = data['task_id']
        await install_fake_agent.wait_started(task_id)

        _, envelope = await call_tool(
            admin_client, 'delete_task', {'task_id': task_id}
        )
        assert envelope['isError'] is True
        assert 'stop' in envelope['content'][0]['text'].lower()

        # Still there.
        r = await admin_client.get(f'/api/tasks/{task_id}')
        assert r.status_code == 200
        gate.set()
        await wait_for_task(admin_client, task_id)


class TestWaitTaskReasons:
    """wait_task reports exactly the five contract reasons."""

    async def test_reason_is_one_of_the_five(
        self, admin_client, install_fake_agent
    ):
        r = await admin_client.post(
            '/api/tasks',
            json={'title': 'Reason', 'description': 'x', 'defer_start': True},
        )
        task_id = r.json()['id']
        data, _ = await call_tool(
            admin_client,
            'wait_task',
            {'task_id': task_id, 'timeout_seconds': 1},
        )
        assert data['reason'] in (
            'question',
            'needs_operator',
            'waiting',
            'terminal',
            'timeout',
        )

    async def test_planned_task_says_needs_operator(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Step', result='done'
        )
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {'description': 'Plan me', 'plan_mode': True},
        )
        task_id = data['task_id']
        await wait_for_task(admin_client, task_id, target='planned')
        waited, _ = await call_tool(
            admin_client,
            'wait_task',
            {'task_id': task_id, 'timeout_seconds': 5},
        )
        assert waited['reason'] == 'needs_operator'
        assert waited['status'] == 'planned'
        await settle_task(admin_client, task_id)


class TestApproveThenWait:
    """Item 6: an approved plan must not still look like one awaiting
    review."""

    async def test_approve_then_wait_never_says_needs_operator(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Step', result='Approved and done'
        )
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {'description': 'Approve me', 'plan_mode': True},
        )
        task_id = data['task_id']
        await wait_for_task(admin_client, task_id, target='planned')

        approved, _ = await call_tool(
            admin_client,
            'review_plan',
            {'task_id': task_id, 'approve': True},
        )
        assert approved['action'] == 'approve'
        # The approval has taken effect by the time the tool returns.
        assert approved['status'] != 'planned'

        waited, _ = await call_tool(
            admin_client,
            'wait_task',
            {'task_id': task_id, 'timeout_seconds': 10},
        )
        assert waited['reason'] != 'needs_operator'

    async def test_approve_reports_the_status_it_reached(
        self, admin_client, install_fake_agent
    ):
        install_fake_agent.default_scenario = FakeAgentScenario(
            plan='## Plan\n1. Step', result='Approved and done'
        )
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {'description': 'Approve and report', 'plan_mode': True},
        )
        task_id = data['task_id']
        await wait_for_task(admin_client, task_id, target='planned')
        approved, _ = await call_tool(
            admin_client,
            'review_plan',
            {'task_id': task_id, 'approve': True},
        )
        assert approved['status'] in ('running', 'completed', 'queued')
        # Let the pipeline finish before the fixture tears the engine
        # down — a background executor still running at teardown is a
        # connection race with the next test.
        await wait_for_task(admin_client, task_id)


class TestPlanModeParity:
    """create_task mirrors the UI's plan-mode choice."""

    async def test_omitted_plan_mode_uses_the_owners_default(
        self,
        admin_client,
        install_fake_agent,
        override_async_session,
        admin_user,
    ):
        async with override_async_session() as db:
            user = await db.get(User, admin_user.id)
            user.plan_mode_default = True
            await db.commit()

        store_id = await make_store(admin_client, 'Plan Mode Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {'description': 'Use the default', 'store_id': store_id},
        )
        assert data['plan_mode'] is True
        # The task is waiting for plan review — stop it so it does not
        # outlive the test.
        await settle_task(admin_client, data['task_id'])

    async def test_storeless_task_always_plans(
        self, admin_client, install_fake_agent
    ):
        """No store → plan mode is forced on, whatever the default."""
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {'description': 'No store here', 'plan_mode': False},
        )
        assert data['plan_mode'] is True
        await settle_task(admin_client, data['task_id'])

    async def test_explicit_plan_mode_false_runs_straight_through(
        self, admin_client, install_fake_agent
    ):
        store_id = await make_store(admin_client, 'Direct Store')
        data, _ = await call_tool(
            admin_client,
            'create_task',
            {
                'description': 'Just do it',
                'store_id': store_id,
                'plan_mode': False,
            },
        )
        assert data['plan_mode'] is False
        await wait_for_task(admin_client, data['task_id'])
