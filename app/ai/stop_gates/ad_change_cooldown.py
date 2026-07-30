"""A target changed days ago must be left alone, and the report must say so.

Ad changes are not tuned continuously. A bid moved yesterday has only
yesterday's data behind it, so moving it again is not tuning — it is
reacting to noise the change has not had time to produce. Worse, the
second move destroys the evidence for the first: nobody can tell which
adjustment caused what.

So the rule is: within ``change_cooldown_days`` of an APPLIED change to a
target, the only defensible recommendation is 维持, and the reason has to
name the change and its date. "Hold" with no reason is indistinguishable
from the agent simply not noticing.

**Where the history comes from.** Nowhere new. The per-campaign TSVs
already exist under ``stores/<slug>/ads/<platform>/<country>/`` and carry
one row per target; execution writes ``applied_action`` / ``applied_at``
/ ``previous_bid`` onto the rows it actually changed. Those files live in
a git-backed workspace, so the file's own history is the audit trail —
there is no second ledger to keep in sync, and it cannot disagree with
the data it annotates.

This gate reads those columns and grades the report's recommendation for
the same target. It is deliberately one-directional: it flags a bid MOVE
inside the window, not a hold outside it. Holding is always allowed.
"""

from __future__ import annotations

import csv
from datetime import UTC, date, datetime
import logging
from pathlib import Path
import re

from app.ai.stop_gates import GateDeny, ad_scope
from app.ai.stop_gates.ad_rules import DEFAULT_RULES
from app.config import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

GATE_NAME = 'ad_change_cooldown'

# A recommendation that MOVES a bid or cuts a target. 维持 is absent by
# design — holding is what the cooldown asks for.
_MOVE_RE = re.compile(
    r'提高至|下调至|降至|下调到|提高出价|降低出价|加投|减投|暂停|否定'
)

# The report row for a target: the first cell is the target text.
_ROW_RE = re.compile(r'^\|([^|]+)\|(.+)\|\s*$')

# Placeholders an agent writes for "nothing here". Observed live: a real
# execution filled every untouched row's applied_* columns with ``-``
# rather than leaving them blank. Those rows were skipped only because the
# DATE failed to parse — so a ``-`` action beside a valid date would have
# registered as an applied change literally named "-". The codebase
# already treats an em dash as absent data when reading figures; this is
# the same convention, applied to the change record.
_ABSENT = {'', '-', '--', '—', '–', 'n/a', 'na', 'none', 'null'}


def _is_absent(raw: str | None) -> bool:
    return (raw or '').strip().lower() in _ABSENT


# A stamp DATED AHEAD of our clock is timezone skew, not corruption. The
# agent writes the seller's local date; this process compares in UTC, so
# for the hours each day that local runs ahead (UTC+8 for a Gulf/Asia
# seller), "today" arrives as tomorrow. Observed live: a change applied
# minutes earlier read as -1 days and was DISCARDED, which silently
# switched the cooldown off for exactly the window it was needed in.
#
# One day of slack covers every real offset (max ±14h). Anything further
# ahead is a bad stamp and stays ignored — we must not let a typo'd year
# freeze a target indefinitely.
_MAX_CLOCK_SKEW_DAYS = 1


def _days_ago(applied: date, today: date) -> int | None:
    """Age in days, tolerating clock skew; None when implausible."""
    ago = (today - applied).days
    if ago < -_MAX_CLOCK_SKEW_DAYS:
        return None
    return max(ago, 0)


def _norm(text: str) -> str:
    """Compare targets the way a human would: case- and space-insensitive."""
    return re.sub(r'\s+', ' ', (text or '').replace('**', '')).strip().lower()


def _parse_day(raw: str) -> date | None:
    """An ISO-ish date from the TSV, or None if it is not one.

    Tolerant on purpose: the column is agent-written, and an unreadable
    date must not be treated as "changed today" (which would freeze a
    target) nor as "never changed" silently — it returns None and the
    caller skips the row, which is the safe direction.
    """
    raw = (raw or '').strip()
    if not raw:
        return None
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})', raw)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def recent_changes(
    ads_root: Path,
    cooldown_days: float,
    today: date | None = None,
) -> dict[str, tuple[str, int]]:
    """``{normalised target: (applied_action, days_ago)}`` inside the window.

    Reads every per-campaign TSV under ``ads_root``. Keyed by target text
    rather than by campaign+target because the report's own tables are the
    thing being graded and they are not guaranteed to repeat the campaign
    id on every row; a target string colliding across campaigns would at
    worst extend a hold, which is the safe direction.
    """
    today = today or datetime.now(UTC).date()
    out: dict[str, tuple[str, int]] = {}
    if not ads_root.is_dir():
        return out
    for path in ads_root.rglob('*.tsv'):
        try:
            with open(path, encoding='utf-8', newline='') as fh:
                for row in csv.DictReader(fh, delimiter='\t'):
                    action = (row.get('applied_action') or '').strip()
                    if _is_absent(action):
                        continue
                    day = _parse_day(row.get('applied_at') or '')
                    if day is None:
                        continue
                    ago = _days_ago(day, today)
                    if ago is None or ago >= cooldown_days:
                        continue
                    target = _norm(
                        row.get('target') or row.get('search_term') or ''
                    )
                    if not target:
                        continue
                    prev = out.get(target)
                    # Keep the most RECENT change for a target.
                    if prev is None or ago < prev[1]:
                        out[target] = (action, ago)
        except (OSError, csv.Error, UnicodeDecodeError):
            # An unreadable history file must not block a report. The
            # cooldown is a guard rail, not the audit's own contract.
            logger.debug('cooldown: unreadable TSV %s', path, exc_info=True)
    return out


def ads_root_for(task_id: str | None) -> Path | None:
    """The store's ad-history directory for this task, if it has one.

    Keyed off the slug the SERVER recorded in ``AUDIT_TARGETS.json`` — not
    anything the agent wrote — so a report cannot point the cooldown at
    another store's history.
    """
    slug = ad_scope.declared_slug(task_id)
    if not slug or '/' in slug or '\\' in slug or slug in {'.', '..'}:
        return None
    return VIBE_SELLER_DIR / 'stores' / slug / 'ads'


def check(
    result_text: str,
    task_id: str | None = None,
    rules: dict[str, float] | None = None,
    *,
    ads_root: Path | None = None,
    today: date | None = None,
) -> GateDeny | None:
    """Deny when the report moves a target that was changed too recently.

    Signature matches the gate registry contract: ``set_task_result``
    calls every gate positionally as ``check(text, task_id, rules)``.
    ``ads_root`` stays available keyword-only so the history location can
    be pinned directly in tests.
    """
    if not result_text:
        return None
    if ads_root is None:
        ads_root = ads_root_for(task_id)
    if ads_root is None:
        return None
    rules = rules or DEFAULT_RULES
    window = float(rules.get('change_cooldown_days', 7.0))
    if window <= 0:
        return None
    changed = recent_changes(ads_root, window, today)
    if not changed:
        return None

    bad: list[str] = []
    for line in result_text.splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        target = _norm(m.group(1))
        hit = changed.get(target)
        if hit is None:
            continue
        rest = m.group(2)
        if not _MOVE_RE.search(rest):
            continue
        action, ago = hit
        # A hold that NAMES the recent change is exactly what we want, so
        # a row mentioning the date/window is not a violation even if it
        # also contains a move verb in its explanation.
        if re.search(r'冷却|观察期|刚(调|改|动)过|天前', rest):
            continue
        bad.append(f'{m.group(1).strip()[:24]}（{ago} 天前 {action}）')

    if not bad:
        return None
    return GateDeny(
        gate=GATE_NAME,
        reason=(
            f'{len(bad)} 行对「刚改过」的定向词又给了调整建议。这些行在 '
            f'{window:.0f} 天冷却期内已经执行过改动，改动后的数据还没攒够，'
            '再调一次只会把上一次的效果也搅乱、之后谁也说不清是哪一次起的作用。'
            '这些行请改成 **维持**，并在建议里写明依据（几天前改过什么、'
            '冷却期还没到、要看满一周数据再判断）。'
            f'违规样例：{"；".join(bad[:5])}。'
        ),
        gaps=tuple(bad[:12]),
    )
