"""A campaign's spend is written three times and must agree everywhere.

Combo table row, the campaign's own drill block, and the per-combo row of
the 汇总 table are one number in three places. The agent kept updating a
drill block after a re-capture and leaving the table and rollup on the
old figure — the LLM reviewer caught it on two consecutive rounds of one
live run and named the second 「与 iter-3 镜像的同构缺陷」. Nothing about
that needs a model to decide, so it is pinned here.
"""

import pytest

from app.ai.stop_gates import ad_completeness_review as acr

# The private helpers ARE the unit under test here: the footer/header
# pairing and the per-metric column lookup are exactly where the bugs were,
# and asserting on them through check_rollups alone would not have located
# either one.
from app.ai.stop_gates.ad_rollup import (
    _footer_extra_metrics,  # noqa: PLC2701
    _total_row_spend,  # noqa: PLC2701
    check_rollups,
)

# Two campaigns, both internally consistent, with a matching rollup.
_CONSISTENT = """## noon AE

| id | name | type | spend | sales | orders | ACOS | ROAS |
|---|---|---|---|---|---|---|---|
| C_AAA11111 | widget auto | Auto | AED 90.00 | AED 300.00 | 9 | 30.00% | 3.33 |
| C_BBB22222 | widget manual | Manual | AED 30.00 | AED 100.00 | 4 | 30.00% | 3.33 |

### C_AAA11111 | widget auto | Auto

搜索词对账: 定向花费 AED 90.00 / 点击 100 = 搜索词花费 AED 90.00 / 点击 100 (✓)

### C_BBB22222 | widget manual | Manual

搜索词对账: 定向花费 AED 30.00 / 点击 40 = 搜索词花费 AED 30.00 / 点击 40 (✓)

## 汇总建议

| 平台 | 国家 | 活跃活动数 | 总花费 | 总销售额 | 总订单 | ROAS |
|---|---|---|---|---|---|---|
| noon | AE | 2 | AED 120.00 | AED 400.00 | 13 | 3.33 |
"""


@pytest.mark.unit
class TestRollupConsistency:
    def test_consistent_report_is_clean(self):
        assert check_rollups(_CONSISTENT) == []

    def test_stale_combo_row_after_drill_update(self):
        # The live iter-4 defect: drill re-captured to 90.00, table left
        # at 85.00. The rollup moves WITH the stale table, so only the
        # row/drill check fires — and its message must still tell the
        # agent to carry the correction into the 汇总 row.
        bad = _CONSISTENT.replace(
            'AED 90.00 | AED 300.00', 'AED 85.00 | AED 300.00'
        )
        bad = bad.replace('AED 120.00', 'AED 115.00')
        gaps = check_rollups(bad)
        assert len(gaps) == 1
        assert '[汇总一致]' in gaps[0]
        assert 'C_AAA11111' in gaps[0]
        assert '85.00' in gaps[0] and '90.00' in gaps[0]
        assert '汇总' in gaps[0]

    def test_stale_rollup_row(self):
        # Table corrected, rollup forgotten — the other half of the
        # live defect, and independently detectable.
        bad = _CONSISTENT.replace('AED 120.00', 'AED 115.00')
        gaps = check_rollups(bad)
        assert len(gaps) == 1
        assert '汇总表写的总花费' in gaps[0]

    def test_both_halves_stale_reports_both(self):
        bad = _CONSISTENT.replace(
            'AED 90.00 | AED 300.00', 'AED 85.00 | AED 300.00'
        )
        gaps = check_rollups(bad)
        assert len(gaps) == 2

    def test_placeholder_spend_is_not_zero(self):
        """A ``—`` row means "no figure", and must not enter the sum.

        Read as 0.0 it would still sum correctly here, but it would make
        a zero-spend row indistinguishable from a genuinely 0.00 one and
        drag any rollup that legitimately omits placeholders.
        """
        with_placeholder = _CONSISTENT.replace(
            '| C_BBB22222 | widget manual | Manual | AED 30.00',
            '| C_ZZZ99999 | zero spend | — | — | — | — | — | — |\n'
            '| C_BBB22222 | widget manual | Manual | AED 30.00',
        )
        assert check_rollups(with_placeholder) == []

    def test_grand_total_row_is_not_a_campaign(self):
        with_total = _CONSISTENT.replace(
            '| C_BBB22222 | widget manual | Manual | AED 30.00 | AED 100.00 | 4 | 30.00% | 3.33 |',
            '| C_BBB22222 | widget manual | Manual | AED 30.00 | AED 100.00 | 4 | 30.00% | 3.33 |\n'
            '| **总计** | — | — | AED 120.00 | AED 462.17 | 13 | 3.77 |',
        )
        assert check_rollups(with_total) == []

    def test_rounding_is_tolerated(self):
        # 2-decimal display rounding must not read as a stale copy.
        near = _CONSISTENT.replace('AED 120.00', 'AED 120.01')
        assert check_rollups(near) == []

    def test_missing_recon_line_skips_row_check(self):
        # No 对账 line means no second copy of the number to compare.
        no_recon = _CONSISTENT.replace(
            '搜索词对账: 定向花费 AED 90.00 / 点击 100 = '
            '搜索词花费 AED 90.00 / 点击 100 (✓)',
            '(搜索词报告不可用)',
        )
        assert check_rollups(no_recon) == []

    def test_currency_prefixes_and_thousands(self):
        au = """## Amazon AU

| id | name | type | spend | sales | orders | ACOS | ROAS |
|---|---|---|---|---|---|---|---|
| A0000001 | w | Auto | A$1,000.00 | A$3,000.00 | 22 | 29.09% | 3.44 |

### A0000001 | w | Auto

搜索词对账: 定向花费 A$1,000.00 / 点击 50 = 搜索词花费 A$1,000.00 / 点击 50 (✓)

## 汇总建议

| 平台 | 国家 | 活跃活动数 | 总花费 | 总销售额 | 总订单 | ROAS |
|---|---|---|---|---|---|---|
| amazon | AU | 1 | A$1,000.00 | A$3,000.00 | 22 | 3.44 |
"""
        assert check_rollups(au) == []
        assert (
            len(
                check_rollups(
                    au.replace(
                        '| amazon | AU | 1 | A$1,000.00',
                        '| amazon | AU | 1 | A$900.00',
                    )
                )
            )
            == 1
        )

    def test_no_combo_section_is_not_a_gap(self):
        assert check_rollups('just some prose about ads') == []
        assert check_rollups('') == []


_WITH_FOOTER = """## noon AE

| id | name | type | spend | sales | orders | ACOS | ROAS |
|---|---|---|---|---|---|---|---|
| C_AAA11111 | widget auto | Auto | AED 90.00 | AED 300.00 | 9 | 30.00% | 3.33 |

### C_AAA11111 | widget auto | Auto

| 关键词/定向 | 匹配 | 出价 (AED) | 点击 | 花费 (AED) | ROAS | 建议 |
|---|---|---|---|---|---|---|
| widget | Broad | 1.00 | 60 | 60.00 | 3.0 | 维持 |
| widget large | Broad | 1.00 | 40 | 30.00 | 3.0 | 维持 |
| **合计** | — | — | 100 | 90.00 | 3.33 | — |
搜索词对账: 定向花费 AED 90.00 / 点击 100 = 搜索词花费 AED 90.00 / 点击 100 (✓)

## 汇总建议

| 平台 | 国家 | 活跃活动数 | 总花费 | 总销售额 | 总订单 | ROAS |
|---|---|---|---|---|---|---|
| noon | AE | 1 | AED 90.00 | AED 300.00 | 9 | 3.33 |
"""


@pytest.mark.unit
class TestBlockSelfContradiction:
    """The 合计 footer and the 对账 line state the same number.

    Both live INSIDE one drill block, two lines apart, so a mismatch is
    the block contradicting itself — and the table-vs-drill check cannot
    see it, because that compares the combo table against the
    reconciliation line and those two can agree while the footer is
    stale. Live: four campaigns whose reconciliation had been corrected
    while their footer still held the old figure — four separate blocks,
    each disagreeing with itself by a wide margin.
    """

    def test_consistent_block_is_clean(self):
        assert [g for g in check_rollups(_WITH_FOOTER) if '前后矛盾' in g] == []

    def test_stale_footer_is_caught(self):
        bad = _WITH_FOOTER.replace(
            '| **合计** | — | — | 100 | 90.00 | 3.33 | — |',
            '| **合计** | — | — | 100 | 60.00 | 3.33 | — |',
        )
        gaps = [g for g in check_rollups(bad) if '前后矛盾' in g]
        assert len(gaps) == 1
        assert '60.00' in gaps[0] and '90.00' in gaps[0]

    def test_currency_suffixed_header_is_still_found(self):
        """`花费 (AED)` must resolve, or the check is a silent no-op.

        An exact header match found nothing here, so the check returned
        None on a block that plainly disagreed with itself — the failure
        was invisible rather than loud.
        """
        bad = _WITH_FOOTER.replace(
            '| **合计** | — | — | 100 | 90.00 | 3.33 | — |',
            '| **合计** | — | — | 100 | 60.00 | 3.33 | — |',
        )
        assert '花费 (AED)' in bad  # the suffixed header
        assert [g for g in check_rollups(bad) if '前后矛盾' in g]

    def test_block_without_a_footer_is_not_a_gap(self):
        no_footer = _WITH_FOOTER.replace(
            '| **合计** | — | — | 100 | 90.00 | 3.33 | — |\n', ''
        )
        assert [g for g in check_rollups(no_footer) if '前后矛盾' in g] == []

    def test_rounding_is_tolerated(self):
        near = _WITH_FOOTER.replace('100 | 90.00 | 3.33', '100 | 90.01 | 3.33')
        assert [g for g in check_rollups(near) if '前后矛盾' in g] == []


@pytest.mark.unit
class TestReportOpensAsAReport:
    """The deliverable must not open with the REVIEW file's verdict line.

    `Status: ok | gaps | incomplete` is the format of
    `REVIEW_<date>_iterN.md`, which the reviewer gate reads. Live, an audit
    shipped with `Status: gaps` as its literal first line, above the H1 —
    so the report a user opens led with an internal gate token, and
    anything scanning it for a verdict would read "gaps" off the report.
    """

    def _gaps(self, text):
        deny = acr.check(text, task_id=None, track=False)
        return [g for g in (deny.gaps if deny else []) if '[格式]' in g]

    def test_status_first_line_is_a_gap(self):
        assert len(self._gaps('Status: gaps\n\n' + _CONSISTENT)) == 1

    def test_bolded_status_is_also_caught(self):
        assert len(self._gaps('**Status: incomplete**\n\n' + _CONSISTENT)) == 1

    def test_a_normal_report_passes(self):
        text = '# 广告优化建议 — acme — 2026-01-02\n\n' + _CONSISTENT
        assert self._gaps(text) == []

    def test_status_deeper_in_the_body_is_not_flagged(self):
        """Only the OPENING line is the review-file tell.

        A report may legitimately quote a reviewer verdict when explaining
        what the review round found; that is prose, not a mis-filed header.
        """
        text = (
            '# 广告优化建议 — acme — 2026-01-02\n\n'
            + _CONSISTENT
            + '\n上一轮 reviewer 给的是 Status: gaps，已按其列出的项修完。\n'
        )
        assert self._gaps(text) == []


_TWICE = """## Amazon SA

| id | name | type | spend | sales | orders | ACOS | ROAS |
|---|---|---|---|---|---|---|---|
| 600000000001 | acme widget video | Manual | SAR 90.00 | SAR 300.00 | 9 | 30.00% | 3.33 |
| A00000001AAAAAAAAAAAA | acme widget video | Brand Video | SAR 90.00 | SAR 300.00 | 9 | 30.00% | 3.33 |

### 600000000001 | acme widget video | Manual

| 关键词/定向 | 匹配 | 出价 (SAR) | 点击 | 花费 (SAR) | ROAS | 建议 |
|---|---|---|---|---|---|---|
| widget | Broad | 1.00 | 100 | 90.00 | 3.33 | 维持 |
| **合计** | — | — | 100 | 90.00 | 3.33 | — |
搜索词对账: 定向花费 SAR 90.00 / 点击 100 = 搜索词花费 SAR 90.00 / 点击 100 (✓)

### A00000001AAAAAAAAAAAA | acme widget video | Brand Video

| 关键词/定向 | 匹配 | 出价 (SAR) | 点击 | 花费 (SAR) | ROAS | 建议 |
|---|---|---|---|---|---|---|
| widget | Broad | 1.00 | 100 | 90.00 | 3.33 | 维持 |
| **合计** | — | — | 100 | 90.00 | 3.33 | — |
搜索词对账: 定向花费 SAR 90.00 / 点击 100 = 搜索词花费 SAR 90.00 / 点击 100 (✓)

## 汇总建议

| 平台 | 国家 | 活跃活动数 | 总花费 | 总销售额 | 总订单 | ROAS |
|---|---|---|---|---|---|---|
| amazon | SA | 2 | SAR 180.00 | SAR 600.00 | 18 | 3.33 |
"""


@pytest.mark.unit
class TestSameCampaignTwice:
    """One campaign, two ids, two blocks — double-counted everywhere.

    A Sponsored Brands campaign carries BOTH a numeric Campaign ID (what
    the bulk export uses) and an entity-style `A…` id (what the console
    shows). Capturing it from both sources writes it twice, and its spend
    then lands twice in the combo table, the drilled count and the rollup.
    The pre-existing duplicate check only catches the same ID twice.
    """

    def _gaps(self, text):
        return [g for g in check_rollups(text) if '[重复活动]' in g]

    def test_same_name_and_spend_under_two_ids_is_caught(self):
        gaps = self._gaps(_TWICE)
        assert len(gaps) == 1
        assert '600000000001' in gaps[0]
        assert 'A00000001AAAAAAAAAAAA' in gaps[0]

    def test_it_says_which_id_to_keep(self):
        """Ambiguity here would just start another edit ping-pong."""
        assert '保留 bulk 导出里那个 id' in self._gaps(_TWICE)[0]

    def test_same_name_different_spend_is_not_a_duplicate(self):
        """Sellers really do reuse names; only identical money is proof."""
        differing = _TWICE.replace(
            '| **合计** | — | — | 100 | 90.00 | 3.33 | — |\n搜索词对账: 定向花费 SAR 90.00 / 点击 100 = 搜索词花费 SAR 90.00 / 点击 100 (✓)\n\n## 汇总建议',
            '| **合计** | — | — | 40 | 30.00 | 3.33 | — |\n搜索词对账: 定向花费 SAR 30.00 / 点击 40 = 搜索词花费 SAR 30.00 / 点击 40 (✓)\n\n## 汇总建议',
        )
        assert self._gaps(differing) == []

    def test_a_single_campaign_is_clean(self):
        assert self._gaps(_CONSISTENT) == []

    def test_unnamed_campaigns_are_not_paired(self):
        """`name == id` means the name was never captured — not a match."""
        anon = _TWICE.replace('acme widget video', '600000000001')
        assert self._gaps(anon) == []


_TWO_TABLES = """## Amazon AE

| id | name | type | spend | sales | orders | ACOS | ROAS |
|---|---|---|---|---|---|---|---|
| A00000001AAAAAAAAAAAA | widget manual | Manual | AED 400.00 | AED 900.00 | 15 | 44% | 2.25 |

### A00000001AAAAAAAAAAAA | widget manual | Manual

| 字段 | Spend | Revenue | Clicks | Orders | Views | CTR | ROAS |
|---|---|---|---|---|---|---|---|
| 活动级 | 400.00 | 900.00 | 0 | 15 | 9000 | 1.2% | 2.25 |

| 关键词/定向 | 匹配 | 出价 (AED) | 点击 | 花费 (AED) | 订单 | 销售额 (AED) | ROAS | 建议 |
|---|---|---|---|---|---|---|---|---|
| widget | Broad | 2.00 | 0 | 400.00 | 15 | 900.00 | 2.25 | 维持 |
| **合计** | — | — | 0 | 400.00 | 15 | 900.00 | 2.25 | — |
搜索词对账: 定向花费 AED 400.00 = 搜索词花费 AED 400.00 (✓)

## 汇总建议

| 平台 | 国家 | 活跃活动数 | 总花费 | 总销售额 | 总订单 | ROAS |
|---|---|---|---|---|---|---|
| amazon | AE | 1 | AED 400.00 | AED 900.00 | 15 | 2.25 |
"""


@pytest.mark.unit
class TestFooterHeaderPairing:
    """The 合计 row must be read against ITS OWN table's header.

    Blocks carry more than one table — a live one opened with a small
    `字段 | Spend | Revenue | Clicks | Orders | …` summary above its
    targeting table. Reading the footer against the block's FIRST
    spend-bearing header resolves a column against the wrong table and
    returns another metric entirely; the spend-only predecessor escaped
    that only because the wrong cell held an em dash and parsed as None.
    """

    def test_footer_uses_its_own_tables_header(self):

        block = _TWO_TABLES.split('\n### ')[1]
        assert _total_row_spend(block) == 400.00
        # 订单 = 15 and 销售额 = 900 — NOT the leading summary's columns.
        assert _footer_extra_metrics(block) == {'sales': 900.00, 'orders': 15.0}

    def test_two_tables_block_is_clean(self):
        assert [
            g for g in check_rollups(_TWO_TABLES) if '[汇总一致]' in g
        ] == []


@pytest.mark.unit
class TestSalesAndOrdersAgree:
    """Spend matching does not make the rest of the row right.

    Every divergence the LLM reviewer kept reporting sat in sales/orders
    while spend matched — a campaign whose head row said 900 sales / 15
    orders over a 合计 row saying 244 / 4, and a summary row claiming far
    more than its table summed to.
    """

    def _gaps(self, text):
        return [g for g in check_rollups(text) if '销售额/订单' in g]

    def test_agreement_is_clean(self):
        assert self._gaps(_TWO_TABLES) == []

    def test_stale_sales_in_the_footer_is_caught(self):
        bad = _TWO_TABLES.replace(
            '| **合计** | — | — | 0 | 400.00 | 15 | 900.00 | 2.25 | — |',
            '| **合计** | — | — | 0 | 400.00 | 4 | 240.00 | 2.25 | — |',
        )
        gaps = self._gaps(bad)
        assert len(gaps) == 1
        assert '销售额' in gaps[0] and '订单' in gaps[0]

    def test_spend_still_matching_does_not_excuse_it(self):
        """The whole point: spend agrees in the failing fixture."""
        bad = _TWO_TABLES.replace(
            '| **合计** | — | — | 0 | 400.00 | 15 | 900.00 | 2.25 | — |',
            '| **合计** | — | — | 0 | 400.00 | 4 | 240.00 | 2.25 | — |',
        )

        assert _total_row_spend(bad.split('\n### ')[1]) == 400.00
        assert self._gaps(bad)
