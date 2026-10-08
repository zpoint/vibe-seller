"""What counts as a recommendation to MOVE a bid or cut a target.

One definition for every gate that asks "does this report hand out
decisions?" — the declaration check (an ``investigate`` phase may not)
and the change cooldown (a target changed days ago may not be moved
again). Each used to carry its own Chinese-only copy.

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

import re

MOVE_RE = re.compile(
    r'提高至|下调至|降至|下调到|提高出价|降低出价|加投|减投|暂停|否定'
    # "Raise bid to 1.20", "lower to 0.80", "cut the bid", "bid down".
    r'|\b(?:raise|increase|lower|reduce|decrease|cut|drop)\s+'
    r'(?:the\s+|its\s+)?(?:bids?\s+)?(?:to|by)\b'
    r'|\b(?:raise|increase|lower|reduce|decrease|cut|drop)\s+'
    r'(?:the\s+|its\s+)?bids?\b'
    r'|\bbid\s+(?:up|down)\b'
    # "Pause" the action — not "Paused", which is a status column.
    r'|\bpause\b'
    r'|\bnegate\b|\badd\s+(?:it\s+)?(?:as\s+)?(?:an?\s+)?negative\b',
    re.IGNORECASE,
)

#: A row that names the recent change it is waiting out. It may carry a
#: move verb in its explanation and still be the hold the cooldown wants.
HOLD_RE = re.compile(
    r'冷却|观察期|刚(?:调|改|动)过|天前'
    r'|cool(?:ing)?[\s-]?down|observation (?:window|period)'
    r'|\bdays? ago\b|recently (?:changed|adjusted|raised|lowered)',
    re.IGNORECASE,
)
