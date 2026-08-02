"""A TSV may not claim a change the execution log does not back.

The per-campaign TSV is the change record the NEXT audit reads: an
``applied_at`` inside the cooldown window freezes that target for a week.
So a row claiming a change that never landed is not a cosmetic error — it
silently suppresses a real adjustment, and nobody looking at the next
report can tell why.

Observed live: the agent wrote ``applied_action`` / ``applied_at`` /
``previous_bid`` into the TSV BEFORE driving the browser. The change did
land that time, so nothing broke — but only because the browser step
happened to succeed. Written in that order, a failed apply leaves the
record asserting something the account never saw.

The contract this enforces is therefore not "write it afterwards" (an
ordering nobody can observe after the fact) but the checkable end state:
**every TSV row claiming an applied change must appear as an applied
action in this task's EXECUTION_LOG.md.** The log is what
``ad_execution_fidelity`` already grades against the report and what the
live-navigating exec reviewer checks against the account, so backing the
TSV with it puts the change record on the same evidence as everything
else rather than on the agent's intent.

Only rows written in THIS run are graded. Earlier runs' entries are
history — they were backed by their own logs, which are long gone.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
import logging
from pathlib import Path
import re

from app.ai.ad_execution_targets import extract_executed_bid_pause
from app.ai.ad_negation_allowlist import _task_dir
from app.ai.stop_gates import GateDeny
from app.ai.stop_gates.ad_change_cooldown import (
    _days_ago,
    _is_absent,
    _norm,
    _parse_day,
    ads_root_for,
)

logger = logging.getLogger(__name__)

GATE_NAME = 'ad_writeback_backing'


# The ad console renders a keyword with its localised translation
# appended, so the log carries ``<term> <译名>`` where the TSV holds the
# plain ``<term>``. Compared verbatim, that denied a change which had
# demonstrably landed in the account — a false refusal of real work, the
# worst failure mode for a backstop. The CJK tail is stripped for
# matching, and an ambiguous match fails OPEN.
_CJK_RE = re.compile(r'[\u3000-\u9fff\uff00-\uffef]+')


def _match_keys(text: str) -> set[str]:
    """Every form of a keyword worth comparing on."""
    base = _norm(text)
    out = {base}
    stripped = _norm(_CJK_RE.sub(' ', base))
    if stripped:
        out.add(stripped)
    return out


def _logged_targets(log_text: str) -> set[str]:
    """Normalised targets the log says were actually acted on."""
    out: set[str] = set()
    for _cid, keyword, _match, _kind, _value in extract_executed_bid_pause(
        log_text
    ):
        if keyword:
            out |= _match_keys(keyword)
    return out


def unbacked_rows(
    ads_root: Path,
    log_text: str,
    today: date | None = None,
) -> list[str]:
    """TSV rows claiming a change made TODAY that the log does not back."""
    today = today or datetime.now(UTC).date()
    bad: list[str] = []
    if not ads_root.is_dir():
        return bad
    logged = _logged_targets(log_text)
    for path in ads_root.rglob('*.tsv'):
        try:
            with open(path, encoding='utf-8', newline='') as fh:
                for row in csv.DictReader(fh, delimiter='\t'):
                    action = (row.get('applied_action') or '').strip()
                    if _is_absent(action):
                        continue
                    day = _parse_day(row.get('applied_at') or '')
                    # Only this run's writes are gradeable; older entries
                    # were backed by logs that no longer exist. Compared
                    # through the same skew tolerance as the cooldown —
                    # an exact date equality here would miss every row the
                    # agent stamped with a local date that is ahead of
                    # ours, making this gate a no-op for those hours.
                    ago = _days_ago(day, today) if day else None
                    if ago is None or ago != 0:
                        continue
                    target = _norm(
                        row.get('target') or row.get('search_term') or ''
                    )
                    # Fail OPEN on an ambiguous match: missing a real
                    # problem is recoverable, denying work that actually
                    # landed is not.
                    if not target or _match_keys(target) & logged:
                        continue
                    bad.append(f'{path.name}: {target[:28]}（{action}）')
        except (OSError, csv.Error, UnicodeDecodeError):
            logger.debug('writeback: unreadable TSV %s', path, exc_info=True)
    return bad


def check(
    result_text: str,
    task_id: str | None = None,
    rules: dict[str, float] | None = None,
    *,
    ads_root: Path | None = None,
    log_text: str | None = None,
    today: date | None = None,
) -> GateDeny | None:
    """Deny when the change record asserts more than the log supports."""
    del rules
    if ads_root is None or log_text is None:
        # Resolved by the caller in production; without both there is
        # nothing to compare and silence is the only safe answer.
        if ads_root is None:
            ads_root = ads_root_for(task_id)
        if log_text is None and task_id:
            try:
                log_text = (
                    Path(_task_dir(task_id)) / 'EXECUTION_LOG.md'
                ).read_text(encoding='utf-8')
            except (OSError, TypeError):
                log_text = None
    if ads_root is None or not log_text:
        return None

    bad = unbacked_rows(ads_root, log_text, today)
    stale = report_bid_mismatches(result_text, ads_root, today)
    if stale and not bad:
        return GateDeny(
            gate=GATE_NAME,
            reason=(
                f'{len(stale)} 行的报告表格还写着改动前的出价。审阅台渲染的'
                '就是这张表，表里是旧价，下一个人看到的就是账户上已经不存在'
                '的数字——照着填只会得到一个「改了等于没改」的动作。'
                'apply 之后请把报告表格里这些行的出价一并更新成新值。'
                f'不一致：{"；".join(stale[:5])}。'
            ),
            gaps=tuple(stale[:12]),
        )
    if not bad:
        return None
    return GateDeny(
        gate=GATE_NAME,
        reason=(
            f'{len(bad)} 行 TSV 写了本次的 applied_action/applied_at，但 '
            'EXECUTION_LOG.md 里没有对应的已执行记录。TSV 是下一轮审计读的'
            '「改动台账」——写了就等于让这一行进入冷却期、下轮不再调整。'
            '所以只有**确认改动已在控制台生效并读回**之后才写这几列；'
            '没改成或还没确认的行，applied_* 留空。'
            f'违规样例：{"；".join(bad[:5])}。'
        ),
        gaps=tuple(bad[:12]),
    )


# Tolerance for comparing a report's printed bid against the record. Wide
# enough for formatting (2.9 vs 2.90), tight enough to catch a real
# divergence.
_BID_EPS = 0.005
_REPORT_ROW_RE = re.compile(r'^\|([^|]+)\|([^|]*)\|([^|]*)\|')


def report_bid_mismatches(
    result_text: str,
    ads_root: Path,
    today: date | None = None,
) -> list[str]:
    """Targets whose report row still shows the pre-change bid.

    The review console renders the report's own table, so a table left at
    the old value shows the next reviewer a bid the account no longer
    has. Typing what they can see then produces a move that changes
    nothing. Only rows this run actually applied are graded.
    """
    today = today or datetime.now(UTC).date()
    applied: dict[str, float] = {}
    if not ads_root.is_dir():
        return []
    for path in ads_root.rglob('*.tsv'):
        try:
            with open(path, encoding='utf-8', newline='') as fh:
                for row in csv.DictReader(fh, delimiter='\t'):
                    if _is_absent(row.get('applied_action')):
                        continue
                    day = _parse_day(row.get('applied_at') or '')
                    if day is None or _days_ago(day, today) != 0:
                        continue
                    target = _norm(
                        row.get('target')
                        or row.get('keyword')
                        or row.get('search_term')
                        or ''
                    )
                    try:
                        bid = float(str(row.get('bid') or '').strip())
                    except ValueError:
                        continue
                    if target:
                        applied[target] = bid
        except (OSError, csv.Error, UnicodeDecodeError):
            logger.debug('report-bid: unreadable TSV %s', path, exc_info=True)
    if not applied:
        return []

    bad: list[str] = []
    for line in result_text.splitlines():
        m = _REPORT_ROW_RE.match(line.strip())
        if not m:
            continue
        target = _norm(m.group(1))
        want = applied.get(target)
        if want is None:
            continue
        try:
            shown = float(m.group(3).strip())
        except ValueError:
            continue
        if abs(shown - want) > _BID_EPS:
            bad.append(
                f'{m.group(1).strip()[:24]}：报告 {shown} vs 实际 {want}'
            )
    return bad
