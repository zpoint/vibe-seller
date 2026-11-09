"""Ad-rule thresholds: defaults, overridden by the store's own notes.

Every number here is a default target, not a law. A store owner — or
the agent, when the user states a different standard — overrides one by
writing a line in ``stores/<slug>/notes.md`` (case-insensitive, ``:`` or
``=``)::

    acos_no_lower: 25
    cpc_floor_multiplier: 1.0

``set_task_result`` reads the task's store notes and passes the
resolved dict to the gates; ``resolve_rules`` is pure (takes the notes
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
    # Days a target is left alone after a change is APPLIED to it.
    #
    # A bid moved yesterday has yesterday's data behind it, so adjusting
    # it again is not tuning — it is reacting to noise the change itself
    # has not had time to produce. The correct recommendation in that
    # window is 维持, with the reason stated (when it was changed, and
    # that the window is not up).
    #
    # 7 days by default because a week covers a full weekly demand cycle.
    # Cadence genuinely varies by seller — some tune hourly, some
    # monthly — so this is overridable per store like every other rule.
    'change_cooldown_days': 7.0,
    # A new bid may not drop to/below the row's actual CPC times this:
    # below the price it already clears, the auction stops serving it.
    'cpc_floor_multiplier': 1.1,
    # Gross margin % assumed when the store has not recorded one; the
    # report says it used the default.
    'margin': 25.0,
}

# Per-store override patterns — ``key: value`` anywhere in notes.md;
# an underscore in the key may also be written as a space.
_OVERRIDE_RES: dict[str, re.Pattern[str]] = {
    key: re.compile(
        key.replace('_', '[_ ]?') + r'\s*[:=]\s*(\d+(?:\.\d+)?)',
        re.IGNORECASE,
    )
    for key in DEFAULT_RULES
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
