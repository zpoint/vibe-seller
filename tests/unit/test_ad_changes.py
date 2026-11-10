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
            'under 25% ACOS',
        ),
        (
            change(action='lower', acos='', roas='5', target_bid='0.90'),
            'under 25% ACOS',
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
            acos='22',
            cpc='1.00',
            current_bid='1.20',
            target_bid='1.02',
        ),
    ])
    assert _gaps(task)  # defaults: ACOS 22 < 25, and 1.02 < 1.10
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
    """The user approved a cut the default rule would refuse (ACOS 20
    under the 25 floor). In the execute phase their decision wins — also
    for an approved row not applied yet (e.g. a partial run)."""
    applied = change(target='gadget')
    approved_cut = change(
        action='lower', acos='20', current_bid='1.20', target_bid='1.00'
    )
    task.changes([applied, approved_cut])
    task.declare('audit')
    assert any('under 25% ACOS' in g for g in _gaps(task))
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
        change(action='lower', acos='20', current_bid='1.20', target_bid='1.00')
    ])
    assert any('under 25% ACOS' in g for g in _gaps(task))
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


def test_a_target_changed_since_the_task_started_is_not_re_judged(
    monkeypatch,
):
    """A parent audit whose execution child applied its proposal: the
    child rewrote the campaign TSV (new bid, fresh applied_at) and logged
    the change, so the parent's proposal row now reads as a cut inside
    the cooldown and a 'raise' that does not go up. A target changed by
    an execution since this task began is no longer this task's proposal
    to judge."""
    start = datetime.now(UTC) - timedelta(hours=1)
    t = AdTask(f'chg-{uuid.uuid4().hex[:8]}', monkeypatch, start.isoformat())
    t.declare('audit')
    t.changes([change(target_bid='1.35')])
    t.execution_child([_done(action='raise', to_bid='1.35')])
    t.drill(
        'A1234567',
        [
            {
                'ad_group': 'g1',
                'target': 'widget',
                'match_type': 'exact',
                'bid': '1.35',
                'applied_action': 'raise',
                'applied_at': datetime.now(UTC).isoformat(),
            }
        ],
        [],
    )
    assert _gaps(t) == []
    # Control: the same change applied BEFORE the task began is still a
    # target inside the cooldown.
    t.drill(
        'A1234567',
        [
            {
                'ad_group': 'g1',
                'target': 'widget',
                'match_type': 'exact',
                'bid': '1.35',
                'applied_action': 'raise',
                'applied_at': (start - timedelta(days=1)).isoformat(),
            }
        ],
        [],
    )
    assert any('cooldown' in g for g in _gaps(t)), _gaps(t)


def test_a_stamp_no_execution_log_backs_is_still_judged(monkeypatch):
    """The writeback columns are the agent's own text: stamping
    ``applied_at`` after the task began used to exempt the target from
    EVERY store rule, without any bid being moved. The same cut — ACOS 5
    under the floor, 0.40 under CPC 2.00 x 1.1 — is refused on its own
    numbers, and the stamp excuses it only once an execution log (this
    task's or a child's) records the change it claims."""
    start = datetime.now(UTC) - timedelta(hours=1)
    t = AdTask(f'chg-{uuid.uuid4().hex[:8]}', monkeypatch, start.isoformat())
    t.declare('audit')
    t.changes([
        change(
            action='lower',
            acos='5',
            cpc='2.00',
            current_bid='1.00',
            target_bid='0.40',
        )
    ])
    t.drill(
        'A1234567',
        [
            {
                'ad_group': 'g1',
                'target': 'widget',
                'match_type': 'exact',
                'bid': '0.40',
                'clicks': '10',
                'spend': '20.00',
                'acos': '5',
                'applied_action': 'lower',
                'applied_at': datetime.now(UTC).isoformat(),
            }
        ],
        [],
    )
    gaps = _gaps(t)
    assert any('under 25% ACOS' in g for g in gaps), gaps
    assert any('below CPC' in g for g in gaps), gaps
    # Backed by the execution that made it, the same stamp is a done
    # change and the proposal is not re-judged.
    t.execution_child([_done(action='lower', from_bid='1.00', to_bid='0.40')])
    assert _gaps(t) == [], _gaps(t)


def test_a_second_approval_keeps_the_first_rounds_changes_approved(task):
    """Approved twice in one task: 'raise widget to 1.20' was applied,
    then the user approved a different change. The first round's logged
    change is still one the user approved."""
    _approve(task, [change()])
    task.declare('execute')
    task.execution([_done()])
    assert _gaps(task) == []
    _approve(task, [change(target='gadget', target_bid='1.50')])
    task.execution([_done(), _done(target='gadget', to_bid='1.50')])
    assert _gaps(task) == []
    # Neither approval holds this one.
    task.execution([_done(), _done(target='gadget', to_bid='1.90')])
    assert any('approved 1.50' in g for g in _gaps(task)), _gaps(task)
    ad_changes.remove_approvals(task.id)
    assert ad_changes.approval_files(task.id) == []


def test_a_re_freeze_before_anything_ran_replaces_the_approval(task):
    """Same turn, nothing executed yet: the newer freeze is the only
    approval, so the replaced value no longer holds."""
    _approve(task, [change(target_bid='1.20')])
    _approve(task, [change(target_bid='1.35')])
    task.declare('execute')
    task.execution([_done(to_bid='1.20')])
    assert any('approved 1.35' in g for g in _gaps(task)), _gaps(task)


def test_a_blank_ad_group_still_finds_its_target(task):
    """CHANGES.tsv without an ad_group, the drilled TSV and the log with
    one: the same keyword. A correct execution is not 'unapproved', and
    the proposal is judged on the drilled numbers (ACOS 20, converting),
    not its own copy (ACOS 45)."""
    task.drill(
        'A1234567',
        [
            {
                'ad_group': 'g1',
                'target': 'widget',
                'match_type': 'exact',
                'bid': '1.20',
                'acos': '20',
            }
        ],
        [],
    )
    cut = change(
        ad_group='',
        action='lower',
        acos='45',
        current_bid='1.20',
        target_bid='1.10',
    )
    task.changes([cut])
    assert any('under 25% ACOS' in g for g in _gaps(task)), _gaps(task)
    _approve(task, [cut])
    task.declare('execute')
    task.execution([_done(action='lower', from_bid='1.20', to_bid='1.10')])
    assert not any('not in the approved' in g for g in _gaps(task))


# ── a blank ad_group names one copy, not any ────────────────────────


def _two_ad_groups(task):
    """The keyword "widget exact" sitting in BOTH g1 and g2."""
    task.drill(
        'A1234567',
        [
            {
                'ad_group': g,
                'target': 'widget',
                'match_type': 'exact',
                'bid': '1.20',
                'acos': '20',
            }
            for g in ('g1', 'g2')
        ],
        [],
    )


def test_a_blank_ad_group_names_one_copy_not_any(task):
    """With the keyword in g1 AND g2, a proposal that names neither is
    no copy's: no drilled row is its own, so it is judged on the numbers
    it carries (ACOS 45, a cut that passes) rather than g1's (ACOS 20,
    which the floor protects — see the single-copy case above)."""
    _two_ad_groups(task)
    task.changes([
        change(
            ad_group='',
            action='lower',
            acos='45',
            current_bid='1.20',
            target_bid='1.10',
        )
    ])
    assert _gaps(task) == [], _gaps(task)


def test_a_blank_approval_does_not_cover_several_ad_groups(task):
    """An executed change in g2 is not covered by an approval that
    names neither copy — a blank names its ad group only when the
    target sits in one, the rule ads_bulk --from-changes enforces."""
    _two_ad_groups(task)
    _approve(task, [change(ad_group='', target_bid='1.20')])
    task.declare('execute')
    task.execution([_done(ad_group='g2')])
    assert any('not in the approved' in g for g in _gaps(task)), _gaps(task)


def test_another_ad_groups_execution_does_not_excuse_a_proposal(task):
    """The ``done`` side of the same rule: a logged change in g2 is not
    the blank proposal's own execution, so the proposal is still judged
    — and its own ACOS 20 is a cut the floor refuses."""
    _two_ad_groups(task)
    task.changes([
        change(
            ad_group='',
            action='lower',
            acos='20',
            current_bid='1.20',
            target_bid='1.10',
        )
    ])
    task.execution([
        _done(ad_group='g2', action='lower', from_bid='1.20', to_bid='1.10')
    ])
    assert any('under 25% ACOS' in g for g in _gaps(task)), _gaps(task)


def test_a_blank_ad_group_on_the_log_row_finds_its_copy(task):
    """EXECUTION_LOG.tsv has no platform/country columns, so a blank
    ad_group on the EXECUTED side is located through the approval it is
    matched against — not raised on, which 500s the submit. One copy of
    the keyword, so the blank matches it."""
    _approve(task, [change(target_bid='1.20')])
    task.declare('execute')
    task.drill(
        'A1234567',
        [
            {
                'ad_group': 'g1',
                'target': 'widget',
                'match_type': 'exact',
                'bid': '1.20',
                'applied_action': 'raise',
            }
        ],
        [],
    )
    task.execution([_done(ad_group='')])
    assert _gaps(task) == [], _gaps(task)


def test_a_blank_ad_group_on_the_log_row_names_one_copy_not_any(task):
    """The same blank executed row, with the keyword in g1 AND g2: it
    names no copy, so an approval naming g2 does not read as covering
    it — the log row is held to the same rule as a proposal."""
    _two_ad_groups(task)
    _approve(task, [change(ad_group='g2', target_bid='1.20')])
    task.declare('execute')
    task.execution([_done(ad_group='', to_bid='1.20')])
    assert any('not in the approved' in g for g in _gaps(task)), _gaps(task)
