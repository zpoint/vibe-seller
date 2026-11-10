"""A skill's ``requires:`` reaches the agent as a hint on the Skill call.

``noon-ads`` declares ``requires: [noon-shared]``. Loading it before the
prerequisite is allowed (no refusal) but the PreToolUse hook attaches
``additionalContext`` naming the prerequisite; once the prerequisite is
loaded, the hint stops.
"""

import asyncio
import logging
from unittest.mock import AsyncMock

import pytest

from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import AUTO_APPROVE_CALLBACK

pytestmark = pytest.mark.unit


def _write_skill(root, name, requires=None):
    d = root / '.claude' / 'skills' / name
    d.mkdir(parents=True)
    req = f'requires: [{", ".join(requires)}]\n' if requires else ''
    (d / 'SKILL.md').write_text(
        f'---\nname: {name}\ndescription: x\n{req}---\n\n# {name}\n'
    )


@pytest.fixture
def session(tmp_path):
    _write_skill(tmp_path, 'noon-shared')
    _write_skill(tmp_path, 'noon-ads', requires=['noon-shared'])
    s = AgentSession(task_id='test-task', prompt='test', mode='auto')
    s.task_dir = tmp_path
    s._sent = []
    s._send_hook_response = AsyncMock(
        side_effect=lambda rid, out: s._sent.append(out)
    )
    return s


def _load(s, skill):
    request = {
        'callback_id': AUTO_APPROVE_CALLBACK,
        'input': {'tool_name': 'Skill', 'tool_input': {'skill': skill}},
    }
    asyncio.run(s._handle_hook_callback('req-1', request))
    return s._sent[-1]['hookSpecificOutput']


def test_loading_before_the_prerequisite_is_allowed_with_a_hint(
    session, caplog
):
    with caplog.at_level(logging.INFO, logger='app.ai.claude_backend_hooks'):
        out = _load(session, 'noon-ads')
    assert out['permissionDecision'] == 'allow'
    assert 'noon-shared' in out.get('additionalContext', '')
    assert any(
        "Skill prereq: agent test-tas loaded 'noon-ads' early" in r.message
        for r in caplog.records
    )


def test_no_hint_once_the_prerequisite_is_loaded(session):
    assert 'additionalContext' not in _load(session, 'noon-shared')
    out = _load(session, 'noon-ads')
    assert out['permissionDecision'] == 'allow'
    assert 'noon-shared' not in out.get('additionalContext', '')
