"""Unit tests for agent SDK hook configuration and routing."""

import asyncio
import re
from unittest.mock import AsyncMock, patch

import pytest

from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import (
    AUTO_APPROVE_CALLBACK,
    TOOL_APPROVAL_CALLBACK,
)

pytestmark = pytest.mark.unit


async def _wait_for_key(d: dict, key: str) -> None:
    """Poll until *key* appears in *d*."""
    while key not in d:
        await asyncio.sleep(0.1)


def _make_session(mode: str = 'plan_then_execute') -> AgentSession:
    return AgentSession(
        task_id='test-task',
        prompt='test',
        mode=mode,
    )


def _extract_hooks(captured: list[dict]) -> dict:
    """Extract hooks dict from the captured _send_stdin calls."""
    for msg in captured:
        req = msg.get('request', {})
        if req.get('subtype') == 'initialize' and 'hooks' in req:
            return req['hooks']
    raise AssertionError('No initialize message with hooks found')


def _matches(matcher: str, tool_name: str) -> bool:
    """Check if a regex matcher matches a tool name."""
    return re.match(matcher, tool_name) is not None


def _find_callback(hooks: dict, tool_name: str) -> str | None:
    """Find which callback ID a tool would route to."""
    for entry in hooks.get('PreToolUse', []):
        if _matches(entry['matcher'], tool_name):
            return entry['hookCallbackIds'][0]
    return None


# -----------------------------------------------------------
# Task 2a: Hook config structure tests
# -----------------------------------------------------------


class TestHookConfig:
    """Verify hook matchers route tools to correct callbacks."""

    @pytest.fixture()
    def plan_hooks(self):
        captured: list[dict] = []
        s = _make_session('plan_then_execute')
        s._send_stdin = AsyncMock(
            side_effect=lambda msg, **kw: captured.append(msg)
        )
        asyncio.run(s._send_sdk_initialize())
        return _extract_hooks(captured)

    @pytest.fixture()
    def auto_hooks(self):
        captured: list[dict] = []
        s = _make_session('auto')
        s._send_stdin = AsyncMock(
            side_effect=lambda msg, **kw: captured.append(msg)
        )
        asyncio.run(s._send_sdk_initialize_auto())
        return _extract_hooks(captured)

    def test_plan_mode_ask_user_question_routes_to_approval(self, plan_hooks):
        assert (
            _find_callback(plan_hooks, 'AskUserQuestion')
            == TOOL_APPROVAL_CALLBACK
        )

    def test_plan_mode_exit_plan_mode_routes_to_approval(self, plan_hooks):
        assert (
            _find_callback(plan_hooks, 'ExitPlanMode') == TOOL_APPROVAL_CALLBACK
        )

    def test_plan_mode_other_tools_route_to_auto_approve(self, plan_hooks):
        for tool in ('Bash', 'Read', 'Write', 'Grep', 'Glob'):
            assert _find_callback(plan_hooks, tool) == AUTO_APPROVE_CALLBACK, (
                f'{tool} should auto-approve in plan mode'
            )

    def test_auto_mode_ask_user_question_routes_to_approval(self, auto_hooks):
        assert (
            _find_callback(auto_hooks, 'AskUserQuestion')
            == TOOL_APPROVAL_CALLBACK
        )

    def test_auto_mode_exit_plan_mode_routes_to_auto_approve(self, auto_hooks):
        assert (
            _find_callback(auto_hooks, 'ExitPlanMode') == AUTO_APPROVE_CALLBACK
        )

    def test_auto_mode_other_tools_route_to_auto_approve(self, auto_hooks):
        for tool in ('Bash', 'Read', 'Write'):
            assert _find_callback(auto_hooks, tool) == AUTO_APPROVE_CALLBACK, (
                f'{tool} should auto-approve in auto mode'
            )


# -----------------------------------------------------------
# Task 2b: Hook routing behavior tests
# -----------------------------------------------------------


class TestHookRouting:
    """Verify actual routing behavior, not just config shape."""

    def test_hook_callback_ask_user_question_returns_ask(self):
        """tool_approval callback for AskUserQuestion should
        respond with permissionDecision 'ask'."""
        s = _make_session('plan_then_execute')
        sent = []
        s._send_hook_response = AsyncMock(
            side_effect=lambda rid, out: sent.append((rid, out))
        )

        request = {
            'callback_id': TOOL_APPROVAL_CALLBACK,
            'input': {
                'tool_name': 'AskUserQuestion',
                'tool_input': {'questions': []},
            },
        }
        asyncio.run(s._handle_hook_callback('req-1', request))

        assert len(sent) == 1
        rid, output = sent[0]
        assert rid == 'req-1'
        decision = output['hookSpecificOutput']
        assert decision['permissionDecision'] == 'ask'

    def test_hook_callback_exit_plan_mode_returns_ask(self):
        """tool_approval callback for ExitPlanMode should
        respond with permissionDecision 'ask'."""
        s = _make_session('plan_then_execute')
        sent = []
        s._send_hook_response = AsyncMock(
            side_effect=lambda rid, out: sent.append((rid, out))
        )

        request = {
            'callback_id': TOOL_APPROVAL_CALLBACK,
            'input': {
                'tool_name': 'ExitPlanMode',
                'tool_input': {'plan': 'my plan'},
            },
        }
        asyncio.run(s._handle_hook_callback('req-2', request))

        assert len(sent) == 1
        decision = sent[0][1]['hookSpecificOutput']
        assert decision['permissionDecision'] == 'ask'

    @patch('app.ai.claude_backend_hooks.event_bus')
    def test_can_use_tool_ask_user_question_emits_event(self, mock_bus):
        """CanUseTool for AskUserQuestion should emit
        task_questions event."""
        mock_bus.emit = AsyncMock()
        s = _make_session('plan_then_execute')
        # Mock _send_control_response so it doesn't fail
        s._send_control_response = AsyncMock()

        questions = [{'question': 'Pick one', 'options': []}]
        request = {
            'tool_name': 'AskUserQuestion',
            'input': {'questions': questions},
        }

        async def run():
            # Start the handler (it will wait for answer)
            task = asyncio.create_task(s._handle_can_use_tool('req-3', request))
            # Wait deterministically for the handler to register
            await asyncio.wait_for(
                _wait_for_key(s._answer_events, 'req-3'),
                timeout=2.0,
            )

            # Verify event was emitted
            mock_bus.emit.assert_called_once()
            call_args = mock_bus.emit.call_args
            assert call_args[0][0] == 'task_questions'
            payload = call_args[0][1]
            assert payload['task_id'] == 'test-task'
            assert payload['request_id'] == 'req-3'
            assert payload['questions'] == questions

            # Submit answer to unblock
            s._answers['req-3'] = {'q1': 'a1'}
            evt = s._answer_events.get('req-3')
            if evt:
                evt.set()
            await task

        asyncio.run(run())
