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
    markdown_format as md_format_gate,
    record_attempt,
    reset_attempts,
    result_language as language_gate,
)


@pytest.fixture(autouse=True)
def _clear_attempts():
    _attempts.clear()
    yield
    _attempts.clear()


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
