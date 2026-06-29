"""Gate: don't park a high-ROAS converter on a bare Hold.

Business rule (store owner, 2026-06-08): the audit must not only avoid
cutting winners (see ``ad_bid_floor``), it must actively **raise** the
ones that are leaving money on the table. A keyword with ROAS > 5
(harvest-grade) and at least one order is a *scale candidate* — the
right call is usually to raise the bid to capture more of the obviously
profitable demand, not to sit on ``Hold``.

So a bare ``Hold`` / ``保持不动`` on a ROAS > 5 converter is a defect
*unless* the recommendation states a concrete reason raising won't help
(bid already at/above suggested-high, impression share already high,
campaign budget-capped, low search volume). This gate forces that
decision to be explicit: raise, or justify the hold — never a lazy
"it's good, leave it".

Runs on the AD_AUDIT report markdown at ``set_task_result``. Parses
keyword/target bid tables (a bid column + an ACOS-or-ROAS column) and
flags rows where ROAS > 5 but the recommendation is a hold with no
raise and no scale-blocker justification. No-op for non-ads results.
See ``amazon-ads/references/tuning-thresholds.md § Scale winners``.
"""

from __future__ import annotations

import re

from app.ai.stop_gates import GateDeny
from app.ai.stop_gates.ad_rules import DEFAULT_RULES

GATE_NAME = 'ad_scale_winners'

# Default ROAS threshold lives in the single source of truth
# (``ad_rules.DEFAULT_RULES['scale_roas']``); a per-store notes.md may
# override it. ``check`` takes the resolved value via ``rules``.

# Same fail-open safety as ad_bid_floor: enforce, but never permanently
# trap a task on a parser edge case.
MAX_DENIALS = 6

_FENCED_CODE_RE = re.compile(r'```.*?```', re.DOTALL)
_SEP_RE = re.compile(r':?-{2,}:?')
_PCT_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*%\s*$')
_NUM_RE = re.compile(r'-?\d+(?:\.\d+)?')

# A recommendation is a "hold" if it says hold/keep and does NOT raise.
_HOLD_RE = re.compile(r'\bhold\b|保持|维持', re.IGNORECASE)
_RAISE_RE = re.compile(
    r'提高|上调|加价|raise|increase bid|bid up', re.IGNORECASE
)

# Concrete reasons a high-ROAS keyword may legitimately be held — if any
# is present in the cell, the hold is justified and not flagged.
# Small-sample is one of them: output-spec's optimizer bar says to
# observe (and state why) when data is too thin — a ROAS-30 read off
# 3 clicks / 1 order is noise, and raising on it is the over-reaction
# the bar forbids.
_BLOCKER_RE = re.compile(
    r'建议上限|建议价上限|出价上限|已达上限|suggested[- ]?high|at max|maxed'
    r'|展示份额|曝光份额|impression share|预算|budget|out of budget'
    r'|搜索量|search volume|low volume|饱和|saturated|已是上限'
    r'|出价已高于|bid already above'
    r'|样本不足|样本太小|数据太少|数据不足|small sample'
    r'|仅\s*\d+\s*(?:次)?\s*点击|点击不足|点击太少',
    re.IGNORECASE,
)


def _cells(line: str) -> list[str] | None:
    s = line.strip()
    if not s.startswith('|'):
        return None
    return [c.strip() for c in s.strip('|').split('|')]


def _is_separator(cells: list[str]) -> bool:
    nonempty = [c.replace(' ', '') for c in cells if c.strip()]
    return bool(nonempty) and all(_SEP_RE.fullmatch(c) for c in nonempty)


def _roas_from(cells: list[str], col: dict[str, int]) -> float | None:
    """Return the row's ROAS, from a ROAS column or 100/ACOS%."""
    if 'roas' in col and col['roas'] < len(cells):
        m = _NUM_RE.search(cells[col['roas']].replace(',', ''))
        if m:
            return float(m.group())
    if 'acos' in col and col['acos'] < len(cells):
        m = _PCT_RE.match(cells[col['acos']])
        if m and float(m.group(1)) > 0:
            return 100.0 / float(m.group(1))
    return None


def check(
    result_text: str, rules: dict[str, float] | None = None
) -> GateDeny | None:
    """Deny when a ROAS>threshold converter is held with no raise/reason.

    ``rules`` carries the resolved thresholds (defaults + per-store
    notes.md override); falls back to ``DEFAULT_RULES``.
    """
    if not result_text or not isinstance(result_text, str):
        return None
    threshold = (rules or DEFAULT_RULES)['scale_roas']
    text = _FENCED_CODE_RE.sub(' ', result_text)

    flagged: list[tuple[str, float]] = []
    col: dict[str, int] = {}
    in_table = False

    for line in text.splitlines():
        cells = _cells(line)
        if cells is None:
            in_table = False
            col = {}
            continue
        if _is_separator(cells):
            continue
        low = [c.lower() for c in cells]
        has_rec = any(h in ('建议', 'recommendation') for h in low)
        has_bid = any(('出价' in h) or (h == 'bid') for h in low)
        has_metric = any(h in ('acos', 'roas') for h in low)
        if has_rec and has_bid and has_metric:
            # Search-term tables (first header cell 搜索词) are a
            # DIFFERENT DIMENSION: the 出价/eCPC columns there belong
            # to the SOURCE keyword, not the term — bid rules act on
            # targeting tables only (ad_explicit_actions owns the
            # search-term vocabulary). Skipping prevents demanding a
            # raise on a term that correctly says 维持——来源词承接.
            if '搜索词' in cells[0]:
                in_table = False
                col = {}
                continue
            col = {}
            for i, h in enumerate(low):
                if h == 'acos':
                    col['acos'] = i
                elif h == 'roas':
                    col.setdefault('roas', i)
                elif h in ('建议', 'recommendation'):
                    col['rec'] = i
                elif ('关键词' in h) or (h in ('keyword', 'target')):
                    col.setdefault('name', i)
            col.setdefault('name', 0)
            in_table = True
            continue
        if not in_table:
            continue
        rec_i = col.get('rec', len(cells) - 1)
        if rec_i >= len(cells):
            continue
        rec = cells[rec_i]
        # Only care about rows recommended as a hold (no raise verb).
        if not _HOLD_RE.search(rec) or _RAISE_RE.search(rec):
            continue
        roas = _roas_from(cells, col)
        if roas is None or roas <= threshold:
            continue
        if _BLOCKER_RE.search(rec):
            continue  # legitimate hold — reason stated
        name_i = col.get('name', 0)
        name = cells[name_i] if name_i < len(cells) else cells[0]
        flagged.append((name, roas))

    if not flagged:
        return None

    sample = '；'.join(f'「{n}」ROAS {r:.1f}' for n, r in flagged[:6])
    more = '' if len(flagged) <= 6 else f'（共 {len(flagged)} 行）'
    reason = (
        f'{len(flagged)} 个 ROAS > {threshold:g} 的高效关键词'
        '被简单 Hold，既没提高出价也没说明原因。高 ROAS + 有订单的词通常'
        '是「投得太少」而非「保护」——应**提高出价**抢占明显盈利的流量。'
        'Efficiency-PROTECT 只是「不砍」，不是「不加」。若确实不能提高，'
        '请在该行建议里写明原因（出价已达建议上限 / 展示份额已高 / 预算'
        f'受限 / 搜索量低）。否则改为提高出价后重新 set_task_result。'
        f'待处理行：{sample}{more}'
    )
    return GateDeny(gate=GATE_NAME, reason=reason)
