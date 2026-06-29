"""Single source of truth for ad-audit bid-rule thresholds.

Both bid-rule gates (``ad_bid_floor``, ``ad_scale_winners``) and the
completeness reviewer resolve their threshold through here instead of
hardcoding it, so each number lives in ONE place (``DEFAULT_RULES``).

A per-store ``stores/<slug>/notes.md`` may override a default, letting a
store owner tune the rules without code changes. Write a line anywhere
in that file (case-insensitive, ``:`` or ``=``):

    scale_roas: 6        # ROAS strictly above this → raise or justify
    acos_no_lower: 28    # ACOS % below this → never lower the bid

``set_task_result`` reads the task's store notes and passes the resolved
dict to the gates; ``resolve_rules`` itself is pure (takes the notes
text) so it stays trivially testable.
"""

from __future__ import annotations

import re

# THE one place to change a default ad-rule threshold.
DEFAULT_RULES: dict[str, float] = {
    # ACOS % at/below which a bid may never be lowered (only hold/raise).
    'acos_no_lower': 30.0,
    # ROAS strictly above which a converter must be raised or have its
    # hold explicitly justified (a bare Hold is a defect).
    'scale_roas': 5.0,
    # Max relative deviation allowed between the targeting-table totals
    # and the search-term-report totals of one campaign (spend and
    # clicks, same date window). Search terms must sum to the targets —
    # a larger gap means the two pages were read on DIFFERENT date
    # windows or the search-term capture is incomplete. Verified 0% on
    # a live campaign (213 terms, Export CSV); 15% leaves slack for
    # same-day attribution drift.
    'reconcile_tolerance': 0.15,
    # noon-only reconciliation FLOOR: Customer Queries attributes only
    # part of a campaign's spend to queries (observed 47–74% across
    # every live campaign after full pagination on a verified same-30d
    # window), so the symmetric tolerance above is unattainable there.
    # noon search-term spend must be ≥ this fraction of targeting
    # spend (upper bound stays 1+reconcile_tolerance). A wrong window
    # is still caught: a 7d read of a 30d page shows ~23% < 40%.
    'noon_reconcile_floor': 0.4,
    # Zero-order waste floors: a row with no orders must be cut (search
    # term → 否定; targeting keyword → 暂停/降) once EITHER threshold is
    # met — spend (store currency) or clicks. Clicks are the stronger
    # evidence: 10+ clicks with zero orders is a proven loser even at
    # low spend (store-owner rule: anything with spend and zero results
    # should be removed/disabled outright).
    'negate_waste_spend': 10.0,
    'negate_waste_clicks': 10.0,
}

# Per-store override patterns — matched anywhere in notes.md.
_OVERRIDE_RES: dict[str, re.Pattern[str]] = {
    'acos_no_lower': re.compile(
        r'acos[_ ]?no[_ ]?lower\s*[:=]\s*(\d+(?:\.\d+)?)', re.IGNORECASE
    ),
    'scale_roas': re.compile(
        r'scale[_ ]?roas\s*[:=]\s*(\d+(?:\.\d+)?)', re.IGNORECASE
    ),
    'reconcile_tolerance': re.compile(
        r'reconcile[_ ]?tolerance\s*[:=]\s*(\d+(?:\.\d+)?)', re.IGNORECASE
    ),
    'noon_reconcile_floor': re.compile(
        r'noon[_ ]?reconcile[_ ]?floor\s*[:=]\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE,
    ),
    'negate_waste_spend': re.compile(
        r'negate[_ ]?waste[_ ]?spend\s*[:=]\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE,
    ),
    'negate_waste_clicks': re.compile(
        r'negate[_ ]?waste[_ ]?clicks\s*[:=]\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE,
    ),
}


def resolve_rules(notes_text: str | None = None) -> dict[str, float]:
    """Return effective thresholds: ``DEFAULT_RULES`` overlaid with any
    overrides found in ``notes_text`` (a store's notes.md contents).
    """
    rules = dict(DEFAULT_RULES)
    if notes_text:
        for key, rx in _OVERRIDE_RES.items():
            m = rx.search(notes_text)
            if m:
                rules[key] = float(m.group(1))
    return rules
