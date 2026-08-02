"""A TSV may not claim a change the execution log does not back.

The TSV is what the NEXT audit reads: an applied_at inside the cooldown
window freezes that target. A row claiming a change that never landed
silently suppresses a real adjustment, and the next report gives no clue
why. Observed live: the agent wrote the columns BEFORE driving the
browser — it worked only because the browser step happened to succeed.
"""

from datetime import date

import pytest

from app.ai.stop_gates import ad_writeback_backing as wb

TODAY = date(2026, 8, 10)

HEADER = (
    'ad_group\ttarget\tmatch_type\tstate\tbid\tcurrency\tclicks\tspend\t'
    'orders\tsales\tacos\troas\tsuggestion\tapplied_action\tapplied_at\t'
    'previous_bid\n'
)


def _tsv(root, rows):
    d = root / 'amazon' / 'SA'
    d.mkdir(parents=True, exist_ok=True)
    (d / '100000000001.tsv').write_text(
        HEADER + ''.join(rows), encoding='utf-8'
    )


def _row(target, applied='', when='', prev=''):
    return (
        f'grp\t{target}\tExact\tenabled\t2.90\tSAR\t40\t80.00\t4\t400.00\t'
        f'20%\t5.00\t维持\t{applied}\t{when}\t{prev}\n'
    )


# The action-table dialect the live agents emit.
LOG_WITH_WIDGET = """## Campaign 100000000001

### 出价
| 提价 | widget red (Exact) | 2.80 | 2.90 | ✅ |
"""

LOG_EMPTY = '## Campaign 100000000001\n\n(no actions applied)\n'


@pytest.mark.unit
class TestWriteBackBacking:
    def test_a_backed_write_back_passes(self, tmp_path):
        _tsv(tmp_path, [_row('widget red', 'raise', '2026-08-10', '2.80')])
        assert (
            wb.check(
                '', ads_root=tmp_path, log_text=LOG_WITH_WIDGET, today=TODAY
            )
            is None
        )

    def test_an_unbacked_write_back_is_denied(self, tmp_path):
        # Written before the browser step — the log has nothing for it.
        _tsv(tmp_path, [_row('widget red', 'raise', '2026-08-10', '2.80')])
        deny = wb.check('', ads_root=tmp_path, log_text=LOG_EMPTY, today=TODAY)
        assert deny is not None
        assert 'widget red' in deny.reason
        # The refusal must say what to do instead.
        assert 'applied_* 留空' in deny.reason or '留空' in deny.reason

    def test_untouched_rows_are_not_graded(self, tmp_path):
        _tsv(tmp_path, [_row('widget red'), _row('widget blue', '-', '-', '-')])
        assert (
            wb.check('', ads_root=tmp_path, log_text=LOG_EMPTY, today=TODAY)
            is None
        )

    def test_earlier_runs_are_history_not_this_run_s_claim(self, tmp_path):
        # Backed by a log that no longer exists; grading it would deny
        # every future task for a change made weeks ago.
        _tsv(tmp_path, [_row('widget red', 'raise', '2026-07-01', '2.80')])
        assert (
            wb.check('', ads_root=tmp_path, log_text=LOG_EMPTY, today=TODAY)
            is None
        )

    def test_no_log_means_no_opinion(self, tmp_path):
        _tsv(tmp_path, [_row('widget red', 'raise', '2026-08-10', '2.80')])
        assert wb.check('', ads_root=tmp_path, log_text='', today=TODAY) is None

    def test_missing_ads_dir_is_not_an_error(self, tmp_path):
        assert (
            wb.check(
                '',
                ads_root=tmp_path / 'nope',
                log_text=LOG_WITH_WIDGET,
                today=TODAY,
            )
            is None
        )


@pytest.mark.unit
class TestLocalisedKeywordText:
    """The console appends a translation; the TSV does not.

    Live, the log carried the keyword with a CJK translation appended
    while the TSV held the plain term. Compared verbatim, the gate denied
    a change that had demonstrably landed in the account — a false
    refusal of real work, which is the worst failure mode for a backstop.
    """

    LOG_LOCALISED = """## Campaign 100000000001

### 出价
| 提价 | widget red 红色小工具 (Exact) | 2.80 | 2.90 | ✅ |
"""

    def test_a_translated_log_entry_still_backs_the_plain_tsv_row(
        self, tmp_path
    ):
        _tsv(tmp_path, [_row('widget red', 'raise', '2026-08-10', '2.80')])
        assert (
            wb.check(
                '', ads_root=tmp_path, log_text=self.LOG_LOCALISED, today=TODAY
            )
            is None
        )

    def test_an_unrelated_keyword_is_still_caught(self, tmp_path):
        # Tolerating the suffix must not turn the gate into a no-op.
        _tsv(tmp_path, [_row('widget blue', 'raise', '2026-08-10', '2.80')])
        deny = wb.check(
            '', ads_root=tmp_path, log_text=self.LOG_LOCALISED, today=TODAY
        )
        assert deny is not None
        assert 'widget blue' in deny.reason


@pytest.mark.unit
class TestReportMatchesTheRecord:
    """After an apply, the report's own table must show the NEW bid.

    Live: an execution raised a bid and appended an "Apply 结果" section
    but left the targeting table showing the pre-change value. The review
    console renders that table, so the next reviewer saw a bid the account
    no longer had — and typing the value they could see produced a
    `lower 2.8 -> 2.8` no-op. A review surface that shows stale numbers
    is worse than one that shows none.
    """

    def _report(self, shown_bid):
        return (
            '# 广告优化建议 — acme — 2026-08-10\n\n## amazon SA\n'
            '**进度**: drilled 1/1 active (1 total, 1 pages)\n\n'
            '| 定向词 | 匹配 | 出价 | 点击 | 花费 | 订单 | ROAS | 建议 |\n'
            '|---|---|---|---|---|---|---|---|\n'
            f'| widget red | Exact | {shown_bid} | 40 | 80.00 | 4 | 5.00 | 维持 |\n'
        )

    def test_a_stale_table_is_flagged(self, tmp_path):
        _tsv(tmp_path, [_row('widget red', 'raise', '2026-08-10', '2.80')])
        # TSV row carries bid 2.90 (see _row); the report still says 2.80.
        stale = wb.report_bid_mismatches(
            self._report('2.80'), tmp_path, today=TODAY
        )
        assert stale and 'widget red' in stale[0]

    def test_an_updated_table_passes(self, tmp_path):
        _tsv(tmp_path, [_row('widget red', 'raise', '2026-08-10', '2.80')])
        assert (
            wb.report_bid_mismatches(
                self._report('2.90'), tmp_path, today=TODAY
            )
            == []
        )

    def test_rows_we_did_not_apply_are_not_graded(self, tmp_path):
        _tsv(tmp_path, [_row('widget red')])
        assert (
            wb.report_bid_mismatches(
                self._report('1.00'), tmp_path, today=TODAY
            )
            == []
        )
