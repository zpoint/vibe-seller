"""Ground-truth scope helpers for the ad-audit completeness gate.

The completeness gate historically trusted the agent's own report prose
for both what to audit (a hardcoded ``(amazon|noon)`` combo regex) and
how much (the self-reported ``**进度**: drilled D/A`` line — the agent
wrote BOTH numbers, so it could shrink the denominator to match its
effort). This module supplies an authoritative alternative:

``AUDIT_SCOPE.json`` (written by the skill's enumeration step at the
task-workspace root) lists the real (platform, country) combos and the
authoritative active campaign-id set per combo. The gate checks report
COVERAGE against this instead of the agent's claim — closing the
"new platform silently passes" hole (#1) and the "lie about the
denominator" hole (#2).

Everything here is **deterministic** — no LLM, no API key. Semantic
"real drill vs page manifest" judgment (#3) is left to the AGENT: the
configured backend can spawn a review subagent (see
``amazon-ads/references/reviewer-loop.md``) with no extra credential.
The server gate keeps the fast, countable checks; the manifest heuristic
(``建议``-column count) lives in ``ad_completeness_review``.

``AUDIT_SCOPE.json`` is a PRECONDITION, not an option. A report section
that writes a ``**进度**: drilled D/A`` line is asserting an authoritative
denominator, so it must back that assertion with a scope entry; absence is
itself a gap (see ``ad_completeness_review``). There is no silent
fall-back to self-report — that fall-back is what let an ungrounded run
be accepted with 27 of 30 Amazon campaigns never checked.

A narrow "investigate one ad" task is still cheap to satisfy: it declares
a narrow scope (``active_ids`` = the one campaign, ``exhaustive: false``)
rather than getting a free pass.

**Self-check.** ``active_ids`` is agent-written, so an agent could shrink
it in the JSON exactly as it used to shrink ``A`` in the prose. Each combo
therefore carries an INDEPENDENTLY-OBSERVED total (``total_active``) that
must equal ``len(active_ids)``:

- Amazon — the ``state=enabled`` row count of the bulk export
  (``ads_bulk.py scope`` prints both, derived from the downloaded file).
- noon — the ``Live N`` status chip on the campaign list, which is a
  server-rendered total independent of how far the lazy list was
  scrolled. This is what catches the "scrolled 20 of 45 rows" failure:
  the ids number 20, the chip says 45, the scope is rejected as stale.
"""

from __future__ import annotations

import json
import re
import time

from app.config import VIBE_SELLER_DIR

SCOPE_FILENAME = 'AUDIT_SCOPE.json'
# Server-written counterpart to AUDIT_SCOPE.json: the combos the store is
# configured for. The agent fills in the campaigns; the server fixes the
# marketplaces. See :func:`write_declared_targets`.
TARGETS_FILENAME = 'AUDIT_TARGETS.json'


def scope_path(task_id: str):
    """Path to a task's AUDIT_SCOPE.json (task-workspace root)."""
    return VIBE_SELLER_DIR / 'tasks' / task_id / SCOPE_FILENAME


def targets_path(task_id: str):
    """Path to a task's AUDIT_TARGETS.json (task-workspace root)."""
    return VIBE_SELLER_DIR / 'tasks' / task_id / TARGETS_FILENAME


# How far back a TSV still counts as evidence of "recently had campaigns".
# ~6 weekly audits. Without a window this count only ever GROWS: TSVs are
# never deleted, so a store audited for a year accumulates every campaign
# it ever ran and the shrink threshold below drifts toward always firing.
PRIOR_TSV_WINDOW_DAYS = 45


def prior_campaign_tsvs(
    slug: str | None,
    platform: str,
    country: str,
    within_days: int = PRIOR_TSV_WINDOW_DAYS,
) -> int:
    """Campaigns this store's RECENT audits drilled in this combo.

    Recorded history, and the only independent evidence the server has
    about a marketplace it cannot browse. Used to challenge an
    "``active_ids: []``, nothing live here" claim, and a collapse against
    it: a combo that produced per-campaign TSVs recently did have
    campaigns, so a zero (or a fraction) now is a regression to explain
    rather than assert.

    **This is an UPPER BOUND on the live-active count, not an equal.** A
    prior audit drills what was active THEN, and campaigns get paused, so
    this counts roughly "All" where the scope declares "Live". Measured on
    one store: noon SA reads `Live 8` / `All 12` on the console, and its
    TSV history is 12 campaigns — of which 8 were touched in the last two
    weeks. That gap is why the caller must challenge only a COLLAPSE
    (declared below HALF of this) rather than any shortfall; comparing for
    equality here would flag every normally-pruned marketplace.

    Counts DISTINCT CAMPAIGNS, not files. Each drilled campaign leaves two
    (``<id>.tsv`` and ``<id>.searchterms.tsv``), so counting files doubles
    every total and any caller comparing it against a campaign count is
    wrong by 2x — which is exactly how a first cut of the shrink check
    flagged 21-of-22 and 8-of-10 combos as collapsed. The stem before the
    first ``.`` is the campaign id.

    Counted under the store's own directory — never a glob across
    ``stores/*``, which would borrow another store's history. Only
    campaigns whose newest TSV is within ``within_days`` count, so the
    bound tracks recent reality instead of growing forever.

    Directory names are matched CASE-INSENSITIVELY. These paths are
    agent-created and their casing is genuinely inconsistent in the wild
    (observed on one store: ``ads/amazon/SA`` beside ``ads/amazon/ae`` and
    ``ads/noon/sa``). A case-sensitive lookup finds them on macOS, whose
    default volume folds case, and silently returns 0 on Linux/WSL — which
    would turn this evidence check into a no-op exactly where the product
    also ships.
    """
    if not slug:
        return 0
    base = VIBE_SELLER_DIR / 'stores' / slug / 'ads'
    want_p, want_c = platform.strip().lower(), country.strip().lower()
    try:
        plat_dirs = [d for d in base.iterdir() if d.name.lower() == want_p]
    except OSError:
        return 0
    cutoff = time.time() - within_days * 86400
    newest: dict[str, float] = {}
    for plat in plat_dirs:
        try:
            for cdir in plat.iterdir():
                if cdir.name.lower() != want_c:
                    continue
                for p in cdir.iterdir():
                    if p.suffix != '.tsv':
                        continue
                    cid = p.name.split('.', 1)[0]
                    try:
                        m = p.stat().st_mtime
                    except OSError:
                        continue
                    newest[cid] = max(newest.get(cid, 0.0), m)
        except OSError:
            continue
    return sum(1 for m in newest.values() if m >= cutoff)


def write_declared_targets(
    task_dir,
    platform_countries: dict[str, list[str]],
    slug: str | None = None,
) -> None:
    """Write the SERVER's combo obligation for a task (best-effort).

    ``AUDIT_SCOPE.json`` grounds *how many* campaigns a combo owes, but
    the agent also writes *which combos exist* — so the one number it
    could still shrink was the combo count itself. Observed live: a store
    configured for 5 combos got a one-combo scope, the gate agreed the
    report was complete, and four marketplaces were never audited.

    The server already knows the answer (``Store.platform_countries`` —
    user-configured in Settings, and a monotonic union of what past tasks
    observed, so it never shrinks). Writing it into the workspace at task
    start makes it BOTH halves of the contract: the reviewer checks the
    scope against it, and the agent can read it to see what it owes
    instead of inferring the list from "reference only" prompt context.

    The agent may still declare a combo EMPTY (``active_ids: []`` /
    ``total_active: 0``) — "no live campaigns here" is a legitimate
    finding. What it can no longer do is omit the combo silently, nor
    assert emptiness for a marketplace that recently had campaigns (see
    :func:`prior_campaign_tsvs`).

    ``slug`` is recorded so the reviewer can find this store's own audit
    history without globbing ``stores/*`` and borrowing another store's.

    Best-effort: an unwritable workspace degrades to the previous
    behaviour (agent-chosen combo set) rather than blocking the task.
    """
    combos = [
        {'platform': str(p).strip().lower(), 'country': str(c).strip().upper()}
        for p, cs in (platform_countries or {}).items()
        if isinstance(cs, list)
        for c in cs
        if str(p).strip() and str(c).strip()
    ]
    if not combos:
        return
    payload: dict = {'combos': combos}
    if slug:
        payload['slug'] = slug
    try:
        (task_dir / TARGETS_FILENAME).write_text(
            json.dumps(payload, indent=2) + '\n',
            encoding='utf-8',
        )
    except OSError:  # pragma: no cover — best-effort
        pass


def declared_slug(task_id: str | None) -> str | None:
    """Store slug the server recorded in AUDIT_TARGETS.json, if any."""
    if not task_id:
        return None
    try:
        data = json.loads(targets_path(task_id).read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    slug = data.get('slug') if isinstance(data, dict) else None
    return str(slug) if slug else None


def load_declared_targets(task_id: str | None) -> list[dict]:
    """``[{platform, country}, …]`` the SERVER says this task must audit.

    Empty when the file is absent (a task from before this contract, or a
    store with no recorded platforms) — callers then fall back to the
    agent-declared combo set, i.e. the previous behaviour. Absence is
    never itself a gap: only a MISSING combo that the server did declare
    is one.
    """
    if not task_id:
        return []
    try:
        raw = targets_path(task_id).read_text(encoding='utf-8')
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for c in data.get('combos') or []:
        if not isinstance(c, dict):
            continue
        platform = str(c.get('platform') or '').strip()
        country = str(c.get('country') or '').strip()
        if platform and country:
            out.append({'platform': platform, 'country': country})
    return out


def same_combo(a: dict, b: dict) -> bool:
    """True if two combos name the same (platform, country), case-insens."""
    return (
        a['platform'].strip().lower() == b['platform'].strip().lower()
        and a['country'].strip().lower() == b['country'].strip().lower()
    )


def missing_declared_combos(
    scope_entries: list[dict], declared: list[dict]
) -> list[dict]:
    """Server-declared combos with no AUDIT_SCOPE.json entry."""
    return [
        d for d in declared if not any(same_combo(d, s) for s in scope_entries)
    ]


def load_audit_scope(task_id: str | None) -> dict | None:
    """Return the parsed AUDIT_SCOPE.json for a task, or None.

    None when there is no task_id, the file is absent/unreadable, or its
    shape is invalid — every None path means "no ground truth available",
    which the gate treats as the escape hatch (fall back to self-report).
    """
    if not task_id:
        return None
    try:
        raw = scope_path(task_id).read_text(encoding='utf-8')
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get('combos'), list):
        return None
    return data


def scope_combos(scope: dict | None) -> list[dict]:
    """Normalised ``{platform, country, active_ids, total_active,
    exhaustive}`` combos.

    ``total_active`` is the independently-observed active count (bulk-export
    enabled rows / noon ``Live N`` chip) used to detect a truncated
    ``active_ids``; ``None`` when the scope omitted it. ``exhaustive``
    defaults to True — a scope must opt OUT of claiming completeness, so a
    missing flag can never silently weaken the check.
    """
    if not scope:
        return []
    out: list[dict] = []
    for c in scope.get('combos') or []:
        if not isinstance(c, dict):
            continue
        platform = str(c.get('platform') or '').strip()
        country = str(c.get('country') or '').strip()
        if not platform or not country:
            continue
        raw_ids = c.get('active_ids')
        # Guard the type: a stray string would otherwise iterate into
        # single characters and produce a bogus id list.
        ids = (
            [str(i).strip() for i in raw_ids if i]
            if isinstance(raw_ids, list)
            else []
        )
        # Accept the platform-specific spellings the skills emit; they all
        # mean "the count observed at enumeration time".
        total = None
        for key in ('total_active', 'live_total', 'active_total'):
            v = c.get(key)
            if isinstance(v, bool):
                continue  # bool is an int subclass — not a count
            if isinstance(v, int) and v >= 0:
                total = v
                break
            if isinstance(v, str) and v.strip().isdigit():
                total = int(v.strip())
                break
        out.append({
            'platform': platform,
            'country': country,
            'total_active_source': str(
                c.get('total_active_source') or ''
            ).strip(),
            'active_ids': ids,
            'total_active': total,
            'exhaustive': c.get('exhaustive') is not False,
        })
    return out


def _token_in(token: str, text: str) -> bool:
    """True if ``token`` appears in ``text`` as a whole word (case-insens).

    Whole-word so a short country code can't match inside another word —
    e.g. ``US`` must NOT match ``business``, ``AE`` must NOT match
    ``header``. Boundaries are alphanumeric-aware (not ``\\b``) so tokens
    next to CJK/punctuation in a header like ``## Amazon SA — 广告审核``
    still match.
    """
    if not token:
        return False
    pat = rf'(?<![0-9a-z]){re.escape(token.lower())}(?![0-9a-z])'
    return re.search(pat, text.lower()) is not None


def section_matches_combo(header: str, combo: dict) -> bool:
    """True if a ``## ...`` header names this combo's platform + country.

    Whole-word token match (``## Amazon SA — 广告审核`` ↔ ``{amazon, SA}``),
    robust to trailing prose and to any platform, not just the hardcoded
    amazon/noon set — and not fooled by a token embedded in another word.
    """
    return _token_in(combo['platform'], header) and _token_in(
        combo['country'], header
    )


def find_combo_section(sections: dict[str, str], combo: dict) -> str | None:
    """Return the report section text for a combo, or None if absent."""
    for header, body in sections.items():
        if section_matches_combo(header, combo):
            return body
    return None


# A drill block is a LEVEL-3 heading exactly ("### <id> | name | …").
# Not level 3+: a campaign block legitimately contains deeper subsections
# (``#### Targeting``, ``#### Search Terms``), and splitting on those too
# would tear the reconciliation line out of the campaign block it belongs
# to. The flip side — an agent writing its campaigns at ``#### `` so they
# all merge into one parsed block — is handled by requiring the
# authoritative id to appear in a level-3 heading (see
# :func:`missing_active_ids`), which then reports them as undrilled rather
# than silently collapsing N obligations into 1.
_DRILL_HEAD_RE = re.compile(r'(?m)^###(?!#)\s+')


def drill_blocks(section_text: str) -> list[tuple[str, str]]:
    """``(heading_line, block_text)`` for every drill block in a section."""
    out: list[tuple[str, str]] = []
    for block in _DRILL_HEAD_RE.split(section_text)[1:]:
        lines = block.splitlines()
        if not lines:
            continue
        out.append((lines[0].strip(), block))
    return out


def blocks_by_active_id(
    section_text: str, active_ids: list[str]
) -> dict[str, str]:
    """Map each authoritative active id to its drill block, when present.

    The id must appear in the block's HEADING (same rule as
    :func:`missing_active_ids`) — an id mentioned only in prose or a
    summary table does not own a block. Ids with no block are simply
    absent from the result; the caller reports them as uncovered.
    """
    blocks = drill_blocks(section_text)
    found: dict[str, str] = {}
    for cid in active_ids:
        if not cid:
            continue
        for heading, body in blocks:
            if cid in heading:
                found[cid] = body
                break
    return found


def missing_active_ids(section_text: str, active_ids: list[str]) -> list[str]:
    """Active ids from the authoritative set with no DRILL BLOCK.

    Coverage requires the id to appear in a ``### ...`` drill-block heading
    — NOT merely somewhere in the section. Checking only headings closes
    the gaming hole where an agent pastes the id list into prose / a footer
    / a summary table without providing the per-campaign drill block.

    Level-3 headings only — the SAME rule as :func:`blocks_by_active_id`.
    The two must agree: if an id counted as covered here while owning no
    parsed block there, it would carry no per-campaign search-term
    obligation and pass unchecked.
    """
    headings = '\n'.join(h for h, _ in drill_blocks(section_text))
    return [cid for cid in active_ids if cid and cid not in headings]


# ── total_active provenance ──────────────────────────────────────────
#
# ``total_active == len(active_ids)`` proves the two agree, not that
# either was observed — trivially true when both come from the same
# parse. Live: a run declared a 12-campaign marketplace as 4/4 because
# its script silently dropped TSVs it could not read, and every check
# passed. The self-check needs an ANCHOR outside the agent's own
# derivation, so the scope must say WHERE the number came from:
#
#   "total_active_source": "bulk:bulk-<acct>-20260628-20260728-<n>.xlsx"
#   "total_active_source": "chip:Live 8"
#
# The bulk form is genuinely verifiable — the file is in the downloads
# dir, so the server opens it and counts ``state=enabled`` campaign rows
# itself. The chip form cannot be verified from disk, but requiring it
# turns "leave the number unexplained" into "state a reading", which a
# reviewer can check against the live page and which makes an invented
# total a claim rather than an omission.
# Matched ANYWHERE in the string, not anchored, and a third form for
# marketplaces with no bulk export at all.
#
# The first cut anchored these (^bulk:…$). An agent then supplied exactly
# what was asked for, wrapped in MORE context than the schema allowed —
#   "bulk:<acct>:bulk-<acct>-<dates>-<n>.xlsx (state=enabled, 行数=21)"
#   "noon ad manager chip:Live 7 at 2026-07-28T18:08 (scrolled inner
#    container until distinct link count = 7)"
# — and every one was rejected as "no provenance". That is the same
# failure the 搜索词对账 check already learned: an agent that answers more
# fully must not be told it answered not at all. The timestamp and the
# method are useful; find the token inside the prose and keep the rest.
#
# ``list:`` exists because Amazon AE has NO bulk-operations page (404), so
# there is no file to name — the honest source there is the campaign-list
# total, and without a token for it the agent had to write prose that
# could only fail.
_SOURCE_BULK_RE = re.compile(
    r'bulk:\s*(?:[\w.\-]+:\s*)?(?P<file>[\w.\-]+\.xlsx)', re.I
)
_SOURCE_CHIP_RE = re.compile(r'chip:\s*(?:live[\s=]*)?(?P<n>\d+)', re.I)
_SOURCE_LIST_RE = re.compile(
    r'list:\s*(?:live[\s=]*)?(?P<n>\d+)|live\s*=\s*(?:n\s*=\s*)?(?P<n2>\d+)',
    re.I,
)


def classify_total_source(source: str):
    """``('bulk', filename)`` / ``('chip'|'list', count)`` / ``(None, None)``.

    Tolerant by design — see the pattern comments. Bulk wins when several
    tokens appear, because it is the only form the server can verify.
    """
    text = source or ''
    m = _SOURCE_BULK_RE.search(text)
    if m:
        return 'bulk', m.group('file')
    m = _SOURCE_CHIP_RE.search(text)
    if m:
        return 'chip', int(m.group('n'))
    m = _SOURCE_LIST_RE.search(text)
    if m:
        return 'list', int(m.group('n') or m.group('n2'))
    return None, None


def count_enabled_in_export(filename: str, downloads_dir=None) -> int | None:
    """``state=enabled`` campaign rows in a bulk export, or None.

    None means "could not check" — file absent, unreadable, or openpyxl
    missing — and callers must treat that as unverified rather than as a
    failure, so a missing download never blocks an otherwise good report.
    """
    base = downloads_dir or (VIBE_SELLER_DIR / 'downloads')
    try:
        matches = [p for p in base.rglob(filename) if p.is_file()]
    except OSError:
        return None
    if not matches:
        return None
    # Imported HERE, not at module scope. openpyxl is a dev/test + skill
    # dependency (`pyproject.toml` test group, `amazon-ads/requirements.txt`)
    # — NOT a server runtime dep, so a production install does not have it.
    # A module-level import makes this file unimportable and takes the whole
    # server down at boot with ModuleNotFoundError; I did exactly that once,
    # chasing ruff's import-outside-top-level rule. Absence is also a
    # legitimate state for this function, whose contract is already
    # "None = could not check".
    try:
        from openpyxl import load_workbook  # noqa: PLC0415
    except ImportError:
        return None
    try:
        # NOT read_only: these workbooks ship without dimension
        # metadata, and openpyxl's read-only mode then reports 1 row per
        # sheet and iterates nothing — the count came back None on a file
        # that plainly has 478 rows. Slower, but it actually reads.
        wb = load_workbook(matches[0], data_only=True)
    except Exception:
        return None
    try:
        for name in wb.sheetnames:
            ws = wb[name]
            rows = ws.iter_rows(values_only=True)
            header = next(rows, None)
            if not header:
                continue
            idx = {str(h).strip().lower(): i for i, h in enumerate(header) if h}
            ent, st = idx.get('entity'), idx.get('state')
            if ent is None or st is None:
                continue
            n = 0
            for r in rows:
                if len(r) <= max(ent, st):
                    continue
                if (
                    str(r[ent] or '').strip().lower() == 'campaign'
                    and str(r[st] or '').strip().lower() == 'enabled'
                ):
                    n += 1
            if n:
                return n
    except Exception:
        return None
    finally:
        wb.close()
    return None
