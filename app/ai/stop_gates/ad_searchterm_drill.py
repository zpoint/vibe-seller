"""Gate: an ad report must drill search terms to the WORD level.

The product promise is that a user asks for an ad report and the agent
does the full drill autonomously — for any report (full audit, one
campaign, a quick compare) on any platform (Amazon or noon). A report
that only COUNTS or CATEGORIZES the wasting search terms
("N 个词全白花 / 浪费词数 58 / 搜索词全是 <category-word> / 主要垃圾词
类型 …") without listing the actual words + a per-term action never
really drilled — it hands the user a summary, not "which words, what's
wrong, how to handle".

This denies such a report: when the result is an ad report (a 建议 table
or ≥2 campaign blocks) and it talks about wasting keywords in AGGREGATE
but carries NO per-search-term drill table (a term column + an action
column), it must go enumerate the terms. Folded into
``ad_completeness_review`` alongside the other rule checks. No-op for
non-report results.
"""

from __future__ import annotations

import re

from app.ai.stop_gates import GateDeny

GATE_NAME = 'ad_searchterm_drill'
MAX_DENIALS = 6

# The result presents as an ad report: a table with a 建议/recommendation
# column, or several campaign blocks.
_IS_REPORT_RE = re.compile(
    r'^\|.*(?:建议|recommendation).*\|', re.MULTILINE | re.IGNORECASE
)

# Aggregate keyword-waste language — counting/categorizing the wasting
# terms instead of listing them (the "didn't really drill" tell).
_AGG_WASTE_RE = re.compile(
    r'\d+\s*个\s*(?:定向|关键|搜索)?词'  # "10 个定向词", "N 个词"
    r'|浪费词数|垃圾词|主要垃圾词'
    r'|搜索词(?:全|都|大多|大部分|主要)是'  # "搜索词全是 X"
    r'|(?:全|大多|大部分)是[^\n。]{0,8}(?:垃圾|无关|品类)词',
    re.IGNORECASE,
)


def _has_searchterm_drill(text: str) -> bool:
    """True if the report has a per-search-term/keyword drill TABLE: a
    table whose HEADER carries a TERM column (搜索词 / 客户搜索词 / 关键词 /
    search term / query — not an aggregate count like 总搜索词 / 浪费词数)
    AND an action column (建议 / 动作 / 操作 / 否定 / recommendation).

    Only genuine header rows are inspected — a header is a ``|…|`` row
    immediately followed by a ``|---|…|`` separator — so a data row whose
    problem cell merely mentions 关键词/否定 is not mistaken for a header.
    """
    lines = text.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if not (s.startswith('|') and s.endswith('|')):
            continue
        if i + 1 >= len(lines):
            continue
        nxt = lines[i + 1].strip()
        if not (nxt.startswith('|') and nxt.endswith('|')):
            continue
        nxt_cells = [c.strip() for c in nxt.strip('|').split('|')]
        ne = [c.replace(' ', '') for c in nxt_cells if c.strip()]
        if not (ne and all(re.fullmatch(r':?-{2,}:?', c) for c in ne)):
            continue  # next row isn't a separator → this isn't a header
        headers = [c.strip() for c in s.strip('|').split('|')]
        has_term_col = any(
            (
                ('搜索词' in h)
                or ('客户搜索' in h)
                or ('关键词' in h)
                or ('search term' in h.lower())
                or ('query' in h.lower())
            )
            and ('总' not in h)
            and ('数' not in h)
            and ('浪费' not in h)
            and ('垃圾' not in h)
            for h in headers
        )
        has_action_col = any(
            h in ('建议', '动作', '操作', '否定', 'recommendation', 'action')
            for h in headers
        )
        if has_term_col and has_action_col:
            return True
    return False


def check(
    result_text: str,
    rules: dict[str, float] | None = None,
) -> GateDeny | None:
    """Deny an ad report that describes wasting keywords in aggregate but
    never drills them to a per-term table. ``rules`` is accepted for a
    uniform gate signature but unused (categorical rule)."""
    if not result_text or not isinstance(result_text, str):
        return None
    is_report = bool(_IS_REPORT_RE.search(result_text)) or (
        len(re.findall(r'(?m)^###\s', result_text)) >= 2
    )
    if not is_report:
        return None
    if not _AGG_WASTE_RE.search(result_text):
        return None
    if _has_searchterm_drill(result_text):
        return None
    reason = (
        '报告只用汇总/分类描述了浪费搜索词（如「N 个词全白花 / 浪费词数 X / '
        '搜索词全是 …/ 主要垃圾词类型 …」），却没有把它们逐词列出来。任何广告'
        '报告都必须下钻到词级：为每个有浪费的活动给一张搜索词表，逐词写 '
        '搜索词 | 点击 | 花费 | 订单 | 动作（否定 / 提价+幅度 / 提取为 Exact），'
        '把“具体哪些词、什么问题、怎么处理”讲清楚——不能只给数量和类型。'
        'Amazon 用 Search Terms 页 Export CSV 全量导出、noon 用 Customer '
        'Queries 全量分页，再逐词列出。'
    )
    return GateDeny(gate=GATE_NAME, reason=reason)
