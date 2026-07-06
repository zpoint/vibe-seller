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

Escape hatch: when ``AUDIT_SCOPE.json`` is ABSENT, none of this runs and
the gate falls back to its self-reported behaviour — so a first-time run
(no baseline enumerated yet) or a narrow "create / investigate one ad"
task is never blocked by ground-truth enforcement.
"""

from __future__ import annotations

import json
import re

from app.config import VIBE_SELLER_DIR

SCOPE_FILENAME = 'AUDIT_SCOPE.json'


def scope_path(task_id: str):
    """Path to a task's AUDIT_SCOPE.json (task-workspace root)."""
    return VIBE_SELLER_DIR / 'tasks' / task_id / SCOPE_FILENAME


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
    """Normalised list of ``{platform, country, active_ids}`` combos."""
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
        out.append({
            'platform': platform,
            'country': country,
            'active_ids': ids,
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


def missing_active_ids(section_text: str, active_ids: list[str]) -> list[str]:
    """Active ids from the authoritative set with no DRILL BLOCK.

    Coverage requires the id to appear in a ``### ...`` drill-block heading
    — NOT merely somewhere in the section. Checking only headings closes
    the gaming hole where an agent pastes the id list into prose / a footer
    / a summary table without providing the per-campaign drill block.
    """
    headings = '\n'.join(
        ln for ln in section_text.splitlines() if ln.lstrip().startswith('###')
    )
    return [cid for cid in active_ids if cid and cid not in headings]
