"""When the prerequisite e2e may demand a hint from the hook.

The hook hints on a Skill-tool load of noon-ads that noon-shared did not
precede. The e2e helper once padded skills the agent never loaded with
the Skill tool into a fixed order, so an agent that read noon-shared
with ``sed`` and loaded nothing was failed for a hint nothing could
have sent.
"""

import json

import pytest

from tests.e2e.test_skill_prereq_hook import hint_owed

pytestmark = pytest.mark.unit


def _load(skill: str) -> dict:
    return {
        'role': 'tool_use',
        'content': json.dumps({'tool': 'Skill', 'input': {'skill': skill}}),
    }


def _bash(cmd: str) -> dict:
    return {
        'role': 'tool_use',
        'content': json.dumps({'tool': 'Bash', 'input': {'command': cmd}}),
    }


@pytest.mark.parametrize(
    ('messages', 'owed'),
    [
        ([_bash('sed -n 1,40p .claude/skills/noon-shared/SKILL.md')], False),
        ([], False),
        ([_load('noon-shared'), _load('noon-ads')], False),
        ([_load('noon-ads'), _load('noon-shared')], True),
        ([_load('noon-ads')], True),
    ],
)
def test_a_hint_is_owed_only_when_noon_ads_came_first(messages, owed):
    assert hint_owed(messages) is owed
