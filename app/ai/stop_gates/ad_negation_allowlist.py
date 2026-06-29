"""Stop-gate: executed search-term negations must be on the report's list.

Runs at ``set_task_result``. Reads the task's EXECUTION_LOG.md for
negations the agent marked DONE (✅) and checks each against the
allowlist derived from the audit report (``ad_negation_allowlist``
module). Any executed negation whose term is approved for NO campaign in
the report is a STRAY — the agent freelanced it (the production bug:
negating a relevant 0-order term as if it were waste). The gate denies
``set_task_result`` (HTTP 400) and names the strays so the agent reverses
them (Negative targeting → select → Bulk actions → Archive) before the
task can complete.

Soft fail-open after ``STALL_CAP`` denials so a parser edge case can
never trap a task forever — the independent live-state review is the
exhaustive check; this is the cheap structural backstop that makes the
common freelance-negation mistake impossible to ship silently.
"""

from __future__ import annotations

from app.ai.ad_negation_allowlist import (
    extract_executed_negations,
    extract_reverted_terms,
    find_report,
    load_allowlist,
    load_exemptions,
    term_allowed,
)
from app.ai.stop_gates import GateDeny, record_attempt

GATE_NAME = 'ad_negation_allowlist'

STALL_CAP = 3

_denials: dict[str, int] = {}


def reset_progress(task_id: str) -> None:
    """Drop per-task denial state (call on terminal success)."""
    _denials.pop(task_id, None)


def is_stalled(task_id: str) -> bool:
    """Fail open once the agent has been denied ``STALL_CAP`` times."""
    return _denials.get(task_id, 0) >= STALL_CAP


def _read_execution_log(task_id: str) -> str | None:
    report = find_report(task_id)
    if report is None:
        return None
    log = report.parent / 'EXECUTION_LOG.md'
    try:
        return log.read_text(encoding='utf-8')
    except OSError:
        return None


def check(
    result_text: str,
    task_id: str | None = None,
    rules: dict | None = None,
) -> GateDeny | None:
    """Deny if EXECUTION_LOG records a negation not on the report list."""
    if not task_id:
        return None
    allowlist = load_allowlist(task_id)
    if not allowlist:  # non-ad task or no report → no-op
        return None
    log_text = _read_execution_log(task_id)
    if not log_text:  # nothing executed yet to validate
        return None

    # A term approved for negation in ANY campaign is acceptable when the
    # log row's campaign id is unattributed; otherwise check that exact
    # campaign. (Lenient on attribution, strict on membership — avoids
    # false strays from log rows that omit the campaign id.)
    any_campaign: set[str] = set()
    for terms in allowlist.values():
        any_campaign |= terms

    # Terms no longer live: undone via the negate→review→archive flow, or
    # human-approved off-report clear waste. Neither is a stray.
    excused = extract_reverted_terms(log_text) | load_exemptions(task_id)

    strays: list[str] = []
    seen: set[str] = set()
    for campaign_id, term in extract_executed_negations(log_text):
        if not term or term in seen or term in excused:
            continue
        seen.add(term)
        ok = (
            term_allowed(allowlist, campaign_id, term)
            if campaign_id
            else term in any_campaign
        )
        if not ok:
            label = f'{term}' + (
                f' (campaign {campaign_id})' if campaign_id else ''
            )
            strays.append(label)

    if not strays:
        return None

    record_attempt(task_id, GATE_NAME)
    _denials[task_id] = _denials.get(task_id, 0) + 1

    listed = '\n'.join(f'  - {s}' for s in strays[:25])
    more = '' if len(strays) <= 25 else f'\n  …and {len(strays) - 25} more'
    return GateDeny(
        gate=GATE_NAME,
        reason=(
            'EXECUTION_LOG records search-term negations that are NOT on '
            "the audit report's approved-negation list. On a production "
            'store these block relevant traffic. Reverse each one '
            '(Negative targeting tab → select the row → Bulk actions → '
            'Archive), then update EXECUTION_LOG (mark 回退/removed, drop '
            'the ✅), and re-submit. Negate ONLY terms the report marks '
            '**否定搜索词** for that campaign — do not negate a term just '
            'because it has 0 orders.\n\nStray negations to reverse:\n'
            f'{listed}{more}'
        ),
    )
