"""The DoD reviewer is a PRODUCT-DEFINED agent, not an improvised one.

The stop gates demanded an "``ads-report-review`` subagent" that
existed only in their text: the main agent spawned a general-purpose
subagent with a brief it invented, and that subagent explored code,
databases and transcripts for minutes on turns with nothing to
verify. These tests pin the contract that replaces the prose: a
registered agent type (launch ``--agents``), a bounded prompt, and a
server-built manifest in the gate's demand.
"""

import inspect
import json
from pathlib import Path
import re

import pytest

import app.ai.bash_safety as bash_safety_mod
from app.ai.bash_safety import check_review_status
import app.ai.reviewer_agent as reviewer_agent_mod
from app.ai.reviewer_agent import (
    REVIEWER_AGENT_NAME,
    REVIEWER_PROMPT,
    reviewer_agents_payload,
    write_agents_file,
)
from app.ai.stop_gates import report_reviewer

pytestmark = pytest.mark.unit


def test_the_registered_type_carries_the_contract():
    payload = reviewer_agents_payload()
    assert REVIEWER_AGENT_NAME == 'ads-report-review'
    entry = payload[REVIEWER_AGENT_NAME]
    assert entry['description'] and entry['prompt'] == REVIEWER_PROMPT
    # the role is bounded in the prompt itself: the manifest is the
    # scope, and the wandering it exists to prevent is named
    assert 'MANIFEST' in REVIEWER_PROMPT
    assert 'Do NOT' in REVIEWER_PROMPT
    assert 'transcripts' in REVIEWER_PROMPT


def test_the_launch_registers_the_type(tmp_path):
    path = write_agents_file('task-rv', tmp_path)
    assert path.name.endswith('.agents.json')
    written = json.loads(path.read_text(encoding='utf-8'))
    assert written == reviewer_agents_payload()


def test_the_demand_names_the_registered_type_with_a_manifest(
    tmp_path, monkeypatch
):
    (tmp_path / 'AD_AUDIT_2026-06-10.md').write_text('# report\n')
    monkeypatch.setattr(
        'app.ai.bash_safety.recorded_skills',
        lambda _t: frozenset({'noon-ads'}),
    )
    deny = check_review_status(
        tmp_path, result_text='Current ACOS is 25 percent.'
    )
    assert deny is not None
    assert f'subagent_type="{REVIEWER_AGENT_NAME}"' in deny
    assert 'MANIFEST (server-built' in deny
    assert 'Current ACOS is 25 percent.' in deny
    assert 'AD_AUDIT_2026-06-10.md' in deny
    assert 'verdict path (absolute)' in deny


def test_the_manifest_names_the_declaration(tmp_path, monkeypatch):
    (tmp_path / 'WIDGET_AD_COMPARISON.md').write_text('# r\n')
    monkeypatch.setattr(
        'app.ai.ad_declaration.load_declaration',
        lambda _tid: {'kind': 'audit', 'scope': {'platform': 'amazon'}},
    )
    manifest = report_reviewer.turn_manifest(tmp_path)
    assert 'ad declaration: audit' in manifest
    monkeypatch.setattr(
        'app.ai.ad_declaration.load_declaration', lambda _tid: None
    )
    manifest = report_reviewer.turn_manifest(tmp_path)
    assert 'ad declaration: none' in manifest


def test_no_gate_text_offers_general_purpose_for_the_reviewer():
    """One definition of the spawn (Brief #14c).

    Server gate texts name a REGISTERED reviewer type. ``general-purpose``
    was the improvisation surface: two contradicting spawn instructions,
    and a reviewer whose brief the model made up on the spot.
    """
    registered = reviewer_agents_payload()
    name_constants = {
        'REVIEWER_AGENT_NAME': reviewer_agent_mod.REVIEWER_AGENT_NAME,
        'EXEC_REVIEWER_NAME': reviewer_agent_mod.EXEC_REVIEWER_NAME,
    }
    for mod in (bash_safety_mod, report_reviewer):
        src = Path(inspect.getfile(mod)).read_text(encoding='utf-8')
        assert 'general-purpose' not in src, mod.__name__
        for match in re.findall(r'subagent_type="([^"]+)"', src):
            # a literal type name, or an interpolation of one of the
            # exported name constants
            bare = match.strip('{}')
            resolved = name_constants.get(bare, bare)
            assert resolved in registered, (
                f'{mod.__name__} gate text offers unregistered {match!r}'
            )


def test_every_registered_reviewer_role_is_a_real_type():
    """Both reviewer roles the gates name exist in the --agents file.

    A gate naming a type that is not registered is exactly the hole
    this module closes: the spawn then improvises.
    """
    assert set(reviewer_agents_payload()) == {
        'ads-report-review',
        'ads-execution-review',
    }
