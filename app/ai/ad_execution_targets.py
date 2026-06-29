"""Bid / pause target parsing for the ``ad_execution_fidelity`` gate.

Split out of ``ad_negation_allowlist`` (which governs 否定 rows) to keep each
module under the line limit and to one concern. Bids and pauses need their
own per-(campaign, keyword, match-type) targets so the execution gate can
catch a bid applied at the wrong value (the live ``1.30→11.3`` overspend), a
pause beyond the report's list (over-pause), an edit to a row the report
never named (off-report), or a 降至 (lower) row skipped as "only-raise"
(an observed production gap). Reuses ``_SECTION_RE`` + the same
table-walking + ``normalize_term`` as the negation parser.
"""

from __future__ import annotations

import re

from app.ai.ad_negation_allowlist import (
    _REVERT_RE,
    _SECTION_RE,
    _is_header_cell,
    find_report,
    normalize_term,
)

# A recommendation cell is a BID directive only when its HEAD is a
# raise/lower verb carrying a target number (startswith, like
# ``_row_verdict`` — so "维持（…提高…）" prose never counts).
_BID_NUM_RE = re.compile(
    r'(?:SAR|AED|USD|\$)?\s*([0-9]+(?:\.[0-9]+)?)', re.IGNORECASE
)
_BID_HEAD_CJK = (
    '提高至',
    '上调至',
    '提高到',
    '降至',
    '下调至',
    '调低至',
    '调整至',
)
_BID_HEAD_EN = ('trim to', 'raise to', 'lower to', 'increase to', 'set to')
_PAUSE_HEAD_CJK = ('暂停',)
_PAUSE_HEAD_EN = ('pause',)

_BID_UP_HEAD = ('提高至', '上调至', '提高到')
_BID_DOWN_HEAD = ('降至', '下调至', '调低至')


def _norm_match(cell: str) -> str:
    """Canonical match-type token: exact|phrase|broad|category|auto|''."""
    low = cell.lower()
    if 'exact' in low:
        return 'exact'
    if 'phrase' in low:
        return 'phrase'
    if 'broad' in low:
        return 'broad'
    if 'cat' in low or '类目' in low:
        return 'category'
    if 'auto' in low or '自动' in low:
        return 'auto'
    return ''


def _bid_target(rec: str) -> float | None:
    """Target bid from a recommendation cell, or None if it isn't a raise/
    lower directive. Head-matched so keep/prose rows never yield a value."""
    r = rec.replace('*', '').strip()
    low = r.lower()
    if not (r.startswith(_BID_HEAD_CJK) or low.startswith(_BID_HEAD_EN)):
        return None
    m = _BID_NUM_RE.search(r)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _bid_direction(rec: str) -> str:
    """'up' (raise), 'down' (lower), or 'exact' for a bid recommendation.

    Direction is load-bearing: 'only-raise' (skip if live ≥ target) is valid
    ONLY for an 'up' row. Applying it to a 'down' row leaves a high-ACOS bid
    overspending — an observed production gap where 18 降至 rows were skipped.
    """
    r = rec.replace('*', '').strip()
    low = r.lower()
    if r.startswith(_BID_UP_HEAD) or low.startswith((
        'raise to',
        'increase to',
    )):
        return 'up'
    if r.startswith(_BID_DOWN_HEAD) or low.startswith(('trim to', 'lower to')):
        return 'down'
    return 'exact'


def _is_pause(rec: str) -> bool:
    r = rec.replace('*', '').strip()
    return r.startswith(_PAUSE_HEAD_CJK) or r.lower().startswith(_PAUSE_HEAD_EN)


def build_bid_pause_targets(report_text: str) -> dict[str, dict]:
    """Report targets keyed by campaign + (normalized keyword, match-type).

    Returns ``{'bids': {cid: {(kw, match): target_bid}},
                'bid_dirs': {cid: {(kw, match): 'up'|'down'|'exact'}},
                'pauses': {cid: {(kw, match), …}},
                'rows':   {cid: {(kw, match), …}}}`` where ``rows`` is every
    actionable bid/pause row (used for the off-report scope check). Only the
    targeting/keyword tables carry bid/pause verbs; search-term tables don't.
    """
    bids: dict[str, dict[tuple[str, str], float]] = {}
    bid_dirs: dict[str, dict[tuple[str, str], str]] = {}
    pauses: dict[str, set[tuple[str, str]]] = {}
    rows: dict[str, set[tuple[str, str]]] = {}
    matches = list(_SECTION_RE.finditer(report_text))
    for i, m in enumerate(matches):
        cid = m.group(1).strip()
        start = m.end()
        end = (
            matches[i + 1].start() if i + 1 < len(matches) else len(report_text)
        )
        for raw in report_text[start:end].splitlines():
            line = raw.strip()
            if not line.startswith('|'):
                continue
            cells = [c.strip() for c in line.strip('|').split('|')]
            if len(cells) < 3:
                continue
            kw = normalize_term(cells[0])
            if not kw or _is_header_cell(kw):
                continue
            match = _norm_match(cells[1])
            rec = cells[-1]
            key = (kw, match)
            target = _bid_target(rec)
            if target is not None:
                bids.setdefault(cid, {})[key] = target
                bid_dirs.setdefault(cid, {})[key] = _bid_direction(rec)
                rows.setdefault(cid, set()).add(key)
            elif _is_pause(rec):
                pauses.setdefault(cid, set()).add(key)
                rows.setdefault(cid, set()).add(key)
    return {
        'bids': bids,
        'bid_dirs': bid_dirs,
        'pauses': pauses,
        'rows': rows,
    }


def load_bid_pause_targets(task_id: str) -> dict[str, dict]:
    """Build bid/pause targets from ``task_id``'s report (empty if none)."""
    report = find_report(task_id)
    if report is None:
        return {'bids': {}, 'bid_dirs': {}, 'pauses': {}, 'rows': {}}
    try:
        text = report.read_text(encoding='utf-8')
    except OSError:
        return {'bids': {}, 'bid_dirs': {}, 'pauses': {}, 'rows': {}}
    return build_bid_pause_targets(text)


# Executed bid/pause rows from EXECUTION_LOG.md (best-effort, conservative).
_LOG_CAMP_RE = re.compile(r'\b(\d{12,}|C_[A-Z0-9]{6,})\b')
_LOG_QUOTED_RE = re.compile(r'["“「]([^"”」]{1,60})["”」]')
_BID_VERB_RE = re.compile(
    r'trim|raise|lower|bid|提高|上调|降至|下调|调低|出价', re.IGNORECASE
)
_PAUSE_VERB_RE = re.compile(r'pause|暂停', re.IGNORECASE)
_APPLIED_RE = re.compile(r'✅|applied|已应用|已完成|done', re.IGNORECASE)
# Headers that open a non-actionable block (summary / owner-flag / notes).
# Rows under these must never be read as executed changes — the summary
# table and the "留给店主复审" flag list both reference campaign ids and
# arrows that otherwise misparse as off-report bids/pauses.
_SUMMARY_HDR_RE = re.compile(
    r'汇总|summary|总结|flag|留给店主|复审|不在本批|备注|说明|legend',
    re.IGNORECASE,
)
# A row that records a skip / no-op / pre-existing state — NOT an executed
# change the agent applied this run. Counting these as agent actions is what
# made the gate flag an already-paused row as an "over-pause" and tell the
# agent to RE-ENABLE a live ad (a state change the report never requested).
_SKIP_MARK_EN = re.compile(
    r'skip|maintain|hold|already|drift|unreachable|inapplicable|n/a|no[- ]op',
    re.IGNORECASE,
)
_SKIP_MARK_CJK = re.compile(
    r'⏭|⚠|🚩|跳过|未点名|勿动|未动|维持|无意义|不适用|已存在|无需'
    r'|漂移|不可达|已暂停|已是|不在本批'
)
_CCY_NUM_RE = re.compile(
    r'(?:SAR|AED|USD|\$)\s*([0-9]+(?:\.[0-9]+)?)', re.IGNORECASE
)


_MATCH_TOKEN_RE = re.compile(
    r'^(keyword\s+)?(exact|phrase|broad|category|auto)(\s+match)?$',
    re.IGNORECASE,
)
# Summary / header / prose cells that must never be read as a keyword.
_JUNK_KW_RE = re.compile(
    r'^\s*$|^\**\s*\d|提价数|暂停数|否定数|总计|合计|小计|数量|^无$|^—+$|^-+$'
    r'|skipped|跳过|aria-|已持久|重读|确认|status|备注|^\*\*|展示\s*\d'
    # file paths and summary-status cells are never a keyword
    r'|\.tsv|\.md|/\w+/|成功执行|成功$|执行$|already',
    re.IGNORECASE,
)
_ARROW_NUM_RE = re.compile(r'(\d+(?:\.\d+)?)\s*(?:→|->|=>|➜)\s*(\d+(?:\.\d+)?)')
_PLAIN_NUM_RE = re.compile(r'(?<![\d.])(\d+\.\d{1,2})(?![\d])')
_HEADER_CELL0 = {
    '操作',
    '关键词',
    '目标',
    'row',
    '#',
    '序号',
    '定向词',
    'target',
    'targets',
    '类型',
    'keyword',
}


def extract_executed_bid_pause(
    log_text: str,
) -> list[tuple[str | None, str, str, str, float | None]]:
    """``[(campaign_id|None, keyword, match, kind, value)]`` from the log.

    ``kind`` ∈ {'bid','pause'}; ``value`` is the applied bid (float) for
    bids, else None. Handles TWO log dialects:

    1. **Quoted-verb prose** (``| Raise "mouse/" Exact bid | USD 0.65 |
       applied``) — the original schema; requires an applied marker.
    2. **Action tables** the live agents actually emit — a per-campaign
       ``## … C_ID`` header then rows like
       ``| 提价 | mouse (Phrase) | 0.27 | 0.35 | 551 | 0.35 ✓ |`` or
       ``| keyword | Phrase | old | new | ✅ |`` under an ``### 出价`` /
       ``### 暂停`` sub-section, or ``| 提价 | cat (Category) | 0.6→0.72 |``.
       (Before this, the gate parsed 0 rows from these → it passed every
       noon/Amazon task vacuously. See ad-audit-execution-phase memory.)

    Conservative: a row that cannot be confidently identified as a
    bid/pause on a real keyword contributes nothing (summary/header/prose
    cells are filtered). Mirrors ``normalize_term`` keyword canon.
    """
    found: list[tuple[str | None, str, str, str, float | None]] = []
    current: str | None = None
    subsec: str | None = None  # current ### action context for tables
    in_summary = False  # inside a 汇总 / owner-flag block → ignore rows
    for raw in log_text.splitlines():
        line = raw.strip()
        if line.startswith('#'):
            if _SUMMARY_HDR_RE.search(line):
                in_summary, subsec = True, None
                continue
            in_summary = False  # any real campaign/action header reopens parse
            cm = _LOG_CAMP_RE.search(line)
            if cm:
                current = cm.group(1)
            if line.startswith('###'):
                low = line.lower()
                if '出价' in line or 'bid' in low or '提价' in line:
                    subsec = 'bid'
                elif '暂停' in line or 'pause' in low:
                    subsec = 'pause'
                elif '否定' in line or 'negativ' in low:
                    subsec = 'neg'
                else:
                    subsec = None
            continue
        if in_summary:  # summary/flag rows: cids + arrows → not real actions
            continue
        if not line.startswith('|'):
            continue
        if _REVERT_RE.search(line):  # applied then undone → not a stray
            continue
        cells = [c.strip() for c in line.strip('|').split('|')]
        if len(cells) < 2:
            continue
        if cells[0].lower() in _HEADER_CELL0 or all(
            set(c) <= set('-: ') for c in cells
        ):
            continue
        row = ' '.join(cells)
        # A row that records a skip / no-op / pre-existing state is NOT an
        # executed change — never count it (this is the fix for the gate
        # reading an "⏭️ already-paused" row as an agent over-pause and then
        # telling the agent to re-enable a live ad it never touched).
        if _SKIP_MARK_EN.search(row) or _SKIP_MARK_CJK.search(row):
            continue
        cm = _LOG_CAMP_RE.search(row)
        cid = cm.group(1) if cm else current

        # ── dialect 1: quoted keyword + applied marker (back-compat) ──
        km = _LOG_QUOTED_RE.search(row)
        if km and _APPLIED_RE.search(row):
            kw = normalize_term(km.group(1))
            match = _norm_match(row)
            if _PAUSE_VERB_RE.search(row):
                found.append((cid, kw, match, 'pause', None))
            elif _BID_VERB_RE.search(row):
                nums = _CCY_NUM_RE.findall(row) or _PLAIN_NUM_RE.findall(row)
                if nums:
                    try:
                        found.append((cid, kw, match, 'bid', float(nums[0])))
                    except ValueError:
                        pass
            continue
        if km:  # quoted but not marked applied → skip (old behaviour)
            continue

        # ── dialect 2: action table rows ──
        # Action may be in cell 0 (``| 提价 | …``), in an ``### 出价``
        # sub-section, OR in a trailing 动作/status cell
        # (``| kw | match | target | live | applied (a→b) |``). Infer from
        # the whole row, preferring an explicit pause/applied marker.
        c0 = cells[0]
        rowl = row.lower()
        has_arrow = bool(_ARROW_NUM_RE.search(row))
        is_pause = (
            '暂停' in c0
            or 'pause' in c0.lower()
            or 'paused' in rowl
            or '已暂停' in row
        )
        is_bid = (
            '提价' in c0
            or '提高' in c0
            or 'raise' in c0.lower()
            or 'applied' in rowl
            or '已应用' in row
            or has_arrow
            # a ✅-marked non-pause row carrying a value is an applied bid
            # (e.g. ``| kw | Broad | 降价 | 1.08 | 3.50 | 1.08 | ✅ 已降低 |``)
            or (_APPLIED_RE.search(row) or '已降低' in row or '已提高' in row)
        )
        if c0 in ('否定',) or '否定' in c0 or 'negativ' in c0.lower():
            action = 'neg'
        elif is_pause and not has_arrow:
            action = 'pause'
        elif is_bid:
            action = 'bid'
        else:
            action = subsec
        if action not in ('bid', 'pause'):
            continue
        # A row that records a skip / no-op is not an executed change.
        if re.search(r'skip|跳过|未点名|勿动|未动|maintain|维持|hold', rowl):
            continue
        match = _norm_match(row)
        # keyword: first cell that is a real target name
        kw_raw = None
        for idx, c in enumerate(cells):
            if (
                idx == 0
                and action
                and (
                    '提价' in c
                    or '暂停' in c
                    or 'raise' in c.lower()
                    or 'pause' in c.lower()
                )
            ):
                continue  # the action verb cell
            if _MATCH_TOKEN_RE.match(c) or _JUNK_KW_RE.search(c):
                continue
            if _ARROW_NUM_RE.search(c) or _PLAIN_NUM_RE.fullmatch(c):
                continue
            kw_raw = c
            break
        if not kw_raw:
            continue
        kw_raw = re.sub(r'\s*\([^)]*\)\s*$', '', kw_raw).strip()
        kw_raw = re.sub(r'\s*(展示|views?)\s*\d.*$', '', kw_raw, flags=re.I)
        if not kw_raw or _JUNK_KW_RE.search(kw_raw):
            continue
        kw = normalize_term(kw_raw)
        if action == 'pause':
            found.append((cid, kw, match, 'pause', None))
            continue
        am = _ARROW_NUM_RE.search(row)
        if am:
            val = float(am.group(2))
        else:
            nums = _CCY_NUM_RE.findall(row) or _PLAIN_NUM_RE.findall(row)
            val = float(nums[-1]) if nums else None
        if val is None:  # a real applied bid always carries a value
            continue
        found.append((cid, kw, match, 'bid', val))
    return found
