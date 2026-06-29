"""Unit tests for the ad-execution-fidelity gate + its parsers.

Guards the production bugs the skill taught but never enforced: a bid
applied at the wrong value (1.30 → 11.3 overspend), pausing more rows than
the report names (over-pause), and editing a row the report never named
(off-report). Plus the pre-live value-shape Bash guard.
"""

from __future__ import annotations

import pytest

from app.ai import ad_execution_targets as et, ad_negation_allowlist as al
from app.ai.bash_safety import check_bid_value_shape
from app.ai.stop_gates import ad_execution_fidelity as gate

pytestmark = pytest.mark.unit


_REPORT = """\
# 广告优化建议 — demo-northshore — 2026-06-10

### C_AAA111 | mouse manual | 手动 | 花费 USD 100
| 定向词 | 匹配 | 出价 | ... | 建议 |
| mouse/ | Exact | 0.50 | ... | 提高至 0.65（ROAS 5.08>5 加投赢家规则） |
| cheap mouse/ | Exact | 0.80 | ... | 暂停定向词 — 零单浪费 |
| good mouse/ | Exact | 0.50 | ... | 维持（ACOS 22%<30） |

### C_BBB222 | phone stand brand | Brand | 花费 USD 200
| 定向词 | 匹配 | 出价 | ... | 建议 |
| phone stand | Exact | 0.80 | ... | 提高至 1.04（ROAS 18>5 加投赢家规则） |
"""


def _setup(tmp_path, monkeypatch, log_text):
    task_id = 'task-exec'
    task_dir = tmp_path / 'tasks' / task_id
    task_dir.mkdir(parents=True)
    (task_dir / 'AD_AUDIT_2026-06-10.md').write_text(_REPORT, encoding='utf-8')
    (task_dir / 'EXECUTION_LOG.md').write_text(log_text, encoding='utf-8')
    monkeypatch.setattr(al, 'VIBE_SELLER_DIR', tmp_path)
    gate.reset_progress(task_id)
    return task_id


def test_build_bid_pause_targets_parses_report():
    t = et.build_bid_pause_targets(_REPORT)
    assert t['bids']['C_AAA111'][('mouse/', 'exact')] == 0.65
    assert t['bids']['C_BBB222'][('phone stand', 'exact')] == 1.04
    assert ('cheap mouse/', 'exact') in t['pauses']['C_AAA111']
    # 维持 row yields neither a bid nor a pause
    assert ('good mouse/', 'exact') not in t['bids'].get('C_AAA111', {})
    assert ('good mouse/', 'exact') not in t['pauses'].get('C_AAA111', set())


def test_gate_passes_faithful_log(tmp_path, monkeypatch):
    log = """\
## C_AAA111
| 1 | noon | EG | C_AAA111 | Raise "mouse/" Exact bid | USD 0.65 | applied | ts | shows 0.65 | ok |
| 2 | noon | EG | C_AAA111 | Pause "cheap mouse/" Exact | paused | applied | ts | switch off | ok |
## C_BBB222
| 3 | noon | EG | C_BBB222 | Raise "phone stand" Exact bid | USD 1.04 | applied | ts | shows 1.04 | ok |
"""
    task_id = _setup(tmp_path, monkeypatch, log)
    assert gate.check('result', task_id, None) is None


def test_gate_denies_bid_mismatch(tmp_path, monkeypatch):
    # the 10x overspend: target 0.65 applied as 6.5
    log = """\
## C_AAA111
| 1 | noon | EG | C_AAA111 | Raise "mouse/" Exact bid | USD 6.5 | applied | ts | shows 6.5 | ok |
"""
    task_id = _setup(tmp_path, monkeypatch, log)
    deny = gate.check('result', task_id, None)
    assert deny is not None
    assert deny.gate == 'ad_execution_fidelity'
    assert 'BID MISMATCH' in deny.reason
    assert '6.5' in deny.reason and '0.65' in deny.reason


def test_gate_denies_over_pause(tmp_path, monkeypatch):
    # report marks only cheap mouse/ for pause; agent also paused good mouse/
    log = """\
## C_AAA111
| 1 | noon | EG | C_AAA111 | Pause "cheap mouse/" Exact | paused | applied | ts | off | ok |
| 2 | noon | EG | C_AAA111 | Pause "good mouse/" Exact | paused | applied | ts | off | ok |
"""
    task_id = _setup(tmp_path, monkeypatch, log)
    deny = gate.check('result', task_id, None)
    assert deny is not None
    assert 'OVER-PAUSE' in deny.reason
    assert 'good mouse' in deny.reason


def test_gate_denies_off_report(tmp_path, monkeypatch):
    # editing a keyword the report never named in this campaign
    log = """\
## C_AAA111
| 1 | noon | EG | C_AAA111 | Raise "wireless mouse/" Exact bid | USD 0.90 | applied | ts | shows 0.90 | ok |
"""
    task_id = _setup(tmp_path, monkeypatch, log)
    deny = gate.check('result', task_id, None)
    assert deny is not None
    assert 'OFF-REPORT' in deny.reason


def test_gate_excuses_reverted(tmp_path, monkeypatch):
    log = """\
## C_AAA111
| 1 | noon | EG | C_AAA111 | Raise "mouse/" Exact bid | USD 6.5 | applied | ts | shows 6.5 | 回退 reverted to 0.65 |
"""
    task_id = _setup(tmp_path, monkeypatch, log)
    # the row is marked 回退/reverted → excused, no deny
    assert gate.check('result', task_id, None) is None


def test_gate_fails_open_after_stall(tmp_path, monkeypatch):
    log = """\
## C_AAA111
| 1 | noon | EG | C_AAA111 | Raise "mouse/" Exact bid | USD 6.5 | applied | ts | shows 6.5 | ok |
"""
    task_id = _setup(tmp_path, monkeypatch, log)
    for _ in range(gate.STALL_CAP):
        assert gate.check('result', task_id, None) is not None
    assert gate.is_stalled(task_id) is True


def test_gate_noop_without_report(tmp_path, monkeypatch):
    monkeypatch.setattr(al, 'VIBE_SELLER_DIR', tmp_path)
    assert gate.check('result', 'no-such-task', None) is None


def test_gate_denies_incomplete_partial_run(tmp_path, monkeypatch):
    # An observed production failure mode: task scoped BOTH campaigns, but the log only
    # addressed C_AAA111's mouse/ — C_BBB222 phone stand was never touched and not
    # marked skipped. A partial run must NOT pass as done.
    log = """\
## C_AAA111
| 1 | noon | EG | C_AAA111 | Raise "mouse/" Exact bid | USD 0.65 | applied | ts | shows 0.65 | ok |
"""
    task_id = _setup(tmp_path, monkeypatch, log)
    # scope names both campaigns (as a real task description would)
    monkeypatch.setattr(
        gate, 'task_scope_text', lambda _tid: 'do C_AAA111 and C_BBB222'
    )
    deny = gate.check('result', task_id, None)
    assert deny is not None
    assert 'INCOMPLETE' in deny.reason
    assert 'phone stand' in deny.reason  # the unaddressed C_BBB222 row


def test_gate_completeness_passes_when_skip_logged(tmp_path, monkeypatch):
    # phone stand IS addressed — recorded as skipped (live already ≥ target).
    # Mentioned in the log → not flagged incomplete; mouse/ applied at target.
    log = """\
## C_AAA111
| 1 | noon | EG | C_AAA111 | Raise "mouse/" Exact bid | USD 0.65 | applied | ts | shows 0.65 | ok |
| 2 | noon | EG | C_AAA111 | Pause "cheap mouse/" Exact | paused | applied | ts | off | ok |
## C_BBB222
| 3 | noon | EG | C_BBB222 | phone stand Exact | 1.04 | 1.20 | skipped — live 1.20 ≥ target 1.04 |
"""
    task_id = _setup(tmp_path, monkeypatch, log)
    monkeypatch.setattr(
        gate, 'task_scope_text', lambda _tid: 'do C_AAA111 and C_BBB222'
    )
    assert gate.check('result', task_id, None) is None


def test_extract_skips_already_paused_rows():
    # An "⏭️ already-paused" row is PRE-EXISTING state, not an executed pause.
    # Counting it caused the gate to flag an over-pause and tell the agent to
    # RE-ENABLE a live ad (an observed production incident).
    log = """\
### 活动 C_AAA111
| 关键词 | 匹配 | 报告方向 | 目标 | 改前live | 改后live | 动作 |
| cheap mouse/ | Exact | 暂停 | — | 0.80 (Paused) | — | ⏭️ already-paused |
| good mouse/ | Exact | 暂停 | — | 0.50 (Paused) | — | ⏭️ already-paused |
"""
    rows = et.extract_executed_bid_pause(log)
    assert rows == []  # no executed actions — all pre-existing


def test_extract_ignores_summary_and_flag_sections():
    # Summary tables and owner-flag lists reference cids/arrows that the old
    # parser read as off-report bids (e.g. kw "✅ 成功执行", a .tsv path).
    log = """\
### 活动 C_AAA111
| 关键词 | 匹配 | 报告方向 | 目标 | 改前live | 改后live | 动作 |
| mouse/ | Exact | 提价 | 0.65 | 0.50 | 0.65 | ✅ 已提高 |

## 汇总
| 类型 | 数量 | 详情 |
| ✅ 成功执行 | 1 | C_AAA111: mouse/ [Exact] 0.50→0.65 |

### 不在本批、留给店主复审 (Flag)
| 活动/关键词 | 说明 |
| C_BBB222 | stores/demo-northshore/ads/amazon/us/C_BBB222.tsv — SB video 无关键词面 |
"""
    rows = et.extract_executed_bid_pause(log)
    # exactly one real applied bid; no summary/flag/path rows leak through
    assert rows == [('C_AAA111', 'mouse/', 'exact', 'bid', 0.65)]


def test_extract_parses_readback_applied_bid():
    # The ✅ 已降低 + read-back-column format the agent emitted: the applied
    # value is the last currency number (the post-change read-back).
    log = """\
### 活动 C_AAA111
| 关键词 | 匹配 | 报告方向 | 目标 | 改前live | 改后live | 动作 |
| mouse/ | Exact | 降价 | 0.65 | USD 0.80 | USD 0.65 | ✅ 已降低 |
"""
    rows = et.extract_executed_bid_pause(log)
    assert rows == [('C_AAA111', 'mouse/', 'exact', 'bid', 0.65)]


def test_gate_no_false_overpause_on_preexisting_pause(tmp_path, monkeypatch):
    # The incident: report names 1 pause; both report pause-rows are already
    # Paused live, logged as ⏭️. The gate must NOT report an over-pause nor
    # tell the agent to re-enable anything.
    log = """\
### 活动 C_AAA111
| 关键词 | 匹配 | 报告方向 | 目标 | 改前live | 改后live | 动作 |
| mouse/ | Exact | 提价 | 0.65 | 0.50 | 0.65 | ✅ 已提高 |
| cheap mouse/ | Exact | 暂停 | — | 0.80 (Paused) | — | ⏭️ already-paused |
| good mouse/ | Exact | 维持 | — | 0.50 (Paused) | — | ⏭️ already-paused |
"""
    task_id = _setup(tmp_path, monkeypatch, log)
    assert gate.check('result', task_id, None) is None


def test_gate_completeness_excuses_flagged_campaign(tmp_path, monkeypatch):
    # C_BBB222 (phone stand raise) is flagged inapplicable (e.g. SB video, no kw
    # surface). A campaign-level flag means its rows are addressed-as-skipped
    # — must NOT read as INCOMPLETE (the false 417 INCOMPLETE that pressured
    # the agent toward the unsafe un-pause).
    log = """\
### 活动 C_AAA111
| 关键词 | 匹配 | 报告方向 | 目标 | 改前live | 改后live | 动作 |
| mouse/ | Exact | 提价 | 0.65 | 0.50 | 0.65 | ✅ 已提高 |
| cheap mouse/ | Exact | 暂停 | — | 0.80 (Paused) | — | ⏭️ already-paused |

### 不在本批、留给店主复审 (Flag)
| 活动 | 说明 |
| C_BBB222 | SB video 无关键词定向面，报告词不适用 — inapplicable |
"""
    task_id = _setup(tmp_path, monkeypatch, log)
    monkeypatch.setattr(
        gate, 'task_scope_text', lambda _tid: 'do C_AAA111 and C_BBB222'
    )
    assert gate.check('result', task_id, None) is None


def test_over_pause_remedy_never_instructs_autonomous_reenable(
    tmp_path, monkeypatch
):
    # A genuine over-pause (agent paused good mouse/ THIS RUN) still denies,
    # but the remedy must NOT bluntly say "re-enable" — un-pausing on a
    # fragile parse is the harm vector. It must qualify + flag for owner.
    log = """\
## C_AAA111
| 1 | noon | EG | C_AAA111 | Pause "cheap mouse/" Exact | paused | applied | ts | off | ok |
| 2 | noon | EG | C_AAA111 | Pause "good mouse/" Exact | paused | applied | ts | off | ok |
"""
    task_id = _setup(tmp_path, monkeypatch, log)
    deny = gate.check('result', task_id, None)
    assert deny is not None and 'OVER-PAUSE' in deny.reason
    assert 'Do NOT autonomously re-enable' in deny.reason
    assert 'FLAG for owner' in deny.reason


_REPORT_LOWER = """\
# 广告优化建议 — demo-northshore — 2026-06-10

### C_LOW111 | wireless mouse manual | 手动 | 花费 USD 100
| 定向词 | 匹配 | 出价 | ... | 建议 |
| usb-c cable | Broad | 3.00 | ... | 降至 2.39（ACOS 34%>30 规则） |
| cable organizer | Broad | 1.00 | ... | 提高至 2.55（ROAS 8>5 加投赢家规则） |
"""


def test_bid_direction_parsed():
    t = et.build_bid_pause_targets(_REPORT_LOWER)
    assert t['bids']['C_LOW111'][('usb-c cable', 'broad')] == 2.39
    assert t['bid_dirs']['C_LOW111'][('usb-c cable', 'broad')] == 'down'
    assert t['bid_dirs']['C_LOW111'][('cable organizer', 'broad')] == 'up'


def test_gate_denies_only_raise_skip_on_lower_row(tmp_path, monkeypatch):
    # An observed production gap: a 降至 row skipped because the run was "only-raise".
    # That leaves a high-ACOS bid overspending — the gate must catch it.
    task_id = 'task-exec'
    task_dir = tmp_path / 'tasks' / task_id
    task_dir.mkdir(parents=True)
    (task_dir / 'AD_AUDIT_2026-06-10.md').write_text(
        _REPORT_LOWER, encoding='utf-8'
    )
    log = """\
### 活动 C_LOW111
| 关键词 | 匹配 | 报告方向 | 目标 | live | 动作 |
| usb-c cable | Broad | 降价 | 2.39 | 3.00 | ⏭️ skipped — only-raise, live 3.00 ≥ target |
| cable organizer | Broad | 提价 | 2.55 | 1.00 | 2.55 | ✅ 已提高 |
"""
    (task_dir / 'EXECUTION_LOG.md').write_text(log, encoding='utf-8')
    monkeypatch.setattr(al, 'VIBE_SELLER_DIR', tmp_path)
    gate.reset_progress(task_id)
    deny = gate.check('result', task_id, None)
    assert deny is not None
    assert 'WRONG-DIRECTION SKIP' in deny.reason
    assert 'usb-c cable' in deny.reason


def test_gate_allows_lower_applied_or_already_below(tmp_path, monkeypatch):
    # A 降至 row genuinely applied (lowered) OR already ≤ target is fine.
    task_id = 'task-exec'
    task_dir = tmp_path / 'tasks' / task_id
    task_dir.mkdir(parents=True)
    (task_dir / 'AD_AUDIT_2026-06-10.md').write_text(
        _REPORT_LOWER, encoding='utf-8'
    )
    log = """\
### 活动 C_LOW111
| 关键词 | 匹配 | 报告方向 | 目标 | 改前 | 改后 | 动作 |
| usb-c cable | Broad | 降价 | 2.39 | 3.00 | 2.39 | ✅ 已降低 |
| cable organizer | Broad | 提价 | 2.55 | 1.00 | 2.55 | ✅ 已提高 |
"""
    (task_dir / 'EXECUTION_LOG.md').write_text(log, encoding='utf-8')
    monkeypatch.setattr(al, 'VIBE_SELLER_DIR', tmp_path)
    gate.reset_progress(task_id)
    assert gate.check('result', task_id, None) is None


@pytest.mark.parametrize(
    'value,denied',
    [
        ('1.30', False),
        ('4.00', False),
        ('24.38', False),  # legit high product-targeting bid (report has one)
        # 11.3 is a plausible-band single-decimal value — the shape guard
        # canNOT catch it (real targets go to 24.38); the exit gate does
        # (see test_gate_denies_bid_mismatch). Pre-live guard only catches
        # the unambiguous pathologies:
        ('11.3', False),
        ('3.002.27', True),  # concatenation (two decimal points)
        ('1.301.30', True),  # concatenation
        ('999', True),  # absurd magnitude (> ceiling)
    ],
)
def test_check_bid_value_shape(value, denied):
    cmd = f'browser-use input 47 "{value}"'
    result = check_bid_value_shape(cmd)
    assert (result is not None) is denied


def test_check_bid_value_shape_ignores_non_bid_commands():
    assert check_bid_value_shape('browser-use open https://x/y') is None
    assert check_bid_value_shape('ls -la') is None
