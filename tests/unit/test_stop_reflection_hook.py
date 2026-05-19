"""Unit tests for the Stop hook reflection mechanism.

Tests that the Stop hook correctly blocks to force post-task
reflection and approves on retry (stop_hook_active=true).
"""

import json
import os

os.environ.setdefault('SECRET_KEY', 'test-secret-key-for-testing-only')

from unittest.mock import AsyncMock

import pytest

from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import STOP_REFLECTION_CALLBACK
from app.prompts import REFLECTION_PROMPT


def _make_session(skip_reflection=False):
    """Create a minimal AgentSession for hook testing."""
    session = AgentSession(
        task_id='test-task-id',
        prompt='test prompt',
        mode='auto',
        skip_reflection=skip_reflection,
    )
    # Stub _send_hook_response so we can capture calls
    session._send_hook_response = AsyncMock()
    # Stub _save_result and _emit_message to avoid DB access
    session._save_result = AsyncMock()
    session._emit_message = AsyncMock()
    return session


@pytest.mark.unit
class TestStopReflectionCallback:
    @pytest.mark.asyncio
    async def test_first_stop_blocks_with_reflection(self):
        """First stop attempt (stop_hook_active absent) blocks."""
        session = _make_session()
        request = {
            'callback_id': STOP_REFLECTION_CALLBACK,
            'input': {},
        }
        await session._handle_hook_callback('req-1', request)
        session._send_hook_response.assert_awaited_once_with(
            'req-1',
            {'decision': 'block', 'reason': REFLECTION_PROMPT},
        )

    @pytest.mark.asyncio
    async def test_retry_stop_approves(self):
        """Retry with stop_hook_active=true approves."""
        session = _make_session()
        request = {
            'callback_id': STOP_REFLECTION_CALLBACK,
            'input': {'stop_hook_active': True},
        }
        await session._handle_hook_callback('req-2', request)
        session._send_hook_response.assert_awaited_once_with(
            'req-2',
            {'decision': 'approve'},
        )

    @pytest.mark.asyncio
    async def test_stop_hook_active_false_blocks(self):
        """Explicit stop_hook_active=false still blocks."""
        session = _make_session()
        request = {
            'callback_id': STOP_REFLECTION_CALLBACK,
            'input': {'stop_hook_active': False},
        }
        await session._handle_hook_callback('req-3', request)
        session._send_hook_response.assert_awaited_once_with(
            'req-3',
            {'decision': 'block', 'reason': REFLECTION_PROMPT},
        )

    @pytest.mark.asyncio
    async def test_first_stop_saves_pre_reflection_result(self):
        """First stop saves last_assistant_message as pre-reflection result."""
        session = _make_session()
        request = {
            'callback_id': STOP_REFLECTION_CALLBACK,
            'input': {
                'stop_hook_active': False,
                'last_assistant_message': 'VERIFY-XYZ-123',
            },
        }
        await session._handle_hook_callback('req-4', request)
        assert session._pre_reflection_result == 'VERIFY-XYZ-123'

    @pytest.mark.asyncio
    async def test_first_stop_emits_reflection_started_event(self):
        """First stop persists a structured reflection_started marker.

        The marker is the contract that downstream consumers (UI,
        e2e tests) use to detect that the reflection PHASE happened,
        independent of how the model phrased the reflection text.
        """
        session = _make_session()
        request = {
            'callback_id': STOP_REFLECTION_CALLBACK,
            'input': {
                'stop_hook_active': False,
                'last_assistant_message': 'done',
            },
        }
        await session._handle_hook_callback('req-5', request)
        # Find the agent_event emit
        agent_event_calls = [
            c
            for c in session._emit_message.await_args_list
            if c.args and c.args[0] == 'agent_event'
        ]
        assert agent_event_calls, 'reflection_started should be emitted'
        payload = json.loads(agent_event_calls[0].args[1])
        assert payload == {'event': 'reflection_started'}

    @pytest.mark.asyncio
    async def test_retry_stop_emits_reflection_completed_event(self):
        """Retry stop persists a reflection_completed marker."""
        session = _make_session()
        request = {
            'callback_id': STOP_REFLECTION_CALLBACK,
            'input': {'stop_hook_active': True},
        }
        await session._handle_hook_callback('req-6', request)
        agent_event_calls = [
            c
            for c in session._emit_message.await_args_list
            if c.args and c.args[0] == 'agent_event'
        ]
        assert agent_event_calls, 'reflection_completed should be emitted'
        payload = json.loads(agent_event_calls[0].args[1])
        assert payload == {'event': 'reflection_completed'}


@pytest.mark.unit
class TestStopHookRegistration:
    @pytest.mark.asyncio
    async def test_auto_mode_has_stop_hook(self):
        """Auto mode registers Stop hook when skip_reflection=False."""
        session = _make_session(skip_reflection=False)
        captured = {}

        async def fake_send(msg, **kw):
            if msg.get('request', {}).get('subtype') == 'initialize':
                captured['hooks'] = msg['request']['hooks']

        session._send_stdin = fake_send
        await session._send_sdk_initialize_auto()
        assert 'Stop' in captured['hooks']
        cb_ids = captured['hooks']['Stop'][0]['hookCallbackIds']
        assert STOP_REFLECTION_CALLBACK in cb_ids

    @pytest.mark.asyncio
    async def test_plan_mode_has_stop_hook(self):
        """Plan mode registers Stop hook when skip_reflection=False."""
        session = _make_session(skip_reflection=False)
        captured = {}

        async def fake_send(msg, **kw):
            if msg.get('request', {}).get('subtype') == 'initialize':
                captured['hooks'] = msg['request']['hooks']

        session._send_stdin = fake_send
        await session._send_sdk_initialize()
        assert 'Stop' in captured['hooks']

    @pytest.mark.asyncio
    async def test_catalog_no_stop_hook(self):
        """skip_reflection=True excludes Stop hook."""
        session = _make_session(skip_reflection=True)
        captured = {}

        async def fake_send(msg, **kw):
            if msg.get('request', {}).get('subtype') == 'initialize':
                captured['hooks'] = msg['request']['hooks']

        session._send_stdin = fake_send
        await session._send_sdk_initialize_auto()
        assert 'Stop' not in captured['hooks']
