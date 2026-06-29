"""Unit tests for the negation allowlist extraction + stop-gate.

Covers the production bug this guards: the agent freelancing a negation
of a relevant term that the report never approved.
"""

from __future__ import annotations

import pytest

from app.ai import ad_negation_allowlist as al
from app.ai.stop_gates import ad_negation_allowlist as gate

pytestmark = pytest.mark.unit


_REPORT = """\
# 广告优化建议 — demo-northshore — 2026-06-10

### 100000000000000 | kw-018-manual-keyword | Manual | 花费USD 482
| 搜索词 | 来源 | 匹配 | 点击 | 花费 | ... | 建议 |
| wireless keyboard | phone stand | Broad | 7.0 | 15.4 | 0 | 0 | 0 | **否定搜索词** |
| usb-c cable braided | phone stand | Broad | 2.0 | 3.0 | 0 | 0 | 1 | 维持 |

### 200000000000000 | kw-023-manual-keyword | Manual | 花费USD 477
| 搜索词 | 来源 | 匹配 | 点击 | 花费 | ... | 建议 |
| usb-c cable short | phone stand | Phrase | 4.0 | 10.7 | 0 | 0 | 0 | **否定搜索词** |
| phone stand adjustable | phone stand | Broad | 1.0 | 0.8 | 0 | 0 | 1 | 维持 |
"""


def test_build_allowlist_collects_only_negation_rows():
    allow = al.build_allowlist(_REPORT)
    assert allow['100000000000000'] == {'wireless keyboard'}
    # 维持 rows are NOT on the allowlist
    assert 'usb-c cable braided' not in allow['100000000000000']
    assert allow['200000000000000'] == {'usb-c cable short'}
    assert 'phone stand adjustable' not in allow['200000000000000']


def test_build_allowlist_ignores_keep_rows_mentioning_negation():
    # A 维持 (keep) row whose prose explains itself with 否定 must NOT be
    # treated as an approved negation — the real directive is the bolded
    # **否定搜索词** at the head, not any mention of 否定.
    report = """\
### 300000000000000 | 240w charger keyword | Manual | 花费USD 1391
| fast charger | Phrase | 3.30 | 57814 | 维持（ACOS 109% 源于已否定的 4 个垃圾变体词） |
| fast charger type c | fast charger | Phrase | 20 | 49 | 0 | 0 | 0 | **否定搜索词** |
"""
    allow = al.build_allowlist(report)
    assert 'fast charger type c' in allow['300000000000000']
    assert 'fast charger' not in allow['300000000000000']  # KEEP, not negate


# The same SEARCH TERM marked 否定 in one row AND 维持/提高 in another
# (it converts) within one campaign. A search-term negative blocks the
# query globally, so negating it kills the converting row — a
# self-contradictory recommendation that the report author produced by
# judging rows one-by-one instead of aggregating by term. The
# contradictory term must be excluded from the allowlist (never blessed)
# and surfaced by find_negation_contradictions.
_CONTRADICTION_REPORT = """\
### 400000000000000 | wireless mouse 022 auto | Auto | 花费USD 399
| 搜索词 | 来源 | 匹配 | 点击 | 花费 | 订单 | 销售额 | ROAS | 建议 |
| phone stand | phone stand |  | 18.0 | 23.0 | 5.0 | 184.9 | 8.01 | 维持——auto 定向承接 |
| cable organizer | cable organizer |  | 9.0 | 12.8 | 3.0 | 112.9 | 8.78 | 维持——auto 定向承接 |
| phone stand | phone stand |  | 3.0 | 3.8 | 0 | 0 | 0 | **否定搜索词** |
| cable organizer | cable organizer |  | 12.0 | 16.0 | 0 | 0 | 0 | **否定搜索词** |
| wireless mouse pad | wireless mouse pad |  | 8.0 | 9.6 | 0 | 0 | 0 | **否定搜索词** |
| close-match | 自动 | 1.5 | 4570 | 94 | 20 | 808 | 7.94 | 提高至 1.95 |
"""


def test_build_allowlist_excludes_self_contradicting_term():
    allow = al.build_allowlist(_CONTRADICTION_REPORT)
    cid = '400000000000000'
    # wireless mouse pad is only ever 否定 → approved.
    assert 'wireless mouse pad' in allow[cid]
    # phone stand + cable organizer are BOTH negated and kept (they convert) →
    # excluded, so no one can be blessed to negate profitable traffic.
    assert 'phone stand' not in allow[cid]
    assert 'cable organizer' not in allow[cid]


def test_find_negation_contradictions_detects_negate_and_keep():
    cx = al.find_negation_contradictions(_CONTRADICTION_REPORT)
    assert cx['400000000000000'] == {'phone stand', 'cable organizer'}
    # a clean report (each term one verdict) has no contradictions
    assert al.find_negation_contradictions(_REPORT) == {}


def test_term_allowed_is_normalized_and_campaign_scoped():
    allow = al.build_allowlist(_REPORT)
    # case / whitespace / non-breaking-space insensitive
    assert al.term_allowed(allow, '100000000000000', 'Wireless  Keyboard')
    assert al.term_allowed(allow, '100000000000000', 'wireless keyboard')
    # right term, wrong campaign → not allowed
    assert not al.term_allowed(allow, '200000000000000', 'wireless keyboard')
    # relevant term never approved → not allowed
    assert not al.term_allowed(
        allow, '200000000000000', 'phone stand adjustable'
    )


def test_extract_executed_negations_only_done_negation_rows():
    log = """\
## 本轮新增
| 100000000000000 | 否定 | wireless keyboard | Negative exact | ✅ |
| 200000000000000 | 否定 | phone stand adjustable | Negative phrase | ✅ |
| 200000000000000 | 否定 | usb-c cable short | Negative phrase |  待执行 |
"""
    got = al.extract_executed_negations(log)
    terms = {t for _c, t in got}
    assert 'wireless keyboard' in terms
    assert 'phone stand adjustable' in terms
    # the not-done row (待执行, no ✅) is excluded
    assert 'usb-c cable short' not in terms


def test_gate_denies_stray_and_passes_clean(tmp_path, monkeypatch):
    # point the allowlist module at a fake task dir
    task_id = 'task-xyz'
    task_dir = tmp_path / 'tasks' / task_id
    task_dir.mkdir(parents=True)
    (task_dir / 'AD_AUDIT_2026-06-10.md').write_text(_REPORT, encoding='utf-8')
    monkeypatch.setattr(al, 'VIBE_SELLER_DIR', tmp_path)

    # 1) a STRAY executed negation (phone stand adjustable — relevant, not approved)
    (task_dir / 'EXECUTION_LOG.md').write_text(
        '| 200000000000000 | 否定 | phone stand adjustable | Negative phrase | ✅ |\n',
        encoding='utf-8',
    )
    gate.reset_progress(task_id)
    deny = gate.check('result', task_id, None)
    assert deny is not None
    assert deny.gate == 'ad_negation_allowlist'
    assert 'phone stand adjustable' in deny.reason

    # 2) only approved negations → passes
    (task_dir / 'EXECUTION_LOG.md').write_text(
        '| 100000000000000 | 否定 | wireless keyboard | Negative exact | ✅ |\n'
        '| 200000000000000 | 否定 | usb-c cable short | Negative phrase | ✅ |\n',
        encoding='utf-8',
    )
    gate.reset_progress(task_id)
    assert gate.check('result', task_id, None) is None


def test_extract_executed_ignores_recipe_and_summary_rows():
    # Recipe-doc + summary rows carry 否定/✅ but are NOT executions.
    log = """\
| 搜索词否定 | state → click Add as index → click 选项 → 验证 | ✅ 已验证 |
| P0 | 500000000000000 | 461 USD | ~85 未滚动 | 前13词已全部否定 ✅ |
| 100000000000000 | 否定 | wireless keyboard | Negative exact | ✅ |
"""
    terms = {t for _c, t in al.extract_executed_negations(log)}
    assert terms == {'wireless keyboard'}


def test_gate_excuses_reverted_and_exempted(tmp_path, monkeypatch):
    task_id = 'excuse'
    task_dir = tmp_path / 'tasks' / task_id
    task_dir.mkdir(parents=True)
    (task_dir / 'AD_AUDIT_2026-06-10.md').write_text(_REPORT, encoding='utf-8')
    monkeypatch.setattr(al, 'VIBE_SELLER_DIR', tmp_path)

    # off-report negations: one later reverted, one human-exempted
    (task_dir / 'EXECUTION_LOG.md').write_text(
        '| 200000000000000 | 否定 | phone stand adjustable | Negative phrase | ✅ |\n'
        '| 100000000000000 | 否定 | usb-c cable organizer | Negative phrase | ✅ |\n'
        '\n## 回退\n'
        '回退以下: phone stand adjustable\n',
        encoding='utf-8',
    )
    (task_dir / 'NEGATION_EXEMPTIONS.txt').write_text(
        '# clear waste\nusb-c cable organizer\n', encoding='utf-8'
    )
    gate.reset_progress(task_id)
    # reverted (phone stand adjustable) + exempted (usb-c cable organizer) → both excused → PASS
    assert gate.check('result', task_id, None) is None


def test_gate_noops_without_report(tmp_path, monkeypatch):
    task_id = 'no-report'
    (tmp_path / 'tasks' / task_id).mkdir(parents=True)
    monkeypatch.setattr(al, 'VIBE_SELLER_DIR', tmp_path)
    assert gate.check('anything', task_id, None) is None


def test_gate_fails_open_after_stall_cap(tmp_path, monkeypatch):
    task_id = 'stall'
    task_dir = tmp_path / 'tasks' / task_id
    task_dir.mkdir(parents=True)
    (task_dir / 'AD_AUDIT_2026-06-10.md').write_text(_REPORT, encoding='utf-8')
    (task_dir / 'EXECUTION_LOG.md').write_text(
        '| 200000000000000 | 否定 | phone stand adjustable | Negative phrase | ✅ |\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(al, 'VIBE_SELLER_DIR', tmp_path)
    gate.reset_progress(task_id)
    for _ in range(gate.STALL_CAP):
        assert gate.check('r', task_id, None) is not None
    assert gate.is_stalled(task_id) is True
