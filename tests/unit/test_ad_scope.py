"""Ground-truth scope enforcement + LLM manifest fail-open for the
ad-audit completeness gate (fixes #1 data-driven combos, #2 authoritative
active set + escape hatch, #3 LLM semantic check fail-open)."""

import json

import pytest

from app.ai.stop_gates import ad_completeness_review as acr, ad_scope

# A minimal Amazon SA section that PASSES the per-block content checks
# (one drilled campaign with a 建议 table + a same-window 对账 line).
_SA_BLOCK = (
    '## Amazon SA\n'
    '**进度**: drilled 1/1 active (1 total, 1 pages)\n'
    '### 600000000001 | acme widgets 004 manual keyword | SP\n'
    '| 关键词 | 出价 | eCPC | ROAS | 建议 |\n'
    '|---|---|---|---|---|\n'
    '| widget | 1.20 | 0.80 | 3.5 | 维持 |\n'
    '搜索词对账: 定向花费 USD 5.00 / 点击 6 = 搜索词花费 USD 5.00 / 点击 6 (✓)\n'
)
_SUMMARY = (
    '## 汇总建议\n'
    '各 combo 花费/销售/ROAS 总览与按影响排序的行动清单：本次覆盖 Amazon SA，'
    'ROAS 3.5，建议维持核心词出价并持续监控搜索词转化，足够长的中文说明。\n'
)


def _write_scope(task_id, combos):
    tdir = ad_scope.VIBE_SELLER_DIR / 'tasks' / task_id
    tdir.mkdir(parents=True, exist_ok=True)
    (tdir / 'AUDIT_SCOPE.json').write_text(
        json.dumps({'combos': combos}), encoding='utf-8'
    )
    return tdir


@pytest.mark.unit
class TestScopeLoader:
    def test_absent_returns_none(self):
        assert ad_scope.load_audit_scope('no-such-task-xyz') is None

    def test_none_task_id(self):
        assert ad_scope.load_audit_scope(None) is None

    def test_malformed_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ad_scope, 'VIBE_SELLER_DIR', tmp_path)
        tdir = tmp_path / 'tasks' / 't1'
        tdir.mkdir(parents=True)
        (tdir / 'AUDIT_SCOPE.json').write_text('not json{', encoding='utf-8')
        assert ad_scope.load_audit_scope('t1') is None


@pytest.mark.unit
class TestGroundTruthCoverage:
    def test_missing_combo_flagged(self, tmp_path, monkeypatch):
        # #1: scope declares a combo the report never opened → blocked.
        monkeypatch.setattr(ad_scope, 'VIBE_SELLER_DIR', tmp_path)
        _write_scope(
            't-combo',
            [
                {
                    'platform': 'amazon',
                    'country': 'SA',
                    'active_ids': ['600000000001'],
                },
                {
                    'platform': 'noon',
                    'country': 'AE',
                    'active_ids': ['C_DEMO0001'],
                },
            ],
        )
        report = _SA_BLOCK + _SUMMARY
        deny = acr.check(report, 't-combo', None)
        assert deny is not None
        assert 'noon AE' in deny.reason  # the un-opened combo

    def test_missing_active_id_flagged(self, tmp_path, monkeypatch):
        # #2: scope lists 2 active ids, report only drills 1 → blocked on
        # the missing id, even though 进度 self-reports 1/1.
        monkeypatch.setattr(ad_scope, 'VIBE_SELLER_DIR', tmp_path)
        _write_scope(
            't-ids',
            [
                {
                    'platform': 'amazon',
                    'country': 'SA',
                    'active_ids': ['600000000001', '600000000002'],
                }
            ],
        )
        report = _SA_BLOCK + _SUMMARY
        deny = acr.check(report, 't-ids', None)
        assert deny is not None
        assert '600000000002' in deny.reason  # the un-drilled id

    def test_full_coverage_passes(self, tmp_path, monkeypatch):
        # Scope satisfied (the one active id has its block) → no scope gap.
        monkeypatch.setattr(ad_scope, 'VIBE_SELLER_DIR', tmp_path)
        _write_scope(
            't-ok',
            [
                {
                    'platform': 'amazon',
                    'country': 'SA',
                    'active_ids': ['600000000001'],
                }
            ],
        )
        report = _SA_BLOCK + _SUMMARY
        deny = acr.check(report, 't-ok', None)
        # No ground-truth gap; any residual gap must not be a scope one.
        if deny is not None:
            assert '按权威 active 名单' not in deny.reason
            assert '尚未开始——报告里没有对应' not in deny.reason


@pytest.mark.unit
class TestEscapeHatch:
    def test_no_scope_simple_task_not_blocked(self):
        # A narrow create/investigate task (no combo sections, no scope
        # file) must never be blocked.
        simple = (
            '# 创建广告完成\n\n已为 ACME WIDGET-001 创建 1 个自动广告，'
            '预算 SAR 10。'
        )
        assert acr.check(simple, 'no-scope-simple', None) is None

    def test_no_scope_falls_back_to_self_report(self):
        # No AUDIT_SCOPE.json → ground-truth is skipped; a fully
        # self-reported single combo is not hard-blocked on coverage.
        report = _SA_BLOCK + _SUMMARY
        deny = acr.check(report, 'no-scope-combo', None)
        if deny is not None:
            assert '按权威 active 名单' not in deny.reason
            assert '尚未开始——报告里没有对应' not in deny.reason


@pytest.mark.unit
class TestLLMManifestFailOpen:
    def test_no_api_key_returns_none(self, monkeypatch):
        # #3: with no ANTHROPIC_API_KEY the semantic check must fail open
        # (return None) rather than raise or block.
        monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
        ad_scope._llm_cache.clear()
        assert (
            ad_scope.llm_is_real_drill('| kw | bid | 建议 |\n| a | 1 | x |')
            is None
        )

    def test_empty_section_none(self):
        assert ad_scope.llm_is_real_drill('') is None
