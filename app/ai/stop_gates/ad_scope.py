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


def prior_campaign_tsvs(slug: str | None, platform: str, country: str) -> int:
    """How many per-campaign TSVs a PRIOR audit left for this combo.

    Recorded history, and the only independent evidence the server has
    about a marketplace it cannot browse. Used to challenge an
    "``active_ids: []``, nothing live here" claim: a combo that produced
    per-campaign TSVs before did have campaigns, so a zero now is a
    regression that has to be explained rather than asserted.

    Per-campaign files only (``<id>.tsv`` / ``<id>.searchterms.tsv``),
    counted under the store's own directory — never a glob across
    ``stores/*``, which would borrow another store's history.
    """
    if not slug:
        return 0
    d = (
        VIBE_SELLER_DIR
        / 'stores'
        / slug
        / 'ads'
        / platform.strip().lower()
        / country.strip().lower()
    )
    try:
        return len([p for p in d.iterdir() if p.suffix == '.tsv'])
    except OSError:
        return 0


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
