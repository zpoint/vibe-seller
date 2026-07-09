"""Gate fixes that stopped a shallow ad-audit from passing.

An ad-audit review shipped a report that (1) falsely declared a
marketplace empty, (2) skipped per-campaign search-term drilling behind
fabricated reconciliation lines, and (3) told a zero-impression campaign
to "维持". Each hole is pinned here. (All fixtures use placeholder
products / ids.)
"""

from __future__ import annotations

import pytest

from app.ai.stop_gates import (
    ad_completeness_review as review,
    ad_zero_impression as zi,
)

pytestmark = pytest.mark.unit


def _reasons(deny):
    return deny.reason if deny else ''


class TestZeroImpressionMaintain:
    HEADER = '| 定向 | 点击 | 花费 | 建议 |\n|---|---|---|---|\n'

    def test_zero_impression_maintain_is_flagged(self):
        txt = self.HEADER + '| Loose match | 0 | 0 | 维持——零曝光30天 |\n'
        deny = zi.check(txt)
        assert deny is not None
        assert 'Loose match' in deny.reason

    def test_zero_impression_with_raise_passes(self):
        txt = (
            self.HEADER
            + '| Loose match | 0 | 0 | 提高出价至 2.00（零曝光，出价偏低） |\n'
        )
        assert zi.check(txt) is None

    def test_zero_impression_with_eligibility_action_passes(self):
        txt = (
            self.HEADER + '| Complements | 0 | 0 | 检查广告资格（疑未过审） |\n'
        )
        assert zi.check(txt) is None

    def test_collapsed_zero_filler_is_exempt(self):
        txt = (
            self.HEADER
            + '| 其余 8 个关键词 (0 展示) | 0 | 0 | 维持 — 0 展示 |\n'
        )
        assert zi.check(txt) is None

    def test_just_created_row_is_exempt(self):
        txt = self.HEADER + '| 新建关键词组 | 0 | 0 | 维持观察（今日新建） |\n'
        assert zi.check(txt) is None

    def test_row_with_traffic_and_maintain_not_flagged(self):
        # Has clicks/spend → not zero-impression → maintain is fine here.
        txt = self.HEADER + '| core kw | 12 | 20.0 | 维持 |\n'
        assert zi.check(txt) is None

    IMPR_HEADER = (
        '| 定向 | 曝光 | 点击 | 花费 | 建议 |\n|---|---|---|---|---|\n'
    )

    def test_eligibility_mention_does_not_rescue_maintain(self):
        # Action head is 维持 (0 impressions); an eligibility note in the
        # same cell must NOT rescue it (original defect: "维持——…未过审…").
        txt = (
            self.IMPR_HEADER
            + '| auto | 0 | 0 | 0 | 维持——未过审，需人工排查 |\n'
        )
        assert zi.check(txt) is not None

    def test_eligibility_as_action_passes(self):
        # Action head IS the eligibility check (no 维持) → actionable → pass.
        txt = (
            self.IMPR_HEADER
            + '| auto | 0 | 0 | 0 | 检查广告资格（疑未过审） |\n'
        )
        assert zi.check(txt) is None

    def test_impressions_present_zero_clicks_not_flagged(self):
        # CPC: impressions > 0 with 0 clicks / 0 spend is a CTR problem, not
        # a serving problem — a 维持观察 there must NOT be flagged.
        hdr = '| 定向 | 曝光 | 点击 | 花费 | 建议 |\n|---|---|---|---|---|\n'
        assert zi.check(hdr + '| kw | 500 | 0 | 0 | 维持观察 |\n') is None

    def test_zero_impression_column_flagged(self):
        hdr = '| 定向 | 曝光 | 点击 | 花费 | 建议 |\n|---|---|---|---|---|\n'
        assert zi.check(hdr + '| kw | 0 | 0 | 0 | 维持 |\n') is not None


class TestCampaignIdShapes:
    """The ad-console short id (A########) must be recognized as a
    campaign block so its search-term layer is checked (the hole that let
    every AE/SA block skip the reconciliation check)."""

    def _block(self, head):
        return (
            '## Amazon AE\n\n'
            '**进度**: drilled 1/1 active (1 total, 1 pages)\n\n'
            f'### {head}\n\n'
            '| 关键词 | 出价 | ACOS | 建议 |\n|---|---|---|---|\n'
            '| close-match | 1.50 | 20% | 维持 |\n'
        )

    def test_short_ad_console_id_is_drilled_checked(self):
        # A1234567: no 搜索词对账 line → must be flagged as missing.
        deny = review.check(self._block('A1234567 | widget 006 auto AE'))
        assert deny is not None and '搜索词' in deny.reason

    def test_long_numeric_id_still_checked(self):
        deny = review.check(self._block('100000000001 | widget SB'))
        assert deny is not None and '搜索词' in deny.reason


class TestEvasivePhrasesRejected:
    BLOCK = (
        '## Amazon SA\n\n**进度**: drilled 1/1 active (1 total, 1 pages)\n\n'
        '### A2345678 | widget-004 auto SA\n\n'
        '| 关键词 | 出价 | ACOS | 建议 |\n|---|---|---|---|\n'
        '| close-match | 1.50 | 20% | 维持 |\n'
        '搜索词对账: {claim}\n'
    )

    @pytest.mark.parametrize(
        'claim',
        [
            '自动活动，搜索词数据已在 Search Terms 页面确认',
            '数据仅 1 天，点击量不足',
            '搜索词报告需从 Search Terms 页面导出 CSV',
            '定向花费 SAR 39 / 点击 41（导出后补充对账）',
        ],
    )
    def test_evasive_reconcile_is_rejected(self, claim):
        deny = review.check(self.BLOCK.format(claim=claim))
        assert deny is not None
        # Either the defer check or the missing-search-term check must fire.
        assert ('不可推迟' in deny.reason) or ('搜索词' in deny.reason)


class TestEmptyMarketClaimNeedsScope:
    # An audit (has a 建议 table) that declares a market empty.
    AUDIT = (
        '# 广告复核\n\n'
        '## Amazon AE\n\n'
        '> AE 站三款产品均无广告投放。\n\n'
        '| 活动 | 花费 | ACOS | 建议 |\n|---|---|---|---|\n'
        '| widget-004 SA auto | 100 | 20% | 维持 |\n'
    )

    def test_empty_claim_without_scope_denied(self):
        deny = review.check(self.AUDIT, task_id=None, scope=None, track=False)
        assert deny is not None and '空市场未证实' in deny.reason

    def test_empty_claim_with_scope_not_flagged(self):
        # scope present (agent enumerated) → the empty-claim rule no longer
        # fires; a genuinely-empty market is a combo with active_ids == [].
        scope = {
            'combos': [
                {'platform': 'amazon', 'country': 'AE', 'active_ids': []}
            ]
        }
        deny = review.check(self.AUDIT, task_id=None, scope=scope, track=False)
        assert '空市场未证实' not in _reasons(deny)

    def test_non_audit_empty_mention_not_flagged(self):
        # No 建议 table and <2 campaign blocks → not an audit → the
        # empty-claim rule is skipped (a create/investigate task may
        # legitimately note "无广告活动").
        txt = '# 新建\n\n该 ASIN 目前无广告活动，已新建 1 个 SP 活动。\n'
        deny = review.check(txt, task_id=None, scope=None, track=False)
        assert '空市场未证实' not in _reasons(deny)


class TestWordLevelDrill:
    """Any ad report must drill search terms to the WORD level; a report
    that only aggregates the wasting keywords (a count / a category) with
    no per-term table is denied."""

    # is_audit via >=2 ### blocks; flags waste in aggregate; no per-term
    # search-term table.
    AGG_ONLY = (
        '# 广告复核\n\n'
        '## 二、AE\n\n'
        '### widget-006 SB 视频\n\n'
        '有花费没出单：26 次点击零转化，10 个定向词全白花，搜索词全是 A/B/C 品类词。\n\n'
        '### widget-006 SP 自动\n\n'
        '| 活动 | 总搜索词 | 浪费词数 | 浪费金额 | 主要垃圾词类型 |\n'
        '|---|---|---|---|---|\n'
        '| widget-006 SB | 32 | 30 | 58.50 | A/B/C |\n'
    )

    def test_aggregate_keywords_without_drill_denied(self):
        deny = review.check(self.AGG_ONLY, task_id=None, track=False)
        assert deny is not None and '未下钻到词' in deny.reason

    def test_per_term_table_satisfies_drill(self):
        drilled = self.AGG_ONLY + (
            '\n| 搜索词 | 点击 | 花费 | 订单 | 建议 |\n'
            '|---|---|---|---|---|\n'
            '| term a | 8 | 12.0 | 0 | 否定（零转化浪费） |\n'
            '| term b | 5 | 9.0 | 0 | 否定 |\n'
        )
        deny = review.check(drilled, task_id=None, track=False)
        assert '未下钻到词' not in _reasons(deny)

    def test_problem_cell_mentioning_keyword_is_not_a_drill_table(self):
        # A data row whose problem cell mentions 关键词/否定 must NOT count
        # as a per-term drill table (the header-only detection guard).
        txt = (
            '# r\n\n## 二、AE\n\n### widget SB\n\n### widget SP\n\n'
            '浪费词数 30。\n\n'
            '| 活动 | 花费 | 建议 |\n|---|---|---|\n'
            '| widget SB | 58.5 | 🔴 关键词含无关词，建议否定 |\n'
        )
        deny = review.check(txt, task_id=None, track=False)
        assert deny is not None and '未下钻到词' in deny.reason

    def test_clean_report_no_waste_language_not_flagged(self):
        txt = (
            '# r\n\n## 二、AE\n\n### widget auto\n\n'
            '| 定向 | 点击 | 花费 | 订单 | 建议 |\n|---|---|---|---|---|\n'
            '| close-match | 40 | 60 | 5 | 维持，ROAS 健康 |\n'
        )
        assert '未下钻到词' not in _reasons(
            review.check(txt, task_id=None, track=False)
        )
