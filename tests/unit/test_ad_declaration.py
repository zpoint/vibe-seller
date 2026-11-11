"""A phase says what it is for, and that is what it is held to.

The regression these pin, concretely: a task asked to create two
campaigns for ONE product on ONE marketplace wrote a single
``## noon AE`` heading. The completeness gate read that heading, decided
the task was an audit, and demanded all five of the store's
marketplaces. The agent had fresh data for none of the other four and
satisfied the gate by transcribing the previous week's audit file — so
what passed was 51 campaigns of four-day-old numbers offered up as
actionable bid decisions.

Every test below is one edge of the contract that makes that
impossible: the obligation comes from what the phase DECLARED before
working, not from the shape of the prose it produced afterwards.
"""

import json
import pathlib

import pytest

from app.ai import ad_declaration as ad
from app.ai.stop_gates import ad_scope

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
    return tdir


# A minimal report section: one marketplace, one campaign, drilled.


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
    only ADDS the coverage obligation), and every other pair does not.
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
        # The escape. `investigate` owes no coverage, so this is how an
        # agent would shed it.
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

    The coverage gate flags audit data that was never declared. If a skill binds
    that gate but never tells the agent to call the tool, every task
    under it is denied with no documented remedy — a trap rather than a
    guard rail. So the two facts are pinned together.
    """

    _SKILLS = pathlib.Path('app/skills_v2')

    def _gated(self) -> list[pathlib.Path]:
        out = []
        for skill in sorted(self._SKILLS.glob('*/SKILL.md')):
            head = skill.read_text(encoding='utf-8')[:2000]
            if 'ad_coverage' in head:
                out.append(skill)
        return out

    def test_there_are_gated_ad_skills_to_check(self):
        # Guard against this whole class passing vacuously if the gate is
        # renamed or the frontmatter key moves.
        assert self._gated(), 'no skill binds ad_coverage'

    def test_each_one_tells_the_agent_to_declare_first(self):
        for skill in self._gated():
            body = skill.read_text(encoding='utf-8')
            assert 'vibe_seller_declare_ad_task' in body, (
                f'{skill} binds the coverage gate, which flags undeclared '
                'audit data, but never tells the agent to declare'
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


class TestTheDocsAgreeWithTheObligationRule:
    """The agent believes the docs, so the docs must match the code.

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


class TestADeclaredTaskAlwaysHasAMenu:
    """The skill says "declare, then read ``AUDIT_TARGETS.json``"; a store
    with no configured marketplace used to get no file. In CI audits on
    such a store hunted for it through dozens of spec re-reads, rewrote
    the store's metadata.json to invent a menu, and ran out of time."""

    def test_declaring_writes_an_empty_menu(self, monkeypatch, tmp_path):
        tdir = _setup(monkeypatch, tmp_path, 't-empty')
        assert not (tdir / 'AUDIT_TARGETS.json').exists()
        ad.write_declaration_file('t-empty', 'audit', {})
        data = json.loads((tdir / 'AUDIT_TARGETS.json').read_text())
        assert data == {'combos': []}
        # An empty menu owes exactly what an absent one did.
        assert ad_scope.load_declared_targets('t-empty') == []

    def test_declaring_keeps_the_server_menu(self, monkeypatch, tmp_path):
        tdir = _setup(monkeypatch, tmp_path, 't-menu', targets={'noon': ['AE']})
        ad.write_declaration_file('t-menu', 'audit', {})
        assert ad_scope.load_declared_targets('t-menu') == [
            {'platform': 'noon', 'country': 'AE'}
        ]
        data = json.loads((tdir / 'AUDIT_TARGETS.json').read_text())
        assert data['slug'] == 'acme'

    def test_task_start_hands_no_menu_to_an_unconfigured_store(self, tmp_path):
        """A store task that never declares (a lookup, a listing edit)
        is not handed an ad artefact: in CI a "find the secret" task
        cat-ed it and spent turns on it."""
        ad_scope.write_declared_targets(tmp_path, {}, slug='acme')
        assert not (tmp_path / 'AUDIT_TARGETS.json').exists()


class TestScopeEntriesNameWhatIsInScope:
    """CI 201k: a follow-up declared
    ``products: ['widget-006 manual campaign only — sprocket-009 manual
    explicitly out of scope']`` — the EXCLUDED product landed in the
    list of what IS in scope (and renders back to the user that way).
    The axis is not free prose: an entry NAMES one thing in scope, and
    exclusions are implied by omission.
    """

    def test_the_ci_shape_is_refused_with_a_helpful_message(self):
        with pytest.raises(ValueError) as exc:
            ad.validate_scope({
                'campaigns': ['A1234567'],
                'products': [
                    'widget-006 manual campaign only — sprocket-009 '
                    'manual explicitly out of scope'
                ],
            })
        msg = str(exc.value)
        assert 'sprocket-009' in msg, msg  # names the offending entry
        assert 'exclusion/negation' in msg, msg
        assert 'implied by omission' in msg, msg  # what to do instead

    @pytest.mark.parametrize(
        'entry',
        [
            'sprocket-009 manual explicitly out of scope',
            'widget-006 except sprocket-009',
            'widget-006 without sprocket-009',
            'not including sprocket-009',
            'all minus sprocket-009',
            'widget-006, excluding the manual line',
            'widget-006 but not sprocket-009',
            'everything outside the manual line',
            'not A7654321',  # bare negation
            'widget-006 手动广告（sprocket-009 除外）',
            '不包括 sprocket-009',
            'widget-006，不含手动组',
            '排除 A7654321',
            '除了 sprocket-009 以外的全部',
        ],
    )
    def test_exclusion_shapes_are_refused(self, entry):
        with pytest.raises(ValueError):
            ad.validate_scope({'products': [entry]})

    @pytest.mark.parametrize(
        'entry',
        [
            'WIDGET-006',
            'the widget-006 family',
            'widget-006 manual campaign',
            'sprocket-009 manual',
            'widget-006 rollout line',  # "out" inside a word is not an
            # exclusion shape
            'widget-006 手动广告',
            'widget-006 全系列',
        ],
    )
    def test_legitimate_anchors_are_accepted(self, entry):
        ad.validate_scope({'products': [entry]})  # must not raise
        ad.validate_scope({'campaigns': ['A1234567']})

    @pytest.mark.parametrize(
        'entry',
        [
            'all',
            'ALL',
            '*',
            'everything',
            'all campaigns in the local ad console at http://127.0.0.1:1',
            'every product',
            'all the keywords',
            '全部',
            '所有广告活动',
        ],
    )
    def test_whole_set_placeholders_are_refused(self, entry):
        """Observed: "audit everything" declared as campaigns=["all"],
        which reads as narrowed to one campaign called "all"; the empty
        list is how a scope says "all of them"."""
        with pytest.raises(ValueError, match='leave that list empty'):
            ad.validate_scope({'campaigns': [entry]})

    @pytest.mark.parametrize(
        'entry',
        ['All-Purpose Cleaner', 'allium seeds', 'Everyday widget', '全能刀'],
    )
    def test_names_that_start_like_all_are_names(self, entry):
        ad.validate_scope({'products': [entry]})  # must not raise

    def test_the_ratchet_behaviour_is_unchanged(self):
        """Validation is at the boundary; the narrowing rules still
        read the scope exactly as before."""
        wide = {'combos': [{'platform': 'amazon', 'country': 'US'}]}
        narrow = {
            'combos': [{'platform': 'amazon', 'country': 'US'}],
            'campaigns': ['A1234567'],
            'products': ['widget-006 manual campaign'],
        }
        ad.validate_scope(narrow)  # a clean scope still validates
        assert ad.is_narrowing(wide, narrow)
        assert ad.supersedes('audit', wide, 'audit', narrow)
        # and a refused entry never reaches the ratchet
        with pytest.raises(ValueError):
            ad.validate_scope({
                'campaigns': ['A1234567'],
                'products': ['not A7654321'],
            })
