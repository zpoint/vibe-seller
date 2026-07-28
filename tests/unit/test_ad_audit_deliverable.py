"""The audit is graded on what the run DELIVERED, over every combo the
store declares, with a per-target row behind every targeting table.

Three holes, all observed on live runs of the same weekly audit:

* the gate graded the agent's chat summary while the finished report sat
  in the workspace (24 denials, then a fail-open that stored the
  summary);
* the agent wrote the combo list itself, so a store configured for five
  marketplaces was audited for one and passed;
* a targeting "table" of one 整体活动 aggregate row counted as a drill,
  because the presence check was satisfied by the header row alone.
"""

import json
import os

import pytest

from app.ai.stop_gates import (
    ad_completeness_review as acr,
    ad_scope,
    ad_scope as sc,
)
from app.routers.tasks_files import resolve_audit_deliverable

# One drilled campaign: per-keyword rows + a same-window 对账 line.
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
# Same campaign, but the targeting table restates the campaign total and
# names no keyword — the shape that used to pass.
_AGGREGATE_ONLY = (
    '### 600000000001 | acme widgets 004 manual | SP\n'
    '| 定位层汇总 | 出价 | 点击 | 花费 | ROAS | 建议 |\n'
    '|---|---|---|---|---|---|\n'
    '| 整体活动 | — | 10 | 8.00 | 3.7 | 钻取关键词级后再降价 |\n'
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


def _setup(monkeypatch, tmp_path, task_id, *, scope=None, targets=None):
    monkeypatch.setattr(ad_scope, 'VIBE_SELLER_DIR', tmp_path)
    tdir = tmp_path / 'tasks' / task_id
    tdir.mkdir(parents=True, exist_ok=True)
    if scope is not None:
        (tdir / 'AUDIT_SCOPE.json').write_text(
            json.dumps({'combos': scope}), encoding='utf-8'
        )
    if targets is not None:
        ad_scope.write_declared_targets(tdir, targets)
    acr.reset_progress(task_id)
    return tdir


def _gaps(text, task_id):
    deny = acr.check(text, task_id=task_id, track=False)
    return list(deny.gaps) if deny else []


_SA_SCOPE = [
    {
        'platform': 'amazon',
        'country': 'SA',
        'active_ids': ['600000000001'],
        'total_active': 1,
    }
]


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

    def test_newest_report_wins(self, tmp_path):
        old = tmp_path / 'AD_AUDIT_2026-01-01.md'
        new = tmp_path / 'AD_AUDIT_2026-01-02.md'
        old.write_text('old')
        new.write_text('new')
        os.utime(old, (1_600_000_000, 1_600_000_000))
        os.utime(new, (1_700_000_000, 1_700_000_000))
        assert resolve_audit_deliverable(tmp_path, 'done').name == new.name


@pytest.mark.unit
class TestDeclaredComboCoverage:
    """The store's marketplaces are the server's fact, not the agent's."""

    def test_omitted_combo_is_a_gap(self, monkeypatch, tmp_path):
        _setup(
            monkeypatch,
            tmp_path,
            't-decl',
            scope=_SA_SCOPE,
            targets={'amazon': ['SA', 'AE'], 'noon': ['SA']},
        )
        gaps = _gaps(_report(_DRILLED), 't-decl')
        assert any('amazon AE' in g for g in gaps), gaps
        assert any('noon SA' in g for g in gaps), gaps
        # The combo that WAS audited raises nothing.
        assert not any('amazon SA」根本没进' in g for g in gaps), gaps

    def test_combo_may_be_declared_empty(self, monkeypatch, tmp_path):
        # "No live campaigns in this marketplace" is a finding, not an
        # omission — it just has to be written down.
        _setup(
            monkeypatch,
            tmp_path,
            't-empty',
            scope=[
                *_SA_SCOPE,
                {
                    'platform': 'amazon',
                    'country': 'AE',
                    'active_ids': [],
                    'total_active': 0,
                },
            ],
            targets={'amazon': ['SA', 'AE']},
        )
        gaps = _gaps(_report(_DRILLED), 't-empty')
        # No [基线] gap AT ALL. Asserting only that the missing-combo gap
        # is gone would miss a deadlock: the empty-active_ids branch used
        # to answer "delete the entry", and deleting it re-raises the
        # missing-combo gap — two remedies, no legal move.
        assert not any('[基线]' in g for g in gaps), gaps

    def test_empty_combo_still_needs_an_observed_zero(
        self, monkeypatch, tmp_path
    ):
        # active_ids: [] with no total_active is a TRUNCATED enumeration
        # wearing the same clothes as an empty marketplace — still a gap.
        _setup(
            monkeypatch,
            tmp_path,
            't-empty-untotalled',
            scope=[
                *_SA_SCOPE,
                {'platform': 'amazon', 'country': 'AE', 'active_ids': []},
            ],
            targets={'amazon': ['SA', 'AE']},
        )
        gaps = _gaps(_report(_DRILLED), 't-empty-untotalled')
        assert any('active_ids 是空的' in g for g in gaps), gaps
        # …and the remedy must NOT be "delete the entry" for a combo the
        # server declared — that is the deadlock.
        assert not any('整条删掉' in g for g in gaps), gaps

    def test_empty_combo_may_not_also_hedge(self, monkeypatch, tmp_path):
        # ``exhaustive: false`` + ``active_ids: []`` reads "I audited a
        # subset, and the subset was nothing" — zero obligations wearing
        # the narrow-task escape hatch. Seen live on two Amazon markets.
        _setup(
            monkeypatch,
            tmp_path,
            't-hedge',
            scope=[
                *_SA_SCOPE,
                {
                    'platform': 'amazon',
                    'country': 'AE',
                    'active_ids': [],
                    'total_active': 0,
                    'exhaustive': False,
                },
            ],
            targets={'amazon': ['SA', 'AE']},
        )
        gaps = _gaps(_report(_DRILLED), 't-hedge')
        assert any('exhaustive' in g and 'amazon AE' in g for g in gaps), gaps

    def test_empty_combo_contradicting_history_is_challenged(
        self, monkeypatch, tmp_path
    ):
        # A market that produced per-campaign TSVs in a PRIOR audit did
        # have campaigns, so a zero now is a regression to justify, not
        # assert. Live: a run that never opened Amazon AE wrote
        # total_active 0 while the previous audit drilled 10 campaigns.
        _setup(
            monkeypatch,
            tmp_path,
            't-hist',
            scope=[
                *_SA_SCOPE,
                {
                    'platform': 'amazon',
                    'country': 'AE',
                    'active_ids': [],
                    'total_active': 0,
                },
            ],
            targets={'amazon': ['SA', 'AE']},
        )
        # Prior audit history for THIS store's amazon/ae.
        hist = tmp_path / 'stores' / 'acme' / 'ads' / 'amazon' / 'ae'
        hist.mkdir(parents=True)
        (hist / '100000000001.tsv').write_text('target\tspend\n')
        (hist / '100000000001.searchterms.tsv').write_text('query\tspend\n')
        targets = tmp_path / 'tasks' / 't-hist' / 'AUDIT_TARGETS.json'
        data = json.loads(targets.read_text())
        data['slug'] = 'acme'
        targets.write_text(json.dumps(data))
        acr.reset_progress('t-hist')
        gaps = _gaps(_report(_DRILLED), 't-hist')
        assert any('无在投活动' in g and 'amazon AE' in g for g in gaps), gaps

    def test_empty_combo_with_no_history_passes(self, monkeypatch, tmp_path):
        # A market that never had campaigns is legitimately empty — the
        # history challenge must not fire, or a genuinely-idle marketplace
        # becomes unauditable.
        _setup(
            monkeypatch,
            tmp_path,
            't-nohist',
            scope=[
                *_SA_SCOPE,
                {
                    'platform': 'amazon',
                    'country': 'AU',
                    'active_ids': [],
                    'total_active': 0,
                },
            ],
            targets={'amazon': ['SA', 'AU']},
        )
        targets = tmp_path / 'tasks' / 't-nohist' / 'AUDIT_TARGETS.json'
        data = json.loads(targets.read_text())
        data['slug'] = 'acme'
        targets.write_text(json.dumps(data))
        acr.reset_progress('t-nohist')
        gaps = _gaps(_report(_DRILLED), 't-nohist')
        # The emptiness CHALLENGE must not fire. The combo still owes a
        # report section saying it is empty, so that gap is expected.
        assert not any('无在投活动' in g for g in gaps), gaps
        assert not any('exhaustive' in g for g in gaps), gaps

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
        orig = sc.VIBE_SELLER_DIR
        try:
            sc.VIBE_SELLER_DIR = monkey
            assert sc.prior_campaign_tsvs('acme', 'amazon', 'AE') == 0
            assert sc.prior_campaign_tsvs('other', 'amazon', 'AE') == 1
            assert sc.prior_campaign_tsvs(None, 'amazon', 'AE') == 0
        finally:
            sc.VIBE_SELLER_DIR = orig

    def test_prior_tsvs_match_dir_casing_insensitively(self, tmp_path):
        # Agent-created paths mix casing in the wild (ads/amazon/SA beside
        # ads/amazon/ae). A case-sensitive lookup works on macOS, whose
        # default volume folds case, and silently returns 0 on Linux/WSL —
        # turning the evidence check into a no-op where it also ships.
        d = tmp_path / 'stores' / 'acme' / 'ads' / 'AMAZON' / 'SA'
        d.mkdir(parents=True)
        (d / '100000000001.tsv').write_text('x')
        orig = sc.VIBE_SELLER_DIR
        try:
            sc.VIBE_SELLER_DIR = tmp_path
            assert sc.prior_campaign_tsvs('acme', 'amazon', 'sa') == 1
            assert sc.prior_campaign_tsvs('acme', 'AMAZON', 'SA') == 1
        finally:
            sc.VIBE_SELLER_DIR = orig

    def test_no_targets_file_keeps_prior_behaviour(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, 't-none', scope=_SA_SCOPE)
        assert not any(
            '根本没进' in g for g in _gaps(_report(_DRILLED), 't-none')
        )

    def test_execution_summary_owes_no_marketplaces(
        self, monkeypatch, tmp_path
    ):
        # Same skill, same gates — but nothing here claims to be an audit.
        _setup(
            monkeypatch,
            tmp_path,
            't-exec',
            targets={'amazon': ['SA', 'AE'], 'noon': ['SA']},
        )
        gaps = _gaps('Amazon SA 广告优化执行完毕，12 项已应用。', 't-exec')
        assert gaps == [], gaps


@pytest.mark.unit
class TestTargetingLayerHasRows:
    """A targeting table must name targets, not restate the campaign."""

    def test_aggregate_only_table_is_a_gap(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, 't-agg', scope=_SA_SCOPE)
        gaps = _gaps(_report(_AGGREGATE_ONLY), 't-agg')
        assert any('[定向层]' in g for g in gaps), gaps

    def test_real_per_keyword_table_passes(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, 't-real', scope=_SA_SCOPE)
        gaps = _gaps(_report(_DRILLED), 't-real')
        assert not any('[定向层]' in g for g in gaps), gaps

    def test_recommendation_cell_saying_jian_yi_is_not_a_header(
        self, monkeypatch, tmp_path
    ):
        # A 建议 cell legitimately contains the word ("建议出价 1.30").
        # Reading it as a new header would lose the rows beneath it.
        _setup(monkeypatch, tmp_path, 't-cell', scope=_SA_SCOPE)
        body = _DRILLED.replace('提高至 1.30', '提取为定向词，建议出价 1.30')
        gaps = _gaps(_report(body), 't-cell')
        assert not any('[定向层]' in g for g in gaps), gaps

    def test_no_data_page_is_exempt(self, monkeypatch, tmp_path):
        _setup(monkeypatch, tmp_path, 't-nodata', scope=_SA_SCOPE)
        body = _AGGREGATE_ONLY.replace(
            '钻取关键词级后再降价', '该活动定向页无数据'
        )
        assert not any(
            '[定向层]' in g for g in _gaps(_report(body), 't-nodata')
        )
