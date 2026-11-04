"""An English ad report must be judged exactly like its Chinese twin.

The words an ad report writes itself (action verbs, column headers, rule
names) follow the language of the user's request. The gates used to parse
Chinese-only literals, which is why the spec forced Chinese on every
report and an English report leaked Chinese into its action table. These
tests pin the single bilingual vocabulary (``app/ai/ad_action_vocab``)
and that every gate reads both spellings the same way.
"""

from datetime import date
from pathlib import Path
import re

import pytest

from app.ai import (
    ad_action_vocab as vocab,
    ad_execution_targets as exec_targets,
    ad_negation_allowlist as allowlist,
)
from app.ai.ad_action_vocab import Col, Kind
from app.ai.stop_gates import (
    ad_bid_floor,
    ad_change_cooldown as cooldown,
    ad_completeness_campaign_blocks as blocks,
    ad_explicit_actions,
    ad_scale_winners,
    result_language,
)
from app.ai.stop_gates.ad_completeness_rules import count_drill_tables
from app.ai.stop_gates.ad_declaration_checks import actionable_rows
from app.ai.stop_gates.ad_moves import MOVE_RE

ZH_TGT_HEAD = (
    '| 定向词 | 广告组 | 匹配 | 出价 | 点击 | 花费 | 订单 | 销售额 '
    '| ACOS | ROAS | 实际 CPC | 建议 |\n'
    '|---|---|---|---|---|---|---|---|---|---|---|---|\n'
)
EN_TGT_HEAD = (
    '| Keyword | Ad group | Match | Bid | Clicks | Spend | Orders | Sales '
    '| ACOS | ROAS | Actual CPC | Recommendation |\n'
    '|---|---|---|---|---|---|---|---|---|---|---|---|\n'
)
ZH_ST_HEAD = (
    '| 搜索词 | 广告组 | 来源关键词 | 匹配 | 点击 | 花费 | 订单 | 销售额 '
    '| ROAS | 建议 |\n|---|---|---|---|---|---|---|---|---|---|\n'
)
EN_ST_HEAD = (
    '| Search term | Ad group | Source keyword | Match | Clicks | Spend '
    '| Orders | Sales | ROAS | Recommendation |\n'
    '|---|---|---|---|---|---|---|---|---|---|\n'
)


def _tgt(rec, *, orders=4, spend='80.00', clicks=40, bid='2.10', cpc='1.00'):
    sales = '400.00' if orders else '0.00'
    roas = '5.00' if orders else '0.00'
    return (
        f'| widget-006 | grp | Exact | {bid} | {clicks} | {spend} '
        f'| {orders} | {sales} | 20% | {roas} | {cpc} | {rec} |\n'
    )


def _st(rec, *, orders=0, spend='30.00', clicks=20, src='widget-006'):
    return (
        f'| red widget | grp | {src} | Broad | {clicks} | {spend} '
        f'| {orders} | 0.00 | 0.00 | {rec} |\n'
    )


def _counts(deny):
    """Per-bucket row counts, the language-independent part of a denial."""
    return [] if deny is None else re.findall(r'(\d+) 行', deny.reason)


# (zh recommendation, en recommendation) twins.
TARGETING_TWINS = [
    (
        '提高至 1.00（ROAS 9>5 加投赢家规则）',
        'Raise to 1.00 (ROAS 9>5 winner rule)',
    ),
    ('提高出价', 'Raise bid'),
    ('下调至 1.50（ACOS 41%>30 规则）', 'Lower to 1.50 (ACOS 41%>30 rule)'),
    ('下调 10%（ACOS 41%>30 规则）', 'Lower by 10% (ACOS 41%>30 rule)'),
    ('维持', 'Keep'),
    ('暂停', 'Pause'),
    ('否定关键词（精准）', 'Negative keyword (exact)'),
]


@pytest.mark.unit
class TestVocabulary:
    @pytest.mark.parametrize('kind', list(Kind))
    def test_every_kind_has_both_languages(self, kind):
        sp = vocab.SPELLINGS[kind]
        assert sp.zh and sp.en
        assert vocab.canonical(kind, 'zh') == sp.zh[0]
        assert vocab.canonical(kind, 'en') == sp.en[0]
        assert ' / ' in vocab.both(kind)

    @pytest.mark.parametrize('kind', list(Kind))
    def test_a_spelling_in_either_language_matches_its_kind(self, kind):
        rx = vocab.pattern(kind)
        for s in (*vocab.SPELLINGS[kind].zh, *vocab.SPELLINGS[kind].en):
            assert rx.search(s), s
            assert rx.search(s.lower()), s

    @pytest.mark.parametrize(
        'zh,en',
        [
            ('提高至 1.20', 'Raise to 1.20'),
            ('下调至 0.80', 'Lower to 0.80'),
            ('暂停', 'Pause'),
            ('否定关键词（精准）', 'Negative keyword (exact)'),
            ('降低出价', 'Lower bid'),
            ('加投', 'Bid up'),
        ],
    )
    def test_moves_are_recognised_in_both_languages(self, zh, en):
        assert MOVE_RE.search(zh)
        assert MOVE_RE.search(en)

    @pytest.mark.parametrize('rec', ['维持', 'Keep', 'Hold', 'Paused'])
    def test_holds_and_status_words_are_not_moves(self, rec):
        assert not MOVE_RE.search(rec)

    def test_column_headers_resolve_in_both_languages(self):
        for head in (ZH_TGT_HEAD, EN_TGT_HEAD):
            cells = [
                c.strip() for c in head.splitlines()[0].strip('|').split('|')
            ]
            assert vocab.has_rec_col(cells)
            assert vocab.find_col(cells, Col.ORDERS) == 6
            assert vocab.find_col(cells, Col.SPEND) == 5
            assert vocab.find_col(cells, Col.BID) == 3  # not the rec col
            assert vocab.find_col(cells, Col.CPC) == 10
            assert vocab.find_col(cells, Col.REC) == 11
        for head in (ZH_ST_HEAD, EN_ST_HEAD):
            cells = [
                c.strip() for c in head.splitlines()[0].strip('|').split('|')
            ]
            assert vocab.is_search_term_head(cells[0])
            assert vocab.find_col(cells, Col.SOURCE) == 2

    def test_suggested_bid_is_not_the_current_bid_column(self):
        cells = ['Keyword', 'Bid', 'Suggested bid', 'Recommendation']
        assert vocab.find_col(cells, Col.BID) == 1

    def test_header_sets_exist_for_both_languages(self):
        for lang in ('zh', 'en'):
            assert vocab.HEADERS[lang]['targeting'][-1] in (
                '建议',
                'Recommendation',
            )


@pytest.mark.unit
class TestExplicitActionsParity:
    @pytest.mark.parametrize('zh,en', TARGETING_TWINS)
    def test_targeting_row_same_verdict(self, zh, en):
        a = ad_explicit_actions.check(ZH_TGT_HEAD + _tgt(zh))
        b = ad_explicit_actions.check(EN_TGT_HEAD + _tgt(en))
        assert (a is None) == (b is None), (zh, en)
        assert _counts(a) == _counts(b)

    def test_bare_raise_is_denied_in_english_too(self):
        deny = ad_explicit_actions.check(EN_TGT_HEAD + _tgt('Raise bid'))
        assert deny is not None
        assert 'Raise to' in deny.reason  # dictates English wording too

    @pytest.mark.parametrize(
        'zh,en',
        [
            ('提高出价 25%', 'Raise bid by 25%'),
            ('加投 25%', 'Increase bid by 25%'),
        ],
    )
    def test_bid_verb_on_search_term_row_same_verdict(self, zh, en):
        a = ad_explicit_actions.check(ZH_ST_HEAD + _st(zh, orders=2))
        b = ad_explicit_actions.check(EN_ST_HEAD + _st(en, orders=2))
        assert a is not None and b is not None
        assert _counts(a) == _counts(b)

    def test_zero_order_waste_hold_same_verdict(self):
        a = ad_explicit_actions.check(ZH_ST_HEAD + _st('维持'))
        b = ad_explicit_actions.check(EN_ST_HEAD + _st('Keep'))
        assert a is not None and b is not None
        assert _counts(a) == _counts(b)

    def test_zero_order_waste_negate_passes_in_both(self):
        assert (
            ad_explicit_actions.check(ZH_ST_HEAD + _st('否定关键词（精准）'))
            is None
        )
        assert (
            ad_explicit_actions.check(
                EN_ST_HEAD + _st('Negative keyword (exact)')
            )
            is None
        )

    def test_zero_order_waste_keyword_hold_same_verdict(self):
        a = ad_explicit_actions.check(ZH_TGT_HEAD + _tgt('维持', orders=0))
        b = ad_explicit_actions.check(EN_TGT_HEAD + _tgt('Keep', orders=0))
        assert a is not None and b is not None
        assert _counts(a) == _counts(b)

    def test_trim_below_cpc_floor_same_verdict(self):
        a = ad_explicit_actions.check(
            ZH_TGT_HEAD + _tgt('下调至 0.50（ACOS 41%>30 规则）', cpc='1.00')
        )
        b = ad_explicit_actions.check(
            EN_TGT_HEAD + _tgt('Lower to 0.50 (ACOS 41%>30 rule)', cpc='1.00')
        )
        assert a is not None and b is not None
        assert _counts(a) == _counts(b)

    def test_percentage_trim_floor_same_verdict(self):
        a = ad_explicit_actions.check(
            ZH_TGT_HEAD
            + _tgt('下调 25%（ACOS 41%>30 规则）', bid='1.20', cpc='1.00')
        )
        b = ad_explicit_actions.check(
            EN_TGT_HEAD
            + _tgt('Lower by 25% (ACOS 41%>30 rule)', bid='1.20', cpc='1.00')
        )
        assert a is not None and b is not None

    def test_lower_to_above_current_bid_is_a_mislabelled_raise(self):
        a = ad_explicit_actions.check(
            ZH_TGT_HEAD + _tgt('下调至 2.50（ACOS 41%>30 规则）', bid='2.10')
        )
        b = ad_explicit_actions.check(
            EN_TGT_HEAD + _tgt('Lower to 2.50 (ACOS 41%>30 rule)', bid='2.10')
        )
        assert a is not None and b is not None
        assert _counts(a) == _counts(b)

    def test_negating_a_search_term_on_a_keyword_row(self):
        a = ad_explicit_actions.check(ZH_TGT_HEAD + _tgt('否定搜索词'))
        b = ad_explicit_actions.check(EN_TGT_HEAD + _tgt('Negate search term'))
        assert a is not None and b is not None
        assert _counts(a) == _counts(b)

    def test_buried_pause_behind_a_hold_head(self):
        a = ad_explicit_actions.check(ZH_TGT_HEAD + _tgt('维持（建议暂停）'))
        b = ad_explicit_actions.check(
            EN_TGT_HEAD + _tgt('Keep (suggest pausing)')
        )
        assert a is not None and b is not None
        assert _counts(a) == _counts(b)

    def test_harvest_needs_a_suggested_bid_in_both(self):
        a = ad_explicit_actions.check(
            ZH_ST_HEAD + _st('添加为关键词（精准）', orders=3, src='other')
        )
        b = ad_explicit_actions.check(
            EN_ST_HEAD + _st('Add as keyword (exact)', orders=3, src='other')
        )
        assert a is not None and b is not None
        ok_a = ad_explicit_actions.check(
            ZH_ST_HEAD
            + _st('添加为关键词（精准，建议出价 0.95）', orders=3, src='other')
        )
        ok_b = ad_explicit_actions.check(
            EN_ST_HEAD
            + _st('Add as keyword (exact, bid 0.95)', orders=3, src='other')
        )
        assert ok_a is None and ok_b is None

    def test_clean_report_passes_in_both(self):
        zh = ZH_TGT_HEAD + _tgt('提高至 2.40（ROAS 5>3.33 加投赢家规则）')
        en = EN_TGT_HEAD + _tgt('Raise to 2.40 (ROAS 5>3.33 winner rule)')
        assert ad_explicit_actions.check(zh) is None
        assert ad_explicit_actions.check(en) is None

    def test_placement_rows_reject_keyword_only_verbs_in_both(self):
        zh = ZH_ST_HEAD.replace('Broad', 'Broad') + (
            '| B0EXAMPLE1 | grp | cat-1 | Category | 5 | 12.00 | 0 | 0.00 '
            '| 0.00 | 否定关键词（精准） |\n'
        )
        en = EN_ST_HEAD + (
            '| B0EXAMPLE1 | grp | cat-1 | Category | 5 | 12.00 | 0 | 0.00 '
            '| 0.00 | Negative keyword (exact) |\n'
        )
        assert ad_explicit_actions.check(zh) is not None
        assert ad_explicit_actions.check(en) is not None


@pytest.mark.unit
class TestBidRuleGatesParity:
    def _amz(self, head_kw, head_rec, rec, roas='4.48'):
        return (
            f'| {head_kw} | State | Clicks | Spend | Orders | Sales | ACOS '
            f'| ROAS | Bid | Actual CPC | {head_rec} |\n'
            '|---|---|---|---|---|---|---|---|---|---|---|\n'
            '| widget-006 (Exact) | Delivering | 269 | 555 | 60 | 2486 '
            f'| 22.3% | {roas} | 2.74 | 2.06 | {rec} |\n'
        )

    def test_bid_floor_denies_a_trim_on_low_acos_in_both(self):
        zh = self._amz('关键词', '建议', '下调至 2.06')
        en = self._amz('Keyword', 'Recommendation', 'Lower to 2.06')
        assert ad_bid_floor.check(zh) is not None
        assert ad_bid_floor.check(en) is not None

    def test_bid_floor_allows_a_keep_that_explains_itself(self):
        zh = self._amz('关键词', '建议', '维持（ACOS 22%，不可下调）')
        en = self._amz(
            'Keyword', 'Recommendation', 'Keep (ACOS 22%, do not lower)'
        )
        assert ad_bid_floor.check(zh) is None
        assert ad_bid_floor.check(en) is None

    def test_scale_winners_flags_a_bare_hold_in_both(self):
        zh = self._amz('关键词', '建议', '维持', roas='24.17')
        en = self._amz('Keyword', 'Recommendation', 'Keep', roas='24.17')
        assert ad_scale_winners.check(zh) is not None
        assert ad_scale_winners.check(en) is not None

    def test_scale_winners_accepts_a_raise_in_both(self):
        zh = self._amz('关键词', '建议', '提高至 3.00（ROAS 4.48 规则）')
        en = self._amz(
            'Keyword', 'Recommendation', 'Raise to 3.00 (ROAS 4.48 rule)'
        )
        assert ad_scale_winners.check(zh) is None
        assert ad_scale_winners.check(en) is None


@pytest.mark.unit
class TestNegationAndExecutionTargetsParity:
    ZH = (
        '### 100000000001 | camp | Manual\n'
        '| 搜索词 | 匹配 | 建议 |\n|---|---|---|\n'
        '| red widget | Broad | **否定关键词（精准）** |\n'
        '| blue widget | Broad | **否定关键词（精准）** |\n'
        '| blue widget | Broad | **维持** |\n'
    )
    EN = (
        '### 100000000001 | camp | Manual\n'
        '| Search term | Match | Recommendation |\n|---|---|---|\n'
        '| red widget | Broad | **Negative keyword (exact)** |\n'
        '| blue widget | Broad | **Negative keyword (exact)** |\n'
        '| blue widget | Broad | **Keep** |\n'
    )

    def test_allowlist_identical(self):
        a = allowlist.build_allowlist(self.ZH)
        b = allowlist.build_allowlist(self.EN)
        assert a == b == {'100000000001': {'red widget'}}

    def test_contradictions_identical(self):
        a = allowlist.find_negation_contradictions(self.ZH)
        b = allowlist.find_negation_contradictions(self.EN)
        assert a == b == {'100000000001': {'blue widget'}}

    def test_header_cells_not_read_as_terms(self):
        assert allowlist._is_header_cell('search term')
        assert allowlist._is_header_cell('搜索词')

    def test_bid_and_pause_targets_identical(self):
        zh = (
            '### 100000000001 | camp | Manual\n'
            '| 定向词 | 匹配 | 出价 | 建议 |\n|---|---|---|---|\n'
            '| alpha | Exact | 1.00 | 提高至 1.20 |\n'
            '| beta | Exact | 1.00 | 下调至 0.80 |\n'
            '| gamma | Exact | 1.00 | 暂停 |\n'
            '| delta | Exact | 1.00 | 维持 |\n'
        )
        en = (
            '### 100000000001 | camp | Manual\n'
            '| Keyword | Match | Bid | Recommendation |\n|---|---|---|---|\n'
            '| alpha | Exact | 1.00 | Raise to 1.20 |\n'
            '| beta | Exact | 1.00 | Lower to 0.80 |\n'
            '| gamma | Exact | 1.00 | Pause |\n'
            '| delta | Exact | 1.00 | Keep |\n'
        )
        a = exec_targets.build_bid_pause_targets(zh)
        b = exec_targets.build_bid_pause_targets(en)
        assert a == b
        assert a['bids']['100000000001'] == {
            ('alpha', 'exact'): 1.2,
            ('beta', 'exact'): 0.8,
        }
        assert a['bid_dirs']['100000000001'][('beta', 'exact')] == 'down'
        assert a['pauses']['100000000001'] == {('gamma', 'exact')}


@pytest.mark.unit
class TestCooldownParity:
    TODAY = date(2026, 8, 10)
    HEADER = (
        'ad_group\ttarget\tmatch_type\tstate\tbid\tcurrency\tclicks\tspend\t'
        'orders\tsales\tacos\troas\tsuggestion\tapplied_action\tapplied_at\t'
        'previous_bid\n'
    )

    def _root(self, tmp_path: Path) -> Path:
        d = tmp_path / 'amazon' / 'SA'
        d.mkdir(parents=True)
        (d / 'c1.tsv').write_text(
            self.HEADER
            + 'grp\twidget red\tExact\tenabled\t2.10\tSAR\t40\t80.00\t4\t'
            '400.00\t20%\t5.00\tKeep\tRaise bid\t2026-08-08\t2.00\n',
            encoding='utf-8',
        )
        return tmp_path

    def _report(self, head, rec):
        return (
            '# Ad audit\n\n### c1 | camp | Manual\n'
            + head
            + f'| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | {rec} |\n'
        )

    ZH_HEAD = (
        '| 定向词 | 匹配 | 出价 | 点击 | 花费 | 订单 | ROAS | 建议 |\n'
        '|---|---|---|---|---|---|---|---|\n'
    )
    EN_HEAD = (
        '| Keyword | Match | Bid | Clicks | Spend | Orders | ROAS '
        '| Recommendation |\n|---|---|---|---|---|---|---|---|\n'
    )

    @pytest.mark.parametrize(
        'zh,en,denied',
        [
            ('下调至 1.80', 'Lower to 1.80', True),
            ('提高至 2.40', 'Raise to 2.40', True),
            ('暂停', 'Pause', True),
            ('维持', 'Keep', False),
            (
                '维持（2 天前刚提过价，冷却期未满）',
                'Keep (raised 2 days ago, cooldown not over)',
                False,
            ),
        ],
    )
    def test_same_decision(self, tmp_path, zh, en, denied):
        root = self._root(tmp_path)
        a = cooldown.check(
            self._report(self.ZH_HEAD, zh), ads_root=root, today=self.TODAY
        )
        b = cooldown.check(
            self._report(self.EN_HEAD, en), ads_root=root, today=self.TODAY
        )
        assert (a is not None) is denied
        assert (b is not None) is denied
        if denied:
            assert len(a.gaps) == len(b.gaps)


@pytest.mark.unit
class TestDrillTableRecognition:
    def test_english_targeting_table_counts_as_a_drill_table(self):
        block = (
            '### c1 | camp | Manual\n'
            + EN_TGT_HEAD
            + _tgt('Keep')
            + _tgt('Pause')
        )
        zh_block = (
            '### c1 | camp | Manual\n'
            + ZH_TGT_HEAD
            + _tgt('维持')
            + _tgt('暂停')
        )
        assert blocks._layer_row_count(block, searchterm=False) == 2
        assert blocks._layer_row_count(zh_block, searchterm=False) == 2

    def test_english_search_term_table_is_the_search_layer(self):
        block = '### c1 | camp | Manual\n' + EN_ST_HEAD + _st('Keep')
        assert blocks._layer_row_count(block, searchterm=True) == 1
        assert blocks._layer_row_count(block, searchterm=False) == 0

    def test_english_total_row_is_not_a_target_row(self):
        block = (
            '### c1 | camp | Manual\n'
            + EN_TGT_HEAD
            + '| Total | | | | 40 | 80.00 | 4 | 400.00 | | 5.00 | | Keep |\n'
        )
        assert blocks._layer_row_count(block, searchterm=False) == 0


EN_TITLE = 'Review acme ads for the last 30 days'
ZH_TITLE = '复盘 acme 最近 30 天的广告'

_EN_PROSE = (
    '# Ad Optimization Recommendations\n\n'
    'This review covers the last thirty days of sponsored products spend '
    'across the store, and the actions below follow the bid rules.\n\n'
)


@pytest.mark.unit
class TestLanguageGate:
    def test_chinese_verbs_in_an_english_action_column_are_flagged(self):
        report = _EN_PROSE + EN_TGT_HEAD + _tgt('暂停 + 否定关键词（精准）')
        deny = result_language.check(report, EN_TITLE, None)
        assert deny is not None
        assert deny.gate == 'result_language'
        assert '暂停' in deny.reason
        assert 'Raise to' in deny.reason  # tells the agent the spellings

    def test_chinese_inside_the_recommendation_cell_after_an_english_verb(
        self,
    ):
        report = (
            _EN_PROSE
            + EN_TGT_HEAD
            + _tgt('Raise to 0.35 (+16.7%，≤ 25% 单次上限)')
        )
        assert result_language.check(report, EN_TITLE, None) is not None

    def test_chinese_column_headers_in_an_english_report_are_flagged(self):
        report = _EN_PROSE + ZH_TGT_HEAD + _tgt('Pause')
        assert result_language.check(report, EN_TITLE, None) is not None

    def test_our_chinese_terms_in_english_prose_are_flagged(self):
        report = (
            _EN_PROSE
            + 'The bid is held at the 地板 0.79 because actual CPC is 0.714 '
            'and the rule exempts the step cap here.\n\n'
            + EN_TGT_HEAD
            + _tgt('Keep')
        )
        assert result_language.check(report, EN_TITLE, None) is not None

    def test_a_clean_english_report_passes(self):
        report = (
            _EN_PROSE
            + EN_TGT_HEAD
            + _tgt('Raise to 2.40 (ROAS 5>3.33 winner rule)')
            + _tgt('Pause')
        )
        assert result_language.check(report, EN_TITLE, None) is None

    def test_quoted_platform_text_is_exempt(self):
        report = (
            _EN_PROSE
            + EN_TGT_HEAD
            + _tgt('Keep — console shows "已暂停" for the sibling target')
        )
        assert result_language.check(report, EN_TITLE, None) is None

    def test_chinese_search_terms_in_data_cells_are_exempt(self):
        report = (
            _EN_PROSE
            + EN_ST_HEAD.replace('---', '---')
            + (
                '| 手机壳 | grp | widget-006 | Broad | 5 | 12.00 | 0 | 0.00 '
                '| 0.00 | Negative keyword (exact) |\n'
            )
        )
        assert result_language.check(report, EN_TITLE, None) is None

    def test_a_non_ad_english_result_may_discuss_chinese_text(self):
        text = (
            'The page labels the button 暂停 and the summary reads 建议 on '
            'the settings screen, which I translated for you in the notes '
            'below so you can follow the steps without reading Chinese.\n'
        )
        assert result_language.check(text, EN_TITLE, None) is None

    def test_a_chinese_report_with_english_ui_labels_still_passes(self):
        report = (
            '# 广告优化建议\n\n本次复盘覆盖最近三十天的广告花费，下面的动作'
            '均按出价规则给出，控制台显示 Delivering 的目标保持不动。\n\n'
            + ZH_TGT_HEAD
            + _tgt('提高至 2.40（ROAS 5>3.33 加投赢家规则）')
            + _tgt('维持（控制台状态 Delivering）')
        )
        assert result_language.check(report, ZH_TITLE, None) is None


@pytest.mark.unit
class TestReviewRegressions:
    """Each case is one review finding on the shared vocabulary."""

    def test_negative_search_term_is_a_decision(self):

        for rec in ('否定搜索词', 'Negative search term'):
            table = f'| Search term | Recommendation |\n|---|---|\n| red widget | {rec} |\n'
            assert actionable_rows([table]) == 1, rec

    @pytest.mark.parametrize('label', ['Negative exact', '否定精准'])
    def test_inventory_of_existing_negatives_decides_nothing(self, label):

        table = (
            '| Keyword | Match | Status |\n|---|---|---|\n'
            f'| red widget | {label} | Enabled |\n'
        )
        assert actionable_rows([table]) == 0
        decided = (
            '| Keyword | Match | Recommendation |\n|---|---|---|\n'
            f'| red widget | Broad | {label} |\n'
        )
        assert actionable_rows([decided]) == 1

    def test_percentage_cut_on_low_acos_is_denied_in_both(self):
        t = TestBidRuleGatesParity()
        zh = t._amz('关键词', '建议', '下调 10%')
        en = t._amz('Keyword', 'Recommendation', 'Lower by 10%')
        assert ad_bid_floor.check(zh) is not None
        assert ad_bid_floor.check(en) is not None

    @pytest.mark.parametrize(
        'rec',
        [
            '维持（不拓词，来源词承接）',
            'Keep (do not harvest; carried by source keyword)',
        ],
    )
    def test_a_hold_that_declines_harvest_is_not_a_harvest(self, rec):
        assert not vocab.EXTRACT_RE.search(rec)
        assert vocab.EXTRACT_RE.search('拓词（精准，出价 0.95）')
        assert vocab.EXTRACT_RE.search('Harvest as keyword (exact, bid 0.95)')

    def test_placement_rows_reject_harvest(self):
        en = EN_ST_HEAD + (
            '| B0EXAMPLE1 | grp | cat-1 | Category | 5 | 12.00 | 2 | 40.00 '
            '| 3.33 | Harvest as keyword (exact, bid 0.95) |\n'
        )
        assert ad_explicit_actions.check(en) is not None

    def test_drill_tables_with_an_action_header_count(self):

        zh = '| 定向词 | 建议 |\n|---|---|\n| widget-006 | 维持 |\n'
        en = '| Keyword | Action |\n|---|---|\n| widget-006 | Keep |\n'
        assert count_drill_tables(zh) == count_drill_tables(en) == 1

    def test_keep_inside_a_keyword_is_not_a_skip(self):
        row = '| 100000000001 | widget keeper | Pause | ✅ |'
        assert not exec_targets._SKIP_MARK_EN.search(row)
        assert exec_targets._SKIP_MARK_EN.search('| x | Keep | skipped |')

    def test_title_cased_action_cell_is_not_the_term(self):
        log = (
            '| 100000000001 | Negate search term | red widget '
            '| Negative exact | ✅ |\n'
        )
        assert allowlist.extract_executed_negations(log) == [
            ('100000000001', 'red widget')
        ]

    @pytest.mark.parametrize(
        'label', ['Negative keyword (exact)', '否定关键词（精准）']
    )
    def test_canonical_negation_label_is_an_execution(self, label):
        log = f'| 100000000001 | red widget | {label} | ✅ |\n'
        assert allowlist.extract_executed_negations(log) == [
            ('100000000001', 'red widget')
        ]
