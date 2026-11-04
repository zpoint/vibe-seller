"""What counts as a recommendation to MOVE a bid or cut a target.

One definition for every gate that asks "does this report hand out
decisions?" — the declaration check (an ``investigate`` phase may not)
and the change cooldown (a target changed days ago may not be moved
again). Each used to carry its own Chinese-only copy; both now live in
``app/ai/ad_action_vocab.py``, the one bilingual vocabulary.

**Both languages, because the report follows the user's.** The language
gate holds a report to the language of the request, so a request written
in English produces an English report — and a Chinese-only pattern counts
"Lower bid to 0.80" as zero decisions. Observed in CI: an English bid
review declared ``investigate``; had it recommended changes, nothing
would have refused them.

维持 / "keep" / "hold" are absent by design: holding is not a move, and it
is exactly what the cooldown asks for.
"""

from __future__ import annotations

from app.ai.ad_action_vocab import HOLD_REASON_RE, MOVE_RE

__all__ = ['HOLD_RE', 'MOVE_RE']

#: A row that names the recent change it is waiting out. It may carry a
#: move verb in its explanation and still be the hold the cooldown wants.
HOLD_RE = HOLD_REASON_RE
