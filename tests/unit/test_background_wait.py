"""No long foreground ``sleep`` while background work is pending.

Pins ``app/ai/background_wait.py`` and its wiring into the live Bash
PreToolUse hook (``claude_backend_hooks._handle_hook_callback``).
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.ai.background_wait import WAIT_FOR_BACKGROUND, check_blocking_sleep
from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import AUTO_APPROVE_CALLBACK

pytestmark = pytest.mark.unit

PENDING = 'research-agent'


@pytest.mark.parametrize(
    'cmd',
    [
        'sleep 200; ls report.md',
        'sleep 30',
        'cd /tmp && sleep 45 && cat x',
        'sleep 1m',
        'sleep 0.5h',
    ],
)
def test_long_sleep_denied_while_work_pending(cmd):
    deny = check_blocking_sleep(cmd, PENDING)
    assert deny is not None
    assert WAIT_FOR_BACKGROUND in deny and PENDING in deny


@pytest.mark.parametrize(
    'cmd',
    [
        'sleep 20',  # a short poll between page checks
        'sleep 29.9',
        'echo "sleep 300"',  # not in command position
        'grep sleep notes.md',
        'until test -f x; do sleep 5; done',
    ],
)
def test_short_or_quoted_sleep_allowed(cmd):
    assert check_blocking_sleep(cmd, PENDING) is None


def test_long_sleep_allowed_with_nothing_pending():
    assert check_blocking_sleep('sleep 300', '') is None
    assert check_blocking_sleep('sleep 300', None) is None


def _bash_hook(command, pending, background=False):
    """Run one Bash call through the live PreToolUse hook."""
    s = AgentSession(task_id='test-task', prompt='test', mode='auto')
    s._async_agents = dict(pending)
    sent = []
    s._send_hook_response = AsyncMock(
        side_effect=lambda rid, out: sent.append(out)
    )
    tool_input = {'command': command}
    if background:
        tool_input['run_in_background'] = True
    request = {
        'callback_id': AUTO_APPROVE_CALLBACK,
        'input': {'tool_name': 'Bash', 'tool_input': tool_input},
    }
    asyncio.run(s._handle_hook_callback('req-1', request))
    return sent[0].get('hookSpecificOutput', {})


def test_hook_denies_foreground_sleep_while_a_subagent_runs():
    out = _bash_hook('sleep 200; ls report.md', {'tu1': 'agent-a'})
    assert out.get('permissionDecision') == 'deny'
    assert WAIT_FOR_BACKGROUND in out['permissionDecisionReason']
    assert 'agent-a' in out['permissionDecisionReason']


def test_hook_lets_a_backgrounded_sleep_through():
    out = _bash_hook('sleep 200', {'tu1': 'agent-a'}, background=True)
    assert out.get('permissionDecision') != 'deny'


def test_hook_lets_sleep_through_once_nothing_is_pending():
    out = _bash_hook('sleep 200', {})
    assert out.get('permissionDecision') != 'deny'
