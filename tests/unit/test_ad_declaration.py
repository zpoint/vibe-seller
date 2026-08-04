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

import json
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
        ad_scope.write_declared_targets(tdir, targets, slug='acme')
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


class TestTheInTurnRatchet:
    """A re-declaration may shrink reach or add obligation. Never both ways.

    The ratchet used to refuse every kind change outright, on the sound
    reasoning that an agent must not re-declare its way around a gate.
    But that left no move at all for the OPPOSITE mistake — an agent that
    declared `investigate`, then produced a table of bid changes, had
    under-declared what it owed and could only comply by deleting the
    recommendations the person had asked for.

    So the rule is stated the way it was always meant: never widen reach,
    never shed an obligation. `investigate` → `audit` satisfies both (it
    adds coverage AND opens the console), and every other pair does not.
    """

    _SA = {'combos': [{'platform': 'amazon', 'country': 'SA'}]}
    _SA_ONE = dict(_SA, campaigns=['A1234567'])

    def test_the_under_declared_phase_can_correct_itself(self):
        assert ad.supersedes('investigate', self._SA, 'audit', self._SA)

    def test_correcting_upward_may_also_narrow(self):
        assert ad.supersedes('investigate', self._SA, 'audit', self._SA_ONE)

    def test_correcting_upward_may_not_widen_the_markets(self):
        wider = {
            'combos': [
                {'platform': 'amazon', 'country': 'SA'},
                {'platform': 'noon', 'country': 'AE'},
            ]
        }
        assert not ad.supersedes('investigate', self._SA, 'audit', wider)

    def test_correcting_upward_may_not_widen_the_campaigns(self):
        assert not ad.supersedes('investigate', self._SA_ONE, 'audit', self._SA)

    def test_whole_store_investigate_may_narrow_as_it_upgrades(self):
        # The safety valve for the expensive case: upgrading an unscoped
        # `investigate` straight to `audit` would owe every marketplace,
        # so naming the markets actually covered must be legal.
        assert ad.supersedes('investigate', {}, 'audit', self._SA)

    def test_a_named_market_may_not_become_the_whole_store(self):
        assert not ad.supersedes('investigate', self._SA, 'audit', {})

    def test_audit_may_not_downgrade_to_investigate(self):
        # The escape. `investigate` owes no coverage and opens no
        # console, so this is how an agent would shed both.
        assert not ad.supersedes('audit', self._SA, 'investigate', self._SA)

    def test_no_other_kind_change_is_allowed(self):
        for prev, new in (
            ('create', 'audit'),
            ('audit', 'create'),
            ('execute', 'audit'),
            ('audit', 'execute'),
            ('create', 'investigate'),
            ('investigate', 'create'),
            ('investigate', 'execute'),
        ):
            assert not ad.supersedes(prev, self._SA, new, self._SA), (
                f'{prev} -> {new}'
            )

    def test_same_kind_still_needs_a_real_narrowing(self):
        # Unchanged behaviour: re-stating an identical scope at the same
        # kind is a no-op, and a no-op re-declaration is refused.
        assert not ad.supersedes('audit', self._SA, 'audit', self._SA)
        assert ad.supersedes('audit', self._SA, 'audit', self._SA_ONE)

    def test_reach_within_accepts_an_unchanged_scope(self):
        # This is the difference from `is_narrowing`, and the reason both
        # exist: a kind correction usually leaves the scope alone.
        assert ad.reach_within(self._SA, self._SA)
        assert not ad.is_narrowing(self._SA, self._SA)


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


class TestInvestigateMayNotHandOutDecisions:
    """Reading is reading. Recommending is an audit, and owes a console.

    Sharpening the investigate/audit line in the skill — a question about
    figures is `investigate` — opens exactly one hole if left unguarded:
    declare `investigate`, owe no marketplace coverage, get no console,
    and hand the user a table of bid changes they have no way to act on.
    """

    def test_investigate_with_bid_changes_is_a_gap(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, 't-inv', declare=('investigate', {}))
        report = _section().replace(
            '提高至 1.20（ROAS 9>5 加投赢家规则）', '下调至 0.60（ROAS 偏低）'
        )
        gaps = _gaps(report, 't-inv')
        assert any('investigate' in g and '可执行建议' in g for g in gaps), gaps

    def test_investigate_that_only_reports_figures_passes(
        self, monkeypatch, tmp_path
    ):
        # The legitimate shape: answer the question, propose nothing.
        _setup(monkeypatch, tmp_path, 't-inv-ok', declare=('investigate', {}))
        report = (
            '## amazon AE\n\n'
            '**进度**: drilled 1/1 active (1 total, 1 page)\n\n'
            '### A1234567 | acme widget 001 manual | Manual\n\n'
            '| 关键词 | 出价 | 点击 | 花费 | 订单 | ROAS |\n'
            '|---|---|---|---|---|---|\n'
            '| widget red | 1.00 | 40 | 80.00 | 4 | 5.00 |\n'
        )
        gaps = _gaps(report, 't-inv-ok')
        assert not any('可执行建议' in g for g in gaps), gaps

    def test_an_audit_may_of_course_recommend(self, monkeypatch, tmp_path):
        # The guard must not fire on the kind whose whole job is deciding.
        _setup(monkeypatch, tmp_path, 't-aud-ok', declare=('audit', {}))
        gaps = _gaps(_section(), 't-aud-ok')
        assert not any('可执行建议' in g for g in gaps), gaps

    def test_prose_observations_are_not_decisions(self, monkeypatch, tmp_path):
        # Noting that something looks off is an observation; a TABLE ROW
        # carrying 下调至 1.80 is a decision someone will act on.
        _setup(
            monkeypatch, tmp_path, 't-inv-prose', declare=('investigate', {})
        )
        report = (
            '## amazon AE\n\n'
            '**进度**: drilled 1/1 active (1 total, 1 page)\n\n'
            'ROAS 偏低，值得下轮复核时看看要不要下调出价。\n'
        )
        assert not any('可执行建议' in g for g in _gaps(report, 't-inv-prose'))

    def test_decisions_without_marketplace_headings_still_count(
        self, monkeypatch, tmp_path
    ):
        """The hole this whole check used to sit in.

        The guard ran only after ``reported_combos`` found at least one
        ``## <platform> <CC>`` section — a condition about markdown
        layout, not about whether decisions were handed out. A report
        written against a console on some other host, or one that simply
        did not use per-marketplace headings, escaped entirely.

        Observed in CI: a bid review declared `investigate`, delivered
        its bid changes, raised no gap, and gave the user no console to
        approve them on.
        """
        _setup(monkeypatch, tmp_path, 't-inv-flat', declare=('investigate', {}))
        report = (
            '关键词出价复核结果：\n\n'
            '| 活动 | 关键词 | 当前出价 | 建议 |\n|---|---|---|---|\n'
            '| A1234567 | widget red | 1.00 | 下调至 0.60（ROAS 偏低） |\n'
        )
        assert '##' not in report, 'the point is that there are no sections'
        gaps = _gaps(report, 't-inv-flat')
        assert any('investigate' in g and '可执行建议' in g for g in gaps), gaps

    def test_the_gap_names_the_legal_correction(self, monkeypatch, tmp_path):
        # A refusal with no way through is a refusal that gets satisfied
        # by deleting the recommendations — which is the opposite of what
        # the person asked for. The gap has to say "re-declare as audit".
        _setup(monkeypatch, tmp_path, 't-inv-fix', declare=('investigate', {}))
        report = _section().replace(
            '提高至 1.20（ROAS 9>5 加投赢家规则）', '下调至 0.60（ROAS 偏低）'
        )
        gap = next(g for g in _gaps(report, 't-inv-fix') if '可执行建议' in g)
        assert 'vibe_seller_declare_ad_task' in gap
        assert 'audit' in gap

    def test_an_undeclared_flat_report_is_still_not_demanded_a_declaration(
        self, monkeypatch, tmp_path
    ):
        # The fail-safe direction stays exactly as documented in
        # CLAUDE.md: a report claiming no marketplace coverage is never
        # required to have declared. Moving the investigate check out
        # from behind that condition must not drag this with it —
        # inferring a declaration from a report is the forbidden
        # heuristic wearing a new hat.
        _setup(monkeypatch, tmp_path, 't-flat-none', declare=None)
        report = '| 活动 | 建议 |\n|---|---|\n| A1234567 | 下调至 0.60 |\n'
        assert not any(
            '没有声明任务范围' in g for g in _gaps(report, 't-flat-none')
        )


class TestTheDocsAgreeWithTheObligationRule:
    """The reviewer believes the docs, so the docs must match the code.

    `owed_combos` was changed so a phase owes the combos it DECLARED —
    but the skill references still said the obligation was fixed by
    `AUDIT_TARGETS.json`, the server's list of every marketplace the
    store sells on. The contract then lived in two places that
    disagreed, and the agent followed the prose.

    Observed live: a request scoped to one SKU family on Amazon SA
    declared `amazon SA` correctly, then stopped mid-run to ask whether
    it should audit the other four marketplaces anyway, because "复评审器
    仍要求我补足 AE/AU/noon 三个组合" — roughly thirty extra campaigns of
    work nobody asked for.
    """

    _DOCS = [
        pathlib.Path('app/skills_v2/amazon-ads/SKILL.md'),
        pathlib.Path('app/skills_v2/noon-ads/SKILL.md'),
        pathlib.Path('app/skills_v2/amazon-ads/references/output-spec.md'),
        pathlib.Path('app/skills_v2/amazon-ads/references/audit-quickref.md'),
        pathlib.Path('app/skills_v2/noon-ads/references/ads-tuning.md'),
    ]

    def test_the_docs_exist(self):
        for d in self._DOCS:
            assert d.is_file(), f'{d} moved — update this test'

    def test_no_doc_claims_the_target_file_fixes_the_obligation(self):
        # The exact phrasings that sent the agent past its own scope.
        banned = (
            'Which marketplaces you owe is fixed by',
            'Which countries you owe is fixed by',
            'Scope is fixed by `AUDIT_TARGETS.json`',
            'Which countries is not your call',
            'audit EVERY combo',
        )
        for d in self._DOCS:
            body = d.read_text(encoding='utf-8')
            for phrase in banned:
                assert phrase not in body, (
                    f'{d} still says the obligation comes from '
                    f'AUDIT_TARGETS.json ({phrase!r}) — an agent reading '
                    'it will audit marketplaces the user excluded'
                )

    def test_every_doc_names_the_declaration_as_the_source_of_scope(self):
        for d in self._DOCS:
            body = d.read_text(encoding='utf-8')
            assert 'declar' in body.lower(), (
                f'{d} never mentions the declaration, so an agent reading '
                'only this file has no way to know what bounds its scope'
            )


class TestAScopedAuditIsNotAShrinkingMarket:
    """Naming your campaigns is not under-enumerating the marketplace.

    The shrink check challenges an `active_ids` count below half the
    campaigns the store has historical TSVs for — it caught a real
    under-enumeration (12 campaigns declared as 4 because a script
    silently skipped unreadable TSVs). But it compared against the whole
    market's history with no idea the phase had DECLARED a subset.

    Observed live: "just the widget-006 family on Amazon SA" is a
    handful of campaigns in a market with 22 historical TSVs, so a
    correct scope
    read as a collapse and the agent was told to re-enumerate a market
    nobody asked about.
    """

    _REPORT_ROWS = (
        '### 600000000001 | acme socks manual | Manual\n\n'
        '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
        '| widget red | 1 | 9 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
        '该活动类型无搜索词报告（SD）。\n'
    )

    def _setup_history(self, monkeypatch, tmp_path, task_id, *, declare):
        tdir = _setup(
            monkeypatch,
            tmp_path,
            task_id,
            declare=declare,
            targets={'amazon': ['SA']},
        )
        # The shrink check only runs once a combo has an authoritative
        # active-id list to compare against.
        (tdir / 'AUDIT_SCOPE.json').write_text(
            json.dumps({
                'combos': [
                    {
                        'platform': 'amazon',
                        'country': 'SA',
                        'active_ids': ['600000000001'],
                        'total_active': 1,
                        'total_active_source': 'chip:Live 1',
                    }
                ]
            }),
            encoding='utf-8',
        )
        # A market with a long history: 22 campaigns ever audited.
        # prior_campaign_tsvs reads the legacy `stores/` tree only —
        # see the note in the module docstring about the two trees.
        d = tmp_path / 'stores' / 'acme' / 'ads' / 'amazon' / 'sa'
        d.mkdir(parents=True, exist_ok=True)
        for i in range(22):
            (d / f'{600000000000 + i}.tsv').write_text('x', encoding='utf-8')
        return tmp_path

    def test_naming_campaigns_silences_the_shrink_challenge(
        self, monkeypatch, tmp_path
    ):
        scope = {
            'combos': [{'platform': 'amazon', 'country': 'SA'}],
            'campaigns': ['600000000001'],
        }
        self._setup_history(
            monkeypatch, tmp_path, 't-scoped-shrink', declare=('audit', scope)
        )
        gaps = _gaps(
            '## amazon SA\n\n**进度**: drilled 1/1 active (1 total, 1 page)\n\n'
            + self._REPORT_ROWS,
            't-scoped-shrink',
        )
        assert not any('不到历史的一半' in g for g in gaps), gaps

    def test_a_whole_market_audit_is_still_challenged(
        self, monkeypatch, tmp_path
    ):
        # The protection this must not cost us: no campaign list means
        # the whole market, and one active out of 22 is worth querying.
        scope = {'combos': [{'platform': 'amazon', 'country': 'SA'}]}
        self._setup_history(
            monkeypatch, tmp_path, 't-wide-shrink', declare=('audit', scope)
        )
        gaps = _gaps(
            '## amazon SA\n\n**进度**: drilled 1/1 active (1 total, 1 page)\n\n'
            + self._REPORT_ROWS,
            't-wide-shrink',
        )
        assert any('不到历史的一半' in g for g in gaps), gaps
