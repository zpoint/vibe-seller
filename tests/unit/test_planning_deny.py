"""While planning, an agent decides WHAT to do; it does not do it.

Observed in CI: a fanout schedule's planner called the live ads API
eight times and filed an ad-task declaration before writing its plan,
and overran its window doing so. Those tools run when the plan executes.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import AUTO_APPROVE_CALLBACK
from app.ai.tool_call_guards import planning_deny

pytestmark = pytest.mark.unit

ADS_CALL = 'mcp__vibe-seller__vibe_seller_ads_call'
DECLARE = 'mcp__vibe-seller__vibe_seller_declare_ad_task'


@pytest.mark.parametrize('tool', [ADS_CALL, DECLARE])
def test_execution_tools_are_refused_while_planning(tool):
    s = SimpleNamespace(mode='plan_then_execute', _executing=False)
    deny = planning_deny(s, tool)
    assert deny and 'plan' in deny


@pytest.mark.parametrize(
    ('mode', 'executing'),
    [
        ('plan_then_execute', True),  # plan approved: now it executes
        ('auto', True),
        ('execute', True),
    ],
)
def test_they_run_once_executing(mode, executing):
    s = SimpleNamespace(mode=mode, _executing=executing)
    assert planning_deny(s, ADS_CALL) is None


def test_reading_tools_stay_open_while_planning():
    s = SimpleNamespace(mode='plan_then_execute', _executing=False)
    for tool in ('Read', 'Skill', 'mcp__vibe-seller__vibe_seller_list_stores'):
        assert planning_deny(s, tool) is None


def test_the_hook_refuses_it():
    s = AgentSession(task_id='planner', prompt='t', mode='plan_then_execute')
    sent = []
    s._send_hook_response = AsyncMock(
        side_effect=lambda rid, out: sent.append(out)
    )
    request = {
        'callback_id': AUTO_APPROVE_CALLBACK,
        'input': {
            'tool_name': ADS_CALL,
            'tool_input': {'path': '/facts/rollup', 'store': 'acme'},
        },
    }
    asyncio.run(s._handle_hook_callback('req', request))
    out = sent[0]['hookSpecificOutput']
    assert out['permissionDecision'] == 'deny'
    assert 'planning' in out['permissionDecisionReason']
