"""Post-result reflection records; it does not act, and it is time-boxed.

In CI a notes task's reflection ran a sign-in script and began editing
skills seven minutes after its result was saved, holding the task
RUNNING past its window."""

import time
from types import SimpleNamespace

import pytest

from app.ai import reflection_guard as rg
from app.ai.tool_call_guards import tool_call_deny

pytestmark = pytest.mark.unit


def _session(active=True, started_ago=0.0):
    s = SimpleNamespace(mode='execute', _executing=True)
    if active:
        rg.begin_reflection(s)
        s._reflection_started_at = time.monotonic() - started_ago
    else:
        s._reflection_active = False
    return s


def _deny(session, name, tool_input):
    return tool_call_deny(
        session, {'tool_name': name, 'tool_input': tool_input}
    )


@pytest.mark.parametrize(
    'command',
    [
        'python3 .claude/skills/amazon-shared/scripts/bh_signin.py',
        'cd x && python scripts/run.py',
        'browser-use <<EOF\nprint(1)\nEOF',
        'curl -s http://127.0.0.1:7777/api/stores',
        './.claude/skills/x/scripts/go.sh',
    ],
)
def test_reflection_refuses_running_programs(command):
    assert _deny(_session(), 'Bash', {'command': command}) == rg._ACT_DENY


@pytest.mark.parametrize(
    'command',
    [
        'grep -rn login .claude/skills/amazon-shared/SKILL.md',
        'cat stores/acme/notes.md',
        'ls -la knowledge/',
        'grep -n "python3 x.py" notes.md',
    ],
)
def test_reflection_allows_reading(command):
    assert _deny(_session(), 'Bash', {'command': command}) is None


def test_reflection_allows_knowledge_tools_not_side_effects():
    s = _session()
    write = 'mcp__vibe-seller__vibe_seller_write_workspace_file'
    assert (
        _deny(s, write, {'path': 'stores/a/notes.md', 'content': 'x'}) is None
    )
    for name in ('ads_call', 'send_email', 'create_task'):
        tool = f'mcp__vibe-seller__vibe_seller_{name}'
        assert _deny(s, tool, {}) is not None, name


def test_past_budget_every_tool_is_refused():
    s = _session(started_ago=rg.REFLECTION_BUDGET_S + 1)
    assert _deny(s, 'Read', {'file_path': '/x/notes.md'}) == rg._BUDGET_DENY


def test_outside_reflection_nothing_changes():
    s = _session(active=False)
    assert _deny(s, 'Bash', {'command': 'python3 run.py'}) is None


def test_reflection_end_lifts_the_guard_for_the_next_turn():
    # The same process serves follow-ups: the next turn's work must not
    # inherit the previous turn's reflection restrictions.
    s = _session(started_ago=rg.REFLECTION_BUDGET_S + 1)
    rg.end_reflection(s)
    assert _deny(s, 'Bash', {'command': 'browser-use <<EOF\nEOF'}) is None
