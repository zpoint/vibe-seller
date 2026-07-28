"""Per-campaign reconciliation helpers for the ad-audit completeness gate.

Split out of ``ad_completeness_review`` to keep that module under the
pre-commit 800-line cap. Owns the per-campaign search-term / targeting
reconciliation contract:

  * the campaign-heading regex (also re-exported for the duplicate-id
    check in ``ad_completeness_review.check``);
  * the per-campaign spending reconciliation / no-search-term escape;
  * the spend-tolerance helpers;
  * ``_check_campaign_blocks`` itself.

Live with the rest of the gate's task-level state and pattern
constants — none of it makes sense without the surrounding
``check``/``is_stalled`` machinery.
"""

from __future__ import annotations

import re

from app.ai.stop_gates import ad_scope

# A campaign block is a "### " heading that carries a campaign id
# (Amazon: long numeric; noon: C_XXXX; or Amazon alphanumeric *entity*
# id ``A`` + ~20 uppercase alphanumerics). Each such block must prove
# its search-term layer was drilled ON THE SAME DATE WINDOW as the
# targeting table. Used both by ``_check_campaign_blocks`` and by
# ``ad_completeness_review.check``'s duplicate-id scan, so it lives
# here and is re-imported by the main module.
_CAMPAIGN_HEAD_RE = re.compile(r'\d{10,}|C_[A-Z0-9]{6,}|A[0-9A-Z]{16,}')

# Machine-readable reconciliation line (output-spec):
#   搜索词对账: 定向花费 USD 942.39 / 点击 762 = 搜索词花费 USD 942.39 / 点击 762 (✓)
_RECONCILE_RE = re.compile(
    r'搜索词对账[:：][^\n]*?定向花费[^\d\n]*([\d,]+(?:\.\d+)?)'
    r'[^\n]*?点击[^\d\n]*([\d,]+)'
    r'[^\n]*?搜索词花费[^\d\n]*([\d,]+(?:\.\d+)?)'
    r'[^\n]*?点击[^\d\n]*([\d,]+)'
)
_NO_SEARCHTERM_RE = re.compile(
    r'无搜索词报告|无\s*Search\s*Terms|该活动类型无搜索词|无点击(?:无搜索词)?'
    r'|0\s*点击.{0,12}无搜索词',
    re.IGNORECASE,
)
# An escape token asserts "this campaign type HAS no search-term report".
# A line that in the same breath says the data still needs fetching makes
# the OPPOSITE claim — it admits the layer is missing — so it must not
# silence the check for that layer. Observed live: 「无搜索词报告（需从
# Search Terms 页面导出全量 CSV）」 was accepted 30× while the layer was
# never captured; an earlier revision wrote 「搜索词花费 待导出」, which
# correctly failed, and the agent then found the phrasing that passed.
_PENDING_WORK_RE = re.compile(
    r'待\s*(?:导出|获取|采集|回采|钻取|补)|尚未|还没|未获取|未导出|未采集'
    r'|需\s*(?:要)?\s*(?:从|回采|重新|再)|需[^\n。；]{0,14}?(?:导出|钻取|采集|回采)'
    r'|to\s?be\s+(?:exported|fetched|drilled)|pending\s+(?:export|drill)',
    re.IGNORECASE,
)


# A row that RESTATES the campaign total is not a per-target row. The
# targeting layer exists so bids can be raised, lowered or paused PER
# KEYWORD; a table whose only row is 整体活动 / 定位层汇总 / 合计 names
# nothing to act on. Observed live: 18 of 20 Amazon SA campaigns shipped
# exactly that shape — one aggregate row plus "需从 Target 页面钻取" —
# and passed, because the presence check was satisfied by the HEADER row
# alone (every markdown table has one).
_AGGREGATE_ROW_RE = re.compile(
    r'合计|总计|汇总|整体活动|定位层|overall|^total\b', re.IGNORECASE
)
# A markdown header underline: ``|---|---|`` (alignment colons allowed).
_TABLE_SEP_RE = re.compile(r'^\|[\s:|-]+\|?$')

# A campaign's search terms cannot spend MORE than the campaign's own
# targeting layer — every query's cost is already counted there. Measured
# against one live account's bulk export (both layers, same 30-day window,
# 17 enabled SP campaigns): ratio min 0.998, median 1.000, max 1.000, with
# 15 of 17 exactly 1.000 and the other two off only by 2-decimal rounding.
# Clicks tracked identically. noon's captured layers likewise never exceed
# 1.000 (median 0.779 — its Customer Queries page attributes only part of
# spend, which is the FLOOR case below, not this one).
#
# So 1.02 is the live maximum plus 2% headroom for rounding and currency
# formatting, not a guess. Above it the report is asserting something
# arithmetically impossible about itself — observed live at 1.26, 1.61 and
# 13.30 from a generator that joined one campaign's targeting rows to
# another's search terms.
IMPOSSIBLE_CEILING = 1.02

# ...but a ratio alone is not enough, because a small denominator turns
# noise into a big ratio. Across 81 live noon reconciliation lines exactly
# ONE exceeded the ceiling: targeting 39.00 vs search-term 41.00 — ratio
# 1.051, but only +2.00 and ONE extra click, i.e. the two pages were read
# moments apart. Flagging that as "impossible" would be a false positive
# on the cheapest campaigns, which is where the ratio is least stable.
#
# Real mis-joins are large in ABSOLUTE terms — the four observed on Amazon
# were +17.00, +96.08, +126.00 and +155.30. So a contradiction needs BOTH
# a ratio above the ceiling AND a material absolute excess. 10 currency
# units sits cleanly between the noise case (+2) and the smallest genuine
# one (+17); SAR and AED are both ~0.27 USD, so this is roughly $3.
IMPOSSIBLE_MIN_EXCESS = 10.0

# The acknowledgment that resolves a contradiction WITHOUT fixing the
# numbers: the block itself declares its figures untrustworthy and tells
# the reader not to act on them. This is the second of the two legal
# answers ("fix it, or mark it clearly"), and it is what keeps the
# non-stallable check from being a trap — it is always satisfiable by
# writing one honest sentence. It must say BOTH things: that the data is
# unreliable, AND that this campaign's recommendations must not be
# executed. Half of it ("数据有偏差") would let the rows still read as
# actionable, which is the outcome the check exists to prevent.
_QUARANTINE_RE = re.compile(
    r'(?=[^\n]*数据不可信|[^\n]*不可信|[^\n]*数据矛盾|[^\n]*data unreliable)'
    r'[^\n]*(?:请勿执行|不要执行|勿执行|不可执行|do not execute|do not act)'
)


def _is_quarantined(block: str) -> bool:
    """True if the block declares its own figures untrustworthy.

    Matched per LINE so the disclaimer and the do-not-execute instruction
    have to travel together — a stray 不可信 somewhere in a long block
    must not silence the check for a campaign whose rows still present
    themselves as actionable.
    """
    return any(_QUARANTINE_RE.search(ln) for ln in block.splitlines())


def _layer_row_count(block: str, *, searchterm: bool) -> int:
    """Per-row data rows in this block's targeting OR search-term tables.

    A table is identified by a header row (carries the ``建议`` column)
    IMMEDIATELY followed by the ``|---|`` separator — not by "a line
    containing 建议", because a recommendation cell legitimately contains
    the word (``建议出价 3.00``) and would otherwise read as a new header.
    ``searchterm`` selects which table kind to count, keyed on whether the
    first column names 搜索词.

    Both layers need this, for the same reason. The targeting layer's
    aggregate-row evasion (one 整体活动 row standing in for the keywords)
    has an exact twin on the search-term side: a single 汇总 row standing
    in for the customer queries. Observed live in one run's own declared
    gaps — "13 个低花费 Amazon SA 活动的 Search Terms 表用汇总行代替逐条
    top-15". That model self-corrected; a weaker one would ship it, and
    the gate could not tell, because the search-term layer was only ever
    checked for PRESENCE plus a reconciliation line.
    """
    lines = block.splitlines()
    count = 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ''
        if not (
            line.startswith('|') and '建议' in line and _TABLE_SEP_RE.match(nxt)
        ):
            i += 1
            continue
        is_st = '搜索词' in line.strip('|').split('|')[0]
        if is_st is not searchterm:
            i += 1
            continue
        i += 2  # past the header and its separator
        while i < len(lines) and lines[i].strip().startswith('|'):
            cell = lines[i].strip().strip('|').split('|')[0].strip(' *')
            if cell and not _AGGREGATE_ROW_RE.search(cell):
                count += 1
            i += 1
    return count


def _targeting_row_count(block: str) -> int:
    """Per-target data rows across this block's targeting tables."""
    return _layer_row_count(block, searchterm=False)


def _searchterm_row_count(block: str) -> int:
    """Per-query data rows across this block's search-term tables."""
    return _layer_row_count(block, searchterm=True)


def _has_valid_escape(block: str) -> bool:
    """True if the block legitimately claims "no search-term report".

    Matched per LINE, not per block: the token has to stand on its own
    line without an admission of pending work beside it. Per-block
    matching also meant a single token anywhere silenced every campaign
    in a block that had merged several.
    """
    for line in block.splitlines():
        if _NO_SEARCHTERM_RE.search(line) and not _PENDING_WORK_RE.search(line):
            return True
    return False


def _num(s: str) -> float:
    return float(s.replace(',', ''))


def _within(a: float, b: float, tol: float) -> bool:
    hi = max(abs(a), abs(b))
    if hi == 0:
        return True
    return abs(a - b) / hi <= tol


def _check_campaign_blocks(
    part: str,
    head: str,
    tol: float,
    gaps: list[str],
    floor: float | None = None,
    active_ids: list[str] | None = None,
    contradictions: list[str] | None = None,
) -> None:
    """Per-campaign search-term reconciliation + collapse checks for one
    combo section. Appends gap strings to ``gaps``.

    ``active_ids`` is the AUDIT_SCOPE authoritative set for this combo.
    When given, the per-campaign obligations are ONE PER AUTHORITATIVE ID
    — the agent cannot change how much it is graded on by choosing how
    many headings to write, or at what depth. Without it the function
    falls back to shape-matching headings via ``_CAMPAIGN_HEAD_RE``, which
    is what allowed 27 of 30 Amazon campaigns to carry no obligation at
    all. Ids with no block are left to the scope coverage check, which
    reports them as undrilled rather than as a missing search-term layer.

    ``floor`` is the platform's lower bound on search-term ÷ targeting
    spend. It used to be looser for noon (0.40) on the belief that its
    Customer Queries page only attributes part of campaign spend. That
    belief was an artifact of reading the CQ TAB, which renders a fixed
    top-15 with no paginator; via its Export the two layers agree
    exactly (measured 1.000 on both an Auto campaign — 10000 query rows
    — and a Manual one — 404 rows). Both platforms now use the same
    floor: a low ratio means an incomplete capture, on either.
    """
    if active_ids:
        found = ad_scope.blocks_by_active_id(part, active_ids)
        # Keyed by the authoritative id: a stable name that is also free
        # of the campaign-name prose the old heading slice carried.
        blocks = [(cid, found[cid]) for cid in active_ids if cid in found]
    else:
        blocks = [
            (h[:48], b)
            for h, b in ad_scope.drill_blocks(part)
            if _CAMPAIGN_HEAD_RE.search(h)  # skip e.g. ### 汇总
        ]
    missing: list[str] = []
    mismatched: list[str] = []
    impossible: list[str] = []
    # Below-floor campaigns the block itself declares unreliable and
    # do-not-execute. Excused individually, but counted: quarantine is an
    # escape hatch for what a platform will not give up, not a way to
    # opt out of capturing a combo.
    quarantined_low: list[str] = []
    no_target_table: list[str] = []
    aggregate_only: list[str] = []
    st_aggregate_only: list[str] = []
    unparsed: list[str] = []
    for name, block in blocks:
        # A drilled block must carry the TARGETING table, not only the
        # search-term layer — a search-term-only block leaves no place
        # for bid/pause decisions (auto campaigns review their auto
        # target groups there). Blocks that explain a no-data page
        # (无数据 / 无SKU) are exempt.
        has_st_table = False
        has_tgt_table = False
        for bl in block.splitlines():
            if not (bl.startswith('|') and '建议' in bl):
                continue
            first = bl.strip().strip('|').split('|')[0]
            if '搜索词' in first:
                has_st_table = True
            else:
                has_tgt_table = True
        no_data_page = bool(re.search(r'无数据|无\s*SKU', block))
        if has_st_table and not has_tgt_table and not no_data_page:
            no_target_table.append(name)
        elif (
            has_tgt_table
            and not no_data_page
            and _targeting_row_count(block) == 0
        ):
            aggregate_only.append(name)
        # Same rule, other layer: a search-term table that exists but
        # names no query is a 汇总 row standing in for the customer
        # queries. Only checked when the block has an ST table at all —
        # a missing layer is the `missing` / escape-token path below.
        if (
            has_st_table
            and not no_data_page
            and _searchterm_row_count(block) == 0
        ):
            st_aggregate_only.append(name)
        m = _RECONCILE_RE.search(block)
        if not m:
            if _has_valid_escape(block):
                continue
            # A block that HAS a 搜索词对账 line we could not parse is a
            # DIFFERENT failure from one that has no line at all, and must
            # say so. Reporting a parse failure as "missing the layer" is
            # what convinced an agent the reviewer was broken: it had
            # written a line, was told the layer was absent, and spent 11
            # rounds rewriting prose instead of fixing the line.
            raw = [
                ln.strip() for ln in block.splitlines() if '搜索词对账' in ln
            ]
            if raw:
                unparsed.append(f'「{name}」{raw[0][:70]}')
            else:
                missing.append(name)
            continue
        t_spend, t_clicks, s_spend, s_clicks = (_num(g) for g in m.groups())
        # SPEND is the window-mismatch signal: a wrong date window shifts
        # spend proportionally, so spend agreeing within tolerance proves
        # both pages were read on the same window. CLICKS are advisory
        # only — Amazon's search-term report strips invalid clicks, so
        # click totals legitimately diverge even on a perfect same-window
        # read (observed: spend within 2% while clicks differ 37%).
        # Requiring clicks too created irreconcilable false positives.
        #
        # A mismatch is TWO different failures and they deserve different
        # treatment (see ``IMPOSSIBLE_CEILING``):
        #   * search-term spend ABOVE targeting spend cannot be true;
        #   * search-term spend BELOW it is an incomplete capture.
        # Only the first is a contradiction.
        excess = s_spend - t_spend
        if (
            s_spend > t_spend * IMPOSSIBLE_CEILING
            and excess > IMPOSSIBLE_MIN_EXCESS
            and not _is_quarantined(block)
        ):
            impossible.append(
                f'「{name}」定向花费 {t_spend:g} < 搜索词花费 {s_spend:g}'
                f'（{s_spend / t_spend:.2f}×）'
            )
        elif s_spend < t_spend * (floor if floor is not None else 1 - tol):
            # Quarantine settles this one too. The contract for a figure
            # the agent cannot make right is "fix it, or mark it clearly",
            # and it has to stay satisfiable — otherwise the gap is a
            # standing order to retry something that cannot succeed.
            #
            # Live: two noon Brand Video campaigns whose Customer-Queries
            # Export never finishes loading (the tab hangs; the 15-row
            # on-page table is all the platform will give). The agent
            # documented exactly that, marked both do-not-execute, and was
            # still handed the same [对账] gap every round — so it kept
            # re-attempting the export, four submissions and five
            # browser-use failures inside five minutes, on a capture the
            # platform does not support.
            if _is_quarantined(block):
                quarantined_low.append(name)
            else:
                mismatched.append(
                    f'「{name}」定向花费 {t_spend:g} vs 搜索词花费 {s_spend:g}'
                )
    if no_target_table:
        sample = '、'.join(f'「{n}」' for n in no_target_table[:4])
        gaps.append(
            f'[定向表] 「{head}」有 {len(no_target_table)} 个活动只有'
            f'搜索词表、没有定向/关键词表：{sample}。出价与暂停决策'
            '发生在定向表上（auto 活动也要列出 auto target 组及其建议）'
            '——补上该活动的定向表（含 建议 列），或在块内注明页面无数据。'
        )
    if aggregate_only:
        sample = '、'.join(f'「{n}」' for n in aggregate_only[:4])
        more = (
            ''
            if len(aggregate_only) <= 4
            else f' 等共 {len(aggregate_only)} 个'
        )
        gaps.append(
            f'[定向层] 「{head}」有 {len(aggregate_only)} 个活动的定向表'
            f'只有一行活动汇总（整体活动 / 定位层汇总 / 合计），'
            f'没有任何一行是具体的关键词或定向组：{sample}{more}。'
            '把活动级数字抄进一张带 建议 列的表里不算下钻——出价、暂停、'
            '加投都是逐个关键词/定向组做的决策，汇总行里没有可执行的对象。'
            '打开该活动的 Targeting 页（noon Manual: Targets 标签；'
            'SP Auto: 四个 auto 定向组；noon Auto 无 Targets 页则以 '
            'Customer Queries 为定向面），逐词/逐组列出 出价、点击、花费、'
            '订单、销售额、ACOS/ROAS 与 建议，合计行只能是最后的补充行。'
            '该活动页面确实没有数据时，在块内写明「无数据」。'
        )
    if st_aggregate_only:
        sample = '、'.join(f'「{n}」' for n in st_aggregate_only[:4])
        more = (
            ''
            if len(st_aggregate_only) <= 4
            else f' 等共 {len(st_aggregate_only)} 个'
        )
        gaps.append(
            f'[搜索词层] 「{head}」有 {len(st_aggregate_only)} 个活动的搜索词表'
            f'只有一行汇总，没有任何一条具体的搜索词：{sample}{more}。'
            '否定、提取为定向词都是逐个搜索词做的决策，汇总行里没有可执行'
            '的对象——低花费活动也一样，花得少不等于不用逐条看。把该活动'
            '搜索词报告里按花费排序的 top 词逐行列出（有展示的词不得折叠；'
            '全零展示的填充行才可以折叠且必须写「0 展示」），合计行只能是'
            '最后的补充行。完整数据已经在 <id>.searchterms.tsv 里，直接取。'
        )
    if missing:
        sample = '、'.join(f'「{n}」' for n in missing[:4])
        more = '' if len(missing) <= 4 else f' 等共 {len(missing)} 个'
        gaps.append(
            f'[搜索词] 「{head}」有 {len(missing)} 个活动缺少搜索词层：'
            f'{sample}{more}。每个活动必须下钻搜索词报告（Amazon: Search '
            'Terms 页 Export CSV 全量导出；noon: Customer Queries），逐词列出'
            '（有展示的词不得折叠），并写一行机器可读的对账：'
            '`搜索词对账: 定向花费 <币> X / 点击 A = 搜索词花费 <币> Y / '
            '点击 B (✓/✗)`。无搜索词报告的活动类型（如 SD）写「无搜索词报告」。'
        )
    if unparsed:
        sample = '；'.join(unparsed[:3])
        more = '' if len(unparsed) <= 3 else f' 等共 {len(unparsed)} 个'
        gaps.append(
            f'[搜索词·格式] 「{head}」有 {len(unparsed)} 个活动写了 '
            f'搜索词对账 行，但**解析不了**（不是内容缺失，是格式不对）：'
            f'{sample}{more}。必须是这一行、四个数字齐全：'
            '`搜索词对账: 定向花费 <币> X / 点击 A = 搜索词花费 <币> Y / '
            '点击 B (✓/✗)`。写「需回采」「待导出」「→ 当前 30 天 …」这类'
            '说明**等于承认这一层没取到**——那就去把搜索词页锁到同一个 30 天'
            '窗口重新取数，再填上四个数字；确实没有搜索词报告的活动类型'
            '（如 SD）才写「无搜索词报告」，且该行不能同时写「需…导出」。'
        )
    if impossible:
        sample = '；'.join(impossible[:3])
        more = '' if len(impossible) <= 3 else f' 等共 {len(impossible)} 个'
        gap = (
            f'[对账·不可能] 「{head}」有 {len(impossible)} 个活动的搜索词'
            f'花费**超过**了该活动定向层的花费：{sample}{more}。这不是'
            '误差，是不可能——每个搜索词的花费本来就已经计在定向层里了，'
            '实测同窗口下这个比值最大就是 1.00。通常是把 A 活动的定向表'
            '和 B 活动的搜索词配到了一起（join 错了 Campaign ID），或者'
            '两个数取自不同活动/不同账户的导出。**这一条不会因为轮次用尽'
            '而放过**：要么把两层重新按同一个 Campaign ID 取一次、改对'
            '数字；要么在该活动块里明确写「⚠️ 数据不可信：本活动两层'
            '对账矛盾，请勿执行本活动的出价建议」，让读报告的人知道这'
            '几行不能用。'
        )
        gaps.append(gap)
        if contradictions is not None:
            contradictions.append(gap)
    if mismatched:
        sample = '；'.join(mismatched[:3])
        band = (
            f'下限 {floor:.0%}（noon CQ 仅归因部分花费）'
            if floor is not None
            else f'下限 {1 - tol:.0%}'
        )
        gaps.append(
            f'[对账] 「{head}」搜索词与定向数据对不上（{band}）：'
            f'{sample}。两边必须用同一个 30 天窗口——对不上通常是搜索词页'
            '日期窗口跟定向页不一致（如 7 天 vs 30 天）或搜索词抓取不全。'
            '回到该活动，把两页锁到同一窗口重新取数。数据确实取不到'
            '（平台不提供全量导出）时，在该活动块内写明「数据不可信 + '
            '请勿执行本活动的出价建议」并说明卡在哪一步，就不再算缺口。'
        )
    # Quarantine excuses individual campaigns, never a whole combo. Past
    # a third of the drilled set the report has stopped being an audit of
    # that market, so say so instead of accepting it silently.
    # A FRACTION alone over-triggers at small N: a combo holding one
    # campaign that genuinely cannot export is 1/1 = 100% quarantined, and
    # flagging it removes the escape hatch precisely where it is the only
    # honest answer. "Quarantined its way out of a market" needs several
    # campaigns to be a real pattern, so require both.
    drilled = max(len(blocks), 1)
    if len(quarantined_low) >= 3 and len(quarantined_low) * 3 > drilled:
        sample = '、'.join(f'「{n}」' for n in quarantined_low[:4])
        gaps.append(
            f'[对账] 「{head}」有 {len(quarantined_low)}/{drilled} 个活动'
            f'因「数据不可信」被整块排除：{sample}。单个活动平台确实导不出'
            '可以这样标注，但这里已经占到该市场的三分之一以上——那不是'
            '个别限制，是这一层的取数方式不对。先确认是不是漏了导出入口'
            '（活动级 Export / bulk 导出），再决定哪些确实标注排除。'
        )
