"""Stop-gate: executed bids/pauses must match the frozen audit report.

Runs at ``set_task_result``. Reads the task's EXECUTION_LOG.md for bid and
pause actions the agent marked applied (✅) and checks each against the
report's per-(campaign, keyword, match-type) targets (``ad_negation_allowlist
.build_bid_pause_targets``). It denies on three real production bugs the
skill teaches but never enforced:

  * **BID_MISMATCH** — an applied bid whose value differs from the report's
    target by > ±0.01 (the live ``1.30 → 11.3`` 10× overspend, and any
    other wrong value).
  * **OVER_PAUSE** — a paused (campaign, keyword) the report did not mark
    暂停, or more rows paused in a campaign than the report names (the run
    that paused 45 of 49 when 2 were intended).
  * **OFF_REPORT** — a bid/pause applied to a (campaign, keyword, match-
    type) that is not an actionable row in the report (the run that edited
    an Exact row the report never named, by matching on text only).

This is the structural backstop the bid/pause variant lacked — the sibling
of ``ad_negation_allowlist`` (which governs 否定 rows). It trusts the
agent-written log; the live-navigating exec reviewer remains the exhaustive
log-vs-live check. Soft fail-open after ``STALL_CAP`` denials so a parser
edge case can never trap a task forever.
"""

from __future__ import annotations

import re

from app.ai.ad_execution_targets import (
    _LOG_CAMP_RE,
    _SKIP_MARK_CJK,
    _SKIP_MARK_EN,
    extract_executed_bid_pause,
    load_bid_pause_targets,
)
from app.ai.ad_negation_allowlist import (
    _task_dir,
    extract_reverted_terms,
    normalize_term,
    task_scope_text,
)
from app.ai.stop_gates import GateDeny, record_attempt

GATE_NAME = 'ad_execution_fidelity'

STALL_CAP = 3

_BID_TOL = 0.01

# An "only-raise / live ≥ target" rationale — valid to skip a RAISE row, but
# INVALID on a 降至 (lower) row (that leaves a high-ACOS bid overspending).
_ONLY_RAISE_SKIP_RE = re.compile(
    r'only[\s-]?raise|skipped?[\s-]?higher|live\s*[≥>]=?\s*target'
    r'|[≥>]\s*target|高于目标|已高于|不下调|仅升|只升',
    re.IGNORECASE,
)

_denials: dict[str, int] = {}


def reset_progress(task_id: str) -> None:
    """Drop per-task denial state (call on terminal success)."""
    _denials.pop(task_id, None)


def is_stalled(task_id: str) -> bool:
    """Fail open once the agent has been denied ``STALL_CAP`` times."""
    return _denials.get(task_id, 0) >= STALL_CAP


def _flagged_campaigns(log_text: str) -> set[str]:
    """Campaign ids the log marks skipped/flagged/unreachable/inapplicable.

    A line that names a campaign id AND carries a skip/flag marker (⏭️,
    already-paused, drift, unreachable, 不适用, 留给店主复审, …) means that
    campaign's report rows were deliberately addressed-as-skipped — they
    must not read as INCOMPLETE.
    """
    flagged: set[str] = set()
    for line in log_text.splitlines():
        if _SKIP_MARK_EN.search(line) or _SKIP_MARK_CJK.search(line):
            m = _LOG_CAMP_RE.search(line)
            if m:
                flagged.add(m.group(1))
    return flagged


def _read_execution_log(task_id: str) -> str | None:
    """The task's own EXECUTION_LOG (written to its task dir by the skill).

    Reads ``<task_dir>/EXECUTION_LOG.md`` first, then any
    ``EXECUTION_LOG*.md`` directly in the task dir (NOT the report's dir —
    the report lives in the parent for batch tasks, the log in the child).
    """
    d = _task_dir(task_id)
    primary = d / 'EXECUTION_LOG.md'
    try:
        if primary.exists():
            return primary.read_text(encoding='utf-8')
        parts = []
        for f in sorted(d.glob('EXECUTION_LOG*.md')):
            parts.append(f.read_text(encoding='utf-8'))
        return '\n'.join(parts) if parts else None
    except OSError:
        return None


def check(
    result_text: str,
    task_id: str | None = None,
    rules: dict | None = None,
) -> GateDeny | None:
    """Deny if EXECUTION_LOG bids/pauses diverge from the report targets."""
    if not task_id:
        return None
    targets = load_bid_pause_targets(task_id)
    bids, pauses, rows = targets['bids'], targets['pauses'], targets['rows']
    bid_dirs = targets.get('bid_dirs', {})
    if not (bids or pauses):  # non-ad task / no report → no-op
        return None
    log_text = _read_execution_log(task_id)
    if not log_text:
        return None

    excused = extract_reverted_terms(log_text)

    bad_bid: list[str] = []
    over_pause: list[str] = []
    off_report: list[str] = []
    bad_lower_skip: list[str] = []
    pause_count: dict[str, int] = {}

    for cid, kw, match, kind, value in extract_executed_bid_pause(log_text):
        if not kw or kw in excused:
            continue
        key = (kw, match)
        camp_rows = rows.get(cid, set()) if cid else set()
        if kind == 'bid':
            target = bids.get(cid, {}).get(key) if cid else None
            if target is None and match:
                # tolerate a missing/garbled match cell in the log
                target = next(
                    (
                        v
                        for (k2, _m2), v in bids.get(cid, {}).items()
                        if k2 == kw
                    ),
                    None,
                )
            if target is not None:
                if abs(value - target) > _BID_TOL:
                    bad_bid.append(
                        f'{cid or "?"} / "{kw}" {match or "?"}: applied '
                        f'{value:g}, report target {target:g}'
                    )
            elif cid and (bids.get(cid) or pauses.get(cid)):
                # report drilled this campaign but never named this row
                off_report.append(f'{cid} / "{kw}" {match or "?"} (bid)')
        elif kind == 'pause':
            if cid:
                pause_count[cid] = pause_count.get(cid, 0) + 1
            in_pause = cid and key in pauses.get(cid, set())
            in_rows = cid and key in camp_rows
            if not in_pause and (cid and (bids.get(cid) or pauses.get(cid))):
                if in_rows:
                    over_pause.append(f'{cid} / "{kw}" {match or "?"}')
                else:
                    off_report.append(f'{cid} / "{kw}" {match or "?"} (pause)')

    # Count-based over-pause: more pauses applied than the report names.
    for cid, n in pause_count.items():
        named = len(pauses.get(cid, set()))
        if named and n > named:
            over_pause.append(f'{cid}: {n} rows paused, report names {named}')

    # ── COMPLETENESS: every report row for a scoped campaign must be
    # addressed in the log (applied OR mentioned as skipped). A report
    # keyword that never appears in the log = a stopped-early / skipped
    # campaign — the bug that let a 25/69 partial run pass as "done".
    # Scope = campaigns the task description names (so a clean partial
    # batch isn't faulted for campaigns it never claimed). Category rows
    # are skipped (console truncates names → unreliable substring match).
    incomplete: list[str] = []
    scope = task_scope_text(task_id).lower()
    if scope:
        scope_cids = [
            c for c in (set(bids) | set(pauses)) if c.lower() in scope
        ]
        log_low = log_text.lower()
        # Campaigns the log marks skipped/flagged/unreachable/inapplicable at
        # the campaign level (e.g. an SB-Video campaign with no keyword
        # surface, or a drifted/unreachable campaign) — their report rows are
        # ADDRESSED. Without this the gate read
        # "100000000000000 / wireless mouse (raise)" as an unaddressed row
        # even though the log flagged 100000000000000 inapplicable, and
        # the deny pressured the agent toward unsafe live action.
        flagged_cids = _flagged_campaigns(log_text)
        for cid in scope_cids:
            if cid in flagged_cids:
                continue
            for (kw, match), tgt in bids.get(cid, {}).items():
                if match == 'category' or kw in excused:
                    continue
                kwn = normalize_term(kw)
                if len(kwn) >= 3 and kwn.lower() not in log_low:
                    incomplete.append(
                        f'{cid} / "{kw}" {match or "?"} (raise→{tgt:g})'
                    )
            for kw, match in pauses.get(cid, set()):
                if match == 'category' or kw in excused:
                    continue
                kwn = normalize_term(kw)
                if len(kwn) >= 3 and kwn.lower() not in log_low:
                    incomplete.append(f'{cid} / "{kw}" {match or "?"} (pause)')

    # ── WRONG-DIRECTION SKIP: a 降至 (lower) row skipped with an only-raise /
    # "live ≥ target" rationale. "only-raise" is valid ONLY for a raise row;
    # citing it to skip a lowering row leaves a high-ACOS bid overspending
    # (an observed production gap: 18 降至 rows skipped as only-raise).
    for cid, dirs in bid_dirs.items():
        for (kw, match), d in dirs.items():
            if d != 'down' or kw in excused:
                continue
            kwn = normalize_term(kw)
            if len(kwn) < 3:
                continue
            for line in log_text.splitlines():
                ll = line.lower()
                if kwn.lower() in ll and _ONLY_RAISE_SKIP_RE.search(line):
                    tgt = bids.get(cid, {}).get((kw, match))
                    bad_lower_skip.append(
                        f'{cid} / "{kw}" {match or "?"} (LOWER→{tgt:g}) — '
                        f'skipped as only-raise/live≥target'
                    )
                    break

    if not (
        bad_bid or over_pause or off_report or incomplete or bad_lower_skip
    ):
        return None

    record_attempt(task_id, GATE_NAME)
    _denials[task_id] = _denials.get(task_id, 0) + 1

    def _sec(title: str, items: list[str]) -> str:
        if not items:
            return ''
        listed = '\n'.join(f'  - {s}' for s in items[:15])
        more = '' if len(items) <= 15 else f'\n  …and {len(items) - 15} more'
        return f'\n{title}:\n{listed}{more}'

    return GateDeny(
        gate=GATE_NAME,
        reason=(
            'EXECUTION_LOG records ad changes that DIVERGE from the frozen '
            'audit report. On a production store these are real-money '
            'errors. Correct each on the live console, update EXECUTION_LOG '
            '(fix the value, or mark 回退/removed), then re-submit.'
            + _sec(
                'BID MISMATCH (applied bid ≠ report target). Re-open the bid '
                'field, clear it FULLY (keys Control+a) before typing — a '
                'value like 11.3 vs target 1.30 is a clear-failure '
                'concatenation — set the EXACT target, verify the cell shows '
                'it',
                bad_bid,
            )
            + _sec(
                'OVER-PAUSE (more rows paused than the report names 暂停). '
                'Do NOT autonomously re-enable a live ad — un-pausing is a '
                'state change the report did not request and an already-paused '
                'row is not your doing. If YOU paused these THIS RUN in error, '
                're-enable ONLY those; otherwise leave them paused, mark each '
                'in the log (already-paused / report-keeps) and FLAG for owner',
                over_pause,
            )
            + _sec(
                'OFF-REPORT (a bid/pause you APPLIED to a row that is not an '
                'actionable report row). If it is a BID, re-open the field and '
                'restore the prior value. If it is a PAUSE you applied THIS '
                'RUN, re-enable ONLY that row. Touch only the exact rows the '
                'report names (match BOTH text and match-type). If unsure '
                'whether you changed it this run, leave it and FLAG for owner '
                '— never un-pause a row you did not pause',
                off_report,
            )
            + _sec(
                'INCOMPLETE (report rows for in-scope campaigns NOT addressed '
                'in the log — neither applied nor recorded as skipped). Do not '
                'submit a partial run: apply each, or add a row marking it '
                'skipped + the reason (e.g. live ≥ target / keyword drifted)',
                incomplete,
            )
            + _sec(
                'WRONG-DIRECTION SKIP (a 降至/LOWER row skipped as "only-raise"'
                ' / "live ≥ target"). only-raise is valid ONLY for a raise '
                'row; a 降至 row is a high-ACOS bid the report wants CUT. Lower'
                ' it to the target if live > target (or log "already ≤ target"'
                ' if it is genuinely at/below). Do not leave it overspending',
                bad_lower_skip,
            )
        ),
    )
