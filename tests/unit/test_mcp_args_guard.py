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
from app.ai.tool_call_guards import loaded_tool_search_deny, tool_call_deny
from app.mcp_tool_schemas import TOOLS
from app.prompts import DESIGN_SYSTEM_PROMPT, DESIGN_SYSTEM_PROMPT_AUTO

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


class TestFinishingToolsAlwaysLoaded:
    """The tools that end a task are never deferred behind tool search.

    Twice in CI a model that had loaded only write_workspace_file meant
    "record the result" and called that instead, until the loop breaker
    stopped a finished session.
    """

    def test_only_the_finishing_tools_are_pinned(self):
        pinned = {
            t['name']
            for t in TOOLS
            if (t.get('_meta') or {}).get('anthropic/alwaysLoad') is True
        }
        assert pinned == {
            'vibe_seller_set_task_result',
            'vibe_seller_set_task_error',
        }

    def test_the_base_prompt_says_they_need_no_search(self):
        # In CI an agent searched for set_task_result, got "no matching
        # deferred tools" (it was already loaded), and searched again.
        for text in (DESIGN_SYSTEM_PROMPT, DESIGN_SYSTEM_PROMPT_AUTO):
            assert 'always loaded' in text
            assert 'vibe_seller_set_task_error' in text


class TestLoadedToolSearch:
    """ToolSearch lists only deferred tools, so searching for a loaded
    one comes back empty. In CI a model searched six times for the
    set_task_result it already had."""

    @pytest.mark.parametrize(
        'query',
        [
            'vibe_seller_set_task_result',
            'select:mcp__vibe-seller__vibe_seller_set_task_result',
            'set_task',
            'set_task_error',
        ],
    )
    def test_a_search_for_a_loaded_tool_is_refused(self, query):
        reason = tool_call_deny(
            None, {'tool_name': 'ToolSearch', 'tool_input': {'query': query}}
        )
        assert reason and 'already loaded' in reason
        assert 'vibe_seller_set_task_result' in reason

    @pytest.mark.parametrize('query', ['task', 'vibe_seller', 'ads_call'])
    def test_other_searches_pass(self, query):
        assert loaded_tool_search_deny('ToolSearch', {'query': query}) is None
