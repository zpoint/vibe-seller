"""Patterns and per-campaign spend reconciliation for the reviewer.

Split out of ``ad_completeness_review`` for the per-file line limit. What
lives here is the report's SHAPE — the regexes that find a combo section,
a progress line, a drill table, a deferral, garbled extraction — plus the
one check that needs a platform export to answer: does a campaign's
reported spend match what the platform says?

The combo header is shape-matched and defined once in ``ad_scope``. A
literal country list here drifted and silently exempted a whole
marketplace from every per-campaign check.
"""

import re

from app.ai.stop_gates import (
    ad_bid_floor,
    ad_explicit_actions,
    ad_rollup,
    ad_scale_winners,
    ad_scope,
)

# A "## <Platform> <Country>" combo section header, e.g.
# "## Amazon US", "## noon EG 市场", "## Noon MX 市场".
# Shape-matched and defined ONCE in ad_scope — a literal country list here
# drifted and silently exempted a whole marketplace from every per-campaign
# check. See ``ad_scope.COMBO_HEAD_PATTERN``.
_COMBO_HEADER_RE = ad_scope.COMBO_HEAD_RE
# "**进度**: drilled 12/46 active (70 total, 5 pages)"
# The <T> total / <P> pages suffix is not machine-enforced here: a
# correct bulk-export enumeration legitimately records a large total as
# "1 page" (one export file), so T/P alone can't distinguish it from a
# grid page-1-only read. Under-enumeration is caught instead by (a) the
# self-disclosed-truncation phrasings in ``_DEFER_RE`` and (b) the
# reviewer independently reading the live account total (reviewer-loop.md).
_PROGRESS_RE = re.compile(
    r'drilled\s+(\d+)\s*/\s*(\d+)\s*active', re.IGNORECASE
)

# A per-campaign keyword/target table header has a 建议/recommendation
# column — the EVIDENCE of a real drill. A page-manifest table
# (活动ID|类型|花费|ROAS, no 建议 column) does NOT match, so a section
# claiming drills but with ~none of these is a manifest, not a drill —
# this closes the "write drilled D/A but no real content" gaming hole.
_DRILL_TABLE_RE = re.compile(
    r'^\|.*(?:建议|recommendation).*\|\s*$', re.IGNORECASE | re.MULTILINE
)

# Excuse phrases that defer work which must be done THIS session
# (Brand Analytics is accessible without OTP; cross-platform / per-
# campaign drills are not "next audit" items).
_DEFER_RE = re.compile(
    r'待下次\s*audit|下次\s*audit|下次任务|无法获取|未获取|本次会话未'
    r'|留待下次|待?下次审计|下一次审计|留待后续'
    r'|需\s*Brand\s*Registry\s*OTP|需要?\s*OTP|待\s*drill'
    r'|pending[^。\n]*drill|代表性样本|快速扫描|仅\s*overview'
    # Self-disclosed pagination truncation: the report ADMITS it only
    # read part of the campaign list (the grid page-export trap — it
    # should have used the account-wide bulk export instead). These
    # phrasings ("仅获取第1页(50/150)", "第2-3页…待翻页获取", "待翻页",
    # "N total 但只…") slipped past because the old list had only the
    # generic "未获取". The disclosure lives in the report's free-prose
    # limitation note, written in the USER'S language (reports are
    # language-followed — see the result_language gate), so match BOTH
    # Chinese AND English phrasings, not Chinese alone.
    r'|仅\s*获取第|只\s*获取\s*(?:了)?\s*(?:当前|第一?)\s*页'
    r'|只\s*导出\s*(?:了)?\s*(?:当前|第一?)\s*页|待\s*翻页|未\s*翻页'
    r'|页[^。\n]{0,8}?(?:未获取|待获取|未翻页|未采集)'
    r'|待[^。\n]{0,6}?翻页|翻页[^。\n]{0,6}?(?:获取|采集)'
    # English equivalents:
    r'|only\s+(?:got|read|captured|scraped|fetched|the\s+first)[^.\n]{0,18}?\bpage'
    r'|(?:first\s+page|page\s*1)\s+only|page\s*1\s+of\s+\d'
    r'|(?:remaining|further|other|additional)\s+pages\b[^.\n]{0,20}?'
    r'(?:pending|not\b|un|missing|to\s?do)'
    r'|pages?\s*\d+\s*[-–]\s*\d+[^.\n]{0,20}?(?:not\b|pending|un|missing)'
    r'|pending\s+pagination|without\s+paginating'
    r'|(?:did\s*n.?t|did\s+not|not)\s+paginat(?:e|ed)',
    re.IGNORECASE,
)

# Garbled extraction: raw DOM attributes or lowercased ASINs left in the
# report. A clean report has UPPERCASE ASINs / readable keywords.
_GARBLED_RE = re.compile(
    r'asin-expanded\s*=|aria-label\s*=|\brole\s*=\s*["\']|\bb0[a-z0-9]{8}\b'
)

# A collapse row ("其余 N 个…") hides per-row data. Only acceptable for
# rows that are explicitly zero-impression/zero-click filler; any
# collapsed row WITH traffic makes the report unauditable.
_COLLAPSE_ROW_RE = re.compile(r'^\|[^\n]*其余\s*\d+\s*个[^\n]*$', re.MULTILINE)
_ZERO_JUSTIFIED_RE = re.compile(
    r'0\s*展示|0\s*点击|0\s*impressions?', re.IGNORECASE
)


# Amazon's figures MOVE. A report captures a campaign at one moment and
# the export is generated later, and attribution keeps landing in between:
# comparing one live report against an export produced 2.5 hours after its
# capture, many campaigns differed — all by well under 1% (e.g. 480.00 vs
# 482.00). Those are not errors, and flagging them would make this
# check noise on every run.
#
# The failures it exists to catch are an order of magnitude larger, because
# they are structural rather than temporal:
#   * enabled-only filtering of the targeting layer — 10%, 20%, 90%+ low
#   * a mis-join, one campaign's figures under another's id — unbounded
# 5% sits an order of magnitude above the drift and an order below the
# smallest real defect, so it separates them cleanly without tuning.
_SPEND_REL_TOL = 0.05
# ...and a floor, so a tiny campaign's rounding can't clear the ratio bar:
# 0.40 vs 0.50 is 25% off and worth nothing.
_SPEND_MIN_ABS = 1.0


def _check_campaign_spend(part, head, ref, combo_label, attr) -> None:
    """Compare each campaign's reported spend against the bulk export."""
    truth = ad_scope.campaign_spend_in_export(ref)
    if not truth:
        return  # unreadable / absent — unverifiable is never a failure
    wrong: list[str] = []
    for cid, reported in ad_rollup.combo_table_spend(part).items():
        actual = truth.get(cid)
        # Absent from THIS export is the shared-bulk case (a marketplace
        # citing another's file, which has none of its campaigns) — that
        # conflict is reported on its own; do not also call it a wrong
        # number.
        if actual is None or reported is None:
            continue
        delta = abs(reported - actual)
        if delta > actual * _SPEND_REL_TOL and delta >= _SPEND_MIN_ABS:
            wrong.append(f'「{cid}」报告 {reported:.2f} vs 导出 {actual:.2f}')
    if not wrong:
        return
    sample = '；'.join(wrong[:4])
    more = '' if len(wrong) <= 4 else f' 等共 {len(wrong)} 个'
    attr(
        combo_label,
        f'[花费核对] 「{head}」有 {len(wrong)} 个活动的花费与 `{ref}` 里'
        f'该活动自己的 Campaign 行对不上：{sample}{more}。导出文件是平台'
        '给的原始数字，报告必须跟它一致。两个常见原因：定向层按 '
        '`state=enabled` 过滤了（漏掉窗口内有花费、之后被暂停的定向词，'
        '报告会偏小），或者把另一个活动的数字写到了这个 id 下面（mis-join，'
        '两层之间反而是自洽的，所以只有跟导出比才看得出来）。以导出的 '
        'Campaign 行为准改正，组合表、合计 行、对账行三处一起改。'
        f'（只报差异超过 {_SPEND_REL_TOL:.0%} 的：Amazon 归因会随时间小幅'
        '变动，报告采集时刻和导出生成时刻之间的零点几个百分点属正常。）',
    )


def cross_cutting_gaps(result_text: str, rules) -> list[str]:
    """Gaps that belong to the RUN, not to any one marketplace.

    Bid-rule violations, deferred work, rollup arithmetic, the opening
    line, garbled extraction — each is decidable from the document alone
    and none is attributable to a country, so they stay global and do not
    move any single combo's convergence distance.
    """
    gaps: list[str] = []
    # 2) Bid-rule violations — fold in the rule checks (short form),
    #    forwarding the resolved thresholds so a per-store override is
    #    honored consistently here too.
    bf = ad_bid_floor.check(result_text, rules)
    if bf:
        gaps.append('[规则·不可下调] ' + bf.reason[:160])
    sw = ad_scale_winners.check(result_text, rules)
    if sw:
        gaps.append('[规则·加投赢家] ' + sw.reason[:160])
    ea = ad_explicit_actions.check(result_text, rules)
    if ea:
        gaps.append('[规则·明确幅度] ' + ea.reason[:400])

    # 3) No-defer: work the agent excused as "next audit" / "needs OTP"
    #    that is actually doable this session (Brand Analytics is
    #    accessible without OTP; cross-platform + per-campaign drills are
    #    in-scope now).
    defers = list(dict.fromkeys(_DEFER_RE.findall(result_text)))
    if defers:
        sample = '、'.join(f'「{d}」' for d in defers[:5])
        gaps.append(
            '[不可推迟] 报告里把本应本次完成的工作推迟/找借口了：'
            + sample
            + '。本会话已同时打开多平台，有足够上下文与时间：Brand Analytics '
            'ASIN 报告无需 OTP 可直接进入获取；跨平台/同-SKU 对比、逐活动 '
            'drill 必须本次完成，不能写“待下次 audit / 无法获取 / 代表性样本”。'
        )

    # 3b) The same number written three times must agree. Decidable from
    #     the document alone, and the LLM reviewer had to catch this by
    #     hand on two consecutive rounds (see ``ad_rollup``).
    gaps.extend(ad_rollup.check_rollups(result_text))

    # 3c) The deliverable must open as a REPORT, not as a review verdict.
    #     `Status: ok|gaps|incomplete` is the REVIEW file's format
    #     (`REVIEW_<date>_iterN.md`), read by `report_reviewer`. Live, an
    #     audit shipped with `Status: gaps` as its literal first line,
    #     above the H1 — so the deliverable a user opens led with an
    #     internal gate token that means nothing to them, and any reader
    #     scanning for a verdict would find "gaps" in the report itself.
    first = next(
        (ln.strip() for ln in result_text.splitlines() if ln.strip()), ''
    )
    if re.match(r'(?i)^\**status\**\s*[:：]', first):
        gaps.append(
            f'[格式] 报告的第一行是 `{first[:40]}` —— 这是 review 文件'
            '（`REVIEW_<date>_iterN.md`）的格式，不是审计报告的。报告要以'
            '`# 广告优化建议 — <店铺> — <日期>` 开头；`Status:` 只写在 '
            'review 文件里。把这一行从报告里删掉。'
        )

    # 4) Garbled extraction — raw DOM attributes / lowercased ASINs.
    if _GARBLED_RE.search(result_text):
        gaps.append(
            '[数据] 报告含未清洗的原始 DOM 值（如 asin-expanded="…"、小写 '
            'b0xxxxxxxx）。搜索词必须干净：要么是可读关键词，要么是大写 ASIN '
            '(商品页投放)，并带 匹配来源/点击/花费/订单/ROAS 列——不要把 DOM '
            '属性或小写串直接塞进表格。'
        )
    return gaps
