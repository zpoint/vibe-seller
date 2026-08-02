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


def _key(target: str, campaign: str = 'c1', layer: str = 'target'):
    """History is keyed by the OBJECT, not by a bare string.

    Same campaign, same layer, same target. The string alone matched a
    keyword's bid change against a same-named search term in another
    campaign — different objects that merely read alike — and the
    resulting denial named only the string, so the agent could never
    find the row it was being blocked on.
    """
    return (campaign, layer, target)


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
        assert got == {_key('widget red'): ('提高出价', 2)}

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

    def test_two_campaigns_are_two_different_keywords(self, tmp_path):
        # The same keyword string lives in many campaigns, each with its
        # own bid and its own data. Moving it in one says nothing about
        # the other, so these must NOT collapse into one entry — merging
        # them is what let a single change freeze rows in campaigns it
        # was never applied to.
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
        got = cooldown.recent_changes(tmp_path, 7, TODAY)
        assert got == {
            _key('widget red', 'c1'): ('提高出价', 6),
            _key('widget red', 'c2'): ('降低出价', 1),
        }

    def test_keeps_the_most_recent_change_for_a_target(self, tmp_path):
        _tsv(
            tmp_path,
            'c1.tsv',
            [
                _row('widget red', '提高出价', '2026-08-04', '2.00'),
                _row('widget red', '降低出价', '2026-08-09', '2.10'),
            ],
        )
        assert (
            cooldown.recent_changes(tmp_path, 7, TODAY)[_key('widget red')][1]
            == 1
        )

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
            _key('widget red'): ('raise', 1)
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
        assert got == {_key('widget red'): ('提高出价', 0)}

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
        assert (
            cooldown.recent_changes(tmp_path, 7, TODAY)[_key('widget red')][1]
            == 2
        )


@pytest.mark.unit
class TestChangeRecordLocation:
    """The change record has lived in two trees, because the platform
    said both: every task's system prompt routes durable run data to
    ``store-data/<slug>/`` while the ads spec named ``stores/<slug>/ads/``.

    Observed live: consecutive executions of the SAME campaign wrote to
    different trees, so the newest change sat where the gate was not
    looking and the cooldown read a stale entry — the exact failure it
    exists to prevent. The schemas diverged with the paths too: the key
    column is ``target`` in one and ``keyword`` in the other.
    """

    def _write(self, root, key_col, target, action, when, bid):
        d = root / 'amazon' / 'SA'
        d.mkdir(parents=True, exist_ok=True)
        (d / 'c1.tsv').write_text(
            f'{key_col}\tbid\tapplied_action\tapplied_at\tprevious_bid\n'
            f'{target}\t{bid}\t{action}\t{when}\t2.00\n',
            encoding='utf-8',
        )

    def test_reads_a_record_keyed_on_keyword_not_target(self, tmp_path):
        self._write(
            tmp_path, 'keyword', 'widget red', 'raise', '2026-08-09', '2.90'
        )
        assert cooldown.recent_changes(tmp_path, 7, TODAY) == {
            _key('widget red'): ('raise', 1)
        }

    def test_reads_a_record_keyed_on_target(self, tmp_path):
        self._write(
            tmp_path, 'target', 'widget red', 'raise', '2026-08-09', '2.90'
        )
        assert cooldown.recent_changes(tmp_path, 7, TODAY) == {
            _key('widget red'): ('raise', 1)
        }

    def test_a_stale_tree_cannot_mask_a_fresher_one(self, tmp_path):
        # The live failure: an older entry in the tree the gate read hid a
        # newer change written to the other tree.
        old_tree, new_tree = tmp_path / 'stores', tmp_path / 'store-data'
        self._write(
            old_tree, 'target', 'widget red', 'revert', '2026-08-04', '2.80'
        )
        self._write(
            new_tree, 'keyword', 'widget red', 'raise', '2026-08-10', '2.90'
        )
        merged: dict = {}
        for root in (new_tree, old_tree):
            for k, v in cooldown.recent_changes(root, 7, TODAY).items():
                if k not in merged or v[1] < merged[k][1]:
                    merged[k] = v
        assert merged == {_key('widget red'): ('raise', 0)}


# The shape the output spec actually produces. Note the first column:
# `ad_group` in the targeting table, `search_term` in the search-term
# one — in NEITHER is it the thing the row is about. Every test above
# uses an older shape where the target happened to sit first, which is
# why a gate that assumed column 1 looked correct for a year.
def _real_report(target_rows: str = '', search_term_rows: str = '') -> str:
    out = '# Ad audit\n\n## amazon SA\n**进度**: drilled 1/1 active (1 total, 1 pages)\n\n'
    out += '### c1 | widgets manual KSA | Manual\n\n'
    if target_rows:
        out += (
            '| ad_group | target | match | state | bid (SAR) | clicks | '
            'spend | orders | sales | ACOS | ROAS | 建议 |\n'
            '|---|---|---|---|---|---|---|---|---|---|---|---|\n'
            + target_rows
            + '\n'
        )
    if search_term_rows:
        out += (
            '| search_term | ad_group | source_keyword | match | clicks | '
            'spend | orders | sales | ROAS | 建议 |\n'
            '|---|---|---|---|---|---|---|---|---|---|\n'
            + search_term_rows
            + '\n'
        )
    return out


@pytest.mark.unit
class TestTheRealReportShape:
    """Reproduces a live run that looped until the agent gave up.

    A bid revert on the keyword "widget red" in one campaign produced a
    denial naming only `widget red`. The agent fixed the targeting row it
    could see and was denied again — because the row actually being
    flagged was a SEARCH TERM of the same name, and same-named keywords
    in three other campaigns. Nothing in the message said which, so there
    was no move that satisfied it. After fifteen submissions it called
    set_task_error to escape.
    """

    def _root(self, tmp_path, name='c1.tsv'):
        _tsv(
            tmp_path, name, [_row('widget red', 'revert', '2026-08-08', '2.90')]
        )
        return tmp_path

    def test_a_bid_move_on_the_cooled_target_is_still_denied(self, tmp_path):
        # The protection itself — and it never fired before, because the
        # gate was reading the ad-group name as the target.
        rows = (
            '| grp | widget red | Broad | enabled | 2.00 | 100 | 200.00 | '
            '20 | 800.00 | 25.00% | 4.00 | 下调至 2.20（ACOS 偏高） |\n'
        )
        deny = cooldown.check(
            _real_report(target_rows=rows),
            ads_root=self._root(tmp_path),
            today=TODAY,
        )
        assert deny is not None, 'the cooldown must fire on the real shape'

    def test_the_denial_says_which_campaign_and_which_layer(self, tmp_path):
        # Without this the agent cannot find the row, retries blind, and
        # loops until it gives up. A gate that cannot be satisfied is
        # worse than no gate.
        rows = (
            '| grp | widget red | Broad | enabled | 2.00 | 100 | 200.00 | '
            '20 | 800.00 | 25.00% | 4.00 | 下调至 2.20 |\n'
        )
        deny = cooldown.check(
            _real_report(target_rows=rows),
            ads_root=self._root(tmp_path),
            today=TODAY,
        )
        assert deny is not None
        gap = ' '.join(deny.gaps)
        assert 'c1' in gap, f'no campaign in the denial: {gap}'
        assert '定向层' in gap, f'no layer in the denial: {gap}'
        assert 'widget red' in gap

    def test_a_hold_naming_the_change_passes_on_the_real_shape(self, tmp_path):
        rows = (
            '| grp | widget red | Broad | enabled | 2.00 | 100 | 200.00 | '
            '20 | 800.00 | 25.00% | 4.00 | '
            '维持（2 天前刚 revert 过 bid 2.20 → 2.00，7 天冷却期未满） |\n'
        )
        assert (
            cooldown.check(
                _real_report(target_rows=rows),
                ads_root=self._root(tmp_path),
                today=TODAY,
            )
            is None
        )

    def test_a_search_term_is_not_frozen_by_a_keyword_bid_change(
        self, tmp_path
    ):
        # Different layer, different object, different action: negating
        # the query "widget red" is not re-moving the keyword's bid.
        rows = (
            '| widget red | grp | widget red | Broad | 5.0 | 10.00 | 0.0 | '
            '0.00 | 0.00 | 否定关键词（精准） |\n'
        )
        assert (
            cooldown.check(
                _real_report(search_term_rows=rows),
                ads_root=self._root(tmp_path),
                today=TODAY,
            )
            is None
        )

    def test_the_same_keyword_in_another_campaign_is_not_frozen(self, tmp_path):
        # The change was applied to c2; this report block is c1. Separate
        # keyword, separate bid, separate data.
        rows = (
            '| grp | widget red | Broad | enabled | 1.50 | 50 | 75.00 | 10 | '
            '300.00 | 25.00% | 4.00 | 下调至 1.80 |\n'
        )
        assert (
            cooldown.check(
                _real_report(target_rows=rows),
                ads_root=self._root(tmp_path, name='c2.tsv'),
                today=TODAY,
            )
            is None
        )
