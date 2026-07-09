"""Exit-hook completeness reviewer for ad-audit reports.

Instead of a binary pass/deny gate, this *reviews* the report against
the OUTPUT SPEC and returns a structured "what's still missing" diff, so
a model can converge over several rounds. Partial is accepted: each
``set_task_result`` it lists the top gaps; the agent fixes what it can
and re-submits; the diff shrinks. The reviewer keeps replying as long
as the agent makes progress and fails open only once it STALLS
(``STALL_CAP`` rounds with no net new drills — see ``is_stalled``) so a
weak model is never trapped yet a slow one is never cut off mid-climb.
Contract: ``amazon-ads/references/output-spec.md``.

Gaps reported:
  * **completeness** — for each ``## <Platform> <Country>`` section the
    spec requires a line ``**进度**: drilled <D>/<A> active``. A missing
    line, or ``D < A`` (under-drilled), is a gap that names the
    shortfall.
  * **bid-rule** — folds in ``ad_bid_floor`` (ACOS<30 bid lowered) and
    ``ad_scale_winners`` (ROAS≥8 converter parked on a bare Hold).

No-op for non-ads results (no combo sections and no ad bid tables).
"""

from __future__ import annotations

import re

from app.ai.stop_gates import (
    GateDeny,
    ad_bid_floor,
    ad_explicit_actions,
    ad_scale_winners,
    ad_scope,
    ad_zero_impression,
)
from app.ai.stop_gates.ad_rules import DEFAULT_RULES

GATE_NAME = 'ad_completeness_review'

# Fail-open is keyed on STALL, not a round count. A weak model that
# drills slowly (a few campaigns per round) still makes real progress
# every round — cutting it off after a fixed number of rounds would
# accept a half-finished report (e.g. noon 3/48) while D was still
# climbing. A round counts toward the stall budget only when NEITHER
# the total drilled count NOR the report itself moved (an agent that
# interleaves polish submits between drilling bursts is misprioritizing,
# not stuck — the deny message redirects it; ending the audit at 15/111
# because of three polish submits rewards exactly the wrong behavior,
# which is how a fail-open accepted 15/111). Only an agent re-submitting
# an essentially UNCHANGED report STALL_CAP times in a row is genuinely
# wedged and gets the partial accepted. Because the active set is
# finite, a progressing agent converges to D==A and the gate returns
# None on its own — there is no infinite loop to bound.
STALL_CAP = 5

# A report-text delta below this many characters counts as "unchanged"
# for stall purposes (cosmetic edits churn a few bytes; a new campaign
# block adds hundreds).
_STALL_MIN_DELTA = 400

# Anti-regression: the highest drilled-count seen per (task_id, combo)
# across this task's rounds. The convergence loop must be MONOTONIC —
# each round adds, never loses prior drills. If a submission reports
# fewer drilled than a previous round (the model rewrote the whole
# report from compacted memory and clobbered earlier work — e.g. Amazon
# US 31/31 → 2/31), the reviewer rejects and tells it to restore +
# append. Cleared per task by ``reset_progress`` on terminal success.
_max_drilled: dict[tuple[str, str], int] = {}

# Stall tracking for the fail-open decision: the best total-drilled sum
# (across all combos) seen for a task, the report length at the last
# submission, and how many consecutive rounds with neither moving.
# Updated by ``check`` each round; read by ``is_stalled``. Cleared by
# ``reset_progress``.
_total_high: dict[str, int] = {}
_last_len: dict[str, int] = {}
_stall_rounds: dict[str, int] = {}

# Stop-path backstop: how many times we've blocked an end-of-turn while
# the audit was still under-drilled, per task. Bounds the stop-path deny
# so a genuinely-stuck agent (can't drill more) isn't trapped forever —
# fails open after STALL_CAP blocks, mirroring the set_task_result stall.
_stop_blocks: dict[str, int] = {}


def reset_progress(task_id: str) -> None:
    """Drop the per-task progress/stall state (call on terminal success)."""
    for key in [k for k in _max_drilled if k[0] == task_id]:
        _max_drilled.pop(key, None)
    _total_high.pop(task_id, None)
    _last_len.pop(task_id, None)
    _stall_rounds.pop(task_id, None)
    _stop_blocks.pop(task_id, None)


def is_stalled(task_id: str) -> bool:
    """True once the agent has gone ``STALL_CAP`` rounds with no progress.

    The fail-open signal: the report still has gaps but the total
    drilled count has not increased for ``STALL_CAP`` consecutive
    rounds, so further "what's missing" replies won't help. Callers
    use this to accept the best report instead of denying forever.
    """
    return _stall_rounds.get(task_id, 0) >= STALL_CAP


def drill_incomplete_reason(
    result_text: str,
    task_id: str | None = None,
) -> str | None:
    """Stop-path backstop: deny reason if the audit isn't complete.

    Unifies the two completion paths. ``set_task_result`` runs the full
    :func:`check`; but an agent can also finish by simply ENDING ITS TURN,
    which persists the streaming result WITHOUT that gate (the 3/24 bypass
    — see ``claude_backend_stream._save_result``). The Stop hook calls
    this so ending the turn is gated by the SAME contract as
    ``set_task_result``.

    Delegates to :func:`check` with ``track=False`` — passing ``task_id``
    so the AUDIT_SCOPE ground-truth (#1/#2) still loads, but suppressing
    the ``_max_drilled`` / stall mutation, so calling it on every Stop
    attempt can't perturb the ``set_task_result`` convergence accounting.
    This enforces the FULL contract (authoritative combo + active-id
    coverage, two-layer per-campaign completeness), closing the hole where
    a report with ``进度 D==A`` but missing campaigns/layers slipped
    through the count-only check on the ending-turn path.

    Bounded: after ``STALL_CAP`` blocks for a task it fails open, so an
    agent that genuinely cannot finish is not trapped. Returns None when
    :func:`check` passes, or once this task has been blocked ``STALL_CAP``
    times.
    """
    if not result_text or not isinstance(result_text, str):
        return None
    deny = check(result_text, task_id, None, track=False)  # no mutation
    if deny is None:
        return None
    if task_id is not None:
        n = _stop_blocks.get(task_id, 0) + 1
        _stop_blocks[task_id] = n
        if n > STALL_CAP:
            return None  # fail open — don't trap a stuck agent
    return (
        '还不能结束：审计报告尚未完成（未 drill 完所有 active campaign，'
        '或部分 campaign 缺少定向/搜索词层）。请补齐下列缺口后再结束；'
        '不要留待“下一轮/下次审计”：\n' + deny.reason
    )


# A "## <Platform> <Country>" combo section header, e.g.
# "## Amazon US", "## noon EG 市场", "## Noon MX 市场".
_COMBO_HEADER_RE = re.compile(
    r'(amazon|noon)\s+(sa|ae|mx|us|eg|com)\b', re.IGNORECASE
)
# "**进度**: drilled 12/46 active (70 total, 5 pages)"
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
    # Search-term drill evasions observed shipping a shallow report:
    # deferring the reconciliation ("导出后补充对账"), claiming a page was
    # "confirmed" with no numbers ("已在 Search Terms 页面确认"), or the
    # unverified "only N days of data" excuse — all mean the search-term
    # layer was NOT drilled this session.
    r'|导出后补充|补充对账|需从\s*Search\s*Terms[^\n]{0,20}导出'
    r'|已在\s*Search\s*Terms[^\n]{0,12}确认|数据仅\s*\d+\s*天',
    re.IGNORECASE,
)

# Garbled extraction: raw DOM attributes or lowercased ASINs left in the
# report. A clean report has UPPERCASE ASINs / readable keywords.
_GARBLED_RE = re.compile(
    r'asin-expanded\s*=|aria-label\s*=|\brole\s*=\s*["\']|\bb0[a-z0-9]{8}\b'
)

# Unproven "this marketplace is empty" claim. Asserting a marketplace has
# NO campaigns is a NEGATIVE that must be PROVEN by enumeration — the
# false-negative class where a report declared one marketplace's products
# had "无广告投放 / 无活动" while that market actually had live campaigns.
# The agent guessed "empty" from a single-marketplace view without ever
# switching to that market and exporting it. Proof = AUDIT_SCOPE.json
# (written by the per-marketplace enumeration step); its presence means the
# agent actually switched + exported each market. When scope EXISTS, the
# coverage loop above already forces every enumerated active id to be
# drilled (and a genuinely-empty market is a combo with active_ids == []).
# The optional token before the noun lets "无 <product> 活动" match for any
# product name without hardcoding one.
_EMPTY_MARKET_CLAIM_RE = re.compile(
    r'无(?:任何)?\s*(?:[A-Za-z0-9一-鿿-]{1,16}\s+)?'
    r'(?:广告活动|活动投放|广告投放|广告|活动)'
    r'|零\s*广告(?:投放)?'
    r'|没有(?:任何)?\s*(?:广告活动|广告投放|广告|活动投放|活动)'
    r'|未(?:进行|做)?[^\n。]{0,6}广告投放'
    r'|no\s+(?:active\s+)?campaigns?',
    re.IGNORECASE,
)
# Only treat the result as an AUDIT (subject to the empty-claim proof
# rule) when it has real per-campaign drill/bid tables or several campaign
# blocks — so a narrow "create/investigate one ad" task that mentions
# "无广告活动" in passing isn't wrongly gated.
_IS_AUDIT_RE = re.compile(
    r'^\|.*(?:建议|recommendation).*\|', re.MULTILINE | re.IGNORECASE
)

# --- Per-campaign search-term drill + reconciliation ------------------
# A campaign block is a "### " heading that carries a campaign id
# (Amazon: long numeric; noon: C_XXXX). Each such block must prove its
# search-term layer was drilled ON THE SAME DATE WINDOW as the targeting
# table, via the machine-readable reconciliation line (output-spec):
#   搜索词对账: 定向花费 USD 942.39 / 点击 762 = 搜索词花费 USD 942.39 / 点击 762 (✓)
# Spend/clicks must agree within rules['reconcile_tolerance'] — a bigger
# gap means the two pages were read on different windows (the 30d-vs-7d
# bug) or the capture is incomplete. Campaign types with no search-term
# report (e.g. Sponsored Display) escape with an explicit
# 无搜索词报告 / 无点击 token instead.
# Campaign-id shapes that mark a "### " heading as a campaign block:
#   * long numeric — the canonical Amazon campaign id (e.g. 100000000001)
#   * A\d{7,}     — the ad-console SHORT display id (e.g. A1234567), which
#     is what the campaign-manager grid shows and what agents paste into
#     headings; missing this let every per-market block escape the
#     per-campaign search-term check (a whole audit shipped with fabricated
#     搜索词对账 lines because the block was never recognized as a campaign).
#   * C_XXXX      — noon.
# Anchored to the literal ad-console prefix `A` (not a generic `[A-Z]`) so
# it can't collide with an all-digit-suffix ASIN like B000123456 in a
# heading — those must NOT be treated as campaign blocks.
_CAMPAIGN_HEAD_RE = re.compile(r'\d{10,}|A\d{7,}|C_[A-Z0-9]{6,}')
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
# A collapse row ("其余 N 个…") hides per-row data. Only acceptable for
# rows that are explicitly zero-impression/zero-click filler; any
# collapsed row WITH traffic makes the report unauditable.
_COLLAPSE_ROW_RE = re.compile(r'^\|[^\n]*其余\s*\d+\s*个[^\n]*$', re.MULTILINE)
_ZERO_JUSTIFIED_RE = re.compile(
    r'0\s*展示|0\s*点击|0\s*impressions?', re.IGNORECASE
)


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
) -> None:
    """Per-campaign search-term reconciliation + collapse checks for one
    combo section. Appends gap strings to ``gaps``.

    ``floor`` switches the spend check to a platform-asymmetric band
    (noon): search-term spend must be ≥ ``floor``×targeting spend and
    ≤ (1+tol)×. noon's Customer Queries page attributes only part of
    campaign spend to queries — observed 47–74% across every live
    campaign after full pagination on a verified same-30d window — so
    symmetric tolerance produced unfixable mismatches. A wrong window
    still gets caught: a 7d read of a 30d targeting page shows ~23%,
    well under the 40% default floor.
    """
    blocks = re.split(r'(?m)^###\s+', part)[1:]
    missing: list[str] = []
    mismatched: list[str] = []
    no_target_table: list[str] = []
    for block in blocks:
        block_head = block.splitlines()[0].strip()
        if not _CAMPAIGN_HEAD_RE.search(block_head):
            continue  # not a campaign block (e.g. ### 汇总)
        name = block_head[:48]
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
        if (
            has_st_table
            and not has_tgt_table
            and not re.search(r'无数据|无\s*SKU', block)
        ):
            no_target_table.append(name)
        m = _RECONCILE_RE.search(block)
        if not m:
            if not _NO_SEARCHTERM_RE.search(block):
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
        if floor is not None:
            # noon asymmetric band (see docstring).
            bad = s_spend < t_spend * floor or s_spend > t_spend * (1 + tol)
        else:
            bad = not _within(t_spend, s_spend, tol)
        if bad:
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
    if mismatched:
        sample = '；'.join(mismatched[:3])
        band = (
            f'允许区间 {floor:.0%}–{1 + tol:.0%}（noon CQ 仅归因部分花费）'
            if floor is not None
            else f'容差 {tol:.0%}'
        )
        gaps.append(
            f'[对账] 「{head}」搜索词与定向数据对不上（{band}）：'
            f'{sample}。两边必须用同一个 30 天窗口——对不上通常是搜索词页'
            '日期窗口跟定向页不一致（如 7 天 vs 30 天）或搜索词抓取不全。'
            '回到该活动，把两页锁到同一窗口重新取数。'
        )


def check(
    result_text: str,
    task_id: str | None = None,
    rules: dict[str, float] | None = None,
    scope: dict | None = None,
    track: bool = True,
) -> GateDeny | None:
    """Return a structured gap diff, or None when the report is complete.

    ``task_id`` enables the per-combo anti-regression guard (monotonic
    convergence): a round that reports fewer drilled than a prior round
    is rejected as a lossy rewrite. ``rules`` carries the resolved
    bid-rule thresholds (defaults + per-store notes.md override),
    forwarded to the folded-in ``ad_bid_floor`` / ``ad_scale_winners``.

    ``scope`` is the parsed ``AUDIT_SCOPE.json`` (auto-loaded from
    ``task_id`` when not passed). When present it grounds completeness in
    the authoritative combo + active-id list instead of the agent's
    self-reported ``进度`` line; when absent the gate falls back to the
    self-reported behaviour (the escape hatch for first-time / narrow
    single-ad tasks). ``track`` gates the per-task mutation of the
    anti-regression / stall state, so the stop-path can run this as a
    pure check (``track=False``) without perturbing set_task_result's
    convergence accounting.
    """
    if not result_text or not isinstance(result_text, str):
        return None

    if scope is None:
        scope = ad_scope.load_audit_scope(task_id)

    gaps: list[str] = []
    round_total = 0  # sum of drilled across all combos this round

    # 1) Per-combo-section completeness, driven by the agent's own
    #    "**进度**: drilled D/A active" line (the OUTPUT-SPEC contract).
    #    Split on level-2 headers. ``parts[0]`` is the PREAMBLE (anything
    #    before the first ``## `` — an audit report's ``# 广告优化建议``
    #    title block, or an EXECUTION-task summary's prose). Skip it: only
    #    text that genuinely followed a ``## `` header is a combo section.
    #    Without this, an execution result whose prose merely STARTS with a
    #    platform name (e.g. "Amazon US 广告优化执行完毕…") matched
    #    _COMBO_HEADER_RE and was denied for missing a ``drilled D/A`` line
    #    — teaching the agent to FABRICATE "drilled 10/10" to pass. This
    #    gate is for audit GENERATION; an execution summary has no ``## ``
    #    combo sections, so it now correctly no-ops.
    parts = re.split(r'(?m)^##\s+', result_text)
    for part in parts[1:]:
        if not part.strip():
            continue
        head = part.splitlines()[0].strip()
        if not _COMBO_HEADER_RE.search(head):
            continue  # not a (platform, country) section
        m = _PROGRESS_RE.search(part)
        if not m:
            gaps.append(
                f'[完整性] 「{head}」缺少进度行 '
                '`**进度**: drilled <D>/<A> active (<T> total, <P> pages)` '
                '——请按 output-spec 记录该国真实 active 数与已 drill 数'
            )
            continue
        drilled, active = int(m.group(1)), int(m.group(2))
        round_total += drilled
        if drilled < active:
            gaps.append(
                f'[完整性] 「{head}」仅 drill {drilled}/{active} 个 active，'
                f'还差 {active - drilled} 个未逐 campaign drill——必须把这 '
                f'{active - drilled} 个全部逐一 drill 完（进度达到 '
                f'{active}/{active}）才能结束本任务；本轮尽量多补，未 drill 完'
                '不可提交完成，也不可结束本轮对话（server 会拦截）。不要留待'
                '“下一轮/下次审计”——没有下一轮。'
            )
        elif drilled > active:
            # Over-report: more drills than the active set. The model
            # swept in non-active (paused/archived) campaigns — typically
            # by batch-generating the report from EVERY on-disk TSV
            # instead of only the active set it enumerated (the dump that
            # produced "drilled 105/56"). Reject: D must equal A.
            gaps.append(
                f'[越界] 「{head}」报告了 {drilled} 个 campaign，但本国只有 '
                f'{active} 个 active——你把非 active（暂停/归档）的 campaign 也'
                '塞进来了（通常是用脚本把磁盘上所有 TSV 一次性灌进报告所致）。'
                '只能逐个 Read+Edit **当前 active 集合内** 的 campaign，'
                f'其余磁盘 TSV 忽略；让 drilled 等于 {active}，不要超过。'
            )
        # Anti-regression: this combo must never go BACKWARDS across
        # rounds. If a prior round already had more drilled, the model
        # rewrote the report from (compacted) memory and clobbered done
        # work — reject and tell it to restore from the on-disk TSVs.
        if task_id is not None and track:
            key = (task_id, head)
            prev = _max_drilled.get(key, 0)
            if drilled < prev:
                gaps.append(
                    f'[回退] 「{head}」上一轮已经 drill {prev} 个，这一轮却'
                    f'只有 {drilled} 个——你重写整份报告时把已完成的 drill 弄丢了。'
                    '**绝不能倒退**：不要从记忆重写整份报告；从磁盘上已写的 '
                    'per-campaign TSV 重建该 combo 段（已 drill 的 campaign 都在 '
                    f'stores/<slug>/ads/ 里），把 {head} 恢复到至少 {prev} 个，再'
                    '继续往上补。'
                )
            _max_drilled[key] = max(prev, drilled)
        # Anti-gaming: a section can CLAIM "drilled 46/46" while being a
        # page manifest with no per-campaign tables. Count real drill
        # tables (those with a 建议 column); flag if far fewer than the
        # active count it claims.
        n_drill_tables = len(_DRILL_TABLE_RE.findall(part))
        if active > 0 and n_drill_tables * 2 < active:
            gaps.append(
                f'[内容] 「{head}」声称 drill {drilled}/{active}，但本节只有 '
                f'{n_drill_tables} 个含「建议」列的逐-campaign 表——这是页面'
                '清单(manifest)不是逐活动 drill。必须像 Amazon 那样：每个 '
                'active campaign 给出 产品/广告组、逐关键词或逐 target 的表格'
                '(含 出价/eCPC/ROAS/建议)，而不是只列 活动ID|花费|ROAS。'
            )
        # Per-campaign search-term layer: each drilled campaign block
        # must carry the 搜索词对账 reconciliation line (same-window
        # proof) or an explicit no-search-terms token. Only meaningful
        # once the section has real drills.
        if drilled > 0:
            eff = rules or DEFAULT_RULES
            tol = eff['reconcile_tolerance']
            # noon attributes only part of campaign spend to Customer
            # Queries, so its floor is platform-specific (see
            # ``noon_reconcile_floor`` in ad_rules).
            floor = (
                eff.get('noon_reconcile_floor')
                if 'noon' in head.lower()
                else None
            )
            _check_campaign_blocks(part, head, tol, gaps, floor=floor)

    # 1a') Ground-truth coverage (#1 + #2) — only when an AUDIT_SCOPE.json
    #      baseline exists. The authoritative combo list closes the
    #      "new platform silently passes" hole (a combo the store audits
    #      but the report never opened), and the authoritative active-id
    #      set closes the "agent shrinks its own denominator" hole (a real
    #      active campaign with no ``### <id>`` block). Absent scope →
    #      skip entirely (escape hatch: first-time / narrow single-ad
    #      tasks fall back to the self-reported checks above).
    combos = ad_scope.scope_combos(scope)
    if combos:
        sections = {
            p.splitlines()[0].strip(): p for p in parts[1:] if p.strip()
        }
        for combo in combos:
            label = f'{combo["platform"]} {combo["country"]}'
            sec = ad_scope.find_combo_section(sections, combo)
            if sec is None:
                gaps.append(
                    f'[完整性] combo 「{label}」尚未开始——报告里没有对应的 '
                    f'`## {label}` 小节（本店按 AUDIT_SCOPE 需要审计该 combo）。'
                    '补上该小节并逐个 drill 其 active campaign。'
                )
                continue
            missing = ad_scope.missing_active_ids(sec, combo['active_ids'])
            if missing:
                n_active = len(combo['active_ids'])
                sample = '、'.join(missing[:8])
                more = '' if len(missing) <= 8 else f' 等共 {len(missing)} 个'
                gaps.append(
                    f'[完整性] 「{label}」按权威 active 名单（共 {n_active} 个）'
                    f'还缺 {len(missing)} 个未 drill 的 campaign：{sample}{more}。'
                    '这些 id 来自枚举时落盘的 AUDIT_SCOPE，不能靠改小 进度 分母'
                    '规避——为每个缺失 id 补出逐-campaign drill 块。'
                )

    # 1b') Duplicate drill blocks — the same campaign id heading twice
    #     means a block was appended instead of edited in place. Review
    #     rounds keep finding these by hand (C_FAKE0001, C_FAKE0002,
    #     then four more); a fix applied to one copy silently leaves the
    #     stale twin, so the class is pinned here.
    seen_ids: dict[str, int] = {}
    for head_line in re.findall(r'(?m)^###\s+(.+)$', result_text):
        m_id = _CAMPAIGN_HEAD_RE.search(head_line)
        if m_id:
            seen_ids[m_id.group()] = seen_ids.get(m_id.group(), 0) + 1
    dup_ids = [cid for cid, n in seen_ids.items() if n > 1]
    if dup_ids:
        sample = '、'.join(f'「{c}」' for c in dup_ids[:5])
        gaps.append(
            f'[重复] {len(dup_ids)} 个 campaign 在报告里有重复的 drill '
            f'块（同一 id 出现多个 ### 标题）：{sample}。同一活动只能有'
            '一个 drill 块——找出每对中数据正确的那份（建议列引用与该行'
            '列值一致的），删除陈旧副本，不要重写保留份。'
        )

    # 1b) Collapse rows — "其余 N 个…" hides per-row data. Tolerated only
    #     for explicitly zero-impression filler; any other collapse makes
    #     the report unauditable (the bug that hid 46 noon keywords).
    bad_collapse = [
        row.strip()[:60]
        for row in _COLLAPSE_ROW_RE.findall(result_text)
        if not _ZERO_JUSTIFIED_RE.search(row)
    ]
    if bad_collapse:
        sample = '；'.join(f'「{r}」' for r in bad_collapse[:3])
        gaps.append(
            f'[折叠] 报告把多行数据折叠成「其余 N 个」({len(bad_collapse)} '
            f'处)：{sample}。有展示/点击的词必须逐行列出（含各自指标与建议）；'
            '只有全部 0 展示的词才允许合并为一行，且该行必须注明「0 展示」。'
        )

    # 1c) Unconsumed scaffold markers — an ``<!-- INSERT: x -->`` left
    #     in the report means a scaffold slot was never filled (the bug
    #     that shipped an empty 汇总建议 section).
    leftover = re.findall(r'<!--\s*INSERT:\s*([^>]+?)\s*-->', result_text)
    if leftover:
        slots = '、'.join(f'「{s}」' for s in leftover[:5])
        gaps.append(
            f'[未完成] 报告还有 {len(leftover)} 个未消费的脚手架标记：'
            f'{slots}。每个 INSERT 标记都是一个还没写的段落——把对应内容'
            '写上并删掉标记。'
        )

    # 1d) Summary section must exist and carry real content. The combo
    #     loop above only validates ``## <platform> <country>`` sections,
    #     so a header-only 汇总建议 used to pass unnoticed.
    if round_total > 0:
        m_sum = re.search(
            r'(?m)^##\s*(汇总|总结)[^\n]*\n(.*?)(?=^##\s|\Z)',
            result_text,
            re.DOTALL,
        )
        body = ''
        if m_sum:
            body = re.sub(r'<!--.*?-->', '', m_sum.group(2), flags=re.DOTALL)
        if len(re.findall(r'[一-鿿]', body)) < 50:
            gaps.append(
                '[汇总] 「汇总建议」缺失或为空。审计报告必须以跨平台汇总收尾：'
                '各 combo 花费/销售/ROAS 总览、本次最重要的 5-10 条行动'
                '（含幅度与依据）、按影响排序的优先级。'
            )

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
    zi = ad_zero_impression.check(result_text, rules)
    if zi:
        gaps.append('[规则·零曝光] ' + zi.reason[:300])

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

    # 3a) Unproven empty-marketplace claim (see _EMPTY_MARKET_CLAIM_RE).
    #     An audit that declares a market empty without AUDIT_SCOPE.json
    #     never enumerated it — that is a guess, not a finding. Force the
    #     agent to switch to that marketplace, export it, and persist
    #     AUDIT_SCOPE (which then drives the coverage loop above).
    is_audit = bool(_IS_AUDIT_RE.search(result_text)) or (
        len(re.findall(r'(?m)^###\s', result_text)) >= 2
    )
    if (
        scope is None
        and is_audit
        and _EMPTY_MARKET_CLAIM_RE.search(result_text)
    ):
        gaps.append(
            '[空市场未证实] 报告断言某市场「无广告 / 无活动 / 零投放」，但工作区'
            '没有 AUDIT_SCOPE.json——你从未枚举该市场，这是猜测不是结论。断言一个'
            '市场为空属于“证明否定”，必须：切到该 marketplace（广告控制台左上角 '
            '`Sponsored ads, <国家>` 切换器）→ 导出该市场 bulk → 运行 '
            '`python scripts/ads_bulk.py scope <该市场导出>` 把每个市场的 active '
            'campaign id 落盘到 `./AUDIT_SCOPE.json`。只有导出确实为空，才能写'
            '「该市场无活动」；否则把导出里的活动逐个 drill（该市场很可能其实有'
            '活动——这正是之前误报“AE 无广告”的原因）。'
        )

    # 4) Garbled extraction — raw DOM attributes / lowercased ASINs.
    if _GARBLED_RE.search(result_text):
        gaps.append(
            '[数据] 报告含未清洗的原始 DOM 值（如 asin-expanded="…"、小写 '
            'b0xxxxxxxx）。搜索词必须干净：要么是可读关键词，要么是大写 ASIN '
            '(商品页投放)，并带 匹配来源/点击/花费/订单/ROAS 列——不要把 DOM '
            '属性或小写串直接塞进表格。'
        )

    if not gaps:
        return None

    # Stall tracking for the fail-open decision (read via ``is_stalled``).
    # Progress = the drilled total climbed OR the report text moved by
    # more than a cosmetic delta — either resets the counter. Only a
    # round that re-submits an essentially unchanged report counts
    # toward the cap. Only meaningful when there are gaps (a complete
    # report returned above and never reaches here).
    if task_id is not None and track:
        best = _total_high.get(task_id, 0)
        prev_len = _last_len.get(task_id)
        moved = prev_len is None or (
            abs(len(result_text) - prev_len) >= _STALL_MIN_DELTA
        )
        if round_total > best or moved:
            _total_high[task_id] = max(best, round_total)
            _stall_rounds[task_id] = 0
        else:
            _stall_rounds[task_id] = _stall_rounds.get(task_id, 0) + 1
        _last_len[task_id] = len(result_text)

    body = '\n'.join('- ' + g for g in gaps[:12])
    extra = '' if len(gaps) <= 12 else f'\n…还有 {len(gaps) - 12} 项'
    reason = (
        '本轮审计报告仍有缺口——逐轮补全，直到每个 combo 的 进度 都达到 '
        'D==A（所有 active campaign 全部 drill 完）才算完成；未 drill 完不可'
        '结束任务（server 会拦截提交与结束对话）。\n'
        '**这是续作（RESUME，不是重做）**：你已经 drill 的数据、已写的 '
        'AD_AUDIT 报告和 per-campaign TSV 都还在——保留它们，只去补下面列出的'
        '缺失部分（打开尚未 drill 的 campaign 详情页、把它们的表格 APPEND 进报告）。'
        '**不要从头重写整份报告，不要重新 drill 已完成的 campaign** —— 那正是导致'
        '卡住、D 不增长的原因。每一轮只需让 D 朝 A 多走几个。补完后重新 '
        'set_task_result；评审会再列出剩余缺口，直到补齐：\n' + body + extra
    )
    return GateDeny(gate=GATE_NAME, reason=reason)
