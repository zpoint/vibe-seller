"""A phase says what it is for, and that is what it is held to.

The regression these pin, concretely: a task asked to create two
campaigns for ONE product on ONE marketplace wrote a single
``## noon AE`` heading. The completeness gate read that heading, decided
the task was an audit, and demanded all five of the store's
marketplaces. The agent had fresh data for none of the other four and
satisfied the gate by transcribing the previous week's audit file — so
the review console then offered 51 campaigns of four-day-old numbers as
actionable bid decisions.

Every test below is one edge of the contract that makes that
impossible: the obligation comes from what the phase DECLARED before
working, not from the shape of the prose it produced afterwards.
"""

import pytest

from app.ai import ad_declaration as ad
from app.ai.stop_gates import (
    ad_completeness_review as acr,
    ad_declaration_checks as adc,
    ad_scope,
)

pytestmark = pytest.mark.unit

_AE_ONLY = {'combos': [{'platform': 'noon', 'country': 'AE'}]}


def _setup(monkeypatch, tmp_path, task_id, *, declare=None, targets=None):
    monkeypatch.setattr(ad_scope, 'VIBE_SELLER_DIR', tmp_path)
    monkeypatch.setattr(ad, 'VIBE_SELLER_DIR', tmp_path)
    tdir = tmp_path / 'tasks' / task_id
    tdir.mkdir(parents=True, exist_ok=True)
    if targets is not None:
        ad_scope.write_declared_targets(tdir, targets)
    if declare is not None:
        ad.write_declaration_file(task_id, declare[0], declare[1])
    acr.reset_progress(task_id)
    return tdir


def _gaps(text, task_id):
    deny = acr.check(text, task_id=task_id, track=False)
    return list(deny.gaps) if deny else []


# A minimal report section: one marketplace, one campaign, drilled.
def _section(platform='noon', country='AE', campaign='C0000001'):
    return (
        f'## {platform} {country}\n\n'
        '**进度**: drilled 1/1 active (1 total, 1 page)\n\n'
        f'### {campaign} | acme widget 001 manual | Manual\n\n'
        '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
        '| widget red | 1.00 | 9 | 提高至 1.20（ROAS 9>5 加投赢家规则） |\n'
        '该活动类型无搜索词报告（SD）。\n'
    )


class TestWhoOwesMarketplaceCoverage:
    """Only a whole-store audit owes every marketplace. Nothing else."""

    def test_whole_store_audit_owes_coverage(self):
        assert ad.owes_marketplace_coverage({'kind': 'audit', 'scope': {}})

    def test_scoped_audit_owes_only_its_own(self):
        assert not ad.owes_marketplace_coverage({
            'kind': 'audit',
            'scope': _AE_ONLY,
        })

    def test_create_owes_nothing(self):
        assert not ad.owes_marketplace_coverage({'kind': 'create', 'scope': {}})

    def test_never_declared_owes_nothing(self):
        # …because it is denied for not declaring at all — see below.
        # Silently owing everything is what produced the incident.
        assert not ad.owes_marketplace_coverage(None)

    def test_absent_combos_means_whole_store(self):
        # Fails toward MORE coverage: an agent that omits the field is
        # asked for everything, never excused from everything.
        assert ad.is_whole_store({})
        assert ad.is_whole_store({'campaigns': ['A1234567']})
        assert not ad.is_whole_store(_AE_ONLY)


class TestTheIncidentCannotRecur:
    """The create-one-campaign task is no longer told it owes five markets."""

    def test_create_phase_is_not_asked_for_other_marketplaces(
        self, monkeypatch, tmp_path
    ):
        _setup(
            monkeypatch,
            tmp_path,
            't-create',
            declare=('create', _AE_ONLY),
            targets={'amazon': ['AE', 'AU', 'SA'], 'noon': ['AE', 'SA']},
        )
        gaps = _gaps(_section(), 't-create')
        assert not any('根本没进 AUDIT_SCOPE.json' in g for g in gaps), gaps

    def test_scoped_audit_is_not_asked_for_other_marketplaces(
        self, monkeypatch, tmp_path
    ):
        # "Audit just this one campaign" is an AUDIT — it opens a console.
        # It still must not be inflated into a whole-store sweep.
        _setup(
            monkeypatch,
            tmp_path,
            't-scoped',
            declare=('audit', _AE_ONLY),
            targets={'amazon': ['AE', 'AU', 'SA'], 'noon': ['AE', 'SA']},
        )
        gaps = _gaps(_section(), 't-scoped')
        assert not any('根本没进 AUDIT_SCOPE.json' in g for g in gaps), gaps

    def test_whole_store_audit_still_owes_every_marketplace(
        self, monkeypatch, tmp_path
    ):
        # The protection the incident fix must not cost us: an unqualified
        # "audit the ads" still cannot quietly cover one market.
        _setup(
            monkeypatch,
            tmp_path,
            't-whole',
            declare=('audit', {}),
            targets={'amazon': ['AE'], 'noon': ['AE', 'SA']},
        )
        gaps = _gaps(_section(), 't-whole')
        assert any('noon SA' in g for g in gaps), gaps
        assert any('amazon AE' in g for g in gaps), gaps


class TestDeclarationIsAPrecondition:
    def test_reporting_without_declaring_is_a_gap(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, 't-undeclared', declare=None)
        gaps = _gaps(_section(), 't-undeclared')
        assert any('没有声明任务范围' in g for g in gaps), gaps

    def test_a_result_claiming_no_marketplaces_needs_no_declaration(
        self, monkeypatch, tmp_path
    ):
        # An execution summary or a plain answer has no combo section.
        # Demanding a declaration of it would deny work that never
        # claimed to audit anything.
        _setup(monkeypatch, tmp_path, 't-prose', declare=None)
        assert (
            _gaps('Amazon SA 广告优化执行完毕，12 项已应用。', 't-prose') == []
        )

    def test_no_task_means_no_declaration_check(self):
        # Called with no task at all (a direct probe), there is nothing
        # that COULD have declared. Denying here would be denying the
        # absence of a task, not the absence of intent.
        assert adc.declaration_gaps([''], None) == []
        deny = acr.check(_section(), task_id=None, track=False)
        assert deny is None or not any(
            '没有声明任务范围' in g for g in deny.gaps
        )


class TestOutputMustAgreeWithTheDeclaration:
    """The old heuristic survives — demoted from decider to auditor."""

    def test_reporting_outside_the_declared_scope_is_a_gap(
        self, monkeypatch, tmp_path
    ):
        _setup(monkeypatch, tmp_path, 't-drift', declare=('audit', _AE_ONLY))
        gaps = _gaps(_section() + '\n' + _section('amazon', 'SA'), 't-drift')
        assert any('范围外的市场' in g and 'amazon SA' in g for g in gaps), gaps

    def test_declaring_create_then_auditing_everything_is_a_gap(
        self, monkeypatch, tmp_path
    ):
        # The false-declaration escape hatch: claim `create` to dodge
        # coverage, then produce a sprawling audit anyway.
        _setup(monkeypatch, tmp_path, 't-liar', declare=('create', _AE_ONLY))
        gaps = _gaps(_section() + '\n' + _section('amazon', 'AU'), 't-liar')
        assert any('范围外的市场' in g for g in gaps), gaps

    def test_in_scope_reporting_raises_nothing(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, 't-ok', declare=('audit', _AE_ONLY))
        gaps = _gaps(_section(), 't-ok')
        assert not any('范围外的市场' in g for g in gaps), gaps

    def test_whole_store_scope_puts_everything_in_scope(
        self, monkeypatch, tmp_path
    ):
        _setup(monkeypatch, tmp_path, 't-any', declare=('audit', {}))
        gaps = _gaps(_section() + '\n' + _section('amazon', 'SA'), 't-any')
        assert not any('范围外的市场' in g for g in gaps), gaps


class TestScopePredicates:
    def test_campaign_scope_narrows_within_a_market(self):
        scope = ad.normalise_scope({
            'combos': [{'platform': 'amazon', 'country': 'AE'}],
            'campaigns': ['A1234567'],
        })
        assert ad.campaign_in_scope(scope, 'A1234567')
        assert not ad.campaign_in_scope(scope, '100000000001')

    def test_no_campaign_list_means_every_campaign(self):
        assert ad.campaign_in_scope(_AE_ONLY, 'anything')

    def test_normalise_is_case_and_whitespace_tolerant(self):
        scope = ad.normalise_scope({
            'combos': [{'platform': 'Amazon', 'country': 'ae'}],
            'campaigns': [' A1234567 ', '', '   '],
            'products': ['WIDGET-006'],
        })
        assert scope['combos'] == [{'platform': 'amazon', 'country': 'AE'}]
        assert scope['campaigns'] == ['A1234567']
        assert ad.combo_in_scope(scope, 'amazon', 'AE')

    def test_garbage_scope_degrades_to_whole_store(self):
        # Not to "no obligation" — an unreadable scope must not become a
        # free pass, which is the direction that hides work.
        assert ad.is_whole_store(ad.normalise_scope('not a dict'))
        assert ad.is_whole_store(ad.normalise_scope({'combos': ['junk']}))
