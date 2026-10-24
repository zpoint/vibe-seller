"""The machine-readable search-term reconciliation line, in both languages.

One definition for every gate that reads it — the per-campaign
completeness check and the rollup cross-check — the way ``ad_moves``
became the one definition of a bid move. Each used to carry its own
Chinese-only copy::

    搜索词对账: 定向花费 USD 200.00 / 点击 200 = 搜索词花费 USD 200.00 / 点击 200 (✓)
    Search-term reconciliation: targeting spend USD 200.00 / clicks 200 =
        search-term spend USD 200.00 / clicks 200 (✓)

**Both languages, because the report follows the user's.** The language
gate holds an English request's report to English prose, and it counted
the Chinese line against it; the completeness gate could parse ONLY the
Chinese line. Observed in CI: an English audit translated the line to
satisfy one gate, was refused as unparseable by the other, switched back,
and ran out its clock between two checks that could not both be met.

Clicks stay optional (see ``ad_completeness_campaign_blocks``): the gate
grades spend, and some pages do not publish clicks. The optional
targeting-clicks group is bounded by ``[^\\n=]`` so it can only match
BEFORE the ``=`` and never pairs the search-term side's count with the
targeting spend. The label may carry markdown emphasis and a bilingual
parenthetical (``**搜索词对账 (search-term reconciliation)**:``).
"""

from __future__ import annotations

import re

LABEL = r'(?:搜索词对账|search[\s-]?term\s+reconciliation)'
_TARGETING = r'(?:定向花费|targeting\s+spend)'
_SEARCH_TERM = r'(?:搜索词花费|search[\s-]?term\s+spend)'
_CLICKS = r'(?:点击|clicks?)'
_EMPH = r'(?:\*\*|__|\*)?'

LABEL_RE = re.compile(LABEL, re.I)

# Groups: targeting spend, targeting clicks?, search-term spend, clicks?.
RECONCILE_RE = re.compile(
    _EMPH
    + r'\s*'
    + LABEL
    + r'(?:\s*[(（][^)）\n]*[)）])?\s*'
    + _EMPH
    + r'\s*[:：]'
    r'[^\n]*?' + _TARGETING + r'[^\d\n]*([\d,]+(?:\.\d+)?)'
    r'(?:[^\n=]*?' + _CLICKS + r'[^\d\n]*([\d,]+))?'
    r'[^\n]*?' + _SEARCH_TERM + r'[^\d\n]*([\d,]+(?:\.\d+)?)'
    r'(?:[^\n]*?' + _CLICKS + r'[^\d\n]*([\d,]+))?',
    re.I,
)
