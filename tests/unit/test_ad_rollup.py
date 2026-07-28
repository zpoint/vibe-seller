"""A campaign's spend is written three times and must agree everywhere.

Combo table row, the campaign's own drill block, and the per-combo row of
the 汇总 table are one number in three places. The agent kept updating a
drill block after a re-capture and leaving the table and rollup on the
old figure — the LLM reviewer caught it on two consecutive rounds of one
live run and named the second 「与 iter-3 镜像的同构缺陷」. Nothing about
that needs a model to decide, so it is pinned here.
"""

import pytest

from app.ai.stop_gates.ad_rollup import check_rollups

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
| noon | AE | 2 | AED 120.00 | AED 462.17 | 13 | 3.77 |
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
        near = _CONSISTENT.replace('AED 120.00', 'AED 120.00')
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
| A0000001 | w | Auto | A$1,000.00 | A$500.00 | 22 | 29.09% | 3.44 |

### A0000001 | w | Auto

搜索词对账: 定向花费 A$1,000.00 / 点击 50 = 搜索词花费 A$1,000.00 / 点击 50 (✓)

## 汇总建议

| 平台 | 国家 | 活跃活动数 | 总花费 | 总销售额 | 总订单 | ROAS |
|---|---|---|---|---|---|---|
| amazon | AU | 1 | A$1,000.00 | A$500.00 | 22 | 3.44 |
"""
        assert check_rollups(au) == []
        assert (
            len(
                check_rollups(
                    au.replace(
                        '| amazon | AU | 1 | A$1,000.00',
                        '| amazon | AU | 1 | A$1,000.00',
                    )
                )
            )
            == 1
        )

    def test_no_combo_section_is_not_a_gap(self):
        assert check_rollups('just some prose about ads') == []
        assert check_rollups('') == []
