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

import pathlib

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


_STORE = [
    {'platform': 'amazon', 'country': 'AE'},
    {'platform': 'noon', 'country': 'SA'},
]


class TestWhatAPhaseOwes:
    """An audit owes what it declared. Declaring nothing declares all."""

    def test_whole_store_audit_owes_every_marketplace(self):
        owed = ad.owed_combos({'kind': 'audit', 'scope': {}}, _STORE)
        assert owed == _STORE

    def test_listing_every_combo_owes_them_all_too(self):
        # The hole the first live run walked through: the agent declared
        # `audit` and then listed all five of the store's marketplaces
        # EXPLICITLY. Semantically the whole store, but the combo list
        # was non-empty, so the phase was judged "scoped" and owed
        # nothing — a full audit could quietly cover one market.
        owed = ad.owed_combos(
            {'kind': 'audit', 'scope': {'combos': _STORE}}, _STORE
        )
        assert owed == _STORE

    def test_scoped_audit_owes_only_what_it_named(self):
        owed = ad.owed_combos({'kind': 'audit', 'scope': _AE_ONLY}, _STORE)
        assert owed == _AE_ONLY['combos']

    def test_create_owes_nothing(self):
        assert ad.owed_combos({'kind': 'create', 'scope': {}}, _STORE) == []

    def test_execute_owes_nothing(self):
        assert ad.owed_combos({'kind': 'execute', 'scope': {}}, _STORE) == []

    def test_never_declared_owes_nothing_here(self):
        # It is refused separately for not declaring at all, which is a
        # better error than silently demanding five marketplaces of a
        # task nobody scoped.
        assert ad.owed_combos(None, _STORE) == []

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
        # It DOES owe the market it named — a scoped audit still has to
        # enumerate that market authoritatively. What it must never be
        # asked for is the four markets nobody mentioned.
        for other in ('amazon AE', 'amazon AU', 'amazon SA', 'noon SA'):
            assert not any(
                '根本没进 AUDIT_SCOPE.json' in g and other in g for g in gaps
            ), f'asked for {other}, which this phase never declared: {gaps}'

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


class TestEverySkillThatIsGatedTeachesTheDeclaration:
    """A fail-closed gate needs the skill to teach the way through it.

    The gate refuses an ad report that never declared. If a skill binds
    that gate but never tells the agent to call the tool, every task
    under it is denied with no documented remedy — a trap rather than a
    guard rail. So the two facts are pinned together.
    """

    _SKILLS = pathlib.Path('app/skills_v2')

    def _gated(self) -> list[pathlib.Path]:
        out = []
        for skill in sorted(self._SKILLS.glob('*/SKILL.md')):
            head = skill.read_text(encoding='utf-8')[:2000]
            if 'ad_completeness_review' in head:
                out.append(skill)
        return out

    def test_there_are_gated_ad_skills_to_check(self):
        # Guard against this whole class passing vacuously if the gate is
        # renamed or the frontmatter key moves.
        assert self._gated(), 'no skill binds ad_completeness_review'

    def test_each_one_tells_the_agent_to_declare_first(self):
        for skill in self._gated():
            body = skill.read_text(encoding='utf-8')
            assert 'vibe_seller_declare_ad_task' in body, (
                f'{skill} binds the completeness gate, which refuses an '
                'undeclared report, but never tells the agent to declare'
            )

    def test_each_one_warns_that_omitting_combos_means_everything(self):
        # The specific trap that caused the incident: a narrow task that
        # leaves `combos` out inherits the whole store's obligation.
        for skill in self._gated():
            body = skill.read_text(encoding='utf-8')
            assert 'scope trap' in body.lower(), (
                f'{skill} must warn that omitting combos means every '
                'marketplace'
            )
