"""Ground-truth scope helpers for the ad-audit completeness gate.

The completeness gate historically trusted the agent's own report prose
for both what to audit (a hardcoded ``(amazon|noon)`` combo regex) and
how much (the self-reported ``**进度**: drilled D/A`` line — the agent
wrote BOTH numbers, so it could shrink the denominator to match its
effort). This module supplies an authoritative alternative:

- ``AUDIT_SCOPE.json`` (written by the skill's enumeration step at the
  task-workspace root) lists the real (platform, country) combos and the
  authoritative active campaign-id set per combo. The gate checks report
  COVERAGE against this instead of the agent's claim — closing the
  "new platform silently passes" hole (#1) and the "lie about the
  denominator" hole (#2).
- ``llm_is_real_drill`` is a bounded, fail-open semantic check (#3) that
  judges "real per-campaign drill vs page manifest" without depending on
  the format-locked ``建议``-column regex.

Escape hatch: when ``AUDIT_SCOPE.json`` is ABSENT, none of this runs and
the gate falls back to its self-reported behaviour — so a first-time run
(no baseline enumerated yet) or a narrow "create / investigate one ad"
task is never blocked by ground-truth enforcement.
"""

from __future__ import annotations

import hashlib
import json
import re

from app.config import VIBE_SELLER_DIR
from app.env_options import Options

SCOPE_FILENAME = 'AUDIT_SCOPE.json'

# Truthy values for the opt-in LLM-manifest flag.
_TRUTHY = frozenset({'1', 'true', 'yes', 'on'})
# Cap the semantic-check cache so a long-running server can't grow it
# without bound (one entry per unique section text).
_LLM_CACHE_MAX = 512


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


# ── #3: LLM semantic manifest check (bounded, fail-open) ──────────────
#
# The deterministic detector counts tables whose header carries a
# ``建议``/``recommendation`` column. That is format-locked — a
# differently-formatted (or English, or new-platform) real drill can be
# misread as a manifest, and vice-versa. This asks an LLM the semantic
# question instead, and is used ONLY to confirm sections the deterministic
# check already passed. It NEVER blocks on its own unavailability: no API
# key / any error / an unclear answer all return None, and the caller then
# trusts the deterministic result.

_MANIFEST_SYSTEM = (
    'You judge one section of an e-commerce ad-audit report. Decide '
    'whether it contains REAL per-campaign drill detail — i.e. for the '
    'campaigns it covers, per-keyword or per-target rows with bids / '
    'metrics / recommendations — or whether it is only a MANIFEST: a '
    'list of campaigns with summary metrics and no per-target breakdown. '
    "Answer with exactly one word: 'real' or 'manifest'."
)

# section-text sha1 -> True(real) / False(manifest). Bounds cost across
# the many set_task_result submits of one converging audit.
_llm_cache: dict[str, bool] = {}


def llm_is_real_drill(section_text: str) -> bool | None:
    """True=real drill, False=manifest, None=unknown (fail open).

    Reuses the event-extractor's Anthropic client pattern. Any failure
    path (SDK missing, no key, API error, unparseable answer) returns
    None so the gate falls back to the deterministic heuristic.
    """
    if not section_text or not section_text.strip():
        return None
    # Opt-in only. Off by default so no synchronous LLM network call ever
    # enters the request path (``check`` runs inside the async
    # set_task_result handler); a deployer enables it deliberately and
    # accepts the bounded (timeout + cache) cost.
    if Options.AD_AUDIT_LLM_MANIFEST.get().strip().lower() not in _TRUTHY:
        return None
    key = hashlib.sha1(section_text.encode('utf-8')).hexdigest()
    if key in _llm_cache:
        return _llm_cache[key]
    try:
        import anthropic  # noqa: PLC0415 — optional; fail open if absent

        api_key = Options.ANTHROPIC_API_KEY.get() or None
        if not api_key:
            return None
        # Configurable model; bounded timeout so a stuck call can't block
        # the event loop indefinitely.
        model = Options.ANTHROPIC_MODEL.get() or 'claude-haiku-4-5-20251001'
        client = anthropic.Anthropic(api_key=api_key, timeout=8.0)
        resp = client.messages.create(
            model=model,
            max_tokens=8,
            system=_MANIFEST_SYSTEM,
            messages=[{'role': 'user', 'content': section_text[:12000]}],
        )
        answer = (resp.content[0].text or '').strip().lower()
    except Exception:
        return None
    verdict = None
    if 'manifest' in answer:
        verdict = False
    elif 'real' in answer:
        verdict = True
    if verdict is not None:
        # Bound the cache; simplest safe policy is to drop it wholesale
        # once it grows past the cap (entries are cheap to recompute).
        if len(_llm_cache) >= _LLM_CACHE_MAX:
            _llm_cache.clear()
        _llm_cache[key] = verdict
    return verdict
