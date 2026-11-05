"""Workflow tests for the MCP workspace-assistant tool.

The assistant manager is mocked at the ROUTE module (the same patch
point the REST tests use); the mock emits the SAME SSE events the
real session does, so what these pin is the tool's contract against
the real event bus and the real routes:

* ``send`` waits for the turn's messages and returns the reply (the
  ``result`` text), exactly what the UI reads off the SSE stream;
* a slow turn times out honestly (``timed_out`` + how to poll) and
  the reply is still collectable afterwards via ``status``;
* ``status`` reports running state and the last reply;
* ``stop`` stops it; validation and auth behave like every tool.
"""

import asyncio
import json

import pytest

from app.events.bus import event_bus

pytestmark = pytest.mark.workflow

MCP_PATH = '/api/mcp'

_SESSION = {}  # user_id -> mock state


class MockAssistantManager:
    """Mirrors WorkspaceAssistantManager's route-facing surface.

    ``reply_plan`` is a list of (delay, text) the fake session
    "streams": each is emitted as a ws_assistant_message, then a
    ws_assistant_done closes the turn — the same events the real
    WorkspaceAgentSession emits.
    """

    def __init__(self):
        self.reply_plan: list[tuple[float, str]] = [(0.0, 'Done')]
        self.never_finishes = False
        self._running: dict[str, bool] = {}
        self._last_reply: dict[str, str] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}

    def session_key(self, user_id):
        return f'__ws_assistant_{user_id}__'

    def send_lock(self, user_id):
        """The per-user send serializer (mirrors the real manager)."""
        key = self.session_key(user_id)
        lock = self._send_locks.get(key)
        if lock is None:
            lock = self._send_locks[key] = asyncio.Lock()
        return lock

    def last_reply(self, user_id):
        return self._last_reply.get(user_id)

    def is_running(self, user_id):
        return self._running.get(user_id, False)

    async def send_or_start(
        self, user_id, message, profile_id='default', system_prompt=''
    ):
        self._running[user_id] = True
        key = self.session_key(user_id)

        async def _turn():
            plan = self.reply_plan
            if self.never_finishes:
                plan = [t for t in plan if False] or []
            for delay, text in plan:
                if delay:
                    await asyncio.sleep(delay)
                await event_bus.emit(
                    'ws_assistant_message',
                    {'session_key': key, 'role': 'result', 'content': text},
                )
            if not self.never_finishes:
                self._last_reply[user_id] = plan[-1][1]
                self._running[user_id] = False
                await event_bus.emit(
                    'ws_assistant_done',
                    {'session_key': key, 'return_code': 0},
                )

        asyncio.create_task(_turn())
        return True

    async def stop(self, user_id):
        was = self._running.pop(user_id, False)
        return was


@pytest.fixture
def mock_ws_assistant(monkeypatch):
    mock = MockAssistantManager()
    monkeypatch.setattr(
        'app.routers.workspace_assistant.ws_assistant_manager', mock
    )
    return mock


async def rpc(client, method, params=None, request_id=1):
    payload = {'jsonrpc': '2.0', 'id': request_id, 'method': method}
    if params is not None:
        payload['params'] = params
    return await client.post(MCP_PATH, json=payload)


async def call_tool(client, name, arguments=None):
    r = await rpc(
        client, 'tools/call', {'name': name, 'arguments': arguments or {}}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert 'error' not in body, body
    result = body['result']
    text = result['content'][0]['text']
    if result.get('isError'):
        return {'error': text}, result
    return (json.loads(text) if text else {}), result


class TestSend:
    async def test_send_returns_the_reply(
        self, admin_client, mock_ws_assistant
    ):
        mock_ws_assistant.reply_plan = [
            (0.0, 'Thinking about it…'),
            (0.0, 'Organized the acme notes under stores/acme/.'),
        ]
        data, envelope = await call_tool(
            admin_client,
            'workspace_assistant',
            {
                'action': 'send',
                'message': 'Tidy the acme store notes',
            },
        )
        assert envelope['isError'] is False, envelope
        assert data['ok'] is True
        assert data['done'] is True
        assert data['timed_out'] is False
        # The reply is the turn's result text.
        assert data['reply'] == ('Organized the acme notes under stores/acme/.')
        roles = [m['role'] for m in data['messages']]
        assert roles == ['result', 'result']

    async def test_a_slow_turn_times_out_honestly(
        self, admin_client, mock_ws_assistant
    ):
        mock_ws_assistant.never_finishes = True
        mock_ws_assistant.reply_plan = [(30.0, 'way too slow to be observed')]
        data, envelope = await call_tool(
            admin_client,
            'workspace_assistant',
            {
                'action': 'send',
                'message': 'Do something slow',
                'wait_seconds': 1,
            },
        )
        assert envelope['isError'] is False, envelope
        assert data['timed_out'] is True
        assert data['done'] is False
        assert data['running'] is True
        assert 'status' in data['note']

    async def test_empty_message_is_rejected(
        self, admin_client, mock_ws_assistant
    ):
        _, envelope = await call_tool(
            admin_client,
            'workspace_assistant',
            {'action': 'send', 'message': '   '},
        )
        assert envelope['isError'] is True
        assert 'message' in envelope['content'][0]['text']


class TestStatus:
    async def test_status_reports_running_and_last_reply(
        self, admin_client, mock_ws_assistant
    ):
        idle, _ = await call_tool(
            admin_client, 'workspace_assistant', {'action': 'status'}
        )
        assert idle['running'] is False
        assert idle['last_reply'] is None

        await call_tool(
            admin_client,
            'workspace_assistant',
            {'action': 'send', 'message': 'hello'},
        )
        after, _ = await call_tool(
            admin_client, 'workspace_assistant', {'action': 'status'}
        )
        assert after['running'] is False  # the turn finished
        assert after['last_reply'] == 'Done'


class TestStop:
    async def test_stop_ends_the_session(self, admin_client, mock_ws_assistant):
        mock_ws_assistant.never_finishes = True
        mock_ws_assistant.reply_plan = [(30.0, 'never')]
        await call_tool(
            admin_client,
            'workspace_assistant',
            {
                'action': 'send',
                'message': 'start something',
                'wait_seconds': 1,
            },
        )
        stopped, envelope = await call_tool(
            admin_client,
            'workspace_assistant',
            {'action': 'stop'},
        )
        assert envelope['isError'] is False, envelope
        assert stopped['ok'] is True

        idle, _ = await call_tool(
            admin_client, 'workspace_assistant', {'action': 'status'}
        )
        assert idle['running'] is False

    async def test_stop_when_idle_is_reported_not_failed(
        self, admin_client, mock_ws_assistant
    ):
        stopped, envelope = await call_tool(
            admin_client,
            'workspace_assistant',
            {'action': 'stop'},
        )
        assert envelope['isError'] is False
        assert stopped['ok'] is False
        assert 'No assistant session' in stopped['note']


class TestValidationAndAuth:
    async def test_unknown_action_is_rejected(
        self, admin_client, mock_ws_assistant
    ):
        _, envelope = await call_tool(
            admin_client,
            'workspace_assistant',
            {'action': 'restart'},
        )
        assert envelope['isError'] is True
        assert 'restart' in envelope['content'][0]['text']

    async def test_member_and_unauthenticated(
        self, member_client, unauthed_client, mock_ws_assistant, admin_user
    ):
        data, envelope = await call_tool(
            member_client,
            'workspace_assistant',
            {'action': 'status'},
        )
        assert envelope['isError'] is False
        assert data['running'] is False

        r = await unauthed_client.post(
            MCP_PATH,
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {
                    'name': 'workspace_assistant',
                    'arguments': {'action': 'status'},
                },
            },
        )
        assert r.status_code == 401
