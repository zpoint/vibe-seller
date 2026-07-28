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
    # noon reconciliation FLOOR — now the SAME as Amazon's, because the
    # premise for a looser one was wrong.
    #
    # This was 0.40, justified as "Customer Queries attributes only part
    # of a campaign's spend to queries (observed 47–74% after full
    # pagination)". Measured directly against the live console: that
    # partial attribution does not exist. noon's Customer Queries TAB
    # renders a fixed TOP-15 and has no paginator, no load-more and no
    # rows-per-page control — the row count does not budge under repeated
    # inner-container scrolling, so "after full pagination" was in fact a
    # 15-row read. Its **Export** button (which the noon skill wrongly
    # called unreliable) yields the complete set, and then the two layers
    # agree exactly:
    #
    #   Auto campaign:   targeting T | ~10k query rows -> T (1.000)
    #   Manual campaign: targeting T |  ~400 query rows -> T (1.000)
    #
    # The same campaigns read off the 15-row tab give ~0.27 and ~0.79 of
    # that total — which is what produced the old low figure. So a low
    # ratio on noon is an INCOMPLETE CAPTURE exactly as on Amazon, and a
    # 0.40 floor silently accepted captures missing three quarters of the
    # data. Same floor for both platforms; the fix on the agent side is
    # "use the export", documented in noon-ads.
    'noon_reconcile_floor': 0.85,
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
