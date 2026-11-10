"""ad_changes: proposed and executed changes obey store rules + approval.

Every case writes the real TSV contract into a task dir and calls the
gate the way set_task_result does. One test per rule, each with the
passing and the failing side.
"""

from datetime import UTC, datetime, timedelta
import uuid

import pytest

from app.ai.stop_gates import ad_changes
from app.ai.stop_gates.ad_rules import resolve_rules
from tests.unit.ad_fixtures import AdTask, change

pytestmark = pytest.mark.unit


@pytest.fixture
def task(monkeypatch):
    t = AdTask(f'chg-{uuid.uuid4().hex[:8]}', monkeypatch, None)
    t.declare('audit')
    return t


def _gaps(task, rules=None):
    deny = ad_changes.check('', task.id, rules)
    return list(deny.gaps) if deny else []


def _contradictions(task, rules=None):
    deny = ad_changes.check('', task.id, rules)
    return list(deny.contradictions) if deny else []


def test_no_files_no_opinion(task):
    assert ad_changes.check('', task.id) is None


def test_a_clean_proposal_passes(task):
    task.changes([change(), change(target='gadget', action='hold')])
    assert _gaps(task) == []


@pytest.mark.parametrize(
    'row,expect',
    [
        (
            change(action='lower', acos='20', target_bid='0.90'),
            'under 30% ACOS',
        ),
        (
            change(action='lower', acos='', roas='5', target_bid='0.90'),
            'under 30% ACOS',
        ),
        (
            change(action='raise', cpc='1.00', target_bid='1.05'),
            'below CPC 1 x 1.1',
        ),
        (change(action='hold', roas='8', reason=''), 'needs a reason'),
        (change(action='bump'), 'use one of'),
        (change(action='raise', target_bid=''), 'without a target_bid'),
    ],
)
def test_owner_rules_refuse(task, row, expect):
    task.changes([row])
    gaps = _gaps(task)
    assert any(expect in g for g in gaps), gaps


@pytest.mark.parametrize(
    'row',
    [
        change(action='lower', acos='45', target_bid='0.95'),
        change(action='raise', cpc='1.00', target_bid='1.10'),
        change(action='hold', roas='8', reason='budget capped'),
        change(action='pause', acos='', roas='', cpc=''),
    ],
)
def test_owner_rules_allow(task, row):
    task.changes([row])
    assert _gaps(task) == []


def test_store_notes_override_the_defaults(task):
    """The thresholds are defaults: a store's notes.md wins."""
    task.changes([
        # 1.20 → 1.02: a real cut (the row once said 1.00 → 1.02, a
        # 'lower' that raised the bid, which the direction check refuses).
        change(
            action='lower',
            acos='25',
            cpc='1.00',
            current_bid='1.20',
            target_bid='1.02',
        ),
    ])
    assert _gaps(task)  # defaults: ACOS 25 < 30, and 1.02 < 1.10
    rules = resolve_rules('acos_no_lower: 20\ncpc_floor_multiplier: 1.0')
    assert _gaps(task, rules) == []


def test_cooldown_holds_a_recently_changed_target(task):
    recent = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    task.drill(
        'A1234567',
        [
            {
                'ad_group': 'g1',
                'target': 'widget',
                'match_type': 'exact',
                'bid': '1.00',
                'applied_action': 'raise',
                'applied_at': recent,
            }
        ],
        [],
    )
    task.changes([change()])
    assert any('cooldown' in g for g in _gaps(task))
    assert _gaps(task, resolve_rules('change_cooldown_days: 1')) == []
    task.changes([change(action='hold', reason='changed 2 days ago')])
    assert _gaps(task) == []


def test_investigate_may_not_propose(task):
    task.declare('investigate')
    task.changes([change(action='hold', reason='fine')])
    assert _gaps(task) == []
    task.changes([change()])
    assert any('investigate' in g for g in _gaps(task))


def test_unreadable_changes_file_is_reported(task):
    (task.dir / 'CHANGES.tsv').write_text('a|b|c\n1|2|3\n')
    assert any('not tab-separated' in g for g in _gaps(task))


# ── execution vs the frozen approval ────────────────────────────────


def _approve(task, rows):
    task.changes(rows)
    assert ad_changes.freeze_approved_changes(task.id) is not None


def _done(**kw):
    row = {
        'campaign_id': 'A1234567',
        'ad_group': 'g1',
        'target': 'widget',
        'match_type': 'exact',
        'action': 'raise',
        'from_bid': '1.00',
        'to_bid': '1.20',
        'applied_at': '2026-01-01T00:00:00+00:00',
    }
    row.update({k: str(v) for k, v in kw.items()})
    return row


def test_execution_matching_the_approval_passes(task):
    _approve(task, [change()])
    task.execution([_done()])
    assert _gaps(task) == []


@pytest.mark.parametrize(
    'executed,expect',
    [
        (_done(to_bid='11.3'), 'approved 1.20'),  # the 10x overspend
        (_done(target='gadget'), 'not in the approved'),  # off-list row
        (_done(action='pause'), 'not in the approved'),  # over-pause
        (_done(action='negate', target='free'), 'not in the approved'),
    ],
)
def test_execution_diverging_from_the_approval_is_refused(
    task, executed, expect
):
    _approve(task, [change()])
    task.execution([executed])
    assert any(expect in g for g in _gaps(task)), _gaps(task)


def test_editing_changes_during_execution_does_not_move_the_approval(task):
    _approve(task, [change(target_bid='1.20')])
    task.changes([change(target_bid='11.3')])  # rewritten mid-execution
    task.execution([_done(to_bid='11.3')])
    assert any('approved 1.20' in g for g in _gaps(task))


def test_execution_without_any_approval_is_refused(task):
    task.execution([_done()])
    assert any('no approved' in g for g in _gaps(task))


def test_per_campaign_record_must_agree_with_the_log(task):
    _approve(task, [change()])
    task.execution([_done()])
    task.drill(
        'A1234567',
        [
            {
                'ad_group': 'g1',
                'target': 'widget',
                'match_type': 'exact',
                'bid': '1.20',
                'applied_action': 'pause',
            }
        ],
        [],
    )
    assert any('records applied_action' in g for g in _gaps(task))


def test_approval_is_inherited_by_an_execution_child(task, monkeypatch):
    """An execution task spawned from an audit freezes, when it declares
    ``execute``, the parent audit's CHANGES.tsv — and is held to it."""
    task.changes([change()])
    child = AdTask(f'{task.id}-exec', monkeypatch, None)
    monkeypatch.setattr(
        ad_changes,
        'parent_task_id',
        lambda t: task.id if t == child.id else None,
    )
    assert ad_changes.freeze_approved_changes(child.id) is not None
    child.execution([_done()])
    assert ad_changes.check('', child.id) is None
    child.execution([_done(to_bid='2.00')])
    assert ad_changes.check('', child.id) is not None


# ── proposals are judged on their numbers, not the row's claims ─────


def test_a_bid_change_with_no_numbers_is_refused(task):
    """Observed class: `lower 1.30 → 0.05` with blank metrics skipped
    every owner rule and passed."""
    task.changes([
        change(
            action='lower',
            current_bid='1.30',
            target_bid='0.05',
            acos='',
            roas='',
            cpc='',
        )
    ])
    assert any('cannot be checked' in g for g in _gaps(task))


def test_a_raise_that_cuts_the_bid_is_refused(task):
    task.changes([
        change(action='raise', current_bid='1.00', target_bid='0.60')
    ])
    assert any('goes the other way' in g for g in _gaps(task))


def test_the_drilled_numbers_win_over_the_rows_claim(task):
    """The row claims ACOS 40 (lowering allowed); the campaign's own
    drilled row says ACOS 20 — a converting keyword the rule protects."""
    task.drill(
        'A1234567',
        [
            {
                'ad_group': 'g1',
                'target': 'widget',
                'match_type': 'exact',
                'bid': '1.00',
                'clicks': '10',
                'spend': '5.00',
                'acos': '20',
            }
        ],
        [],
    )
    task.changes([change(action='lower', acos='40', target_bid='0.90')])
    assert any('ACOS 20' in g for g in _gaps(task))


def test_an_approved_change_is_not_re_judged_by_the_defaults(task):
    """The user approved a cut the default rule would refuse (ACOS 25
    under the 30 floor). In the execute phase their decision wins — also
    for an approved row not applied yet (e.g. a partial run)."""
    applied = change(target='gadget')
    approved_cut = change(
        action='lower', acos='25', current_bid='1.20', target_bid='1.00'
    )
    task.changes([applied, approved_cut])
    task.declare('audit')
    assert any('under 30% ACOS' in g for g in _gaps(task))
    _approve(task, [applied, approved_cut])
    task.declare('execute')
    task.execution([_done(target='gadget')])
    assert _gaps(task) == []


def test_a_rule_breaking_proposal_is_shown_not_held(task):
    """A proposal spends nothing: one that breaks a store rule (e.g. the
    user's own value under the ACOS floor) is flagged beside it for the
    user to decide, not refused as a money contradiction — that weight
    stays for execution that differs from the approval."""
    task.declare('audit')
    task.changes([
        change(action='lower', acos='25', current_bid='1.20', target_bid='1.00')
    ])
    assert any('under 30% ACOS' in g for g in _gaps(task))
    assert _contradictions(task) == []
    _approve(task, [change(target_bid='1.20')])
    task.declare('execute')
    task.execution([_done(to_bid='11.3')])
    assert any('approved 1.2' in c for c in _contradictions(task))


def test_a_raise_needs_no_cpc(task):
    """A raise (often on a target with no clicks yet) is valid without a
    CPC; the floor only bounds a cut."""
    task.changes([change(action='raise', cpc='', target_bid='1.20')])
    assert not any('cannot be checked' in g for g in _gaps(task))


def test_a_proposed_cpc_of_zero_is_no_evidence(task):
    """`cpc 0` would void the floor and reopen the `1.30 → 0.05` class."""
    task.changes([
        change(action='lower', cpc='0', current_bid='1.30', target_bid='0.05')
    ])
    assert any('no CPC' in g for g in _gaps(task))


def test_pausing_a_converting_target_is_flagged(task):
    task.changes([change(action='pause', acos='20', target_bid='')])
    assert any('pause with ACOS 20' in g for g in _gaps(task))
