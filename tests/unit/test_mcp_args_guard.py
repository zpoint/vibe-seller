"""A vibe-seller MCP call missing required arguments is refused up front.

Observed in CI: after writing a catalog, a model sent ten parallel
``vibe_seller_write_workspace_file`` calls with ``{}``. Each ran, came
back ``{"error": "'path'"}``, and the identical signatures tripped the
loop circuit breaker -- which stopped a session whose deliverable was
already written. A malformed request is refused with the missing names,
and a refused call never reaches the loop detector.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import (
    AUTO_APPROVE_CALLBACK,
    MAX_REPEAT_TOOL_CALLS,
)
from app.ai.mcp_args_guard import missing_args_deny

pytestmark = pytest.mark.unit

_WRITE = 'mcp__vibe-seller__vibe_seller_write_workspace_file'


class TestMissingArgsDeny:
    def test_empty_call_names_every_missing_argument(self):
        deny = missing_args_deny(_WRITE, {})
        assert deny and 'path' in deny and 'content' in deny
        assert 'Nothing ran' in deny

    def test_complete_call_passes(self):
        args = {'path': 'stores/acme/notes.md', 'content': ''}
        assert missing_args_deny(_WRITE, args) is None  # empty file is legal

    def test_partial_call_names_what_is_left(self):
        deny = missing_args_deny(_WRITE, {'path': 'stores/acme/x.md'})
        assert deny and 'content' in deny and 'path,' not in deny

    @pytest.mark.parametrize(
        'name',
        [
            'Bash',  # not an MCP tool
            'mcp__other-server__vibe_seller_write_workspace_file',
            'mcp__vibe-seller__no_such_tool',  # unknown: let it through
        ],
    )
    def test_other_tools_untouched(self, name):
        assert missing_args_deny(name, {}) is None


def _hook(session, tool_input):
    sent = []
    session._send_hook_response = AsyncMock(
        side_effect=lambda rid, out: sent.append(out)
    )
    request = {
        'callback_id': AUTO_APPROVE_CALLBACK,
        'input': {'tool_name': _WRITE, 'tool_input': tool_input},
    }
    asyncio.run(session._handle_hook_callback('req', request))
    return sent[0].get('hookSpecificOutput', {})


class TestHookWiring:
    def test_a_batch_of_empty_calls_is_refused_not_killed(self):
        """More identical malformed calls than the loop cap: each is
        refused with the reason, and the session is not stopped."""
        s = AgentSession(task_id='test-task', prompt='t', mode='auto')
        s.stop = AsyncMock()
        for _ in range(MAX_REPEAT_TOOL_CALLS + 4):
            out = _hook(s, {})
            assert out.get('permissionDecision') == 'deny'
            assert 'required argument' in out['permissionDecisionReason']
        s.stop.assert_not_called()
        assert s._recent_tool_calls == []
        assert getattr(s, '_error_category', None) != 'agent_loop'
