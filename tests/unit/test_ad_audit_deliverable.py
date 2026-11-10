"""The audit deliverable file, and the store history the gates trust.

* A run is graded on the report it delivered, not its chat summary
  (24 denials once followed from grading the summary), and on the copy
  it last touched when the report has two homes.
* ``prior_campaign_tsvs`` — the only independent evidence about a
  marketplace the server cannot browse — counts campaigns, per store,
  recently, case-insensitively, in both history homes.
"""

from contextlib import contextmanager
import json
import os

import pytest

from app.ai.stop_gates import ad_scope as sc, ad_tables
from app.routers.tasks_files import resolve_audit_deliverable

# One drilled campaign, as an inlined report would carry it.
_DRILLED = (
    '### 600000000001 | acme widgets 004 manual | SP\n'
    '| 关键词 | 出价 | 点击 | 花费 | ROAS | 建议 |\n'
    '|---|---|---|---|---|---|\n'
    '| widget | 1.20 | 6 | 5.00 | 3.5 | 维持 |\n'
    '| widget large | 1.00 | 4 | 3.00 | 4.1 | 提高至 1.30 |\n'
    '| **合计** | — | 10 | 8.00 | 3.7 | — |\n'
    '搜索词对账: 定向花费 USD 8.00 / 点击 10 = '
    '搜索词花费 USD 8.00 / 点击 10 (✓)\n'
)
_SUMMARY_SECTION = (
    '## 汇总建议\n'
    '各 combo 花费/销售/ROAS 总览与按影响排序的行动清单：本次覆盖 '
    'Amazon SA，ROAS 3.5，建议维持核心词出价并持续监控搜索词转化。\n'
)


def _report(body: str) -> str:
    return (
        '## Amazon SA\n**进度**: drilled 1/1 active (1 total, 1 pages)\n' + body
    ) + _SUMMARY_SECTION


@pytest.mark.unit
class TestDeliverableOverNarration:
    """A run is graded on the report it wrote, not the prose about it."""

    def test_narration_resolves_to_the_report(self, tmp_path):
        (tmp_path / 'AD_AUDIT_2026-01-02.md').write_text('## Amazon SA\n')
        found = resolve_audit_deliverable(
            tmp_path, 'Amazon SA 审计完成 — drilled 20/20，见报告文件。'
        )
        assert found is not None
        assert found.name == 'AD_AUDIT_2026-01-02.md'

    def test_inlined_report_is_never_downgraded(self, tmp_path):
        # A stale file from an earlier round must not replace a full
        # report the agent submitted inline.
        (tmp_path / 'AD_AUDIT_2026-01-01.md').write_text('stale')
        assert resolve_audit_deliverable(tmp_path, _report(_DRILLED)) is None

    def test_no_report_file_leaves_submission_alone(self, tmp_path):
        assert resolve_audit_deliverable(tmp_path, 'done, 20/20') is None

    def test_execution_task_keeps_its_own_result(self, tmp_path):
        # Phase-4 execution reads a prior audit; that audit is its INPUT.
        (tmp_path / 'AD_AUDIT_2026-01-02.md').write_text('## Amazon SA\n')
        (tmp_path / 'EXECUTION_LOG.md').write_text('applied 12 bid changes')
        assert (
            resolve_audit_deliverable(tmp_path, '执行完毕，12 项已应用') is None
        )

    def test_tsv_era_execution_task_keeps_its_own_result(self, tmp_path):
        # Same guard, the data contract's filename: an execution task
        # that wrote EXECUTION_LOG.tsv must not have its INPUT audit
        # picked as the deliverable.
        (tmp_path / 'AD_AUDIT_2026-01-02.md').write_text('## Amazon SA\n')
        (tmp_path / 'EXECUTION_LOG.tsv').write_text(
            'campaign_id\tad_group\ttarget\tmatch_type\taction\t'
            'from_bid\tto_bid\tapplied_at\n'
        )
        assert (
            resolve_audit_deliverable(tmp_path, '执行完毕，12 项已应用') is None
        )

    def test_newest_report_wins(self, tmp_path):
        old = tmp_path / 'AD_AUDIT_2026-01-01.md'
        new = tmp_path / 'AD_AUDIT_2026-01-02.md'
        old.write_text('old')
        new.write_text('new')
        os.utime(old, (1_600_000_000, 1_600_000_000))
        os.utime(new, (1_700_000_000, 1_700_000_000))
        assert resolve_audit_deliverable(tmp_path, 'done').name == new.name


def _two_copy_task(tmp_path, *, slug='acme', root_mtime, data_mtime):
    """Task root holding BOTH audit homes, with controlled mtimes."""
    (tmp_path / 'AUDIT_TARGETS.json').write_text(
        json.dumps({'slug': slug, 'combos': []})
    )
    root = tmp_path / 'AD_AUDIT_2026-01-02.md'
    root.write_text('task-root copy')
    data = (
        tmp_path
        / 'store-data'
        / slug
        / 'ads-audit'
        / '2026-01'
        / 'AD_AUDIT_2026-01-02.md'
    )
    data.parent.mkdir(parents=True)
    data.write_text('store-data copy')
    os.utime(root, (root_mtime, root_mtime))
    os.utime(data, (data_mtime, data_mtime))
    return root, data


@pytest.mark.unit
class TestTwoWritableCopies:
    """The report has two homes; the run is graded on the one it TOUCHED.

    The workspace layout puts a dated artifact in
    ``store-data/<slug>/ads-audit/<YYYY-MM>/`` while the skill also has
    the agent keep a task-root copy, and only a hand-copy kept them in
    step. Live, the store-data copy ran 12 minutes ahead of the task-root
    one mid-round; grading the task root would have re-scored a stale
    report and returned byte-identical gaps, hiding a converging agent.
    """

    def test_newer_store_data_copy_is_graded(self, tmp_path):
        _root, data = _two_copy_task(
            tmp_path, root_mtime=1_700_000_000, data_mtime=1_700_000_900
        )
        got = resolve_audit_deliverable(tmp_path, '审计完成，见报告')
        assert got is not None
        assert got.read_text() == data.read_text()

    def test_newer_task_root_copy_still_wins(self, tmp_path):
        # Symmetric: whichever was touched last, no home is privileged.
        root, _data = _two_copy_task(
            tmp_path, root_mtime=1_700_000_900, data_mtime=1_700_000_000
        )
        got = resolve_audit_deliverable(tmp_path, '审计完成，见报告')
        assert got is not None
        assert got.read_text() == root.read_text()

    def test_other_stores_reports_are_never_candidates(self, tmp_path):
        """``store-data`` is ONE shared tree symlinked into every task.

        An unscoped ``store-data/*/`` sweep collected a second store's
        dated reports against the live tree — grading this run on another
        store's audit, which is worse than the stale copy the sweep was
        added to prevent.
        """
        _root, _data = _two_copy_task(
            tmp_path, root_mtime=1_700_000_000, data_mtime=1_700_000_100
        )
        other = (
            tmp_path
            / 'store-data'
            / 'store-beef1234'
            / 'ads-audit'
            / '2026-01'
            / 'AD_AUDIT_2026-01-09.md'
        )
        other.parent.mkdir(parents=True)
        other.write_text('a DIFFERENT store audit')
        os.utime(other, (1_800_000_000, 1_800_000_000))  # newest of all
        got = resolve_audit_deliverable(tmp_path, '审计完成，见报告')
        assert got is not None
        assert 'store-beef1234' not in str(got)
        assert got.read_text() != 'a DIFFERENT store audit'

    def test_no_declared_slug_falls_back_to_task_root(self, tmp_path):
        # Nothing pins the store, so don't guess which store-data subtree
        # belongs to this run — fail closed to the task root.
        root = tmp_path / 'AD_AUDIT_2026-01-02.md'
        root.write_text('task-root copy')
        stray = (
            tmp_path
            / 'store-data'
            / 'acme'
            / 'ads-audit'
            / '2026-01'
            / 'AD_AUDIT_2026-01-02.md'
        )
        stray.parent.mkdir(parents=True)
        stray.write_text('store-data copy')
        os.utime(root, (1_700_000_000, 1_700_000_000))
        os.utime(stray, (1_800_000_000, 1_800_000_000))
        got = resolve_audit_deliverable(tmp_path, '审计完成，见报告')
        assert got is not None
        assert got.read_text() == 'task-root copy'

    @pytest.mark.parametrize('slug', ['..', '../other', 'a/b', '.'])
    def test_slug_cannot_escape_its_store(self, tmp_path, slug):
        # The slug is interpolated into a glob; traversal would let a
        # server-written value reach outside the store it must pin.
        (tmp_path / 'AUDIT_TARGETS.json').write_text(
            json.dumps({'slug': slug, 'combos': []})
        )
        root = tmp_path / 'AD_AUDIT_2026-01-02.md'
        root.write_text('task-root copy')
        got = resolve_audit_deliverable(tmp_path, '审计完成，见报告')
        assert got is not None
        assert got.read_text() == 'task-root copy'

    def test_hardlinked_copies_are_not_double_counted(self, tmp_path):
        # Some runs link rather than copy; the same inode under two paths
        # is ONE deliverable, and the shorter path is the stable name.
        _root, data = _two_copy_task(
            tmp_path, root_mtime=1_700_000_000, data_mtime=1_700_000_000
        )
        data.unlink()
        os.link(tmp_path / 'AD_AUDIT_2026-01-02.md', data)
        got = resolve_audit_deliverable(tmp_path, '审计完成，见报告')
        assert got is not None
        assert got.parent == tmp_path


@contextmanager
def _home(path):
    orig = ad_tables.VIBE_SELLER_DIR
    ad_tables.VIBE_SELLER_DIR = path
    try:
        yield
    finally:
        ad_tables.VIBE_SELLER_DIR = orig


@pytest.mark.unit
class TestPriorCampaignHistory:
    def test_prior_tsvs_count_campaigns_not_files(self, tmp_path):
        # Each campaign leaves TWO files; counting files doubles every
        # total and made the shrink check flag 21-of-22 combos as
        # collapsed.
        d = tmp_path / 'stores' / 'acme' / 'ads' / 'amazon' / 'SA'
        d.mkdir(parents=True)
        (d / '100000000001.tsv').write_text('t\n')
        (d / '100000000001.searchterms.tsv').write_text('q\n')
        (d / '100000000002.tsv').write_text('t\n')
        with _home(tmp_path):
            assert sc.prior_campaign_tsvs('acme', 'amazon', 'SA') == 2

    def test_prior_tsvs_never_borrow_another_stores_history(self, tmp_path):
        # Counting must be scoped to the store's own directory — a glob
        # across stores/* would let one store's history challenge another.
        monkey = tmp_path
        (monkey / 'stores' / 'other' / 'ads' / 'amazon' / 'ae').mkdir(
            parents=True
        )
        (
            monkey / 'stores' / 'other' / 'ads' / 'amazon' / 'ae' / 'x.tsv'
        ).write_text('t\n')
        with _home(monkey):
            assert sc.prior_campaign_tsvs('acme', 'amazon', 'AE') == 0
            assert sc.prior_campaign_tsvs('other', 'amazon', 'AE') == 1
            assert sc.prior_campaign_tsvs(None, 'amazon', 'AE') == 0

    def test_prior_tsvs_ignore_stale_history(self, tmp_path):
        # Without a recency window this count only grows — TSVs are never
        # deleted, so a store audited for a year accumulates every campaign
        # it ever ran and the collapse threshold drifts toward always
        # firing. Only campaigns drilled recently count as evidence that
        # the marketplace currently has campaigns.
        d = tmp_path / 'stores' / 'acme' / 'ads' / 'amazon' / 'SA'
        d.mkdir(parents=True)
        old = 1_600_000_000  # years ago
        for i in range(5):
            f = d / f'10000000000{i}.tsv'
            f.write_text('t\n')
            os.utime(f, (old, old))
        for i in range(5, 7):
            (d / f'10000000000{i}.tsv').write_text('t\n')  # now
        with _home(tmp_path):
            assert sc.prior_campaign_tsvs('acme', 'amazon', 'SA') == 2
            # the full history is still reachable when asked for
            assert (
                sc.prior_campaign_tsvs(
                    'acme', 'amazon', 'SA', within_days=36500
                )
                == 7
            )

    def test_prior_tsvs_match_dir_casing_insensitively(self, tmp_path):
        # Agent-created paths mix casing in the wild (ads/amazon/SA beside
        # ads/amazon/ae). A case-sensitive lookup works on macOS, whose
        # default volume folds case, and silently returns 0 on Linux/WSL —
        # turning the evidence check into a no-op where it also ships.
        d = tmp_path / 'stores' / 'acme' / 'ads' / 'AMAZON' / 'SA'
        d.mkdir(parents=True)
        (d / '100000000001.tsv').write_text('x')
        with _home(tmp_path):
            assert sc.prior_campaign_tsvs('acme', 'amazon', 'sa') == 1
            assert sc.prior_campaign_tsvs('acme', 'AMAZON', 'SA') == 1

    def test_prior_tsvs_read_the_store_data_home_too(self, tmp_path):
        # Runs write per-campaign TSVs under store-data/ today; the older
        # stores/<slug>/ads history still counts, never twice.
        new = tmp_path / 'store-data' / 'acme' / 'ads' / 'amazon' / 'SA'
        old = tmp_path / 'stores' / 'acme' / 'ads' / 'amazon' / 'SA'
        new.mkdir(parents=True)
        old.mkdir(parents=True)
        (new / '100000000001.tsv').write_text('t\n')
        (old / '100000000001.tsv').write_text('t\n')
        (old / '100000000002.tsv').write_text('t\n')
        with _home(tmp_path):
            assert sc.prior_campaign_tsvs('acme', 'amazon', 'SA') == 2
