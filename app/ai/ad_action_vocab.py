"""One bilingual vocabulary for the words an ad report writes itself.

**The rule.** Words that are OURS — action verbs, column headers, rule
names (bid floor, actual CPC, 25 % step cap), match-type labels — follow
the REPORT language, which is the language of the user's request. Only
text that belongs to the PLATFORM (UI labels, buttons, export column
names, quoted exactly as the marketplace shows them) stays in the
platform's language.

**Why one module.** Every ad gate parses the recommendation column, and
each used to carry its own Chinese-only copy of the verbs. The skill spec
therefore had to force Chinese literals, and an English report leaked
Chinese into its action table — because writing the English verb would
have been refused or miscounted. A parser that recognises a zh spelling
here recognises the en one, and vice versa; gates import from this file
instead of keeping their own list. Add a spelling HERE and every gate,
the language gate and the denial wording pick it up.

Spellings are listed canonical-first. Older zh spellings stay because
reports, notes and execution logs written before the rename still carry
them and the gates must keep reading them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re


class Kind(StrEnum):
    """Canonical action kinds a recommendation cell can lead with."""

    RAISE_TO = 'raise_to'
    LOWER_TO = 'lower_to'
    PAUSE = 'pause'
    KEEP = 'keep'
    NEG_EXACT = 'neg_exact'
    NEG_PHRASE = 'neg_phrase'
    NEG_TERM = 'neg_term'
    NEG_TARGET = 'neg_target'
    ADD_KEYWORD = 'add_keyword'
    BID_UP = 'bid_up'
    BID_DOWN = 'bid_down'


@dataclass(frozen=True)
class Spelling:
    """zh and en spellings of one kind; index 0 is the canonical one."""

    zh: tuple[str, ...]
    en: tuple[str, ...]


#: ``en`` entries are regex fragments (they are matched with word
#: boundaries, case-insensitively); ``zh`` entries are literal strings.
#: The canonical (first) entry of each is what the spec tells the agent
#: to write, so it must be a literal in both lists.
SPELLINGS: dict[Kind, Spelling] = {
    Kind.RAISE_TO: Spelling(
        zh=('提高至', '上调至', '提高到', '提至'),
        en=('Raise to', 'Increase to', 'Raise bid to', 'Increase bid to'),
    ),
    Kind.LOWER_TO: Spelling(
        zh=('下调至', '降至', '下调到', '降到', '调低至'),
        en=(
            'Lower to',
            'Reduce to',
            'Decrease to',
            'Lower bid to',
            'Reduce bid to',
        ),
    ),
    Kind.PAUSE: Spelling(
        zh=('暂停', '暂停定向词'),
        en=('Pause', 'Pause target', 'Pause keyword'),
    ),
    Kind.KEEP: Spelling(
        zh=('维持', '保持', '观察', '维持观察'),
        en=('Keep', 'Hold', 'Maintain', 'Monitor', 'Keep and monitor'),
    ),
    Kind.NEG_EXACT: Spelling(
        zh=('否定关键词（精准）', '否定精确'),
        en=('Negative keyword (exact)', 'Negative exact', 'Negate exact'),
    ),
    Kind.NEG_PHRASE: Spelling(
        zh=('否定关键词（词组）', '否定词组'),
        en=('Negative keyword (phrase)', 'Negative phrase', 'Negate phrase'),
    ),
    Kind.NEG_TERM: Spelling(
        zh=('否定搜索词',),
        en=('Negate search term', 'Negative search term'),
    ),
    Kind.NEG_TARGET: Spelling(
        zh=('否定投放',),
        en=('Negative target', 'Negate target'),
    ),
    Kind.ADD_KEYWORD: Spelling(
        zh=('添加为关键词', '拓词', '提取为定向词'),
        en=('Add as keyword', 'Promote to keyword', 'Harvest as keyword'),
    ),
    Kind.BID_UP: Spelling(
        zh=('提高', '上调', '加投', '提至'),
        en=('Raise', 'Increase', 'Bid up'),
    ),
    Kind.BID_DOWN: Spelling(
        zh=('降低', '下调', '减投', '降至', '调低'),
        en=('Lower', 'Reduce', 'Decrease', 'Bid down'),
    ),
}


def canonical(kind: Kind, lang: str) -> str:
    """The spelling the spec tells the agent to write for ``lang``."""
    sp = SPELLINGS[kind]
    return sp.zh[0] if lang == 'zh' else sp.en[0]


def both(kind: Kind) -> str:
    """``'提高至 / Raise to'`` — for messages that dictate wording."""
    sp = SPELLINGS[kind]
    return f'{sp.zh[0]} / {sp.en[0]}'


def _en_frag(spelling: str) -> str:
    body = re.escape(spelling).replace(r'\ ', r'\s+')
    # Word boundary only where the edge is a word char: a spelling that
    # ends in a paren would otherwise demand a following word char.
    lead = r'\b' if spelling[0].isalnum() else ''
    trail = r'\b' if spelling[-1].isalnum() else ''
    return lead + body + trail


def alt(*kinds: Kind, zh: bool = True, en: bool = True) -> str:
    """Regex alternation (no group) matching any spelling of ``kinds``."""
    parts: list[str] = []
    for k in kinds:
        sp = SPELLINGS[k]
        if zh:
            parts.extend(re.escape(s) for s in sp.zh)
        if en:
            parts.extend(_en_frag(s) for s in sp.en)
    return '|'.join(parts)


def pattern(*kinds: Kind, head: bool = False) -> re.Pattern[str]:
    """Compiled matcher for ``kinds``; ``head`` anchors it to cell start
    (optionally bold, as ``**Pause**``)."""
    body = alt(*kinds)
    if head:
        return re.compile(rf'^\s*(?:\*\*)?\s*(?:{body})', re.IGNORECASE)
    return re.compile(body, re.IGNORECASE)


# ── Recognisers shared by the gates ──────────────────────────────────

#: A recommendation that MOVES a bid or cuts a target. ``Keep`` is absent
#: by design: holding is not a move. ``Pause`` as the action, not the
#: ``Paused`` status column.
MOVE_RE = re.compile(
    r'提高至|下调至|降至|下调到|提高出价|降低出价|加投|减投|暂停|否定'
    r'|\b(?:raise|increase|lower|reduce|decrease|cut|drop)\s+'
    r'(?:the\s+|its\s+)?(?:bids?\s+)?(?:to|by)\b'
    r'|\b(?:raise|increase|lower|reduce|decrease|cut|drop)\s+'
    r'(?:the\s+|its\s+)?bids?\b'
    r'|\bbid\s+(?:up|down)\b'
    r'|\bpause\b'
    r'|\bnegate\b|\bnegative\s+(?:keyword|exact|phrase|target)\b'
    r'|\badd\s+(?:it\s+)?(?:as\s+)?(?:an?\s+)?negative\b',
    re.IGNORECASE,
)

#: A row that names the recent change it is waiting out.
HOLD_REASON_RE = re.compile(
    r'冷却|观察期|刚(?:调|改|动)过|天前'
    r'|cool(?:ing)?[\s-]?down|observation (?:window|period)'
    r'|\bdays? ago\b|recently (?:changed|adjusted|raised|lowered)',
    re.IGNORECASE,
)

#: Any bid-direction verb (raise or lower) used as an action.
BID_VERB_RE = re.compile(
    r'提高|降低|上调|下调|加投|减投|降至|提至'
    r'|\b(?:raise|increase|lower|reduce|decrease)\b'
    r'|\bbid\s+(?:up|down)\b|\bcut\s+(?:the\s+)?bid\b',
    re.IGNORECASE,
)
RAISE_VERB_RE = re.compile(
    r'提高|提至|上调|加投|\b(?:raise|increase)\b|\bbid\s+up\b',
    re.IGNORECASE,
)
#: Lowering verbs (bid_floor): the zh ones plus en spellings.
LOWER_VERB_RE = re.compile(
    r'下调|调低|降低出价|降至|\blower (?:the )?bid\b|\breduce (?:the )?bid\b'
    r'|\btrim to\b|\bbid trim\b|\bcut (?:the )?bid\b|\blower to\b'
    r'|\breduce to\b|\bdecrease to\b|\bbid down\b',
    re.IGNORECASE,
)
#: A negated mention of lowering ("do not lower", "不可下调").
NEGATED_LOWER_RE = re.compile(
    r'不可下调|不能下调|不得下调|不下调|不应下调|不予下调|无需下调'
    r'|不建议下调|不宜下调|禁止下调|勿下调|切勿下调'
    r"|(?:do not|don't|cannot|can't|must not|no need to|never)\s+"
    r'(?:lower|reduce|cut|trim|decrease)',
    re.IGNORECASE,
)
HOLD_VERB_RE = re.compile(
    r'\bhold\b|\bkeep\b|\bmaintain\b|保持|维持', re.IGNORECASE
)

#: Leading action of a recommendation cell, by family.
HOLD_HEAD_RE = re.compile(
    r'^\s*(?:\*\*)?\s*(?:维持|观察|否定|暂停'
    r'|(?:keep|hold|maintain|monitor|observe|negative|negate|pause)\b)',
    re.IGNORECASE,
)
HEALTHY_HEAD_RE = re.compile(
    r'^\s*(?:\*\*)?\s*(?:提高|提至|上调|加投|维持|观察'
    r'|(?:raise|increase|bid up|keep|hold|maintain|monitor|observe)\b)',
    re.IGNORECASE,
)
CUT_HEAD_RE = re.compile(
    r'^\s*(?:\*\*)?\s*(?:否定|暂停|停|降至|下调|降低|大幅降低|移除'
    r'|(?:negative|negate|pause|lower|reduce|decrease|cut|remove|drop)\b)',
    re.IGNORECASE,
)
#: A cut action ANYWHERE in a cell.
CUT_ACT_RE = re.compile(
    r'否定|暂停|停|\bnegat(?:e|ive)\b|\bpause\b', re.IGNORECASE
)
#: A raise or keep ANYWHERE in a cell (to tell a cut cell from a mixed one).
RAISE_OR_KEEP_RE = re.compile(
    r'提高|维持|\b(?:raise|increase|keep|hold|maintain)\b', re.IGNORECASE
)
#: ``Keep`` in a cell and no cut in it.
KEEP_ANY_RE = re.compile(r'维持|\b(?:keep|hold|maintain)\b', re.IGNORECASE)

#: Search-term extraction / harvest ("add as keyword").
EXTRACT_RE = re.compile(
    r'(?<!不可)(?<!无法)(?<!没法)提取'
    r'|(?<!不可)(?<!无法)(?<!没法)添加为?关键词'
    r'|拓词'
    r'|(?<!not )(?<!cannot )\badd\s+(?:it\s+)?as\s+(?:a\s+)?keyword\b'
    r'|\bpromote\s+to\s+(?:a\s+)?keyword\b|\bharvest\b',
    re.IGNORECASE,
)
#: A bid figure for a NEW keyword: ``建议出价 0.95`` / ``bid 0.95``.
SUGGEST_BID_RE = re.compile(
    r'(?:出价|至)\s*[=＝]?\s*\d+(?:\.\d+)?'
    r'|\b(?:suggested\s+)?bid\s*[=＝:]?\s*\d+(?:\.\d+)?'
    r'|\bto\s+\d+(?:\.\d+)?',
    re.IGNORECASE,
)
#: An explicit magnitude on a raise/lower.
MAGNITUDE_RE = re.compile(
    r'(?:至|→|->)\s*\d+(?:\.\d+)?'
    r'|\bto\s+\d+(?:\.\d+)?'
    r'|[+\-±]?\d+(?:\.\d+)?\s*%'
    r'|\d+(?:\.\d+)?\s*(?:SAR|AED|USD)',
    re.IGNORECASE,
)
#: Absolute / percentage trims, for the CPC-floor check.
TRIM_TO_RE = re.compile(
    r'(?:降至|下调至|降到|下调到)\s*([\d.]+)'
    r'|\b(?:lower|reduce|decrease|trim|cut)(?:\s+(?:the\s+)?bid)?\s+to\s+'
    r'([\d.]+)',
    re.IGNORECASE,
)
TRIM_PCT_RE = re.compile(
    r'(?:大幅)?(?:降低|下调|调低)(?:出价)?\s*(\d{1,2})\s*%'
    r'|\b(?:lower|reduce|decrease|cut)(?:\s+(?:the\s+)?bid)?\s+by\s+'
    r'(\d{1,2})\s*%',
    re.IGNORECASE,
)
RAISE_TO_RE = re.compile(
    r'(?:提高至|上调至|提高到)\s*([\d.]+)'
    r'|\b(?:raise|increase)(?:\s+(?:the\s+)?bid)?\s+to\s+([\d.]+)',
    re.IGNORECASE,
)
#: ``Lower to the minimum bid`` — a waste row starved on purpose.
MINIMUM_RE = re.compile(r'最低|\bminimum\b|\blowest\b', re.IGNORECASE)
#: A cited rule ("ACOS 41%>30 rule").
RULE_WORD_RE = re.compile(r'规则|\brule\b', re.IGNORECASE)
#: ``carried by the source keyword``.
CARRIED_RE = re.compile(
    r'承接|\b(?:carried|covered|caught|captured)\s+by\b', re.IGNORECASE
)
AUTO_RE = re.compile(r'自动|\bauto\b', re.IGNORECASE)
#: The claim "this term is already a target".
MANAGED_CLAIM_RE = re.compile(
    r'已是定向词|由定向表管理'
    r'|\balready\s+(?:a\s+)?(?:keyword|target(?:ed)?)\b'
    r'|\bmanaged\s+by\s+the\s+(?:keyword|targeting)\s+table\b',
    re.IGNORECASE,
)
MANAGED_MATCH_RE = re.compile(
    r'已是定向词\s*[（(]\s*([A-Za-z]+)'
    r'|\balready\s+(?:a\s+)?(?:keyword|target(?:ed)?)\s*\(\s*([A-Za-z]+)',
    re.IGNORECASE,
)
#: "suggest pausing" buried behind a hold head.
BURIED_CUT_RE = re.compile(
    r'(?<!已)建议\s*(?:暂停|否定|停)'
    r'|(?<!already )\b(?:suggest|recommend)(?:ed|s)?\s+(?:to\s+)?'
    r'(?:pausing|pause|negat(?:e|ing|ive))\b',
    re.IGNORECASE,
)
NEG_TERM_RE = re.compile(
    r'否定搜索词|\bnegat(?:e|ive)\s+(?:the\s+)?search\s+terms?\b',
    re.IGNORECASE,
)
#: Actions that only exist for KEYWORDS (match type is a keyword trait).
KEYWORD_ONLY_ACT_RE = re.compile(
    r'否定精确|否定词组|否定关键词|拓词|提取为定向词|添加为?关键词'
    r'|\bnegative\s+(?:keyword|exact|phrase)\b'
    r'|\bnegate\s+(?:exact|phrase)\b'
    r'|\badd\s+as\s+(?:a\s+)?keyword\b|\bpromote\s+to\s+keyword\b',
    re.IGNORECASE,
)
#: Attribution markers: a cited metric belonging to another entity.
CITED_OTHER_RE = re.compile(
    r'来源|预计|预期|目标|另一|其他|其它|整体|全店|汇总|历史|新词'
    r'|\b(?:source|projected|expected|target|another|other|overall|'
    r'store-wide|total|historical|new term)\b',
    re.IGNORECASE,
)
CLAUSE_SEPS = ('；', ';', '。', '（', '(', '——', '—', '\n')
TOTAL_ROW_RE = re.compile(
    r'合计|总计|汇总|^\W*(?:grand\s+)?(?:total|subtotal|overall|summary)\b',
    re.IGNORECASE,
)


# ── Column headers ───────────────────────────────────────────────────


class Col(StrEnum):
    REC = 'rec'
    SEARCH_TERM = 'search_term'
    KEYWORD = 'keyword'
    SOURCE = 'source'
    AD_GROUP = 'ad_group'
    MATCH = 'match'
    CLICKS = 'clicks'
    SPEND = 'spend'
    ORDERS = 'orders'
    SALES = 'sales'
    BID = 'bid'
    CPC = 'cpc'
    ROAS = 'roas'
    ACOS = 'acos'


# Substring needles (matched against the lower-cased header cell).
_NEEDLES: dict[Col, tuple[str, ...]] = {
    Col.SEARCH_TERM: ('搜索词', 'search term', 'search_term'),
    Col.KEYWORD: ('定向词', '关键词', 'keyword', 'target'),
    Col.SOURCE: ('来源', 'source'),
    Col.AD_GROUP: ('广告组', 'ad group', 'ad_group', 'adgroup'),
    Col.MATCH: ('匹配', 'match'),
    Col.CLICKS: ('点击', 'click'),
    Col.SPEND: ('花费', 'spend', 'cost'),
    Col.ORDERS: ('订单', 'order'),
    Col.SALES: ('销售额', 'sales'),
    Col.BID: ('出价', 'bid'),
    Col.CPC: ('cpc',),
}
# Exact cells that count as the recommendation column.
_REC_EXACT = frozenset({
    'recommendation',
    'recommendations',
    'action',
    'recommended action',
    'suggested action',
    'suggestion',
})
_REC_ZH = ('建议',)
_ROAS_ACOS = {Col.ROAS: 'roas', Col.ACOS: 'acos'}


def _clean(cell: str) -> str:
    return cell.replace('*', '').strip().lower()


def is_col(cell: str, col: Col) -> bool:
    """Does header ``cell`` name column ``col`` (either language)?"""
    c = _clean(cell)
    if col is Col.REC:
        return c in _REC_EXACT or any(z in c for z in _REC_ZH)
    if col in _ROAS_ACOS:
        return c == _ROAS_ACOS[col]
    if col is Col.BID:
        # ``建议出价`` / ``Suggested bid`` is the NEW bid, not the current.
        if is_col(cell, Col.REC) or 'suggest' in c:
            return False
    if col is Col.KEYWORD and 'source' in c:
        return False
    if col is Col.SEARCH_TERM or col is Col.AD_GROUP:
        pass
    return any(n in c for n in _NEEDLES[col])


def find_col(cells: list[str], col: Col) -> int | None:
    """Index of the first header cell naming ``col``, else None."""
    return next((j for j, c in enumerate(cells) if is_col(c, col)), None)


def has_rec_col(cells: list[str]) -> bool:
    return any(is_col(c, Col.REC) for c in cells)


def is_search_term_head(cell: str) -> bool:
    return is_col(cell, Col.SEARCH_TERM)


#: Column header sets for gates that compare against a lower-cased,
#: whole-cell name (both languages).
TARGET_COLS = ('target', 'keyword', '定向词', '关键词')
SEARCH_TERM_COLS = ('search_term', 'search term', '搜索词')

# Canonical header names the spec asks for, per language.
HEADERS: dict[str, dict[str, tuple[str, ...]]] = {
    'zh': {
        'targeting': (
            '定向词',
            '广告组',
            '匹配',
            '出价',
            '展示',
            '点击',
            '花费',
            '订单',
            '销售额',
            'ACOS',
            'ROAS',
            '实际 CPC',
            '建议',
        ),
        'search_term': (
            '搜索词',
            '广告组',
            '来源关键词',
            '匹配',
            '点击',
            '花费',
            '订单',
            '销售额',
            'ROAS',
            '建议',
        ),
    },
    'en': {
        'targeting': (
            'Keyword',
            'Ad group',
            'Match',
            'Bid',
            'Impressions',
            'Clicks',
            'Spend',
            'Orders',
            'Sales',
            'ACOS',
            'ROAS',
            'Actual CPC',
            'Recommendation',
        ),
        'search_term': (
            'Search term',
            'Ad group',
            'Source keyword',
            'Match',
            'Clicks',
            'Spend',
            'Orders',
            'Sales',
            'ROAS',
            'Recommendation',
        ),
    },
}


# ── Language gate support ────────────────────────────────────────────

# Chinese terms that are OURS (not the platform's): what an English
# report must not contain outside quotes.
_OUR_ZH_TERMS = (
    '地板',
    '单次上限',
    '步长',
    '豁免',
    '实际 CPC',
    '冷却',
    '建议',
    '定向词',
    '搜索词',
    '广告组',
    '来源关键词',
    '精准',
    '词组',
    '出价',
    '花费',
    '订单',
    '销售额',
    '汇总',
    '合计',
)

_CJK_RE = re.compile(r'[一-鿿]')
_QUOTED_RE = re.compile(
    r'`[^`]*`|「[^」]*」|“[^”]*”|"[^"]*"|‘[^’]*’|\'[^\']*\''
)


def strip_quoted(text: str) -> str:
    """Drop quoted spans: text quoted as the platform shows it is exempt."""
    return _QUOTED_RE.sub(' ', text)


def our_zh_words(text: str) -> list[str]:
    """CJK fragments of ``text`` that are OUR vocabulary (not quoted)."""
    t = strip_quoted(text)
    zh_verbs = [s for sp in SPELLINGS.values() for s in sp.zh]
    return [w for w in (*zh_verbs, *_OUR_ZH_TERMS) if w in t]


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text))


# ── Spec rendering ───────────────────────────────────────────────────


def spec_actions(lang: str) -> str:
    """Action list for ``lang`` as the spec prints it (for docs/tests)."""
    return ' / '.join(
        canonical(k, lang)
        for k in (
            Kind.RAISE_TO,
            Kind.LOWER_TO,
            Kind.PAUSE,
            Kind.KEEP,
            Kind.NEG_EXACT,
            Kind.ADD_KEYWORD,
            Kind.NEG_TARGET,
        )
    )
