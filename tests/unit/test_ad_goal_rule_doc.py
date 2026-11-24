"""The Amazon ad skill picks the tuning goal by a fixed rule.

The goal sets target ACOS (launch / scale / profit). With only "the
user's, else scale" to go on, a user who said "the goal is profitable
growth" named no goal, and the agent reopened that choice on every turn
— about two minutes of reasoning each — until the task ran out of time
without writing its tables. The skill now gives a closed rule: the
store notes, else only a plainly named goal, else ``scale``; decided
once and stated in the report. These tests keep that rule in place and
in step with the Target ACOS table it chooses from.
"""

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.unit

THRESHOLDS = (
    Path(__file__).resolve().parents[2]
    / 'app'
    / 'skills_v2'
    / 'amazon-ads'
    / 'references'
    / 'tuning-thresholds.md'
)


def _section(text: str, heading: str) -> str:
    match = re.search(
        rf'^## {re.escape(heading)}\n(.*?)(?=^## |\Z)', text, re.M | re.S
    )
    assert match, f'tuning-thresholds.md has no "## {heading}" section'
    return match.group(1)


def _target_acos_goals(text: str) -> set[str]:
    table = re.search(
        r'\| Goal \| Target ACOS \|\n\|---\|---\|\n((?:\|.*\n)+)', text
    )
    assert table, 'the Target ACOS table is missing'
    return {
        row.split('|')[1].split('(')[0].strip()
        for row in table.group(1).splitlines()
    }


def test_the_goal_rule_chooses_among_the_target_acos_goals():
    text = THRESHOLDS.read_text(encoding='utf-8')
    goals = _target_acos_goals(text)
    assert goals == {'launch', 'scale', 'profit'}
    rule = _section(text, 'Goal')
    for goal in goals:
        assert f'`{goal}`' in rule, f'the Goal rule never names {goal}'


def test_anything_not_plainly_named_falls_to_scale():
    rule = _section(THRESHOLDS.read_text(encoding='utf-8'), 'Goal')
    # The catch-all is what removes the judgement call; mixed wording is
    # the case that stalled the agent, so it is named explicitly.
    assert 'Everything else is `scale`' in rule
    assert 'profitable growth' in rule
    assert "don't\nreopen it" in rule or "don't reopen it" in rule


def test_the_inputs_table_points_at_the_rule():
    inputs = _section(THRESHOLDS.read_text(encoding='utf-8'), 'Inputs')
    goal_row = next(
        line for line in inputs.splitlines() if line.startswith('| Goal |')
    )
    assert '(#goal)' in goal_row
