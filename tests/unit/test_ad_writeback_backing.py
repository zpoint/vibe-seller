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
