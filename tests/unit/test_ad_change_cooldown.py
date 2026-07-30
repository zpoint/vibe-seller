"""A target changed days ago must be held, and the report must say why.

The history is the per-campaign TSVs themselves — one file per campaign
in a git-backed workspace, with the columns execution writes onto the
rows it changed. No separate ledger, so nothing can drift out of sync
with the data it annotates.
"""

from datetime import date
from pathlib import Path

import pytest

from app.ai.stop_gates import ad_change_cooldown as cooldown
from app.ai.stop_gates.ad_rules import DEFAULT_RULES, resolve_rules

TODAY = date(2026, 8, 10)

HEADER = (
    'ad_group\ttarget\tmatch_type\tstate\tbid\tcurrency\tclicks\tspend\t'
    'orders\tsales\tacos\troas\tsuggestion\tapplied_action\tapplied_at\t'
    'previous_bid\n'
)


def _tsv(root: Path, name: str, rows: list[str]) -> Path:
    d = root / 'amazon' / 'SA'
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(HEADER + ''.join(rows), encoding='utf-8')
    return p


def _row(target, applied='', when='', prev=''):
    return (
        f'grp\t{target}\tExact\tenabled\t2.10\tSAR\t40\t80.00\t4\t400.00\t'
        f'20%\t5.00\t维持\t{applied}\t{when}\t{prev}\n'
    )


def _report(rows: str) -> str:
    return (
        '# Ad audit\n\n## amazon SA\n'
        '**进度**: drilled 1/1 active (1 total, 1 pages)\n\n'
        '| 定向词 | 匹配 | 出价 | 点击 | 花费 | 订单 | ROAS | 建议 |\n'
        '|---|---|---|---|---|---|---|---|\n' + rows
    )


@pytest.mark.unit
class TestRecentChanges:
    def test_reads_applied_columns_from_the_tsv(self, tmp_path):
        _tsv(
            tmp_path,
            'c1.tsv',
            [_row('widget red', '提高出价', '2026-08-08', '2.00')],
        )
        got = cooldown.recent_changes(tmp_path, 7, TODAY)
        assert got == {'widget red': ('提高出价', 2)}

    def test_ignores_a_change_older_than_the_window(self, tmp_path):
        _tsv(
            tmp_path,
            'c1.tsv',
            [_row('widget red', '提高出价', '2026-07-01', '2.00')],
        )
        assert cooldown.recent_changes(tmp_path, 7, TODAY) == {}

    def test_ignores_rows_that_were_never_applied(self, tmp_path):
        # A suggestion is not a change; only execution writes these.
        _tsv(tmp_path, 'c1.tsv', [_row('widget red')])
        assert cooldown.recent_changes(tmp_path, 7, TODAY) == {}

    def test_keeps_the_most_recent_change_for_a_target(self, tmp_path):
        _tsv(
            tmp_path,
            'c1.tsv',
            [_row('widget red', '提高出价', '2026-08-04', '2.00')],
        )
        _tsv(
            tmp_path,
            'c2.tsv',
            [_row('widget red', '降低出价', '2026-08-09', '2.10')],
        )
        assert cooldown.recent_changes(tmp_path, 7, TODAY)['widget red'][1] == 1

    def test_an_unreadable_date_is_skipped_not_treated_as_today(self, tmp_path):
        # Treating it as "changed today" would freeze the target forever.
        _tsv(
            tmp_path, 'c1.tsv', [_row('widget red', '提高出价', 'last tuesday')]
        )
        assert cooldown.recent_changes(tmp_path, 7, TODAY) == {}

    def test_missing_directory_is_not_an_error(self, tmp_path):
        assert cooldown.recent_changes(tmp_path / 'nope', 7, TODAY) == {}


@pytest.mark.unit
class TestCooldownGate:
    def _root(self, tmp_path, when='2026-08-08'):
        _tsv(tmp_path, 'c1.tsv', [_row('widget red', '提高出价', when, '2.00')])
        return tmp_path

    def test_moving_a_freshly_changed_target_is_denied(self, tmp_path):
        rows = '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | 下调至 1.80 |\n'
        deny = cooldown.check(
            _report(rows), ads_root=self._root(tmp_path), today=TODAY
        )
        assert deny is not None
        assert '冷却期' in deny.reason
        assert 'widget red' in deny.reason

    def test_holding_it_passes(self, tmp_path):
        rows = '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | 维持 |\n'
        assert (
            cooldown.check(
                _report(rows), ads_root=self._root(tmp_path), today=TODAY
            )
            is None
        )

    def test_a_hold_that_names_the_change_passes(self, tmp_path):
        # This is the recommendation we actually want to see.
        rows = (
            '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | '
            '维持（2 天前刚提过价，冷却期未满，等满一周数据再判断） |\n'
        )
        assert (
            cooldown.check(
                _report(rows), ads_root=self._root(tmp_path), today=TODAY
            )
            is None
        )

    def test_an_untouched_target_may_still_be_adjusted(self, tmp_path):
        rows = (
            '| widget blue | Exact | 1.00 | 9 | 9.00 | 0 | — | 下调至 0.80 |\n'
        )
        assert (
            cooldown.check(
                _report(rows), ads_root=self._root(tmp_path), today=TODAY
            )
            is None
        )

    def test_outside_the_window_adjusting_is_fine_again(self, tmp_path):
        rows = '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | 下调至 1.80 |\n'
        root = self._root(tmp_path, when='2026-07-01')
        assert cooldown.check(_report(rows), ads_root=root, today=TODAY) is None

    def test_the_window_is_per_store_overridable(self, tmp_path):
        rows = '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | 下调至 1.80 |\n'
        root = self._root(tmp_path)
        # A seller who tunes daily sets a 1-day window; 2 days ago is then
        # outside it and the adjustment is allowed.
        fast = resolve_rules('change_cooldown_days: 1')
        assert (
            cooldown.check(
                _report(rows), None, fast, ads_root=root, today=TODAY
            )
            is None
        )
        # The default holds it.
        assert cooldown.check(
            _report(rows), None, DEFAULT_RULES, ads_root=root, today=TODAY
        )

    def test_zero_window_disables_the_gate(self, tmp_path):
        rows = '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | 下调至 1.80 |\n'
        off = dict(DEFAULT_RULES, change_cooldown_days=0.0)
        assert (
            cooldown.check(
                _report(rows),
                None,
                off,
                ads_root=self._root(tmp_path),
                today=TODAY,
            )
            is None
        )

    def test_no_history_means_no_opinion(self, tmp_path):
        rows = '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | 下调至 1.80 |\n'
        assert (
            cooldown.check(_report(rows), ads_root=tmp_path, today=TODAY)
            is None
        )


@pytest.mark.unit
class TestAgentWrittenPlaceholders:
    """A real execution filled untouched rows with ``-``, not blanks.

    Those rows were skipped only because the date failed to parse, so a
    ``-`` action beside a valid date would have registered as an applied
    change literally named "-", freezing that target for a week.
    """

    def test_dash_action_is_not_an_applied_change(self, tmp_path):
        _tsv(
            tmp_path,
            'c1.tsv',
            [_row('widget red', '-', '2026-08-09', '-')],
        )
        assert cooldown.recent_changes(tmp_path, 7, TODAY) == {}

    def test_other_absent_markers_too(self, tmp_path):
        for marker in ('—', '–', 'n/a', 'none', 'NULL', '--'):
            _tsv(tmp_path, 'c1.tsv', [_row('widget red', marker, '2026-08-09')])
            assert cooldown.recent_changes(tmp_path, 7, TODAY) == {}, marker

    def test_a_real_action_beside_dash_rows_still_registers(self, tmp_path):
        # The shape an execution actually produces: one changed row among
        # many placeholder rows.
        _tsv(
            tmp_path,
            'c1.tsv',
            [
                _row('widget blue', '-', '-', '-'),
                _row('widget red', 'raise', '2026-08-09', '2.80'),
                _row('widget green', '-', '-', '-'),
            ],
        )
        assert cooldown.recent_changes(tmp_path, 7, TODAY) == {
            'widget red': ('raise', 1)
        }


@pytest.mark.unit
class TestClockSkew:
    """A stamp dated AHEAD of our clock is timezone skew, not corruption.

    The agent writes the seller's LOCAL date; this process compares in
    UTC. For the hours each day that local runs ahead (UTC+8 for a
    Gulf/Asia seller) "today" arrives as tomorrow. Observed live: a change
    applied minutes earlier read as -1 days and was discarded, silently
    switching the cooldown off for exactly the window it was needed in.
    Every earlier test pinned `today` to match its fixture, so none saw it.
    """

    def test_a_change_stamped_tomorrow_still_counts_as_today(self, tmp_path):
        _tsv(
            tmp_path,
            'c1.tsv',
            [_row('widget red', '提高出价', '2026-08-11', '2.00')],
        )
        got = cooldown.recent_changes(tmp_path, 7, TODAY)  # TODAY = 08-10
        assert got == {'widget red': ('提高出价', 0)}

    def test_it_is_still_protected_by_the_gate(self, tmp_path):
        _tsv(
            tmp_path,
            'c1.tsv',
            [_row('widget red', '提高出价', '2026-08-11', '2.00')],
        )
        rows = '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | 下调至 1.80 |\n'
        assert cooldown.check(_report(rows), ads_root=tmp_path, today=TODAY)

    def test_an_implausible_future_date_is_still_ignored(self, tmp_path):
        # A typo'd year must not freeze a target indefinitely.
        _tsv(
            tmp_path,
            'c1.tsv',
            [_row('widget red', '提高出价', '2126-08-10', '2.00')],
        )
        assert cooldown.recent_changes(tmp_path, 7, TODAY) == {}

    def test_ordinary_past_dates_are_unaffected(self, tmp_path):
        _tsv(
            tmp_path,
            'c1.tsv',
            [_row('widget red', '提高出价', '2026-08-08', '2.00')],
        )
        assert cooldown.recent_changes(tmp_path, 7, TODAY)['widget red'][1] == 2
