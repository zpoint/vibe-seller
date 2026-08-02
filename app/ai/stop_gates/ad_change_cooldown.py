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

# A markdown table separator row, e.g. '|---|---|'. Marks the end of the
# header, which is where the key column is resolved.
_SEPARATOR_RE = re.compile(r'^\|[\s:|-]+\|$')
# '### <campaign id> | <name> | <type>' — the block a row belongs to.
_CAMPAIGN_HEAD_RE = re.compile(r'^###\s+([^|]+?)\s*(?:\||$)')

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


# The two ad layers live in SEPARATE files — ``<id>.tsv`` holds targets
# (keywords / product targets, the things that carry a bid) and
# ``<id>.searchterms.tsv`` holds the queries those targets matched. They
# share a namespace of strings: a keyword "widget red" and a search term
# "widget red" are different objects that read identically.
#
# Merging them, as this gate first did, made a BID change on the keyword
# freeze a NEGATION of the same-named search term — different layer,
# different object, different action. Observed live, and unfixable from
# the agent's side because the denial named only the string.
LAYER_TARGET = 'target'
LAYER_SEARCH_TERM = 'searchterm'


def _layer_of_file(path: Path) -> str:
    return (
        LAYER_SEARCH_TERM
        if path.name.endswith('.searchterms.tsv')
        else LAYER_TARGET
    )


def _campaign_of_file(path: Path) -> str:
    """Campaign id a history file belongs to — it is the filename.

    The same keyword string lives in many campaigns, each with its own
    bid and its own data. Moving "widget red" in one campaign says
    nothing about "widget red" in another, so a cooldown that keyed on
    the string alone froze rows it had no evidence about. With the layer
    bug fixed and real matches finally surfacing, that over-reach became
    the dominant effect: one change flagged seven rows across three
    campaigns it was never applied to.
    """
    return path.name.split('.', 1)[0].strip().lower()


# Column that holds the thing a row is ABOUT, looked up by header name.
# Never by position: the output spec puts ``ad_group`` first in the
# targeting table and ``search_term`` first in the search-term one, so a
# gate assuming column 1 compares an AD GROUP name against target
# history — it never matches, and the bid cooldown silently never fires.
_TARGET_COLS = ('target', '定向词', 'keyword', '关键词')
_SEARCH_TERM_COLS = ('search_term', 'search term', '搜索词')


def row_key_column(header: list[str]) -> tuple[str, int] | None:
    """``(layer, column index)`` for a table header, or None."""
    cells = [h.strip().lower() for h in header]
    for i, c in enumerate(cells):
        if c in _SEARCH_TERM_COLS:
            return LAYER_SEARCH_TERM, i
    for i, c in enumerate(cells):
        if c in _TARGET_COLS:
            return LAYER_TARGET, i
    return None


def recent_changes(
    ads_root: Path,
    cooldown_days: float,
    today: date | None = None,
) -> dict[tuple[str, str, str], tuple[str, int]]:
    """``{normalised target: (applied_action, days_ago)}`` inside the window.

    Reads every per-campaign TSV under ``ads_root``. Keyed by target text
    rather than by campaign+target because the report's own tables are the
    thing being graded and they are not guaranteed to repeat the campaign
    id on every row; a target string colliding across campaigns would at
    worst extend a hold, which is the safe direction.
    """
    today = today or datetime.now(UTC).date()
    out: dict[tuple[str, str, str], tuple[str, int]] = {}
    if not ads_root.is_dir():
        return out
    for path in ads_root.rglob('*.tsv'):
        layer = _layer_of_file(path)
        campaign_id = _campaign_of_file(path)
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
                    # The two trees also disagree on the key column
                    # (`target` vs `keyword`), so a reader pinned to one
                    # name silently matched nothing in the other.
                    target = _norm(
                        row.get('target')
                        or row.get('keyword')
                        or row.get('search_term')
                        or ''
                    )
                    if not target:
                        continue
                    key = (campaign_id, layer, target)
                    prev = out.get(key)
                    # Keep the most RECENT change for a target.
                    if prev is None or ago < prev[1]:
                        out[key] = (action, ago)
        except (OSError, csv.Error, UnicodeDecodeError):
            # An unreadable history file must not block a report. The
            # cooldown is a guard rail, not the audit's own contract.
            logger.debug('cooldown: unreadable TSV %s', path, exc_info=True)
    return out


# The change record has lived in TWO trees, because the platform said
# both. Every task's system prompt routes durable run data to
# ``store-data/<slug>/`` ("never in stores/"), while the ads spec named
# ``stores/<slug>/ads/``. Observed live: one execution wrote each, so the
# newest change sat in the tree the gate was not reading and the cooldown
# read a stale entry — the exact failure it exists to prevent.
#
# The spec is now corrected to ``store-data``, but BOTH are read: history
# written under the old convention is still real history, and a reader
# that ignored it would forget every change made before the fix.
_ADS_SUBTREES = ('store-data', 'stores')


def ads_roots_for(task_id: str | None) -> list[Path]:
    """Every directory that may hold this store's ad-change history.

    Keyed off the slug the SERVER recorded in ``AUDIT_TARGETS.json`` — not
    anything the agent wrote — so a report cannot point the cooldown at
    another store's history.
    """
    slug = ad_scope.declared_slug(task_id)
    if not slug or '/' in slug or '\\' in slug or slug in {'.', '..'}:
        return []
    return [VIBE_SELLER_DIR / sub / slug / 'ads' for sub in _ADS_SUBTREES]


def ads_root_for(task_id: str | None) -> Path | None:
    """First existing ad-history root. Kept for single-root callers."""
    for root in ads_roots_for(task_id):
        if root.is_dir():
            return root
    return None


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
    roots = [ads_root] if ads_root is not None else ads_roots_for(task_id)
    if not roots:
        return None
    rules = rules or DEFAULT_RULES
    window = float(rules.get('change_cooldown_days', 7.0))
    if window <= 0:
        return None
    # Merge across trees, keeping the most RECENT change per target — a
    # stale entry in one tree must not mask a fresher one in the other.
    changed: dict[tuple[str, str, str], tuple[str, int]] = {}
    for root in roots:
        for key, hit in recent_changes(root, window, today).items():
            prev = changed.get(key)
            if prev is None or hit[1] < prev[1]:
                changed[key] = hit
    if not changed:
        return None

    bad: list[str] = []
    # Which table we are inside, and where its key column sits. Read from
    # the header rather than assumed: see ``row_key_column``.
    layer: str | None = None
    key_col = 0
    header: list[str] | None = None
    campaign = ''
    for line in result_text.splitlines():
        stripped = line.strip()
        # Campaign blocks are '### <id> | <name> | <type>'. Carried so a
        # denial can say WHICH campaign — without it the agent cannot
        # find the row, retries blind, and loops until it gives up.
        cm = _CAMPAIGN_HEAD_RE.match(stripped)
        if cm:
            campaign = cm.group(1).strip()[:40]
            header = None
            layer = None
            continue
        if not stripped.startswith('|'):
            header = None
            layer = None
            continue
        cells = [c.strip() for c in stripped.strip('|').split('|')]
        if _SEPARATOR_RE.match(stripped):
            found = row_key_column(header or [])
            layer, key_col = found if found else (None, 0)
            continue
        if layer is None:
            header = cells
            continue
        if key_col >= len(cells):
            continue
        target = _norm(cells[key_col])
        # Same object means same campaign, same layer, same target. A
        # report block that never names its campaign falls back to
        # layer+target so the guard still applies where structure is
        # missing.
        cid = campaign.strip().lower()
        hit = changed.get((cid, layer, target)) if cid else None
        if hit is None and not cid:
            hit = next(
                (
                    v
                    for (_c, lyr, tgt), v in changed.items()
                    if lyr == layer and tgt == target
                ),
                None,
            )
        if hit is None:
            continue
        rest = '|'.join(cells[key_col + 1 :])
        if not _MOVE_RE.search(rest):
            continue
        action, ago = hit
        # A hold that NAMES the recent change is exactly what we want, so
        # a row mentioning the date/window is not a violation even if it
        # also contains a move verb in its explanation.
        if re.search(r'冷却|观察期|刚(调|改|动)过|天前', rest):
            continue
        where = f'{campaign} · ' if campaign else ''
        layer_cn = '搜索词层' if layer == LAYER_SEARCH_TERM else '定向层'
        bad.append(
            f'{where}{layer_cn} 「{cells[key_col].strip()[:24]}」'
            f'（{ago} 天前 {action}）'
        )

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
