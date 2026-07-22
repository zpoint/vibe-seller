"""Unit tests for the generic set_task_result soft gates.

Covers two gates:

- markdown_format: catches malformed GFM tables that remark-gfm would
  silently downgrade to <p>. mistune is the oracle (same accept/reject
  behavior as the frontend renderer).
- result_language: catches results whose prose runs in the wrong
  script for a Chinese-/English-authored task (< 85% of prose chars
  in the expected script).

Both gates are *soft*: after SOFT_GATE_MAX_DENIALS denies per
(task_id, gate) the next call passes through. The attempt counter
lives in module state and is reset between tests.
"""

from __future__ import annotations

import pytest

from app.ai.stop_gates import (
    SOFT_GATE_MAX_DENIALS,
    _attempts,  # noqa: PLC2701 — tests inspect/clear the counter
    ad_bid_floor as bid_floor_gate,
    ad_completeness_review as completeness_gate,
    ad_explicit_actions as explicit_actions_gate,
    ad_scale_winners as scale_winners_gate,
    markdown_format as md_format_gate,
    record_attempt,
    reset_attempts,
    result_language as language_gate,
)
from app.ai.stop_gates.ad_rules import DEFAULT_RULES, resolve_rules


@pytest.fixture(autouse=True)
def _clear_attempts():
    _attempts.clear()
    completeness_gate._max_drilled.clear()
    completeness_gate._total_high.clear()
    completeness_gate._stall_rounds.clear()
    completeness_gate._last_len.clear()
    yield
    _attempts.clear()
    completeness_gate._max_drilled.clear()
    completeness_gate._total_high.clear()
    completeness_gate._stall_rounds.clear()
    completeness_gate._last_len.clear()


@pytest.mark.unit
class TestMarkdownFormatGate:
    def test_passes_when_no_tables(self):
        assert md_format_gate.check('# Hello\n\nJust prose.') is None

    def test_passes_well_formed_gfm_table(self):
        text = (
            '# Hi\n\n'
            '| a | b | c |\n'
            '|---|---|---|\n'
            '| 1 | 2 | 3 |\n'
            '| 4 | 5 | 6 |\n'
        )
        assert md_format_gate.check(text) is None

    def test_flags_header_separator_mismatch(self):
        """Reproduces the column-count mismatch class: header
        has N cells, separator has N+1 — what remark-gfm rejects
        and silently downgrades to <p>.
        """
        text = (
            '# Report\n\n'
            '| col1 | col2 | col3 |\n'
            '|---|---|---|---|\n'
            '| a | b | c |\n'
        )
        deny = md_format_gate.check(text)
        assert deny is not None
        assert deny.gate == 'markdown_format'
        assert 'column counts' in deny.reason

    def test_flags_body_row_count_mismatch(self):
        text = (
            '# Hi\n\n'
            '| a | b | c |\n'
            '|---|---|---|\n'
            '| 1 | 2 |\n'
            '| 4 | 5 | 6 | 7 |\n'
        )
        assert md_format_gate.check(text) is not None

    def test_ignores_pipes_in_fenced_code(self):
        text = (
            '# Hi\n\n```\n| not | a | table |\n|---|---|\n| at | all |\n```\n'
        )
        assert md_format_gate.check(text) is None

    def test_handles_empty_input(self):
        assert md_format_gate.check('') is None
        assert md_format_gate.check(None) is None  # type: ignore[arg-type]


@pytest.mark.unit
class TestLanguageGate:
    ZH_TITLE = '审计本店当前广告并给出优化建议'
    EN_TITLE = 'Audit current ads and recommend optimizations'

    def test_passes_pure_chinese_result_for_zh_task(self):
        result = '# 广告优化建议\n\n本次审计花费 $183.80，销售额 $436.53。'
        assert language_gate.check(result, self.ZH_TITLE, None) is None

    def test_flags_english_recommendation_in_zh_task(self):
        # Mostly English prose under a Chinese task title.
        result = (
            '# Ad audit\n\nThe top campaign is a workhorse holding most '
            'of the orders. The bid is below the suggested range. The '
            'ACOS is approaching the break-even threshold. Maintain the '
            'current bid and continue to observe conversion trends over '
            'the next reporting period before making further adjustments.'
        )
        deny = language_gate.check(result, self.ZH_TITLE, None)
        assert deny is not None
        assert deny.gate == 'result_language'
        assert '85%' in deny.reason or 'Chinese' in deny.reason

    def test_passes_legitimate_chinese_audit(self):
        """Long Chinese prose, with a few unavoidable Latin tokens
        (metric names, opaque IDs, a verbatim search term). This is
        the shape of a real audit after the agent has obeyed the
        language hint; it should clear 85%.
        """
        result = (
            '# 广告优化建议 — 2099-01-01\n\n'
            '本次分析窗口为最近 30 天，覆盖店铺所有活跃广告活动。'
            '阈值设定为毛利率 25%，盈亏平衡 ROAS 4.00，目标 ROAS 5.71。'
            '本次审计将按活动逐一审查，输出包含出价调整、否定关键词、'
            '以及暂停建议在内的优化清单。\n\n'
            '## 活动总览\n\n共 12 个广告活动，其中 5 个活跃投放、'
            '4 个已暂停、3 个新建尚无数据。\n\n'
            '## 异常要点\n\n所有活跃活动的 ROAS 均低于盈亏平衡线。'
            '活动 CMP_PLACEHOLDER_001 的 ACOS 34.1%，关键词出价远低于'
            '建议范围，需要适度提高出价以争取更多曝光。该活动承担'
            '本店铺 42% 的订单，是核心转化活动，建议维持当前策略不调整。\n\n'
            '## 建议\n\n保持核心关键词当前出价不变。'
            '该关键词承担本活动 42% 的订单，属于核心转化词；'
            '当前 ACOS 34.1% 已接近盈亏平衡，再次降价风险较高；'
            '维持现有出价不调整，继续观察转化趋势。\n'
        )
        assert language_gate.check(result, self.ZH_TITLE, None) is None

    def test_passes_table_heavy_chinese_audit(self):
        """A Chinese audit dominated by GFM tables whose cells mix
        Chinese prose with English keywords / opaque campaign IDs.

        Regression for the whole-document ``detect_multiple_languages_of``
        false positive: it merged the Chinese header + notes into the
        surrounding English table runs and reported ~0 % Chinese for a
        report that visibly reads as Chinese, forcing the agent into a
        set_task_result workaround. Per-line detection keeps each
        Chinese prose line counted on its own.
        """
        result = (
            '# 广告优化建议 — 店铺A — 2099-01-01\n\n'
            '分析窗口：最近 30 天。盈亏平衡 ACOS 25%，目标 ROAS 5.71。\n\n'
            '## Amazon US\n\n'
            '总体数据：8 个活动，2 个活跃。以下逐活动给出建议。\n\n'
            '| 活动 | 关键词 | 出价 | ROAS | 建议 |\n'
            '|---|---|---|---|---|\n'
            '| A06883661 | keyboard wireless | 1.17 | 4.45 '
            '| 维持投入——核心转化词，出价处于建议区间，承担 65% 订单 |\n'
            '| A06883661 | keyboard gaming | 0.88 | 2.73 '
            '| 下调至 0.71——出价高于建议中值，10 单转化偏弱 |\n'
            '| A01100624 | keyboard wireless | 1.50 | 3.49 '
            '| 软性下调 15% 至 1.28——主力词，仍高于实际点击成本 |\n\n'
            '## 汇总建议\n\n'
            '优先处理 A06883661 的出价分布，其余活动维持现状继续观察。\n'
        )
        assert language_gate.check(result, self.ZH_TITLE, None) is None

    def test_data_tables_and_english_progress_line_dont_fail_zh(self):
        """Regression: the language gate must not conflict with the
        data-table format + the English '**进度**: drilled D/A active'
        contract line. Table rows are data (excluded); a Chinese-
        narrative report with English metrics/IDs/进度 lines passes.
        """
        report = (
            '# 广告优化建议 — demo-northshore\n'
            '## Amazon US 市场\n'
            '本节覆盖该店铺所有活跃广告活动，逐个分析关键词表现并给出优化建议。\n'
            '**进度**: drilled 31/31 active (175 total, 1 pages)\n'
            '| 关键词 | 花费 | 订单 | ROAS | 建议 |\n'
            '|---|---|---|---|---|\n'
            '| keyboard wireless | 626 | 30 | 4.45 | 维持出价 |\n'
        ) * 3
        assert language_gate.check(report, self.ZH_TITLE, None) is None

    def test_strips_code_blocks_before_counting(self):
        # A Chinese task with a big code block — code shouldn't drag
        # the ratio down. The ID below is a placeholder shape.
        result = (
            '# 报告\n\n下面是数据：\n\n```python\n'
            'campaign_id = "CMP_PLACEHOLDER_001"\n'
            'roas = 2.93\n'
            'acos = 0.341\n'
            '```\n\n本活动表现稳定，维持出价不调。'
        )
        assert language_gate.check(result, self.ZH_TITLE, None) is None

    def test_passes_english_result_for_en_task(self):
        result = (
            '# Audit\n\nThe top campaign is a workhorse holding most '
            'orders; bid stays.'
        )
        assert language_gate.check(result, self.EN_TITLE, None) is None

    def test_flags_chinese_result_for_en_task(self):
        result = '# 审计报告\n\n本活动是核心转化词，维持出价不调。'
        deny = language_gate.check(result, self.EN_TITLE, None)
        assert deny is not None


@pytest.mark.unit
class TestAdBidFloorGate:
    """ACOS < 30% → bid may only be raised/held, never lowered."""

    AMZ_HEADER = (
        '| 关键词（匹配方式） | 状态 | 点击 | 花费 | 订单 | 销售额 '
        '| ACOS | ROAS | 出价 | 实际CPC | 建议 |\n'
        '|---|---|---|---|---|---|---|---|---|---|---|\n'
    )

    def _amz_row(self, acos, rec):
        return (
            f'| wireless mouse (Exact) | Delivering | 269 | USD 555 | 60 | USD 2486 '
            f'| {acos} | 4.48 | USD 2.74 | USD 2.06 | {rec} |\n'
        )

    def test_denies_trim_on_low_acos(self):
        report = self.AMZ_HEADER + self._amz_row(
            '22.3%', '**下调至 USD 2.06** (−25%) — ACOS 22.3%'
        )
        deny = bid_floor_gate.check(report)
        assert deny is not None
        assert deny.gate == 'ad_bid_floor'
        assert '30%' in deny.reason

    def test_allows_hold_on_low_acos(self):
        report = self.AMZ_HEADER + self._amz_row('22.3%', 'Hold')
        assert bid_floor_gate.check(report) is None

    def test_allows_raise_on_low_acos(self):
        report = self.AMZ_HEADER + self._amz_row(
            '12.0%', '提高至 USD 3.20 — 抢量'
        )
        assert bid_floor_gate.check(report) is None

    def test_allows_trim_on_high_acos(self):
        # ACOS 36.6% ≥ 30% — genuinely inefficient, trim permitted.
        report = self.AMZ_HEADER + self._amz_row(
            '36.6%', '**下调至 USD 2.06** (−25%) — ACOS 36.6%'
        )
        assert bid_floor_gate.check(report) is None

    def test_allows_hold_whose_note_says_cannot_lower(self):
        # A compliant Hold may EXPLAIN it must not be lowered. The note
        # contains 下调 inside 不可下调 — must NOT be mistaken for a trim
        # directive (regression: this false-flagged a correct report).
        for note in (
            '维持 — ACOS 22.3% 低于 30%，盈利中，不可下调',
            '维持 — 5 订单，不下调',
            'Hold — ACOS 22.3% < 30%, do not lower the bid',
        ):
            report = self.AMZ_HEADER + self._amz_row('22.3%', note)
            assert bid_floor_gate.check(report) is None, note

    def test_boundary_30pct_allows_trim(self):
        report = self.AMZ_HEADER + self._amz_row('30.0%', '下调至 USD 2.00')
        assert bid_floor_gate.check(report) is None

    def test_noon_roas_table_denies_trim(self):
        # noon reports ROAS; ROAS 4.50 == ACOS 22% < 30% → deny.
        report = (
            '| 关键词（匹配方式） | 出价 | 建议价（范围） | 花费 | 订单 '
            '| 收入 | ROAS | 建议 |\n'
            '|---|---|---|---|---|---|---|---|\n'
            '| mouse/ (Phrase) | USD 1.00 | 0.6–0.8 | USD 50 | 10 '
            '| USD 400 | 4.50 | 下调 bid 到 USD 0.75 |\n'
        )
        deny = bid_floor_gate.check(report)
        assert deny is not None

    def test_noon_roas_table_allows_trim_when_inefficient(self):
        # ROAS 2.50 == ACOS 40% ≥ 30% → trim allowed.
        report = (
            '| 关键词（匹配方式） | 出价 | 建议价（范围） | 花费 | 订单 '
            '| 收入 | ROAS | 建议 |\n'
            '|---|---|---|---|---|---|---|---|\n'
            '| x/ (Phrase) | USD 1.00 | 0.4–0.6 | USD 50 | 2 '
            '| USD 50 | 2.50 | 下调 bid 到 USD 0.60 |\n'
        )
        assert bid_floor_gate.check(report) is None

    def test_zero_order_row_not_flagged(self):
        # No measurable ACOS (— / 0 orders) → rule N/A even if trimmed.
        report = self.AMZ_HEADER + (
            '| dead kw (Exact) | Delivering | 6 | USD 20 | 0 | — '
            '| — | — | USD 3.00 | USD 3.41 | 下调至 USD 2.25 |\n'
        )
        assert bid_floor_gate.check(report) is None

    def test_non_ads_result_passes(self):
        assert (
            bid_floor_gate.check('# 任务完成\n\n花费 $5，无广告表格。') is None
        )

    def test_multiple_violations_counted(self):
        report = (
            self.AMZ_HEADER
            + self._amz_row('20.7%', '下调至 USD 2.06')
            + self._amz_row('22.3%', '下调至 USD 2.06')
        )
        deny = bid_floor_gate.check(report)
        assert deny is not None
        assert '2 个关键词' in deny.reason


@pytest.mark.unit
class TestAdScaleWinnersGate:
    """ROAS > 5 converter must be raised or hold-justified, not bare Hold."""

    HEADER = (
        '| 关键词（匹配方式） | 状态 | 点击 | 花费 | 订单 | 销售额 '
        '| ACOS | ROAS | 出价 | 实际CPC | 建议 |\n'
        '|---|---|---|---|---|---|---|---|---|---|---|\n'
    )

    def _row(self, roas, rec, acos='4.0%'):
        return (
            f'| 100w charger (Exact) | Delivering | 2 | USD 3.45 | 1 '
            f'| USD 83.39 | {acos} | {roas} | USD 2.50 | USD 1.70 | {rec} |\n'
        )

    def test_flags_bare_hold_on_high_roas(self):
        report = self.HEADER + self._row(
            '24.17', '保持不动（受保护） — ROAS 24.17'
        )
        deny = scale_winners_gate.check(report)
        assert deny is not None
        assert deny.gate == 'ad_scale_winners'

    def test_allows_raise_on_high_roas(self):
        report = self.HEADER + self._row(
            '24.17', '提高至 USD 3.10 — 抢量，ROAS 24 远超目标'
        )
        assert scale_winners_gate.check(report) is None

    def test_allows_justified_hold_suggested_high(self):
        report = self.HEADER + self._row(
            '24.17', 'Hold — 出价已达建议上限，无盈利空间继续加价'
        )
        assert scale_winners_gate.check(report) is None

    def test_allows_justified_hold_budget(self):
        report = self.HEADER + self._row(
            '11.75', 'Hold — campaign out of budget，先提预算再加价'
        )
        assert scale_winners_gate.check(report) is None

    def test_ignores_low_roas_hold(self):
        # ROAS 4.5 ≤ 5 — a plain Hold is fine, not a scale candidate.
        report = self.HEADER + self._row('4.5', 'Hold', acos='22.2%')
        assert scale_winners_gate.check(report) is None

    def test_boundary_roas_5_not_flagged_just_above_flagged(self):
        # Threshold is strict (> 5): exactly 5.0 is not a scale candidate;
        # 5.5 is.
        at = self.HEADER + self._row('5.0', 'Hold', acos='20%')
        assert scale_winners_gate.check(at) is None
        above = self.HEADER + self._row('5.5', 'Hold', acos='18%')
        assert scale_winners_gate.check(above) is not None

    def test_per_store_override_raises_threshold(self):
        # A store whose notes.md sets scale_roas: 30 → a ROAS 24 bare
        # Hold is below the override and must NOT be flagged.
        report = self.HEADER + self._row('24.17', 'Hold')
        assert scale_winners_gate.check(report) is not None  # default >5
        rules = resolve_rules('scale_roas: 30')
        assert scale_winners_gate.check(report, rules) is None

    def test_noon_roas_table_flags_bare_hold(self):
        report = (
            '| 关键词（匹配方式） | 出价 | 建议价（范围） | 花费 | 订单 '
            '| 收入 | ROAS | 建议 |\n'
            '|---|---|---|---|---|---|---|---|\n'
            '| mouse/ (Exact) | USD 0.40 | 0.3–0.6 | USD 35 | 26 '
            '| USD 1092 | 31.04 | 保持不动（受保护） |\n'
        )
        assert scale_winners_gate.check(report) is not None

    def test_non_ads_result_passes(self):
        assert scale_winners_gate.check('# 完成\n\n无广告表格。') is None


@pytest.mark.unit
class TestAdCompletenessReview:
    """Exit-hook reviewer: structured 'what's missing' diff, converges."""

    def test_complete_report_passes(self):
        # Each combo has its 进度 line AND real per-campaign drill tables
        # (建议 column) — not a bare manifest.
        drill = (
            '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 1.0 | 9.0 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
        )
        summary = (
            '## 汇总建议\n\n本次审计覆盖各市场，总花费与销售额见各节'
            '合计。最高优先级行动：提高核心转化词出价抢占盈利流量，'
            '下调长期零单高花费词，零展示词保持观察。预算向高回报'
            '活动倾斜，整体结构健康。\n'
        )
        report = (
            '# 广告优化建议\n\n'
            '## Amazon US\n\n**进度**: drilled 2/2 active (175 total, 1 page)\n'
            + drill
            + '## noon EG\n\n**进度**: drilled 2/2 active (70 total, 5 pages)\n'
            + drill
            + summary
        )
        assert completeness_gate.check(report) is None

    def test_self_disclosed_pagination_truncation_flagged(self):
        # Regression: an audit that under-enumerated (read only grid page 1
        # via the inline Export trap, missing pages 2-3) but drilled every
        # id it DID find reports D==A and used to pass — while its own prose
        # admitted "仅获取第1页(50/150)" / "待翻页获取". The gate must catch
        # the self-disclosed truncation, not trust the D==A denominator.
        drill = (
            '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 1.0 | 9.0 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
        )
        report = (
            '# 广告优化建议\n\n'
            '## Amazon SA\n\n**进度**: drilled 20/20 active (50 total, 1 pages)\n'
            + drill
            + '\n### ⚠️ 限制说明\n- Amazon SA: 仅获取第1页(50/150)，'
            '第2-3页 ~100 个活动待翻页获取\n'
        )
        deny = completeness_gate.check(report)
        assert deny is not None
        assert deny.gate == 'ad_completeness_review'

    def test_under_drilled_combo_flagged(self):
        report = (
            '## noon EG\n\n**进度**: drilled 12/46 active (70 total, 5 pages)\n'
            '逐campaign数据...\n'
        )
        deny = completeness_gate.check(report)
        assert deny is not None
        assert deny.gate == 'ad_completeness_review'
        assert '12/46' in deny.reason
        assert '34' in deny.reason  # missing count

    def test_missing_progress_line_flagged(self):
        report = '## noon EG 市场\n\n| 活动 | ... |\n（没有进度行）\n'
        deny = completeness_gate.check(report)
        assert deny is not None
        assert '进度' in deny.reason

    def test_execution_summary_prose_not_treated_as_audit_section(self):
        # An EXECUTION task's result is prose that may START with a
        # platform name ("Amazon US 广告优化执行完成…"). It has no ``## ``
        # combo header and no ``drilled D/A`` line — it is NOT an audit
        # report and must pass. Regression: the preamble part used to be
        # matched by _COMBO_HEADER_RE, denying it for a missing progress
        # line, which taught the agent to FABRICATE "drilled 10/10".
        execution_result = (
            'Amazon US 广告优化执行完成。依据已审核报告 '
            'AD_AUDIT_2026-06-10.md，对 10 个活动逐一处理：\n\n'
            '已完成的活动：\n'
            '- 活动 100000000000001：添加 14 个否定搜索词，已验证保存\n'
            '- 活动 100000000000002：close-match 出价提高至 USD 2.71\n'
            '阻断的活动：3 个 SB 类型广告组导航不可用，需人工操作。\n'
        )
        assert completeness_gate.check(execution_result) is None

    def test_folds_in_bid_rule_violation(self):
        report = (
            '## Amazon US\n\n**进度**: drilled 31/31 active (175 total, 1 page)\n'
            '| 关键词（匹配方式） | 状态 | 点击 | 花费 | 订单 | 销售额 '
            '| ACOS | ROAS | 出价 | 实际CPC | 建议 |\n'
            '|---|---|---|---|---|---|---|---|---|---|---|\n'
            '| wireless mouse (Exact) | Delivering | 269 | USD 555 | 60 | USD 2486 '
            '| 22.3% | 4.48 | USD 2.74 | USD 2.06 | 下调至 USD 2.06 |\n'
        )
        deny = completeness_gate.check(report)
        assert deny is not None
        assert '规则' in deny.reason

    def test_non_ads_result_passes(self):
        assert completeness_gate.check('# 完成\n\n普通任务，无广告。') is None

    def test_regression_flagged(self):
        # Round 1: Amazon US fully drilled 31/31 (sets high-water mark).
        drill = (
            '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 1 | 9 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
            * 20
        )
        summary = (
            '## 汇总建议\n\n本次审计覆盖各市场，总花费与销售额见各节'
            '合计。最高优先级行动：提高核心转化词出价抢占盈利流量，'
            '下调长期零单高花费词，零展示词保持观察。预算向高回报'
            '活动倾斜，整体结构健康。\n'
        )
        r1 = (
            '## Amazon US\n\n**进度**: drilled 31/31 active (175 total, 1 page)\n'
            + drill
            + summary
        )
        assert completeness_gate.check(r1, task_id='t1') is None
        # Round 2: rewrote from memory, lost work → 2/31 (regression).
        r2 = (
            '## Amazon US\n\n**进度**: drilled 2/31 active (175 total, 1 page)\n'
            + drill
        )
        deny = completeness_gate.check(r2, task_id='t1')
        assert deny is not None
        assert '回退' in deny.reason and '31' in deny.reason

    def test_stall_fail_open_after_no_progress(self):
        # A report with a real gap (noon 3/48) that never improves: after
        # STALL_CAP rounds of no net new drills, is_stalled() flips True
        # so the caller can accept the partial report.
        drill = (
            '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 1 | 9 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
        )
        report = (
            '## Noon EG\n\n**进度**: drilled 3/48 active (70 total, 5 pages)\n'
            + drill * 3
        )
        # Round 1 registers progress (0→3); the next STALL_CAP rounds
        # stand still. Each still returns a deny (the gap remains)...
        for _ in range(completeness_gate.STALL_CAP + 1):
            assert completeness_gate.check(report, task_id='t1') is not None
        # ...and after STALL_CAP no-progress rounds it's considered stalled.
        assert completeness_gate.is_stalled('t1') is True

    def test_polish_submits_do_not_burn_stall_budget(self):
        # Same D each round but the report text changes substantially
        # (the agent is polishing/expanding between drilling bursts) —
        # NOT stalled. Premature fail-open at 15/111 was caused by
        # counting these as no-progress rounds.
        base = (
            '## Noon EG\n\n**进度**: drilled 3/48 active (70 total, 5 pages)\n'
            '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 1 | 9 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
            * 3
        )
        for i in range(completeness_gate.STALL_CAP + 2):
            report = base + ('\n更多分析内容补充。' * 40 * (i + 1))
            assert completeness_gate.check(report, task_id='t8') is not None
            assert completeness_gate.is_stalled('t8') is False

    def test_progress_resets_stall(self):
        # A climbing D must keep is_stalled() False (never cut off a
        # slow-but-progressing driller).
        drill = (
            '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 1 | 9 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
        )
        for d in (3, 10, 20, 30, 40):
            report = (
                f'## Noon EG\n\n**进度**: drilled {d}/48 active '
                '(70 total, 5 pages)\n' + drill * d
            )
            assert completeness_gate.check(report, task_id='t2') is not None
            assert completeness_gate.is_stalled('t2') is False

    def test_reset_progress_clears_stall(self):
        report = (
            '## Noon EG\n\n**进度**: drilled 3/48 active (70 total, 5 pages)\n'
            '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 1 | 9 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
            * 3
        )
        for _ in range(completeness_gate.STALL_CAP + 1):
            completeness_gate.check(report, task_id='t3')
        assert completeness_gate.is_stalled('t3') is True
        completeness_gate.reset_progress('t3')
        assert completeness_gate.is_stalled('t3') is False

    def test_over_report_drilled_exceeds_active_flagged(self):
        # Dumped non-active campaigns: drilled 105 > active 56.
        report = (
            '## Amazon US\n\n**进度**: drilled 105/56 active (105 total, 1 pages)\n'
            '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 1 | 9 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
            * 60
        )
        deny = completeness_gate.check(report, task_id='t9')
        assert deny is not None
        assert '越界' in deny.reason and '105' in deny.reason

    # --- search-term layer: reconciliation + collapse ---

    DRILL = (
        '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
        '| wireless mouse | 1 | 9 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
    )

    SUMMARY = (
        '\n## 汇总建议\n\n本次审计覆盖各市场，总花费与销售额见各节合计。'
        '最高优先级行动：提高核心转化词出价抢占盈利流量，下调长期零单'
        '高花费词，零展示词保持观察。整体预算向高回报活动倾斜，结构健康。\n'
    )

    def _campaign_block(self, extra=''):
        return (
            '## Amazon US\n\n**进度**: drilled 1/1 active (1 TSV)\n\n'
            '### 100000000000003 | wireless mouse 006 | Manual\n\n'
            '#### Targeting\n' + self.DRILL + extra + self.SUMMARY
        )

    def test_campaign_without_reconcile_line_flagged(self):
        deny = completeness_gate.check(self._campaign_block())
        assert deny is not None
        assert '搜索词' in deny.reason and '对账' in deny.reason

    def test_campaign_with_good_reconcile_passes(self):
        block = self._campaign_block(
            '\n#### Search Terms\n'
            + self.DRILL
            + '\n搜索词对账: 定向花费 USD 942.39 / 点击 762 = '
            '搜索词花费 USD 942.39 / 点击 762 (✓ 偏差 0%)\n'
        )
        assert completeness_gate.check(block) is None

    def test_campaign_with_mismatched_reconcile_flagged(self):
        # 30d targeting vs 7d search-terms: spend 942 vs 215 — way out.
        block = self._campaign_block(
            '\n#### Search Terms\n'
            + self.DRILL
            + '\n搜索词对账: 定向花费 USD 942.39 / 点击 762 = '
            '搜索词花费 USD 215.10 / 点击 180 (✗)\n'
        )
        deny = completeness_gate.check(block)
        assert deny is not None
        assert '[对账]' in deny.reason and '窗口' in deny.reason

    def test_reconcile_click_divergence_alone_passes(self):
        # Amazon strips invalid clicks from the search-term report, so
        # clicks legitimately diverge (observed live: 37%) even on a
        # perfect same-window read. SPEND is the window signal — spend
        # within tolerance must pass regardless of click delta.
        block = self._campaign_block(
            '\n#### Search Terms\n'
            + self.DRILL
            + '\n搜索词对账: 定向花费 USD 1391.56 / 点击 1094 = '
            '搜索词花费 USD 1413.23 / 点击 688 (✓ 花费偏差 1.6%)\n'
        )
        assert completeness_gate.check(block) is None

    def test_campaign_no_searchterm_token_escapes(self):
        # SD-type campaign: no search-term report exists — explicit token.
        block = self._campaign_block('\n该活动类型无搜索词报告（SD）。\n')
        assert completeness_gate.check(block) is None

    def test_collapse_row_with_traffic_flagged(self):
        block = self._campaign_block(
            '\n#### Search Terms\n'
            '| 搜索词 | 点击 | 花费 | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 10 | 20.0 | 维持 |\n'
            '| 其余 46 个关键词 | - | - | 维持 |\n'
            '\n搜索词对账: 定向花费 USD 942.39 / 点击 762 = '
            '搜索词花费 USD 942.39 / 点击 762 (✓)\n'
        )
        deny = completeness_gate.check(block)
        assert deny is not None
        assert '[折叠]' in deny.reason

    def test_collapse_row_zero_impressions_allowed(self):
        block = self._campaign_block(
            '\n#### Search Terms\n'
            '| 搜索词 | 点击 | 花费 | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 10 | 20.0 | 维持 |\n'
            '| 其余 8 个关键词 (0 展示) | 0 | 0 | 维持 — 0 展示 |\n'
            '\n搜索词对账: 定向花费 USD 942.39 / 点击 762 = '
            '搜索词花费 USD 942.39 / 点击 762 (✓)\n'
        )
        assert completeness_gate.check(block) is None

    def test_reconcile_tolerance_override(self):
        # 20% off: fails default 15% tolerance, passes with override 0.3.
        block = self._campaign_block(
            '\n#### Search Terms\n'
            + self.DRILL
            + '\n搜索词对账: 定向花费 USD 1000.00 / 点击 800 = '
            '搜索词花费 USD 800.00 / 点击 800 (✗)\n'
        )
        assert completeness_gate.check(block) is not None
        rules = resolve_rules('reconcile_tolerance: 0.3')
        assert completeness_gate.check(block, rules=rules) is None

    def _noon_block(self, reconcile):
        return (
            '## noon EG\n\n**进度**: drilled 1/1 active (1 TSV)\n\n'
            '### C_FAKE0004 | wireless mouse 023 manual | 手动\n\n'
            '#### Targeting\n'
            + self.DRILL
            + '\n#### Search Terms\n'
            + self.DRILL
            + reconcile
            + self.SUMMARY
        )

    def test_noon_cq_underreport_within_floor_passes(self):
        # noon Customer Queries attributes only part of spend to
        # queries (observed 47-74% live). 54% is a correct same-window
        # read, not a window error — must pass the noon floor (40%).
        block = self._noon_block(
            '\n搜索词对账: 定向花费 USD 89.00 / 点击 60 = '
            '搜索词花费 USD 48.00 / 点击 41 (✓ CQ 部分归因)\n'
        )
        assert completeness_gate.check(block) is None

    def test_noon_wrong_window_still_caught(self):
        # A 7d read of a 30d targeting page shows ~23% — below the
        # 40% floor, so window mismatches still get flagged on noon.
        block = self._noon_block(
            '\n搜索词对账: 定向花费 USD 100.00 / 点击 80 = '
            '搜索词花费 USD 23.00 / 点击 18 (✗)\n'
        )
        deny = completeness_gate.check(block)
        assert deny is not None
        assert '[对账]' in deny.reason

    def test_noon_floor_override(self):
        # notes.md can tighten the floor: 54% passes default 0.4 but
        # fails noon_reconcile_floor: 0.6.
        block = self._noon_block(
            '\n搜索词对账: 定向花费 USD 89.00 / 点击 60 = '
            '搜索词花费 USD 48.00 / 点击 41 (✓)\n'
        )
        assert completeness_gate.check(block) is None
        rules = resolve_rules('noon_reconcile_floor: 0.6')
        assert completeness_gate.check(block, rules=rules) is not None

    def test_amazon_keeps_symmetric_tolerance(self):
        # The floor is noon-only: the same 54% ratio on an Amazon
        # section is still a window-mismatch defect.
        block = self._campaign_block(
            '\n#### Search Terms\n'
            + self.DRILL
            + '\n搜索词对账: 定向花费 USD 89.00 / 点击 60 = '
            '搜索词花费 USD 48.00 / 点击 41 (✗)\n'
        )
        deny = completeness_gate.check(block)
        assert deny is not None
        assert '[对账]' in deny.reason

    def test_manifest_not_drill_flagged(self):
        # Claims 46/46 but only a page-manifest table (no 建议 column).
        report = (
            '## Noon EG\n\n**进度**: drilled 46/46 active (70 total, 5 pages)\n'
            '| 活动ID | 类型 | 花费 | ROAS |\n|---|---|---|---|\n'
            '| C_ABC | 品牌广告 | 476 | 15.5 |\n'
        )
        deny = completeness_gate.check(report)
        assert deny is not None
        assert 'manifest' in deny.reason or '清单' in deny.reason

    def test_defer_excuse_flagged(self):
        report = (
            '## Noon EG\n\n**进度**: drilled 46/46 active (70 total, 5 pages)\n'
            '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 1 | 9 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
            'Brand Analytics: 本次会话未获取（需 Brand Registry OTP），待下次 audit。\n'
        )
        deny = completeness_gate.check(report)
        assert deny is not None
        assert '不可推迟' in deny.reason or '推迟' in deny.reason

    def test_garbled_extraction_flagged(self):
        report = (
            '## Amazon US\n\n**进度**: drilled 31/31 active (175 total, 1 pages)\n'
            '| 搜索词 | 花费 | 建议 |\n|---|---|---|\n'
            '| b0xq4mz7t2 | 13.42 | 否定 |\n'
            '| asin-expanded="B0XXXXXXXX" | 6.60 | 否定 |\n'
        )
        deny = completeness_gate.check(report)
        assert deny is not None
        assert '原始 DOM' in deny.reason or 'DOM' in deny.reason


@pytest.mark.unit
class TestAttemptCounter:
    def test_record_attempt_increments(self):
        assert record_attempt('t1', 'g1') == 1
        assert record_attempt('t1', 'g1') == 2
        assert record_attempt('t1', 'g2') == 1
        assert record_attempt('t2', 'g1') == 1

    def test_reset_attempts_scoped_per_task(self):
        record_attempt('t1', 'g1')
        record_attempt('t2', 'g1')
        reset_attempts('t1')
        assert ('t1', 'g1') not in _attempts
        assert ('t2', 'g1') in _attempts

    def test_soft_max_denials_is_one(self):
        # The user explicitly asked: "let agent fix once but not
        # mandatory". This pins the contract.
        assert SOFT_GATE_MAX_DENIALS == 1


@pytest.mark.unit
class TestAdRules:
    """Single source of truth for bid-rule thresholds + notes.md override."""

    def test_defaults(self):
        assert DEFAULT_RULES['acos_no_lower'] == 30.0
        assert DEFAULT_RULES['scale_roas'] == 5.0

    def test_resolve_no_notes_returns_defaults(self):
        assert resolve_rules(None) == DEFAULT_RULES
        assert resolve_rules('') == DEFAULT_RULES
        # A copy, not the shared dict — mutating must not poison defaults.
        resolve_rules('scale_roas: 9')
        assert DEFAULT_RULES['scale_roas'] == 5.0

    def test_resolve_overrides_both_keys(self):
        notes = (
            '# demo-northshore notes\n\n## 广告规则 (ad-rules)\n'
            'scale_roas: 6\nacos_no_lower = 28\n'
        )
        r = resolve_rules(notes)
        assert r['scale_roas'] == 6.0
        assert r['acos_no_lower'] == 28.0

    def test_resolve_partial_override(self):
        r = resolve_rules('scale_roas: 7.5')
        assert r['scale_roas'] == 7.5
        assert r['acos_no_lower'] == 30.0  # untouched default

    def test_bid_floor_honors_acos_override(self):
        # ACOS 27% trim: a violation under default 30, but allowed if the
        # store lowers the no-lower floor to 25.
        report = (
            '| 关键词 | 出价 | ACOS | ROAS | 建议 |\n|---|---|---|---|---|\n'
            '| wireless mouse | 1.0 | 27% | 3.7 | 下调出价至 0.5 |\n'
        )
        assert bid_floor_gate.check(report) is not None  # default 30
        assert (
            bid_floor_gate.check(report, resolve_rules('acos_no_lower: 25'))
            is None
        )


@pytest.mark.unit
class TestExplicitActions:
    """Every raise/lower must state HOW MUCH and WHY — a bare 提高出价
    is unreviewable (store-owner feedback, 2026-06-11)."""

    HEAD = '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'

    def test_bare_action_flagged(self):
        report = self.HEAD + '| wireless mouse | 0.8 | 9 | 提高出价 |\n'
        deny = explicit_actions_gate.check(report)
        assert deny is not None
        assert '幅度' in deny.reason and '依据' in deny.reason

    def test_magnitude_without_reason_flagged(self):
        report = self.HEAD + '| wireless mouse | 0.8 | 9 | 提高至 1.00 |\n'
        assert explicit_actions_gate.check(report) is not None

    def test_target_bid_with_rule_basis_passes(self):
        report = (
            self.HEAD
            + '| wireless mouse | 0.8 | 9 | 提高至 1.00（ROAS 9>5 加投赢家规则） |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_percent_with_assumption_basis_passes(self):
        report = (
            self.HEAD
            + '| wireless mouse | 0.8 | 2.1 | 加投 25%——主力订单来源，'
            'ROAS 偏低也保留（假设） |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_hold_and_negate_exempt(self):
        report = (
            self.HEAD
            + '| a | 1 | 0 | 维持 |\n'
            + '| b | 1 | 0 | 否定 — 零单高点击 |\n'
            + '| c | 1 | 0 | 暂停 |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_non_ads_result_passes(self):
        assert explicit_actions_gate.check('# 完成\n普通任务。') is None
        assert explicit_actions_gate.check('') is None

    def test_folded_into_completeness(self):
        report = (
            '## Amazon US\n\n**进度**: drilled 1/1 active (1 total)\n'
            + self.HEAD
            + '| wireless mouse | 0.8 | 9 | 降低出价 |\n'
        )
        deny = completeness_gate.check(report)
        assert deny is not None
        assert '[规则·明确幅度]' in deny.reason


@pytest.mark.unit
class TestScaffoldAndSummary:
    """Unconsumed INSERT markers and a header-only 汇总建议 shipped
    unnoticed in a live report — both are now reviewer gaps."""

    GOOD = (
        '## Amazon US\n\n**进度**: drilled 1/1 active (1 total)\n'
        '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
        '| wireless mouse | 1 | 9 | 提高至 1.2（ROAS 9>5 加投赢家规则） |\n'
    )
    SUMMARY = (
        '## 汇总建议\n\n本次审计覆盖各市场，总花费与销售额见各节合计。'
        '最高优先级行动：提高核心转化词出价抢占盈利流量，下调长期零单'
        '高花费词，零展示词保持观察。预算向高回报活动倾斜，结构健康。\n'
    )

    def test_leftover_insert_marker_flagged(self):
        report = self.GOOD + self.SUMMARY + '\n<!-- INSERT: noon-eg -->\n'
        deny = completeness_gate.check(report)
        assert deny is not None
        assert '[未完成]' in deny.reason and 'noon-eg' in deny.reason

    def test_marker_only_summary_flagged(self):
        # The exact live failure: header present, body is one marker.
        report = self.GOOD + '## 汇总建议\n\n<!-- INSERT: summary -->\n'
        deny = completeness_gate.check(report)
        assert deny is not None
        assert '[汇总]' in deny.reason

    def test_missing_summary_flagged(self):
        deny = completeness_gate.check(self.GOOD)
        assert deny is not None
        assert '[汇总]' in deny.reason

    def test_filled_summary_passes(self):
        assert completeness_gate.check(self.GOOD + self.SUMMARY) is None


@pytest.mark.unit
class TestSearchTermDimension:
    """Search terms are not biddable — bid verbs on search-term rows
    are wrong-dimension defects; the valid vocabulary is 提取为定向词
    (with a suggested bid for the NEW keyword) / 否定 / 维持观察
    (store-owner review, 2026-06-11)."""

    ST_HEAD = (
        '| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 | 订单 | ROAS | 建议 |\n'
        '|---|---|---|---|---|---|---|---|\n'
    )

    def test_bid_verb_on_search_term_flagged(self):
        report = (
            self.ST_HEAD
            + '| wireless keyboard | keyboard wireless | Broad | 275 | '
            '247.87 | 13 | 4.84 | 提高出价 15% （ROAS 4.84 转化词规则） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None
        assert '维度' in deny.reason and '提取为定向词' in deny.reason

    def test_jiatou_on_search_term_flagged(self):
        report = (
            self.ST_HEAD
            + '| gaming keyboards | keyboard gaming | Broad | 11 | 8.15 '
            '| 1 | 11.04 | 加投 30% （ROAS 11.04>5 加投赢家规则） |\n'
        )
        assert explicit_actions_gate.check(report) is not None

    def test_extract_with_bid_passes(self):
        report = (
            self.ST_HEAD
            + '| wireless gaming keyboard | keyboard wireless | Broad | '
            '144 | 128.88 | 8 | 5.66 | 提取为定向词（Exact，建议出价 '
            '0.95——ROAS 5.66>5，来源 broad 词可降档） |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_extract_without_bid_flagged(self):
        report = (
            self.ST_HEAD
            + '| wireless gaming keyboard | keyboard wireless | Broad | '
            '144 | 128.88 | 8 | 5.66 | 提取为定向词（表现好） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None
        assert '建议出价' in deny.reason

    def test_negate_and_observe_pass(self):
        report = (
            self.ST_HEAD
            + '| keyboard gaming wireless | keyboard wireless | Broad | '
            '15 | 13.25 | 0 | 0 | 否定搜索词 |\n'
            + '| cable organizer | cable organizer | Phrase | 1 | 0.25 | 0 | 0 '
            '| 维持观察 — 小样本 |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_targeting_table_keeps_bid_rules(self):
        # A targeting table (关键词 first column) still allows raise
        # with magnitude+basis and still flags bare raises.
        head = '| 关键词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
        ok = (
            head
            + '| wireless mouse | 0.8 | 9 | 提高至 1.00（ROAS 9>5 规则X） |\n'
        )
        assert explicit_actions_gate.check(ok) is None
        bad = head + '| wireless mouse | 0.8 | 9 | 提高出价 |\n'
        assert explicit_actions_gate.check(bad) is not None

    def test_negated_extraction_mention_not_flagged(self):
        # 「不可提取为定向词」 explains why a category placement can't
        # be extracted — it is a hold, not an extraction lacking a bid.
        report = (
            self.ST_HEAD
            + '| 类目/CART匹配 | Category | 42 | 26 | 3 | 180 | 6.81 '
            '| 维持——类目匹配流量（非关键词，不可提取为定向词） |\n'
        )
        assert explicit_actions_gate.check(report) is None


@pytest.mark.unit
class TestHarvestPolicy:
    """Extraction is a RESCUE move (store-owner, 2026-06-11 PM):
    only when the SOURCE keyword is being cut. A healthy broad
    source keeps catching its terms — blanket extraction bloats the
    targeting list and self-competes. Identity terms (term == source)
    already ARE targeting keywords."""

    TGT = '| 关键词 | 出价 | ACOS | ROAS | 建议 |\n|---|---|---|---|---|\n'
    ST = (
        '| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 | 订单 | ROAS | 建议 |\n'
        '|---|---|---|---|---|---|---|---|\n'
    )

    def _block(self, tgt_row, st_row):
        return (
            '### 100000000000005 | charger | Manual\n\n'
            '#### Targeting\n'
            + self.TGT
            + tgt_row
            + '\n#### Search Terms\n'
            + self.ST
            + st_row
        )

    def test_identity_extraction_flagged(self):
        report = self._block(
            '| charging station | 2.40 | 22% | 6.1 | '
            '维持（ACOS 22%<30 规则） |\n',
            '| charging station | charging station | Broad | 219 | 503 '
            '| 30 | 6.1 | 提取为定向词（Exact，建议出价 2.64——ROAS 6.1>5） |\n',
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None
        assert '本来' in deny.reason and '就是定向词' in deny.reason

    def test_healthy_source_extraction_flagged(self):
        report = self._block(
            '| charging station | 2.40 | 22% | 6.1 | '
            '提高至 2.76（ROAS 6.1>5 加投赢家规则） |\n',
            '| usb charging station | charging station | Broad | 32 | 60 '
            '| 6 | 8.5 | 提取为定向词（Exact，建议出价 2.24——ROAS 8.5>>5） |\n',
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None
        assert '救援' in deny.reason or '一锅粥' in deny.reason

    def test_extraction_from_cut_source_passes(self):
        # Source keyword is being downgraded → harvesting its
        # converting term is exactly right.
        report = self._block(
            '| charging station | 2.40 | 45% | 2.1 | '
            '降至 1.90（ACOS 45%>30 规则） |\n',
            '| usb charging station | charging station | Broad | 32 | 60 '
            '| 6 | 8.5 | 提取为定向词（Exact，建议出价 2.24——ROAS 8.5>>5，'
            '来源词 ACOS 45% 已降档） |\n',
        )
        assert explicit_actions_gate.check(report) is None

    def test_hold_under_healthy_source_passes(self):
        report = self._block(
            '| charging station | 2.40 | 22% | 6.1 | '
            '提高至 2.76（ROAS 6.1>5 加投赢家规则） |\n',
            '| usb charging station | charging station | Broad | 32 | 60 '
            '| 6 | 8.5 | 维持——来源 Broad 词承接，ROAS 8.5 |\n',
        )
        assert explicit_actions_gate.check(report) is None

    def test_unknown_source_extraction_not_flagged_for_health(self):
        # Source not present in the targeting table (e.g. auto camp):
        # health unknowable — only the bid requirement applies.
        report = (
            '### C_ABC123 | auto | Auto\n\n#### Search Terms\n'
            + self.ST
            + '| some term | (auto) | Auto | 30 | 60 | 6 | 8.5 | '
            '提取为定向词（Exact，建议出价 2.20——ROAS 8.5>>5） |\n'
        )
        assert explicit_actions_gate.check(report) is None


@pytest.mark.unit
class TestBidRulesSkipSearchTermTables:
    """noon search-term tables carry 出价/eCPC columns (the SOURCE
    keyword's bid) — bid rules must not demand raises/flag trims on
    rows that aren't biddable. Dimension owner: ad_explicit_actions."""

    ST = (
        '| 搜索词 | 来源关键词 | 匹配 | 出价 | eCPC | 花费(USD) '
        '| 销售额(USD) | 订单 | ROAS | 建议 |\n'
        '|---|---|---|---|---|---|---|---|---|---|\n'
    )

    def test_scale_winners_skips_search_term_hold(self):
        report = (
            self.ST
            + '| wireless mouse | wireless mouse | Phrase | 0.25 | 0.25 | 20.75 | 1495.44 '
            '| 36 | 72.07 | 维持——已是定向词（Phrase），由定向表管理 |\n'
        )
        assert scale_winners_gate.check(report) is None

    def test_bid_floor_skips_search_term_trim_wording(self):
        # Even a 降-word inside a search-term cell must not trip the
        # ACOS bid-direction lock — there is no bid to lower here.
        report = (
            self.ST + '| good term | src kw | Broad | 0.30 | 0.30 | 10 | 100 '
            '| 5 | 10.0 | 维持——来源词承接（来源 Broad 词已降档） |\n'
        )
        assert bid_floor_gate.check(report) is None

    def test_targeting_table_still_enforced(self):
        report = (
            '| 关键词 | 出价 | ACOS | ROAS | 建议 |\n'
            '|---|---|---|---|---|\n'
            '| wireless mouse | 1.0 | 12% | 8.3 | 维持 |\n'
        )
        deny = scale_winners_gate.check(report)
        assert deny is not None  # high-ROAS bare hold on a real keyword


@pytest.mark.unit
class TestMissedRescue:
    """Inverse harvest check: 「来源词承接」 is false when the source
    is being cut; a converting term there is the missed rescue."""

    TGT = '| 关键词 | 出价 | ACOS | ROAS | 建议 |\n|---|---|---|---|---|\n'
    ST = (
        '| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 | 订单 | ROAS | 建议 |\n'
        '|---|---|---|---|---|---|---|---|\n'
    )

    def _block(self, tgt_row, st_row):
        return (
            '### 100000000000005 | charger | Manual\n\n'
            '#### Targeting\n'
            + self.TGT
            + tgt_row
            + '\n#### Search Terms\n'
            + self.ST
            + st_row
        )

    def test_missed_rescue_under_cut_source_flagged(self):
        report = self._block(
            '| charging station | 2.40 | 45% | 2.1 | '
            '降至 1.90（ACOS 45%>30 规则） |\n',
            '| usb charging station | charging station | Broad | 32 | 60 '
            '| 6 | 8.5 | 维持——来源 Broad 词承接，ROAS 8.5 |\n',
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None
        assert '承接说法不成立' in deny.reason

    def test_zero_order_hold_under_cut_source_passes(self):
        # No conversion → nothing to rescue; the hold may stand
        # (worded 观察, not 承接 — but even 承接 with 0 orders passes).
        report = self._block(
            '| charging station | 2.40 | 45% | 2.1 | '
            '降至 1.90（ACOS 45%>30 规则） |\n',
            '| some long tail | charging station | Broad | 3 | 5 '
            '| 0 | 0 | 维持观察（来源词已降档，量小） |\n',
        )
        assert explicit_actions_gate.check(report) is None

    def test_hold_under_healthy_source_still_passes(self):
        report = self._block(
            '| charging station | 2.40 | 22% | 6.1 | '
            '提高至 2.76（ROAS 6.1>5 规则） |\n',
            '| usb charging station | charging station | Broad | 32 | 60 '
            '| 6 | 8.5 | 维持——来源 Broad 词承接，ROAS 8.5 |\n',
        )
        assert explicit_actions_gate.check(report) is None

    def test_duplicate_keyword_match_types_disambiguated(self):
        # Live bug: phone stand Broad 降至 (cut) + phone stand Phrase 维持
        # in ONE campaign. Keyword-only keying let Phrase overwrite
        # Broad and falsely flagged the legitimate Broad-source rescue.
        tgt = (
            '| 关键词 | 匹配 | 出价 | ACOS | ROAS | 建议 |\n'
            '|---|---|---|---|---|---|\n'
            '| phone stand | Broad | 3.00 | 82.67% | 1.21 | '
            '降至 1.65 （ACOS 82.67%>30 规则） |\n'
            '| phone stand | Phrase | 3.00 | 0.00% | 0.00 | 维持 |\n'
        )
        st = (
            '| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 | 订单 | ROAS '
            '| 建议 |\n|---|---|---|---|---|---|---|---|\n'
            '| logitech wireless mouse | phone stand | Broad | 10 | 17 | 2 '
            '| 6.1 | 提取为定向词（Exact，建议出价 1.90——ROAS 6.1；'
            '来源 Broad 词已降档） |\n'
        )
        report = (
            '### 100000000000004 | wireless-mouse-019 | Manual\n\n'
            '#### Targeting\n' + tgt + '\n#### Search Terms\n' + st
        )
        assert explicit_actions_gate.check(report) is None

    def test_extract_bid_with_equals_and_range_passes(self):
        # Live form: 建议出价=2.13~2.42 (equals sign + range).
        tgt = (
            '| 关键词 | 匹配 | 出价 | ACOS | ROAS | 建议 |\n'
            '|---|---|---|---|---|---|\n'
            '| mouse kw | Broad | 3.00 | 82% | 1.2 | 降至 1.65（规则X） |\n'
        )
        st = (
            '| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 | 订单 | ROAS '
            '| 建议 |\n|---|---|---|---|---|---|---|---|\n'
            '| good term | mouse kw | Broad | 10 | 17 | 2 | 6.1 | '
            '提取为定向词（Exact，建议出价=2.13~2.42，ROAS 6.1；'
            '来源词已降档） |\n'
        )
        report = (
            '### 1234567890123 | c | Manual\n\n#### Targeting\n'
            + tgt
            + '\n#### Search Terms\n'
            + st
        )
        assert explicit_actions_gate.check(report) is None


@pytest.mark.unit
class TestNegateWasteSpend:
    """Zero-order search terms with meaningful spend must be 否定 —
    a hold leaves money burning (store owner: 有花费没有任何效果的，
    直接移除/禁用). Auto campaigns have no per-target row, so the
    search-term row is the only actionable place."""

    ST = (
        '| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 | 订单 | 销售额 '
        '| ROAS | 建议 |\n|---|---|---|---|---|---|---|---|---|\n'
    )

    def test_zero_order_high_spend_hold_flagged(self):
        report = (
            self.ST + '| B0EXAMPLE1 | B0EXAMPLE1 |  | 8.0 | 49.26 | 0 | 0 | 0 '
            '| 维持——已是定向词（Product Targeting），由定向表管理 |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None
        assert '零订单' in deny.reason and '否定' in deny.reason

    def test_negated_waste_passes(self):
        report = (
            self.ST + '| B0EXAMPLE1 | B0EXAMPLE1 |  | 8.0 | 49.26 | 0 | 0 | 0 '
            '| **否定该 ASIN**——花费 USD 49.26 零单 |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_low_spend_zero_order_hold_passes(self):
        # Below the waste floor: observation is fine.
        report = (
            self.ST + '| small term | src kw | Broad | 2.0 | 4.10 | 0 | 0 | 0 '
            '| 维持观察（样本小） |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_floor_override(self):
        # Below both defaults (spend 10 / clicks 10) → passes; a store
        # can tighten via notes.md.
        report = (
            self.ST + '| mid term | mid term |  | 5.0 | 8.00 | 0 | 0 | 0 '
            '| 维持——已是定向词（Auto），由定向表管理 |\n'
        )
        assert explicit_actions_gate.check(report) is None
        rules = resolve_rules('negate_waste_spend: 5')
        assert explicit_actions_gate.check(report, rules) is not None

    def test_clicks_floor_catches_low_spend_loser(self):
        report = (
            self.ST + '| keyboard term | kb kw | Exact | 21.0 | 9.00 | 0 | 0 '
            '| 0 | 维持——已是定向词（Exact），由定向表管理 |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and '点击' in deny.reason

    def test_targeting_zero_order_clicks_flagged(self):
        report = (
            '| 关键词 | 匹配 | 出价 | 点击 | 花费 | 订单 | ROAS | 建议 |\n'
            '|---|---|---|---|---|---|---|---|\n'
            '| electronics-cat | Category | 0.5 | 13 | 6.5 | 0 | 0 '
            '| 维持观察 |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and '定向关键词零订单' in deny.reason

    def test_hold_reason_mentioning_bid_verb_not_flagged(self):
        # 「维持观察（ROAS 4.44<5 不满足加投条件）」 is a hold; the 加投
        # inside the REASON must not read as a bid action.
        tgt = (
            '| 关键词 | 匹配 | 出价 | ACOS | ROAS | 建议 |\n'
            '|---|---|---|---|---|---|\n'
            '| gaming keyboard | Phrase | 0.90 | 22% | 4.44 | '
            '维持观察（ROAS 4.44<5 不满足加投条件） |\n'
        )
        st = (
            '| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 | 订单 | ROAS '
            '| 建议 |\n|---|---|---|---|---|---|---|---|\n'
            '| some term | gaming keyboard | Phrase | 5 | 4 | 1 | 4.6 | '
            '维持观察（ROAS 4.6<5 不满足加投条件） |\n'
        )
        report = (
            '### 1234567890123 | kb | Manual\n\n#### Targeting\n'
            + tgt
            + '\n#### Search Terms\n'
            + st
        )
        assert explicit_actions_gate.check(report) is None


class TestCitedNumberTruthfulness:
    """A ROAS/ACOS number cited inside a 建议 cell must match the row's
    OWN column — three review rounds found the clicks column quoted as
    ROAS (「ROAS 64.00>8」 on a ROAS-17.09 row) and a fabricated
    ACOS 0.0% on a 25.9% row."""

    NOON_HEAD = (
        '| 定向词 | 匹配 | 出价 | eCPC | 订单 | 销售额 | ROAS | 花费 '
        '| 展示 | 点击 | 建议 |\n'
        '|---|---|---|---|---|---|---|---|---|---|---|\n'
    )

    def test_clicks_quoted_as_roas_flagged(self):
        # Live bug: row ROAS 24.97, hold justified with 「ROAS 4.0<5」
        # — 4 is the 点击 column.
        report = self.NOON_HEAD + (
            '| wireless mouse | Exact | 0.80 | 0.80 | 2 | 80 | 24.97 | 3 '
            '| — | 4 | 维持观察（ROAS 4.0<5 不满足加投条件） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None
        assert '点击数不是 ROAS' in deny.reason
        assert '24.97' in deny.reason

    def test_clicks_quoted_as_roas_on_raise_flagged(self):
        # Fabricated citation on a RAISE row too: ROAS col 17.09,
        # cite says 64 (the clicks column).
        report = self.NOON_HEAD + (
            '| wireless mouse | Exact | 0.80 | 0.80 | 21 | 875 | 17.09 | 51 | — '
            '| 64 | 提高至 1.04 （ROAS 64.00>8 加投赢家规则） |\n'
        )
        assert explicit_actions_gate.check(report) is not None

    def test_matching_cite_passes(self):
        report = self.NOON_HEAD + (
            '| wireless mouse | Exact | 0.80 | 0.80 | 8 | 314 | 8.34 | 38 | — '
            '| 47 | 提高至 1.04（ROAS 8.34>5 加投赢家规则） |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_rounded_cite_passes(self):
        report = self.NOON_HEAD + (
            '| wireless mouse | Exact | 0.80 | 0.80 | 8 | 314 | 4.43 | 38 | — '
            '| 47 | 维持观察（ROAS 4.4<5 不满足加投条件） |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_fabricated_acos_flagged(self):
        report = (
            '| 关键词 | 出价 | ACOS | ROAS | 建议 |\n'
            '|---|---|---|---|---|\n'
            '| desk lamp | 0.9 | 25.9% | 3.9 | 维持（ACOS 0.0% 表现健康） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None
        assert 'ACOS' in deny.reason

    def test_source_keyword_cite_skipped(self):
        # 「来源 …」 cites the SOURCE keyword's metric, not this row's —
        # skipped, never compared.
        report = (
            '| 搜索词 | 来源关键词 | 匹配 | 点击 | 花费 | 订单 | ROAS '
            '| 建议 |\n|---|---|---|---|---|---|---|---|\n'
            '| phone stand | phone stand | Exact | 22 | 21.21 | 5 | 8.4 '
            '| 维持——来源 Exact 词承接，ROAS 12.0 |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_no_roas_column_no_check(self):
        report = (
            '| 关键词 | 出价 | 建议 |\n|---|---|---|\n'
            '| wireless mouse | 0.8 | 维持观察（ROAS 4.0<5 不满足加投条件） |\n'
        )
        assert explicit_actions_gate.check(report) is None


class TestDuplicateDrillBlocks:
    """The same campaign id heading twice means an appended stale twin —
    fixes applied to one copy silently leave the other (found by hand in
    review rounds: C_FAKE0001, C_FAKE0002, then four more)."""

    BLOCK = (
        '### C_FAKE0003 | mouse brand video | Brand Video\n\n'
        '**进度**: drilled 1/1 active (1 total, 1 pages)\n\n'
        '| 定向词 | 出价 | ROAS | 建议 |\n|---|---|---|---|\n'
        '| wireless mouse | 0.8 | 9 | 提高至 1.00（ROAS 9>5 加投赢家规则） |\n\n'
        '搜索词对账: 定向花费 USD 10 / 点击 5 = '
        '搜索词花费 USD 10 / 点击 5 (✓)\n\n'
    )

    def test_duplicate_block_flagged(self):
        report = (
            '## noon EG\n\n**进度**: drilled 1/1 active (1 total, 1 '
            'pages)\n\n' + self.BLOCK + self.BLOCK
        )
        deny = completeness_gate.check(report)
        assert deny is not None
        assert '重复' in deny.reason and 'C_FAKE0003' in deny.reason

    def test_single_block_not_flagged(self):
        report = (
            '## noon EG\n\n**进度**: drilled 1/1 active (1 total, 1 '
            'pages)\n\n' + self.BLOCK
        )
        deny = completeness_gate.check(report)
        assert deny is None or '重复' not in deny.reason


class TestSmallSampleHoldJustified:
    """output-spec optimizer bar: 数据太少 → 观察（写明原因）. A hold on
    a high-ROAS row citing sample size is a justified hold, not a
    parked winner."""

    HEAD = '| 关键词 | 出价 | 订单 | ROAS | 建议 |\n|---|---|---|---|---|\n'

    def test_small_sample_hold_passes(self):
        report = self.HEAD + (
            '| electronics/accessories (cat-5) | 0.40 | 1 | 30.79 | '
            '维持观察（ROAS 30.79 但仅 3 点击/1 单，样本不足） |\n'
        )
        assert scale_winners_gate.check(report) is None

    def test_bare_hold_still_flagged(self):
        report = self.HEAD + ('| wireless mouse | 0.80 | 5 | 12.0 | 维持 |\n')
        assert scale_winners_gate.check(report) is not None


class TestCpcFloor:
    """output-spec floor: a trim target must be ≥ actual CPC × 1.1 —
    below that the ad never wins another impression or locks a loss
    (round-3 AND round-4 reviews both found violations by hand)."""

    HEAD = (
        '| 关键词 | 匹配 | 出价 | eCPC | 订单 | ACOS | 建议 |\n'
        '|---|---|---|---|---|---|---|\n'
    )

    def test_trim_below_floor_flagged(self):
        report = self.HEAD + (
            '| usb-c cable | Broad | 3.00 | 2.83 | 2 | 45% | '
            '降至 2.25（ACOS 45%>30 规则） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and 'CPC' in deny.reason

    def test_trim_above_floor_passes(self):
        report = self.HEAD + (
            '| usb-c cable | Broad | 4.00 | 2.83 | 2 | 45% | '
            '降至 3.20（ACOS 45%>30 规则） |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_rounding_slack(self):
        # 3.20 vs floor 3.201 — within the half-cent representation
        # slack, not flagged; 3.19 (0.011 under) IS flagged (round-5
        # tightened the tolerance after 1.28-vs-1.287 slipped through).
        report = self.HEAD + (
            '| cable organizer | Phrase | 3.60 | 2.91 | 1 | 41% | '
            '降至 3.20（ACOS 41%>30 规则） |\n'
        )
        assert explicit_actions_gate.check(report) is None
        report = self.HEAD + (
            '| cable organizer | Phrase | 3.60 | 2.91 | 1 | 41% | '
            '降至 3.19（ACOS 41%>30 规则） |\n'
        )
        assert explicit_actions_gate.check(report) is not None

    def test_percent_trim_floor_checked(self):
        # Round-6: percent trims ARE floor-checked against the bid
        # column (2.00 −10% → 1.80 < 1.80×1.1 floor).
        report = self.HEAD + (
            '| wireless mouse | Broad | 2.00 | 1.80 | 1 | 45% | '
            '下调 10%（ACOS 45%>30 规则） |\n'
        )
        assert explicit_actions_gate.check(report) is not None


class TestPhantomMetricRaise:
    """ACOS on a zero-spend row is 0/0 — a raise citing
    「ACOS 0.0%<30 规则」 there fabricates the metric."""

    HEAD = (
        '| 关键词 | 出价 | 点击 | 花费 | 订单 | ACOS | 建议 |\n'
        '|---|---|---|---|---|---|---|\n'
    )

    def test_zero_data_rule_raise_flagged(self):
        report = self.HEAD + (
            '| desk lamp | 1.15 | 0 | 0 | 0 | 0.0% | '
            '提高至 1.38（ACOS 0.0%<30 规则） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and '零' in deny.reason

    def test_zero_data_hold_passes(self):
        report = self.HEAD + (
            '| desk lamp | 1.15 | 0 | 0 | 0 | — | 维持（无数据信号） |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_raise_with_real_data_passes(self):
        report = self.HEAD + (
            '| wireless mouse | 1.15 | 40 | 50 | 5 | 12.0% | '
            '提高至 1.38（ACOS 12%<30 规则） |\n'
        )
        assert explicit_actions_gate.check(report) is None


class TestFalseManagedClaim:
    """「已是定向词/由定向表管理」 must be true: the term has a LIVE row
    in this campaign's targeting table (round-4 F-4/F-5: CART labeled
    Exact with no row; negate-then-'managed' contradiction; Phrase
    derivatives labeled Exact)."""

    def _report(self, tgt_rows: str, st_rows: str) -> str:
        return (
            '### C_TEST1234567 | test | Manual\n\n'
            '| 定向词 | 匹配 | 出价 | 订单 | 建议 |\n'
            '|---|---|---|---|---|\n' + tgt_rows + '\n'
            '| 搜索词 | 来源关键词 | 匹配 | 点击 | 订单 | 建议 |\n'
            '|---|---|---|---|---|---|\n' + st_rows
        )

    def test_claim_with_no_targeting_row_flagged(self):
        report = self._report(
            '| wireless mouse | Phrase | 0.8 | 3 | 维持 |\n',
            '| CART | wireless mouse | Phrase | 5 | 1 | '
            '维持——已是定向词（Exact），由定向表管理 |\n',
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and '定向表无此行' in deny.reason

    def test_claim_over_cut_row_flagged(self):
        # F-4: targeting row negated, search term says "managed by
        # the targeting table" — orphaned claim.
        report = self._report(
            '| phone stand | Keyword Exact | 0.8 | 0 | 否定精确 |\n',
            '| Phone Stand | phone stand | Exact | 9 | 2 | '
            '维持——已是定向词（Exact），由定向表管理 |\n',
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and '已被停用' in deny.reason

    def test_claim_wrong_match_type_flagged(self):
        report = self._report(
            '| wireless mouse | Phrase | 0.8 | 3 | 维持 |\n',
            '| Wireless Mouse | wireless mouse | Phrase | 5 | 1 | '
            '维持——已是定向词（Exact），由定向表管理 |\n',
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and '无 exact 行' in deny.reason

    def test_true_claim_passes(self):
        report = self._report(
            '| wireless mouse | Exact | 0.8 | 3 | 提高至 1.0（ROAS 6>5 规则） |\n',
            '| wireless mouse | wireless mouse | Exact | 5 | 1 | '
            '维持——已是定向词（Exact），由定向表管理 |\n',
        )
        assert explicit_actions_gate.check(report) is None

    def test_noon_trailing_slash_and_case(self):
        # noon targeting cells carry a trailing slash; term case differs.
        report = self._report(
            '| phone stand/ | Keyword Exact | 0.8 | 3 | 维持 |\n',
            '| Phone Stand | phone stand | Exact | 5 | 1 | '
            '维持——已是定向词（Exact），由定向表管理 |\n',
        )
        assert explicit_actions_gate.check(report) is None


class TestWasteTrimNotEnough:
    """Store-owner rule: proven zero-converters are 移除/禁用 — a trim
    that still buys clicks is not a cut (round-4 F-2)."""

    HEAD = (
        '| 定向词 | 匹配 | 出价 | 点击 | 花费 | 订单 | 建议 |\n'
        '|---|---|---|---|---|---|---|\n'
    )

    def test_trim_on_waste_row_flagged(self):
        report = self.HEAD + (
            '| keyboard gaming | Phrase | 2.0 | 16 | 34.72 | 0 | '
            '下调至1.63（高点击零单） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and '零单' in deny.reason

    def test_pause_on_waste_row_passes(self):
        report = self.HEAD + (
            '| keyboard gaming | Phrase | 2.0 | 16 | 34.72 | 0 | '
            '暂停——16 点击零单 |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_trim_to_minimum_passes(self):
        report = self.HEAD + (
            '| keyboard gaming | Phrase | 2.0 | 16 | 34.72 | 0 | '
            '降至 0.30（最低出价——16 点击零单，保留排名信号） |\n'
        )
        assert explicit_actions_gate.check(report) is None


class TestMissingTargetingTable:
    """A drilled block with only a search-term table has no place for
    bid/pause decisions (round-4 F-7/F-9)."""

    ST = (
        '| 搜索词 | 点击 | 花费 | 订单 | 建议 |\n'
        '|---|---|---|---|---|\n'
        '| wireless mouse | 5 | 10 | 1 | 维持 |\n\n'
        '搜索词对账: 定向花费 USD 10 / 点击 5 = '
        '搜索词花费 USD 10 / 点击 5 (✓)\n\n'
    )

    def _combo(self, block_body: str) -> str:
        return (
            '## Amazon US\n\n**进度**: drilled 1/1 active (1 total, '
            '1 pages)\n\n### 1234567890123 | camp | Manual\n\n' + block_body
        )

    def test_search_only_block_flagged(self):
        deny = completeness_gate.check(self._combo(self.ST))
        assert deny is not None and '定向表' in deny.reason

    def test_block_with_targeting_table_passes(self):
        body = (
            '| 关键词 | 出价 | 订单 | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 0.8 | 3 | 维持 |\n\n' + self.ST
        )
        deny = completeness_gate.check(self._combo(body))
        assert deny is None or '定向表' not in deny.reason

    def test_no_data_block_exempt(self):
        body = '该活动Targets页无数据返回。\n\n' + self.ST
        deny = completeness_gate.check(self._combo(body))
        assert deny is None or '定向表' not in deny.reason


class TestComputedAcosCite:
    """noon tables have no ACOS column — a cited ACOS is verified
    against 花费/销售额 (round-5: 「ACOS 0.0%」 cited on rows whose
    computed ACOS is 21.9%/29.6%)."""

    HEAD = (
        '| 定向词 | 匹配 | 出价 | 订单 | 销售额 | ROAS | 花费 | 建议 |\n'
        '|---|---|---|---|---|---|---|---|\n'
    )

    def test_phantom_zero_acos_flagged(self):
        report = self.HEAD + (
            '| electronics/accessories-cat-4 | Category | 0.6 | 2 | 69.95 | 4.57 '
            '| 15.30 | 维持——ACOS 0.0%<30 不可下调 |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and 'ACOS' in deny.reason

    def test_correct_computed_acos_passes(self):
        # 15.30/69.95 = 21.9%
        report = self.HEAD + (
            '| electronics/accessories-cat-4 | Category | 0.6 | 2 | 69.95 | 4.57 '
            '| 15.30 | 维持——ACOS 21.9%<30 不可下调 |\n'
        )
        assert explicit_actions_gate.check(report) is None


class TestChineseDeferralPhrases:
    """「留待下次审计」 slipped past the excuse-phrase check (only the
    English 待下次 audit form was matched) — a report admitting it
    skipped in-window actives must be flagged, not accepted."""

    def test_liudai_xiaci_shenji_flagged(self):
        report = (
            '## Amazon US\n\n**进度**: drilled 25/25 active (25 total)\n\n'
            '> 另有 6 个 SB 活动在窗口内已 active，留待下次审计。\n\n'
            '| 关键词 | 出价 | 订单 | 建议 |\n|---|---|---|---|\n'
            '| wireless mouse | 0.8 | 3 | 维持 |\n'
        )
        deny = completeness_gate.check(report)
        assert deny is not None and '本次完成' in deny.reason


class TestHeaderTableNotRecommendations:
    """A header/summary table (id|name|…|status) has no 建议 column —
    its ✓/✗ status cell must not be read as a hold (live FP: campaign
    header row flagged as zero-order waste)."""

    def test_status_table_ignored(self):
        report = (
            '| id | name | type | 花费 | 销售额 | 订单 | ACOS | ROAS '
            '| status |\n|---|---|---|---|---|---|---|---|---|\n'
            '| 100000000000006 | wireless mouse 001 | Manual | 21.18 | 0.00 | 0 '
            '| N/A | 0.00 | ✓ |\n'
        )
        assert explicit_actions_gate.check(report) is None


class TestTrimDirectionAndPercentFloor:
    """Round-6: 「下调至 0.88」 on a 0.80 bid actually raises it
    (mislabeled direction); a percent trim's implied target must clear
    the CPC×1.1 floor like an absolute one."""

    HEAD = (
        '| 关键词 | 匹配 | 出价 | eCPC | 订单 | ACOS | 建议 |\n'
        '|---|---|---|---|---|---|---|\n'
    )

    def test_trim_above_current_bid_flagged(self):
        report = self.HEAD + (
            '| travel backpack | Phrase | 0.66 | 0.66 | 1 | 45% | '
            '下调至0.73（CPC×1.1 地板，ROAS=2.82<3） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and '实为提价' in deny.reason

    def test_real_trim_passes_direction(self):
        report = self.HEAD + (
            '| wireless mouse | Phrase | 4.00 | 2.83 | 2 | 45% | '
            '降至 3.20（ACOS 45%>30 规则） |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_percent_trim_below_floor_flagged(self):
        report = self.HEAD + (
            '| type c charger | Phrase | 0.66 | 0.66 | 2 | 298% | '
            '大幅降低出价 25%（ACOS 298%>100 浪费规则） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and 'CPC' in deny.reason

    def test_percent_trim_clearing_floor_passes(self):
        report = self.HEAD + (
            '| wireless mouse | Phrase | 4.00 | 2.83 | 2 | 45% | '
            '下调 10%（ACOS 45%>30 规则） |\n'
        )
        assert explicit_actions_gate.check(report) is None


class TestComputedCpcFloor:
    """Tables without a CPC column still floor-check trims — CPC is
    computed from 花费/点击 (round-7: 降至0.60 on a 6.40/8-click row,
    floor 0.88, escaped because the table had no eCPC column)."""

    HEAD = (
        '| 定向词 | 匹配 | 出价 | 花费 | 订单 | 销售额 | ROAS | 点击 '
        '| 建议 |\n|---|---|---|---|---|---|---|---|---|\n'
    )

    def test_trim_below_computed_floor_flagged(self):
        report = self.HEAD + (
            '| keyboard for playstation | Phrase | 0.80 | 6.40 | 1 | 5 '
            '| 0.78 | 8 | 降至0.60（ROAS 0.78<3） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and 'CPC' in deny.reason

    def test_trim_above_computed_floor_passes(self):
        # spend 4.00 / 8 clicks → CPC 0.50, floor 0.55; 降至0.60 OK
        # (bid 0.80 so direction is also fine).
        report = self.HEAD + (
            '| keyboard for playstation | Phrase | 0.80 | 4.00 | 1 | 5 '
            '| 1.25 | 8 | 降至0.60（ACOS 80%>30 规则） |\n'
        )
        assert explicit_actions_gate.check(report) is None


class TestExtractionJustificationCitesBidRule:
    """An extraction rec citing 加投赢家规则 in its justification is not
    a dimension error — it must reach the harvest-policy check, which
    flags it when the source is healthy (the dimension flag previously
    MASKED that violation)."""

    def _report(self, src_action: str) -> str:
        return (
            '### 1234567890123 | charger | Manual\n\n'
            '| 关键词 | 匹配 | 出价 | 订单 | 建议 |\n|---|---|---|---|---|\n'
            f'| fast charger | Phrase | 3.30 | 3 | {src_action} |\n\n'
            '| 搜索词 | 来源关键词 | 匹配 | 点击 | 订单 | 建议 |\n'
            '|---|---|---|---|---|---|\n'
            '| authentic fast charger | fast charger | Phrase | 3 | 1 | '
            '提取为定向词（Exact，建议出价 3.08——ROAS 9.94>5 加投赢家规则） |\n'
        )

    def test_extraction_under_healthy_source_flagged_not_dimension(self):
        deny = explicit_actions_gate.check(self._report('维持（否定后回归）'))
        assert deny is not None
        assert '来源关键词健康' in deny.reason
        assert '维度错误' not in deny.reason

    def test_extraction_under_cut_source_passes(self):
        assert (
            explicit_actions_gate.check(
                self._report('降至 2.64（ACOS 109%>30 规则）')
            )
            is None
        )


class TestSplitActionHead:
    """A 维持 head hiding 建议暂停 in the same cell is a decision not
    made — the executor reads the head (round-13: pattern recurred
    wherever floor-lock + bad ROAS co-occurred)."""

    HEAD = (
        '| 关键词 | 匹配 | 出价 | 订单 | ACOS | 建议 |\n'
        '|---|---|---|---|---|---|\n'
    )

    def test_buried_pause_flagged(self):
        report = self.HEAD + (
            '| charger | Exact | 3.78 | 2 | 34% | 维持（出价 3.78 低于'
            '地板 3.86，无法下调）；ACOS 34% ROAS 2.94，建议暂停定向词 |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and '动作头' in deny.reason

    def test_clean_pause_head_passes(self):
        report = self.HEAD + (
            '| charger | Exact | 3.78 | 2 | 34% | 暂停定向词（出价已低于'
            'CPC×1.1 地板且 ACOS 34%>30 亏损） |\n'
        )
        assert explicit_actions_gate.check(report) is None

    def test_plain_hold_passes(self):
        report = self.HEAD + (
            '| wireless mouse | Exact | 0.80 | 3 | 12% | 维持（ACOS 12%<30 '
            '不可下调，ROAS 4.4<5 不满足加投条件） |\n'
        )
        assert explicit_actions_gate.check(report) is None


class TestSplitHeadReferenceNotFlagged:
    """「来源定向词已建议暂停」 references ANOTHER row's already-made
    decision — not a buried decision on this row."""

    def test_reference_to_source_pause_passes(self):
        report = (
            '| 搜索词 | 来源关键词 | 匹配 | 点击 | 订单 | 建议 |\n'
            '|---|---|---|---|---|---|\n'
            '| usb-c cable | phone stand | Broad | 4 | 1 | '
            '维持观察——ROAS 2.39<3.33 样本不足，来源定向词已建议暂停，'
            '流量将随之停止 |\n'
        )
        assert explicit_actions_gate.check(report) is None


class TestNegateSearchTermOnTargetingRow:
    """否定搜索词 in a targeting table is wrong-dimension — a keyword
    can only be 暂停, not search-term-negated (round-14)."""

    HEAD = (
        '| 关键词 | 匹配 | 出价 | 点击 | 订单 | 建议 |\n'
        '|---|---|---|---|---|---|\n'
    )

    def test_negate_on_targeting_flagged(self):
        report = self.HEAD + (
            '| back pack | Phrase | 0.80 | 45 | 0 | 否定搜索词（45点击零单） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and '维度错误' in deny.reason

    def test_pause_on_targeting_passes(self):
        report = self.HEAD + (
            '| back pack | Phrase | 0.80 | 45 | 0 | 暂停定向词（45点击零单） |\n'
        )
        assert explicit_actions_gate.check(report) is None


class TestRaiseBelowFloor:
    """A 提高至 target still below CPC×1.1 is incoherent — even raised,
    the bid can't clear its own cost floor (round-16: SB Video rows
    where CPC > current bid)."""

    HEAD = (
        '| 关键词 | 匹配 | 出价 | eCPC | 订单 | ROAS | 建议 |\n'
        '|---|---|---|---|---|---|---|\n'
    )

    def test_raise_below_cpc_floor_flagged(self):
        report = self.HEAD + (
            '| usb-c cable | Exact | 0.90 | 1.21 | 2 | 6.0 | '
            '提高至 1.12（ROAS 6>5 加投赢家规则） |\n'
        )
        deny = explicit_actions_gate.check(report)
        assert deny is not None and 'CPC' in deny.reason

    def test_raise_above_floor_passes(self):
        report = self.HEAD + (
            '| wireless mouse | Exact | 0.90 | 0.80 | 5 | 9.0 | '
            '提高至 1.17（ROAS 9>5 加投赢家规则） |\n'
        )
        assert explicit_actions_gate.check(report) is None


class TestAutoGroupPauseVsCarry:
    """Owner-found logic bug: an auto target group paused while its
    search terms claim 维持——auto 定向承接 — pausing the group orphans
    them (round-18)."""

    def _report(self, close_match_action: str) -> str:
        return (
            '### 100000000000001 | mouse auto | Auto\n\n'
            '| 关键词 | 匹配 | 出价 | 点击 | 订单 | 建议 |\n'
            '|---|---|---|---|---|---|\n'
            '| complements | 自动 | 3.0 | 0 | 0 | 维持 |\n'
            f'| close-match | 自动 | 2.5 | 80 | 13 | {close_match_action} |\n\n'
            '| 搜索词 | 来源关键词 | 匹配 | 点击 | 订单 | 建议 |\n'
            '|---|---|---|---|---|---|\n'
            '| wireless mouse | wireless mouse | | 25 | 5 | 维持——auto 定向承接 |\n'
            '| usb-c cable | usb-c cable | | 5 | 0 | 否定搜索词 |\n'
        )

    def test_auto_pause_with_carry_flagged(self):
        deny = explicit_actions_gate.check(
            self._report('暂停该自动定向（ACOS 60%>30 亏损）')
        )
        assert deny is not None and 'auto' in deny.reason.lower()

    def test_auto_kept_with_carry_passes(self):
        # close-match maintained → 承接 is valid, no contradiction
        deny = explicit_actions_gate.check(
            self._report('维持（剪枝零单浪费词后观察）')
        )
        assert deny is None or 'auto 定向承接' not in deny.reason
