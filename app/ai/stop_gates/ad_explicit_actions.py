"""Gate: recommendations must be actionable in the RIGHT dimension.

Two store-owner review rounds define this gate:

1. (2026-06-11 AM) Every bid action must state HOW MUCH and WHY — a
   bare 「提高出价」 is unreviewable.
2. (2026-06-11 PM) Targeting keywords and search terms are DIFFERENT
   DIMENSIONS. You bid on ~10 targeting keywords; the ~100 search
   terms are what users actually typed. A search term has no bid of
   its own, so 「加投 25%」 on a search-term row is meaningless. Valid
   search-term actions: **提取为定向词** (good term → promote to
   targeting, with a suggested bid/match type; the broad source
   keyword that caught it may then be adjusted), **否定** (bad term),
   or **维持观察**. Bid verbs belong only on targeting rows.

Table dimension is detected from the header row: a first column named
搜索词 marks a search-term table; anything else is treated as a
targeting/keyword table.

- Targeting rows with raise/lower verbs need a magnitude — target bid
  (``提高至 0.96``) or percentage (``下调 10%``) — AND a short basis
  tagged rule or assumption (``（ACOS 41%>30 规则）``).
- Search-term rows must use 提取为定向词 (with a suggested bid for
  the NEW targeting keyword), 否定, or 维持/观察 — any bid verb is a
  wrong-dimension defect.
- 维持/否定/暂停 are exempt from the magnitude requirement everywhere
  (high-ROAS holds stay covered by ``ad_scale_winners``).

Runs on the AD_AUDIT report markdown at ``set_task_result``; no-op
for non-ads results. See ``amazon-ads/references/output-spec.md §
建议列格式`` for the contract the agent is held to.
"""

from __future__ import annotations

import re

from app.ai.stop_gates import GateDeny
from app.ai.stop_gates.ad_rules import DEFAULT_RULES

GATE_NAME = 'ad_explicit_actions'

# Raise/lower verbs. On targeting rows they demand magnitude+basis;
# on search-term rows they are categorically invalid.
_BID_VERB_RE = re.compile(r'提高|降低|上调|下调|加投|减投|降至|提至')

# An explicit magnitude: a target value (至/→ 0.96), a percentage
# (25% / +25% / -10%), or an absolute currency target.
_MAGNITUDE_RE = re.compile(
    r'(?:至|→|->)\s*\d+(?:\.\d+)?'
    r'|[+\-±]?\d+(?:\.\d+)?\s*%'
    r'|\d+(?:\.\d+)?\s*(?:SAR|AED|USD)'
)

# A stated basis: an explanatory clause opener followed by ≥4 chars.
_REASON_RE = re.compile(r'[（(，,：:—\-]\s*\S{4,}')

# Search-term extraction action + its required suggested bid for the
# NEW targeting keyword (e.g. 提取为定向词（Exact，建议出价 0.95…）).
# Negated mentions (「不可提取」「无法提取」 explaining WHY a category
# placement can't be extracted) are not extraction recommendations.
_EXTRACT_RE = re.compile(r'(?<!不可)(?<!无法)(?<!没法)提取')
_SUGGEST_BID_RE = re.compile(r'(?:出价|至)\s*[=＝]?\s*\d+(?:\.\d+)?')

# A ROAS/ACOS number CITED inside a 建议 cell. Three review rounds
# found fabricated citations — the clicks column quoted as ROAS
# (「ROAS 64.00>8」 on a ROAS-17.09 row), ACOS 0.0% on a 25.9% row —
# so cited numbers are checked against the row's OWN column.
_CITED_ROAS_RE = re.compile(r'ROAS\s*[=＝:：约]?\s*(\d+(?:\.\d+)?)')
_CITED_ACOS_RE = re.compile(r'ACOS\s*[=＝:：约]?\s*(\d+(?:\.\d+)?)\s*%')
# A cite about some OTHER row/value (the source keyword's metric, a
# projected value, another campaign's number) is skipped, not
# compared — skipping is safe (it can only reduce coverage).
_CITED_OTHER_RE = re.compile(
    r'来源|预计|预期|目标|另一|其他|其它|整体|全店|汇总|历史|新词'
)

# An absolute bid trim (「降至 1.20」「下调至1.63」). Percentage-only
# trims (「下调 10%」) are not floor-checked — the base is ambiguous.
_TRIM_TO_RE = re.compile(r'(?:降至|下调至|降到)\s*([\d.]+)')
# A percentage trim (「降低出价 25%」「下调 10%」) — the base is the
# row's own 出价 column, so the implied target IS floor-checkable.
_TRIM_PCT_RE = re.compile(
    r'(?:大幅)?(?:降低|下调|调低)(?:出价)?\s*(\d{1,2})\s*%'
)
# A raise verb, for the phantom-metric check below.
_RAISE_VERB_RE = re.compile(r'提高|提至|上调|加投')
# 「已是定向词」-style claims: the search term is said to already be
# managed by a targeting row. Verified against the campaign's actual
# targeting table (the claim is false when no such row exists, and
# orphaned when every matching row is itself being cut).
_MANAGED_CLAIM_RE = re.compile(r'已是定向词|由定向表管理')
_CUT_ACT_RE = re.compile(r'否定|暂停|停')

_DENY_TARGETING = (
    '{n} 行定向关键词的「提高/降低」类建议缺少明确幅度或依据。每行必须'
    '写明 (a) 幅度——目标价（如「提高至 0.96」）或百分比（如「下调 '
    '10%」）；(b) 简短依据——规则（如「ACOS 41%>30 规则」）或假设。'
    '维持/否定/暂停行不需要幅度。违规样例：{samples}。'
)

_DENY_SEARCHTERM = (
    '{n} 行搜索词建议用了出价动作（提高/降低/加投…）——维度错误：搜索词'
    '不是出价对象，出价在定向关键词上。搜索词只有三种有效建议：'
    '(a) **提取为定向词**（表现好的词，写明匹配方式与新定向词的建议出价，'
    '如「提取为定向词（Exact，建议出价 0.95——ROAS 5.66>5，来源 broad 词'
    '可相应降档）」，必要时在来源关键词行级联调整）；(b) **否定**（高花费'
    '零单词）；(c) **维持观察**（样本不足）。违规样例：{samples}。'
)

_DENY_EXTRACT_NO_BID = (
    '{n} 行「提取为定向词」建议缺少新定向词的建议出价（如「建议出价 '
    '0.95」，通常取该搜索词实际 CPC × 1.1~1.25）。违规样例：{samples}。'
)

_DENY_IDENTITY_EXTRACT = (
    '{n} 行把与来源关键词完全相同的搜索词「提取为定向词」——该词本来'
    '就是定向词（只是 Broad/Phrase 匹配），提取无意义。改为 维持'
    '（来源词承接）或在定向关键词表上处理。违规样例：{samples}。'
)

_DENY_WASTE_HOLD = (
    '{n} 行搜索词零订单、花费 ≥ negate_waste_spend（默认 10）或点击 ≥ '
    'negate_waste_clicks（默认 10）仍写「维持」——有花费/点击但没有任何'
    '效果的词应直接 **否定**（auto 活动否定该 ASIN/词即等于移除定向）。'
    '违规样例：{samples}。'
)

_DENY_WASTE_KW = (
    '{n} 行定向关键词零订单、花费 ≥ negate_waste_spend 或点击 ≥ '
    'negate_waste_clicks 仍写「维持/观察」——无效定向应 **暂停** 或'
    '降至最低出价并写明（如「暂停——13 点击零单」）。违规样例：{samples}。'
)

_DENY_MISSED_RESCUE = (
    '{n} 行搜索词在来源关键词被 降/停/否定 时仍写「维持——来源词承接」'
    '——来源词降档/否定后不会再承接这些词，承接说法不成立。有转化'
    '（订单>0）的此类词正是收割时机：改为「提取为定向词（Exact，建议'
    '出价=该词CPC×1.1~1.25——ROAS X；来源词已降档）」。'
    '违规样例：{samples}。'
)

_DENY_CPC_FLOOR = (
    '{n} 行降价建议的目标价低于该行实际 CPC×1.1 ——降到 CPC 以下意味着'
    '要么再也拿不到展示、要么锁定亏损出价（output-spec 地板规则：新价 '
    '≥ max(实际CPC×1.1, 建议价下限)）。重新计算目标价或改为暂停。'
    '违规样例：{samples}。'
)

_DENY_AUTO_PAUSE_CARRY = (
    '{n} 行搜索词写「维持——auto 定向承接」，但同活动的 auto 定向组'
    '（close-match/loose-match 等）已被建议暂停/否定——组停了就不再'
    '承接这些词，自相矛盾。Auto 活动有转化词时正确做法是：否定零单'
    '浪费词、保留 auto 组承接转化词、否定后再观察 ROAS；不要一刀切'
    '暂停整组。若确需停组，把这些转化词改为「提取为定向词(Exact)」'
    '救出来。违规样例：{samples}。'
)

_DENY_NEGATE_ON_TARGET = (
    '{n} 行定向关键词写了「否定搜索词」——维度错误：否定搜索词是搜索词层'
    '动作，控制台里关键词只能 暂停定向词/否定精确/降至，不能「否定搜索'
    '词」。改为 暂停定向词（依据）。违规样例：{samples}。'
)

_DENY_SPLIT_HEAD = (
    '{n} 行动作头写 维持 但单元格内藏着「建议暂停/建议否定」——操作者'
    '只执行动作头，藏起来的决定等于没做（output-spec：动作头就是决定）。'
    '把动作头改成 暂停定向词/否定（括注地板锁死+ROAS 依据），或删掉'
    '矛盾的建议尾巴。违规样例：{samples}。'
)

_DENY_TRIM_DIRECTION = (
    '{n} 行「下调至 X」的目标价高于该行当前出价——这实际是提价，动作'
    '方向标错了。出价已低于地板且表现差的词：写 维持（出价已低于'
    'CPC×1.1 地板，无法再降）并视情况建议暂停，不要写成「下调」。'
    '违规样例：{samples}。'
)

_DENY_PHANTOM_RAISE = (
    '{n} 行在零点击/零花费的行上用 ACOS/ROAS 规则做「提高」依据——零花费'
    '行的 ACOS 是未定义（0÷0），不是 0.0%，不能触发任何表现规则。'
    '改为 维持（无数据信号）或以「预算内试投/新词启动」之类的意图依据'
    '另行说明。违规样例：{samples}。'
)

_DENY_FALSE_MANAGED = (
    '{n} 行搜索词声称「已是定向词/由定向表管理」但该活动定向表里'
    '查不到对应行，或对应定向行本身已被 否定/暂停（停用后「由定向表'
    '管理」不成立，反而是该词的处置缺口）。核对定向表：词不存在 → '
    '改为如实的 维持/否定/提取 建议；定向行被停 → 给该搜索词独立的'
    '处置。违规样例：{samples}。'
)

_DENY_CITED_NUM = (
    '{n} 行建议里引用的 ROAS/ACOS 数字与该行自己的列值不符。引用数字'
    '必须取自该行的 ROAS/ACOS 列（ROAS=销售额÷花费）——不能拿点击数、'
    '花费或其他列充当 ROAS（点击数不是 ROAS！）。注意 noon 定向表列序'
    '是 …|订单|销售额|ROAS|花费|展示|点击|建议。逐行核对建议里的每个'
    '数字。违规样例：{samples}。'
)

_DENY_HEALTHY_SRC_EXTRACT = (
    '{n} 行在来源关键词健康（定向表建议为 提高/维持）时做了「提取为'
    '定向词」——提取是救援动作，只在来源词要被 降/停/否定 时才提取'
    '（先救出好流量再砍来源词）。来源词健康时 Broad/Phrase 会继续'
    '承接这些搜索词，全部提取只会把定向词列表搞成一锅粥并和来源词'
    '自我竞价。改为 维持（来源 Broad 词承接，ROAS X）。'
    '违规样例：{samples}。'
)


def _is_header(line: str, next_line: str) -> bool:
    return line.startswith('|') and next_line.replace(' ', '').startswith('|--')


def _norm(cell: str) -> str:
    return cell.replace('*', '').strip()


def _norm_kw(cell: str) -> str:
    """Keyword key for cross-table lookups: noon keyword cells carry a
    trailing slash (``mouse/``), search-term capitalization differs
    from the targeting row (``Phone Stand`` vs ``phone stand``), and Amazon
    Exact close-variants make ``kid's mouse`` the same keyword as
    ``kid mouse`` (possessive stripped)."""
    s = _norm(cell).rstrip('/').strip().casefold()
    return re.sub(r"[’']s\b", '', s).strip()


def _strip_cat(key: str) -> str:
    """Drop a trailing ``(cat-N)`` style parenthetical: the Customer
    Queries page reports the category placement without the suffix the
    targeting table uses (``electronics/accessories-31225 (cat-3)``)."""
    return re.sub(r'\s*[（(][^（）()]*[）)]\s*$', '', key).strip()


_HOLD_HEAD_RE = re.compile(r'^\s*(?:\*\*)?\s*(?:维持|观察|否定|暂停)')
# Source-action heads: the ACTION is the leading verb; reason text may
# cite the opposite verb negatively (维持（…已否定的垃圾词…回归）).
_HEALTHY_HEAD_RE = re.compile(
    r'^\s*(?:\*\*)?\s*(?:提高|提至|上调|加投|维持|观察)'
)
_CUT_HEAD_RE = re.compile(
    r'^\s*(?:\*\*)?\s*(?:否定|暂停|停|降至|下调|降低|大幅降低|移除)'
)


def _is_hold(rec: str) -> bool:
    """The ACTION is the leading verb; a hold whose REASON mentions a
    bid verb negatively (「维持观察（ROAS 4.4<5 不满足加投条件）」) is
    still a hold — not a bid action missing its magnitude."""
    return bool(_HOLD_HEAD_RE.match(rec))


def _cellnum(cells: list[str], idx: int | None) -> float | None:
    if idx is None or idx >= len(cells):
        return None
    m = re.search(r'\d+(?:\.\d+)?', cells[idx].replace(',', ''))
    return float(m.group()) if m else None


def _cited_metric(rec: str, rx: re.Pattern[str]) -> float | None:
    """First value cited in ``rec`` that refers to THIS row (cites about
    the source keyword or projected values are skipped, not compared)."""
    for m in rx.finditer(rec):
        if _CITED_OTHER_RE.search(rec[max(0, m.start() - 22) : m.start()]):
            continue
        return float(m.group(1))
    return None


def _cited_mismatch(
    rec: str,
    cells: list[str],
    roas_idx: int | None,
    acos_idx: int | None,
    spend_idx: int | None = None,
    sales_idx: int | None = None,
) -> str | None:
    """Sample string when a cited ROAS/ACOS contradicts the row's own
    column, else None. Tolerance is generous (rounding-safe): real
    fabrications are wildly off (cited 4.0 on a 24.97 row). When the
    table has no ACOS column (noon), the actual ACOS is COMPUTED from
    花费/销售额 — round-5 found 「ACOS 0.0%」 cites on rows whose real
    ACOS is 21.9%/29.6%, unverifiable column-against-column."""
    name = _norm(cells[0])[:25]
    cited = _cited_metric(rec, _CITED_ROAS_RE)
    actual = _cellnum(cells, roas_idx)
    if (
        cited is not None
        and actual is not None
        and abs(cited - actual) > max(0.6, 0.25 * actual)
    ):
        return f'「{name}」建议引 ROAS {cited:g} ≠ 行 ROAS 列 {actual:g}'
    cited = _cited_metric(rec, _CITED_ACOS_RE)
    actual = _cellnum(cells, acos_idx)
    if actual is None and cited is not None:
        spend = _cellnum(cells, spend_idx)
        sales = _cellnum(cells, sales_idx)
        if spend is not None and sales and sales > 0:
            actual = spend / sales * 100.0
    if (
        cited is not None
        and actual is not None
        and abs(cited - actual) > max(2.0, 0.25 * actual)
    ):
        return f'「{name}」建议引 ACOS {cited:g}% ≠ 行实际 ACOS {actual:.1f}%'
    return None


def check(
    result_text: str,
    rules: dict[str, float] | None = None,
) -> GateDeny | None:
    """Flag rows whose 建议 is in the wrong dimension or inexplicit."""
    eff = rules or DEFAULT_RULES
    waste_spend = eff.get(
        'negate_waste_spend', DEFAULT_RULES['negate_waste_spend']
    )
    waste_clicks = eff.get(
        'negate_waste_clicks', DEFAULT_RULES['negate_waste_clicks']
    )
    if not result_text or not isinstance(result_text, str):
        return None

    bad_targeting: list[str] = []
    bad_dimension: list[str] = []
    bad_extract: list[str] = []
    bad_identity: list[str] = []
    bad_healthy_src: list[str] = []
    bad_missed_rescue: list[str] = []
    bad_waste_hold: list[str] = []
    bad_waste_kw: list[str] = []
    bad_cited_num: list[str] = []
    bad_cpc_floor: list[str] = []
    bad_trim_direction: list[str] = []
    bad_split_head: list[str] = []
    bad_negate_on_target: list[str] = []
    bad_auto_pause_carry: list[str] = []
    bad_phantom: list[str] = []
    bad_false_managed: list[str] = []

    lines = result_text.splitlines()
    in_search_table = False
    in_targeting_table = False
    src_idx: int | None = None
    orders_idx: int | None = None
    spend_idx: int | None = None
    clicks_idx: int | None = None
    roas_idx: int | None = None
    acos_idx: int | None = None
    sales_idx: int | None = None
    bid_idx: int | None = None
    cpc_idx: int | None = None
    st_match_idx: int | None = None
    tgt_match_idx: int | None = None
    # Per-campaign targeting map: (keyword, match) → 建议 action, plus
    # a keyword-only view for tables without a match column. A keyword
    # can appear at several match types with OPPOSITE actions (live:
    # wireless mouse Broad 降至 1.65 vs wireless mouse Phrase 维持) — keying by
    # keyword alone let one overwrite the other and produced false
    # flags on legitimate rescues. Ambiguous keyword-only lookups
    # (conflicting actions) are skipped rather than guessed.
    targeting_actions: dict[tuple[str, str], str] = {}
    targeting_by_kw: dict[str, set[str]] = {}

    def _src_action(src: str, match: str) -> str:
        key = _norm_kw(src)
        act = targeting_actions.get((key, match))
        if act is not None:
            return act
        acts = targeting_by_kw.get(key, set())
        return next(iter(acts)) if len(acts) == 1 else ''

    auto_group_cut = False  # an auto target group paused/negated this block
    _AUTO_ROW_RE = re.compile(
        r'close[- ]?match|loose[- ]?match|complements|substitutes|自动|auto'
    )
    for i, line in enumerate(lines):
        if line.startswith('### '):
            targeting_actions = {}
            auto_group_cut = False
        if not line.startswith('|'):
            in_search_table = False
            in_targeting_table = False
            continue
        nxt = lines[i + 1] if i + 1 < len(lines) else ''
        if _is_header(line, nxt):
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            # Only tables WITH a 建议 column carry recommendations —
            # header/summary tables (id|name|…|status) end in a ✓/✗
            # status cell that must not be read as a hold (live FP:
            # a campaign header row flagged as a zero-order waste
            # hold because its last cell was 「✓」).
            has_rec = any(
                '建议' in c or c.lower() == 'recommendation' for c in cells
            )
            in_search_table = has_rec and '搜索词' in cells[0]
            in_targeting_table = has_rec and '搜索词' not in cells[0]
            src_idx = next(
                (j for j, c in enumerate(cells) if '来源' in c), None
            )
            orders_idx = next(
                (j for j, c in enumerate(cells) if '订单' in c), None
            )
            spend_idx = next(
                (j for j, c in enumerate(cells) if '花费' in c), None
            )
            sales_idx = next(
                (j for j, c in enumerate(cells) if '销售额' in c), None
            )
            clicks_idx = next(
                (j for j, c in enumerate(cells) if '点击' in c), None
            )
            roas_idx = next(
                (j for j, c in enumerate(cells) if c.lower() == 'roas'),
                None,
            )
            acos_idx = next(
                (j for j, c in enumerate(cells) if c.lower() == 'acos'),
                None,
            )
            bid_idx = next(
                (
                    j
                    for j, c in enumerate(cells)
                    if '出价' in c and '建议' not in c
                ),
                None,
            )
            cpc_idx = next(
                (j for j, c in enumerate(cells) if 'cpc' in c.lower()),
                None,
            )
            match_i = next(
                (j for j, c in enumerate(cells) if '匹配' in c), None
            )
            if in_search_table:
                st_match_idx = match_i
            else:
                tgt_match_idx = match_i
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if not cells or cells[0].startswith('--'):
            continue
        rec = cells[-1]
        if (in_search_table or in_targeting_table) and _is_hold(rec):
            # Split head: a 维持/观察 head hiding a 建议暂停/否定 tail —
            # the executor reads the head, the buried decision dies
            # (round-13: pattern recurred in 7 rows beyond the listed
            # fixes whenever floor-lock + bad ROAS co-occurred).
            if re.search(r'(?<!已)建议\s*(?:暂停|否定|停)', rec):
                bad_split_head.append(f'{_norm(cells[0])[:25]} → {rec[:45]}')
        if in_search_table or in_targeting_table:
            mm = _cited_mismatch(
                rec, cells, roas_idx, acos_idx, spend_idx, sales_idx
            )
            if mm:
                bad_cited_num.append(mm)
        if in_search_table:
            term = _norm(cells[0])
            src = (
                _norm(cells[src_idx])
                if src_idx is not None and src_idx < len(cells)
                else ''
            )
            st_match = (
                _norm(cells[st_match_idx])
                if st_match_idx is not None and st_match_idx < len(cells)
                else ''
            )
            src_act = _src_action(src, st_match) if src else ''
            if (
                not _is_hold(rec)
                and not _EXTRACT_RE.search(rec)
                and _BID_VERB_RE.search(rec)
            ):
                # Extraction-headed recs are a VALID search-term action
                # whose justification may cite bid rules (「ROAS 9.94>5
                # 加投赢家规则」) — they must reach the harvest-policy
                # branch below, not die here as a dimension error
                # (round-12: the dimension flag MASKED a real
                # healthy-source extraction violation).
                bad_dimension.append(rec[:60])
                continue
            if _MANAGED_CLAIM_RE.search(rec) and targeting_actions:
                # 「已是定向词/由定向表管理」 must be TRUE: the term has
                # a live row in this campaign's targeting table. False
                # claim (no row), orphaned claim (every matching row is
                # itself cut — F-4 negate-then-"managed"), or wrong
                # match type (claims Exact, table only has Phrase).
                # Only checkable when the block's targeting table was
                # seen (else the structural gate owns the gap).
                t_norm = _norm_kw(term)
                rows = [
                    (m_t, act)
                    for (k, m_t), act in targeting_actions.items()
                    if k == t_norm or _strip_cat(k) == t_norm
                ]
                m_claim = re.search(r'已是定向词\s*[（(]\s*([A-Za-z]+)', rec)
                claimed = m_claim.group(1).casefold() if m_claim else ''
                if not rows:
                    bad_false_managed.append(f'{term[:30]}（定向表无此行）')
                elif all(
                    _CUT_ACT_RE.search(act) and not re.search(r'提高|维持', act)
                    for _, act in rows
                ):
                    bad_false_managed.append(
                        f'{term[:30]}（对应定向行已被停用）'
                    )
                elif claimed in ('exact', 'phrase', 'broad') and not any(
                    claimed in m_t.casefold() for m_t, _ in rows
                ):
                    bad_false_managed.append(
                        f'{term[:30]}（定向表中无 {claimed} 行）'
                    )
            if _EXTRACT_RE.search(rec):
                # Harvest policy: extraction is a RESCUE move, only
                # valid when the SOURCE keyword is itself being cut.
                if term and src and term == src:
                    bad_identity.append(f'{term[:30]} → {rec[:40]}')
                elif src_act:
                    healthy = bool(
                        _HEALTHY_HEAD_RE.match(src_act)
                    ) and not _CUT_HEAD_RE.match(src_act)
                    if healthy:
                        bad_healthy_src.append(
                            f'{term[:30]}（来源 {src[:20]}）'
                        )
                if not _SUGGEST_BID_RE.search(rec):
                    bad_extract.append(rec[:60])
                continue
            if re.search(r'维持', rec) and not re.search(r'否定|暂停|停', rec):
                # Zero-order waste must be negated, never held — auto
                # campaigns have no per-target row, so the search-term
                # row IS the only place to act (store-owner rule).
                orders = _cellnum(cells, orders_idx)
                spend = _cellnum(cells, spend_idx)
                clicks = _cellnum(cells, clicks_idx)
                if orders == 0 and (
                    (spend or 0) >= waste_spend or (clicks or 0) >= waste_clicks
                ):
                    bad_waste_hold.append(
                        f'{_norm(cells[0])[:25]}'
                        f'（花费 {spend or 0:g}/点击 {clicks or 0:g} 零单）'
                    )
                    continue
            if (
                auto_group_cut
                and '承接' in rec
                and re.search(r'auto|自动', rec)
            ):
                bad_auto_pause_carry.append(f'{_norm(cells[0])[:25]}')
            if '承接' in rec and src and src != term and src_act:
                # Inverse harvest check: 「来源词承接」 is FALSE when
                # the source is being cut — it won't keep catching the
                # term. A CONVERTING term there is the missed rescue
                # the policy exists for.
                cut = bool(_CUT_HEAD_RE.match(src_act))
                if cut and orders_idx is not None and orders_idx < len(cells):
                    m_orders = re.search(r'\d+', cells[orders_idx])
                    if m_orders and int(m_orders.group()) > 0:
                        bad_missed_rescue.append(
                            f'{term[:30]}（来源 {src[:20]} 已降档）'
                        )
            continue
        if in_targeting_table and len(cells) >= 2:
            if re.search(r'合计|总计|汇总', cells[0]) or not rec:
                continue  # totals row / data-only row, not a 建议
            t_match_cell = (
                cells[tgt_match_idx]
                if tgt_match_idx is not None and tgt_match_idx < len(cells)
                else ''
            )
            if (
                _AUTO_ROW_RE.search(cells[0])
                or _AUTO_ROW_RE.search(t_match_cell)
            ) and _CUT_HEAD_RE.match(rec):
                # An auto target group (close/loose-match etc.) being
                # paused/negated kills every search term it was
                # carrying — any 维持承接 below is then orphaned, and
                # blanket-pausing an auto group with converters is the
                # wrong lever (negate waste, keep the group). Round-18.
                auto_group_cut = True
            if '否定搜索词' in rec:
                # Wrong dimension: 否定搜索词 is a search-term action;
                # a targeting keyword is 暂停定向词/降至/否定精确, never
                # "negate the search term" (you can't negate a keyword
                # in the console — only pause it). Round-14.
                bad_negate_on_target.append(f'{_norm(cells[0])[:25]}')
            orders = _cellnum(cells, orders_idx)
            spend = _cellnum(cells, spend_idx)
            clicks = _cellnum(cells, clicks_idx)
            cpc = _cellnum(cells, cpc_idx)
            if (
                orders == 0
                and (
                    (spend or 0) >= waste_spend or (clicks or 0) >= waste_clicks
                )
                and not _CUT_ACT_RE.search(rec)
                and '最低' not in rec
            ):
                # Proven zero-converter: ANY non-cut action — a hold,
                # a trim that still buys clicks, a raise — keeps
                # spending on it. 暂停/否定, or trim-to-minimum with
                # the 最低 wording (store-owner rule: remove/disable
                # outright).
                bad_waste_kw.append(
                    f'{_norm(cells[0])[:25]}'
                    f'（花费 {spend or 0:g}/点击 {clicks or 0:g} 零单）'
                )
            bid = _cellnum(cells, bid_idx)
            if cpc is None and clicks and clicks > 0 and spend:
                # No CPC column (some noon tables) — compute it, same
                # precedent as the computed-ACOS cite check (round-7:
                # 降至0.60 on a spend 6.40 / 8-click row escaped the
                # floor because the table had no eCPC column).
                cpc = spend / clicks
            m_trim = _TRIM_TO_RE.search(rec)
            if m_trim:
                target = float(m_trim.group(1))
                # output-spec floor: a trim target below actual
                # CPC×1.1 never wins another impression or locks in
                # a loss; half-cent slack forgives rounding only.
                # 「降至最低出价」 on a waste row is exempt — starving
                # a proven zero-converter is the POINT there.
                if cpc and '最低' not in rec and target < cpc * 1.1 - 0.005:
                    bad_cpc_floor.append(
                        f'{_norm(cells[0])[:25]}（降至 {target:g} < '
                        f'CPC {cpc:g}×1.1≈{cpc * 1.1:.2f}）'
                    )
                # Direction truth: a 「下调至 X」 with X above the
                # row's current bid actually RAISES it (live: 下调至
                # 0.88 on a 0.80 bid) — mislabeled action.
                if bid and target > bid + 0.005:
                    bad_trim_direction.append(
                        f'{_norm(cells[0])[:25]}（下调至 {target:g} > '
                        f'当前出价 {bid:g}——实为提价）'
                    )
            m_raise = re.search(r'提高至\s*([\d.]+)', rec)
            if m_raise and cpc:
                # A RAISE target still below CPC×1.1 is incoherent:
                # even after raising, the bid can't clear its own cost
                # floor (round-16: SB Video rows where CPC > current
                # bid got a +30% raise that still landed under floor).
                rt = float(m_raise.group(1))
                if rt < cpc * 1.1 - 0.005:
                    bad_cpc_floor.append(
                        f'{_norm(cells[0])[:25]}（提高至 {rt:g} 仍 < '
                        f'CPC {cpc:g}×1.1≈{cpc * 1.1:.2f}）'
                    )
            m_pct = _TRIM_PCT_RE.search(rec)
            if m_pct and bid and cpc:
                # A percent trim's implied target must clear the same
                # floor (live: 降低 25% off 0.66 → 0.495 vs 0.726).
                implied = bid * (1 - float(m_pct.group(1)) / 100.0)
                if implied < cpc * 1.1 - 0.005:
                    bad_cpc_floor.append(
                        f'{_norm(cells[0])[:25]}（降 {m_pct.group(1)}% '
                        f'→ {implied:.2f} < CPC {cpc:g}×1.1≈'
                        f'{cpc * 1.1:.2f}）'
                    )
            if (
                clicks == 0
                and (spend or 0) == 0
                and _RAISE_VERB_RE.search(rec)
                and re.search(r'ACOS|ROAS', rec)
                and '规则' in rec
            ):
                # ACOS on a zero-spend row is 0/0 — citing
                # 「ACOS 0.0%<30 规则」 there is a fabricated metric.
                bad_phantom.append(f'{_norm(cells[0])[:25]} → {rec[:45]}')
            kw = _norm_kw(cells[0])
            t_match = (
                _norm(cells[tgt_match_idx])
                if tgt_match_idx is not None and tgt_match_idx < len(cells)
                else ''
            )
            targeting_actions[(kw, t_match)] = rec
            targeting_by_kw.setdefault(kw, set()).add(rec)
        if _is_hold(rec) or not _BID_VERB_RE.search(rec):
            continue
        if _MAGNITUDE_RE.search(rec) and _REASON_RE.search(rec):
            continue
        bad_targeting.append(rec[:60])

    parts: list[str] = []
    for bucket, template in (
        (bad_cited_num, _DENY_CITED_NUM),
        (bad_cpc_floor, _DENY_CPC_FLOOR),
        (bad_split_head, _DENY_SPLIT_HEAD),
        (bad_negate_on_target, _DENY_NEGATE_ON_TARGET),
        (bad_auto_pause_carry, _DENY_AUTO_PAUSE_CARRY),
        (bad_trim_direction, _DENY_TRIM_DIRECTION),
        (bad_phantom, _DENY_PHANTOM_RAISE),
        (bad_false_managed, _DENY_FALSE_MANAGED),
        (bad_dimension, _DENY_SEARCHTERM),
        (bad_identity, _DENY_IDENTITY_EXTRACT),
        (bad_healthy_src, _DENY_HEALTHY_SRC_EXTRACT),
        (bad_missed_rescue, _DENY_MISSED_RESCUE),
        (bad_waste_hold, _DENY_WASTE_HOLD),
        (bad_waste_kw, _DENY_WASTE_KW),
        (bad_extract, _DENY_EXTRACT_NO_BID),
        (bad_targeting, _DENY_TARGETING),
    ):
        if bucket:
            samples = '；'.join(f'「{v}」' for v in bucket[:5])
            parts.append(template.format(n=len(bucket), samples=samples))
    if not parts:
        return None
    return GateDeny(gate=GATE_NAME, reason=' '.join(parts))
