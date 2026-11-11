"""The agent-facing data contract says exactly what the server checks.

``data-contract.md`` is what the ad skills teach; ``ad_tables`` is what
the gates read. If a column, an action word or a rule key drifts in one
and not the other, agents write files the server rejects (or the server
accepts files the skill never asked for). Both ad skills ship the same
contract, byte for byte.
"""

from pathlib import Path
import re

import pytest

from app.ai.stop_gates import ad_tables
from app.ai.stop_gates.ad_rules import DEFAULT_RULES

pytestmark = pytest.mark.unit

SKILLS = Path(__file__).resolve().parents[2] / 'app' / 'skills_v2'
AMAZON = SKILLS / 'amazon-ads' / 'references' / 'data-contract.md'
NOON = SKILLS / 'noon-ads' / 'references' / 'data-contract.md'


def _code_blocks(text: str) -> list[tuple[str, ...]]:
    return [
        tuple(block.split())
        for block in re.findall(r'```\n(.*?)\n```', text, re.DOTALL)
    ]


def test_both_ad_skills_ship_the_same_contract():
    assert AMAZON.read_bytes() == NOON.read_bytes()


def test_every_header_in_the_doc_is_one_the_server_reads():
    blocks = _code_blocks(AMAZON.read_text(encoding='utf-8'))
    for columns in (
        ad_tables.CAMPAIGN_COLUMNS,
        ad_tables.TARGETING_COLUMNS,
        ad_tables.SEARCHTERM_COLUMNS,
        ad_tables.CHANGE_COLUMNS,
        ad_tables.EXECUTION_COLUMNS,
    ):
        assert columns in blocks, f'doc lacks header {columns}'


def test_actions_and_statuses_match():
    text = AMAZON.read_text(encoding='utf-8')
    for word in ad_tables.ACTIONS + ad_tables.DRILL_STATUSES:
        assert f'`{word}`' in text, word


def test_documented_rule_defaults_match_the_code():
    rows = re.findall(
        r'^\| `(\w+)` \| ([\d.]+) \|', AMAZON.read_text(encoding='utf-8'), re.M
    )
    assert rows, 'rule table not found'
    for key, value in rows:
        assert key in DEFAULT_RULES, key
        assert float(value) == DEFAULT_RULES[key], key
