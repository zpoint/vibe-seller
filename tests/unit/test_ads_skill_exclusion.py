"""A skill routed away from a task is refused however it is reached.

Observed: a task on a store NOT bound to the ads service loaded
``amazon-ads-api`` from the shared ``~/.vibe-seller/.claude/skills`` —
the task folder sits inside the shared workspace, so leaving the skill out
of the task's own copy did not hide it. The agent then called the ads
tool three times and was refused three times before falling back to the
browser. The routing decision has to hold at load time too.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app import ads_routing
from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import AUTO_APPROVE_CALLBACK

pytestmark = pytest.mark.unit

SHARED = '/home/u/.vibe-seller/.claude/skills'


def _decision(excluded, tool, tool_input) -> dict:
    s = AgentSession(
        task_id='test-task',
        prompt='test',
        mode='auto',
        excluded_skills=excluded,
    )
    sent = []
    s._send_hook_response = AsyncMock(
        side_effect=lambda rid, out: sent.append(out)
    )
    request = {
        'callback_id': AUTO_APPROVE_CALLBACK,
        'input': {'tool_name': tool, 'tool_input': tool_input},
    }
    with (
        patch(
            'app.ai.claude_backend_hooks.ProfileManager.get_env_for_profile',
            return_value={},
        ),
        patch('app.ai.claude_backend_hooks.record_skill_load') as recorded,
    ):
        asyncio.run(s._handle_hook_callback('req-1', request))
    assert len(sent) == 1
    out = sent[0]['hookSpecificOutput']
    out['recorded'] = [c.args[1] for c in recorded.call_args_list]
    return out


AUTHORIZED = ads_routing.skills_to_exclude([True])  # {'amazon-ads'}
UNBOUND = ads_routing.skills_to_exclude([False])  # {'amazon-ads-api'}


class TestAuthorizedStore:
    def test_the_browser_skill_is_refused_by_name(self):
        out = _decision(AUTHORIZED, 'Skill', {'skill': 'amazon-ads'})
        assert out['permissionDecision'] == 'deny'
        assert 'amazon-ads-api' in out['permissionDecisionReason']
        assert out['recorded'] == [], 'a refused load must bind no gates'

    @pytest.mark.parametrize(
        'path',
        [
            f'{SHARED}/amazon-ads/SKILL.md',
            f'{SHARED}/amazon-ads/references/reviewer-loop.md',
        ],
    )
    def test_reading_its_files_from_the_shared_copy_is_refused(self, path):
        out = _decision(AUTHORIZED, 'Read', {'file_path': path})
        assert out['permissionDecision'] == 'deny'

    def test_its_own_skill_loads(self):
        out = _decision(
            AUTHORIZED,
            'Read',
            {'file_path': f'{SHARED}/amazon-ads-api/SKILL.md'},
        )
        assert out['permissionDecision'] == 'allow'
        assert out['recorded'] == ['amazon-ads-api']


class TestUnboundStore:
    def test_the_api_skill_is_refused_and_the_browser_named(self):
        out = _decision(UNBOUND, 'Skill', {'skill': 'amazon-ads-api'})
        assert out['permissionDecision'] == 'deny'
        reason = out['permissionDecisionReason']
        assert 'not' in reason and '`amazon-ads`' in reason

    def test_the_browser_skill_loads(self):
        out = _decision(
            UNBOUND, 'Read', {'file_path': f'{SHARED}/amazon-ads/SKILL.md'}
        )
        assert out['permissionDecision'] == 'allow'


def test_nothing_is_refused_when_nothing_is_excluded():
    """A task spanning both kinds of store keeps both skills."""
    for skill in ('amazon-ads', 'amazon-ads-api'):
        out = _decision(
            set(), 'Read', {'file_path': f'{SHARED}/{skill}/SKILL.md'}
        )
        assert out['permissionDecision'] == 'allow'
