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
    ad_rollup,
    ad_scale_winners,
    ad_scope,
)
from app.ai.stop_gates.ad_completeness_campaign_blocks import (
    _CAMPAIGN_HEAD_RE,
    _check_campaign_blocks,
)
from app.ai.stop_gates.ad_rules import DEFAULT_RULES

GATE_NAME = 'ad_completeness_review'

# Fail-open is keyed on STALL, not a round count. A weak model that
# drills slowly (a few campaigns per round) still makes real progress
# every round — cutting it off after a fixed number of rounds would
# accept a half-finished report (e.g. noon 3/48) while D was still
# climbing.
#
# PROGRESS MEANS THE DISTANCE TO DONE SHRANK — where distance is
# ``unmet gaps + campaigns still owed``. Nothing else counts.
#
# This used to reset the counter whenever the drilled total climbed OR
# the report text moved by >=400 chars. The text-delta clause made the
# fail-open UNREACHABLE for any gap fixed by editing prose rather than
# by drilling — rule violations, format/parse gaps. Those never move
# the drilled total, but rewriting a suggestion column trivially moves
# far more than 400 characters, so every round reset the counter to
# zero. Observed: one run took 41 submissions and another 14, neither
# ever reaching STALL_CAP, both ending with no stored deliverable.
#
# Distance covers both kinds of work in one number: closing a rule gap
# drops the gap term, drilling a campaign drops the deficit term. A
# plain gap COUNT would not — an under-drilled combo emits exactly one
# gap entry at 3/48 and at 47/48 alike, so a slow driller would look
# stalled. An agent making either kind of progress keeps its budget; an
# agent rewriting the same report against a bar it cannot clear runs out.
STALL_CAP = 5

# Anti-regression: the highest drilled-count seen per (task_id, combo)
# across this task's rounds. The convergence loop must be MONOTONIC —
# each round adds, never loses prior drills. If a submission reports
# fewer drilled than a previous round (the model rewrote the whole
# report from compacted memory and clobbered earlier work — e.g. Amazon
# US 31/31 → 2/31), the reviewer rejects and tells it to restore +
# append. Cleared per task by ``reset_progress`` on terminal success.
_max_drilled: dict[tuple[str, str], int] = {}

# Stall tracking for the fail-open decision: the SMALLEST distance to
# done (unmet gaps + campaigns still owed) seen for a task so far, and
# how many consecutive rounds have failed to beat it. Updated by
# ``check`` each round; read by ``is_stalled``. Cleared by
# ``reset_progress``.
_min_distance: dict[str, int] = {}
_stall_rounds: dict[str, int] = {}

# Per-combo stall (D6): one frozen combo on a multi-combo task used to
# mask ongoing progress on the others — the global counter only fell
# when every combo moved at once. Track distance per (task_id, combo)
# so each combo's convergence is judged on its own. Global
# ``is_stalled`` only fires when every seen combo has hit STALL_CAP,
# which is what bounds a wholly-stuck agent; a single frozen combo
# fails open locally while the others keep their budget.
_combo_min_distance: dict[tuple[str, str], int] = {}
_combo_stall_rounds: dict[tuple[str, str], int] = {}
_seen_combos: dict[str, set[str]] = {}

# Stop-path backstop: how many times we've blocked an end-of-turn while
# the audit was still under-drilled, per task. Bounds the stop-path deny
# so a genuinely-stuck agent (can't drill more) isn't trapped forever —
# fails open after STALL_CAP blocks, mirroring the set_task_result stall.
_stop_blocks: dict[str, int] = {}


def reset_progress(task_id: str) -> None:
    """Drop the per-task progress/stall state (call on terminal success)."""
    for key in [k for k in _max_drilled if k[0] == task_id]:
        _max_drilled.pop(key, None)
    for key in [k for k in _combo_min_distance if k[0] == task_id]:
        _combo_min_distance.pop(key, None)
    for key in [k for k in _combo_stall_rounds if k[0] == task_id]:
        _combo_stall_rounds.pop(key, None)
    _seen_combos.pop(task_id, None)
    _min_distance.pop(task_id, None)
    _stall_rounds.pop(task_id, None)
    _stop_blocks.pop(task_id, None)


def is_stalled(task_id: str) -> bool:
    """True after ``STALL_CAP`` rounds that got no closer to done.

    The fail-open signal: the report still has gaps and the agent has
    not reduced the distance to done — unmet gaps plus campaigns still
    owed — for ``STALL_CAP`` consecutive rounds, so further "what's
    missing" replies won't help. Callers use this to accept the best
    report instead of denying forever.

    With per-combo tracking (D6) the global signal fires only when
    EVERY seen combo has stalled — a single frozen combo on a multi-
    combo task fails open locally while the rest keep their budget.
    A task with no combo sections seen yet falls back to the legacy
    global counter so narrow single-combo audits still fail open.
    """
    seen = _seen_combos.get(task_id)
    if not seen:
        return _stall_rounds.get(task_id, 0) >= STALL_CAP
    return all(
        _combo_stall_rounds.get((task_id, c), 0) >= STALL_CAP for c in seen
    )


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
        # ``>=``, matching ``is_stalled``. These two paths bound the
        # same contract and must agree: while the stop path fell open
        # one round later than the submit path, an agent could end its
        # turn but never get a submission accepted — which is how a run
        # reached a terminal state holding no stored deliverable.
        if n >= STALL_CAP:
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
    ``task_id`` when not passed). It grounds completeness in the
    authoritative combo + active-id list instead of the agent's
    self-reported ``进度`` line, and it is a PRECONDITION: a section that
    claims a ``drilled D/A`` denominator with no scope entry to back it is
    a gap, not a free pass. The old fall-back-to-self-report silently
    turned every per-campaign check into whatever the agent's own markdown
    happened to assert. ``track`` gates the per-task mutation of the
    anti-regression / stall state, so the stop-path can run this as a
    pure check (``track=False``) without perturbing set_task_result's
    convergence accounting.
    """
    if not result_text or not isinstance(result_text, str):
        return None

    if scope is None:
        scope = ad_scope.load_audit_scope(task_id)
    all_combos = ad_scope.scope_combos(scope)
    # What the SERVER says this store owes, independent of what the agent
    # enumerated. Empty for tasks predating the contract / stores with no
    # recorded platforms — then nothing below changes.
    declared = ad_scope.load_declared_targets(task_id)
    # Bulk export files cited by MORE THAN ONE combo. An export is
    # per-marketplace, so a shared reference cannot verify any of them —
    # see the check below.
    # Keep the CITING COMBOS per ref, not just a count: the conflict is
    # one fact about a set of combos, so it is reported once naming all
    # of them (below, after the per-combo loop) rather than as an
    # identical gap on each. Reported per combo it was unsatisfiable —
    # the file IS the right evidence for exactly one of them, so telling
    # all three "give this market its own evidence" tells the rightful
    # owner its correct citation is wrong. Live, three Amazon combos
    # citing one export produced three gaps that survived ~10
    # submissions untouched, and inflated the stall metric's
    # distance-to-done threefold while nothing was actually wrong with
    # SA's reference.
    _bulk_by_ref: dict[str, list[str]] = {}
    for _c in ad_scope.scope_combos(scope):
        _k, _r = ad_scope.classify_total_source(
            _c.get('total_active_source', '')
        )
        if _k == 'bulk' and _r:
            _bulk_by_ref.setdefault(_r, []).append(
                f'{_c.get("platform")} {_c.get("country")}'
            )
    _shared_bulk = {r for r, labels in _bulk_by_ref.items() if len(labels) > 1}

    gaps: list[str] = []
    # Gaps that are not "unfinished" but "cannot be true" (see
    # ``GateDeny.contradictions``). Kept separate so the stall fail-open,
    # which is right for incompleteness, cannot silently accept an
    # impossible figure.
    contradictions: list[str] = []
    round_total = 0  # sum of drilled across all combos this round
    # Undrilled campaigns still owed, summed across combos. Half of the
    # stall metric — see the ``_stall_rounds`` block at the tail.
    round_deficit = 0

    # Per-combo attribution of gaps + deficit for D6's per-combo stall.
    # A gap attributed to a combo moves that combo's distance only;
    # gaps from the cross-cutting checks (no-defer, summary, garbled,
    # duplicate-id, collapse, scaffold, bid-rule) sit in ``gaps`` with
    # no combo label and stay global — they apply to the run as a whole,
    # not to any one country.
    _combo_gaps: dict[str, list[str]] = {}
    _combo_deficit: dict[str, int] = {}

    def _attr(combo_label: str | None, gap: str) -> None:
        """Append ``gap`` to the global list AND the combo bucket.

        Combos without a scope match (agent invented a combo, or no
        scope file at all) bucket under ``combo_label=None`` and stay
        global — they affect the run as a whole, not one country.
        """
        gaps.append(gap)
        if combo_label is not None:
            _combo_gaps.setdefault(combo_label, []).append(gap)

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
        # Resolve the combo up-front so every gap in this section can be
        # attributed to its combo label for D6 per-combo stall tracking.
        # Sections with no matching combo (agent invented a country, or
        # no scope at all) keep their gaps unattributed — they fall back
        # to the global stall counter.
        combo = next(
            (c for c in all_combos if ad_scope.section_matches_combo(head, c)),
            None,
        )
        combo_label = (
            f'{combo["platform"]} {combo["country"]}' if combo else None
        )
        if combo_label is not None and task_id is not None:
            _seen_combos.setdefault(task_id, set()).add(combo_label)
        m = _PROGRESS_RE.search(part)
        if not m:
            _attr(
                combo_label,
                f'[完整性] 「{head}」缺少进度行 '
                '`**进度**: drilled <D>/<A> active (<T> total, <P> pages)` '
                '——请按 output-spec 记录该国真实 active 数与已 drill 数',
            )
            continue
        drilled, active = int(m.group(1)), int(m.group(2))
        round_total += drilled
        round_deficit += max(0, active - drilled)
        if combo_label is not None:
            _combo_deficit[combo_label] = _combo_deficit.get(
                combo_label, 0
            ) + max(0, active - drilled)
        if drilled < active:
            _attr(
                combo_label,
                f'[完整性] 「{head}」仅 drill {drilled}/{active} 个 active，'
                f'还差 {active - drilled} 个未逐 campaign drill——必须把这 '
                f'{active - drilled} 个全部逐一 drill 完（进度达到 '
                f'{active}/{active}）才能结束本任务；本轮尽量多补，未 drill 完'
                '不可提交完成，也不可结束本轮对话（server 会拦截）。不要留待'
                '“下一轮/下次审计”——没有下一轮。',
            )
        elif drilled > active:
            # Over-report: more drills than the active set. The model
            # swept in non-active (paused/archived) campaigns — typically
            # by batch-generating the report from EVERY on-disk TSV
            # instead of only the active set it enumerated (the dump that
            # produced "drilled 105/56"). Reject: D must equal A.
            _attr(
                combo_label,
                f'[越界] 「{head}」报告了 {drilled} 个 campaign，但本国只有 '
                f'{active} 个 active——你把非 active（暂停/归档）的 campaign 也'
                '塞进来了（通常是用脚本把磁盘上所有 TSV 一次性灌进报告所致）。'
                '只能逐个 Read+Edit **当前 active 集合内** 的 campaign，'
                f'其余磁盘 TSV 忽略；让 drilled 等于 {active}，不要超过。',
            )
        # Anti-regression: this combo must never go BACKWARDS across
        # rounds. If a prior round already had more drilled, the model
        # rewrote the report from (compacted) memory and clobbered done
        # work — reject and tell it to restore from the on-disk TSVs.
        if task_id is not None and track:
            key = (task_id, head)
            prev = _max_drilled.get(key, 0)
            if drilled < prev:
                _attr(
                    combo_label,
                    f'[回退] 「{head}」上一轮已经 drill {prev} 个，这一轮却'
                    f'只有 {drilled} 个——你重写整份报告时把已完成的 drill 弄丢了。'
                    '**绝不能倒退**：不要从记忆重写整份报告；从磁盘上已写的 '
                    'per-campaign TSV 重建该 combo 段（已 drill 的 campaign 都在 '
                    f'stores/<slug>/ads/ 里），把 {head} 恢复到至少 {prev} 个，再'
                    '继续往上补。',
                )
            _max_drilled[key] = max(prev, drilled)
        # Anti-gaming: a section can CLAIM "drilled 46/46" while being a
        # page manifest with no per-campaign tables. Count real drill
        # tables (those with a 建议 column) and compare against the
        # AUTHORITATIVE active set when one is known — the agent's own
        # ``active`` denominator is what D4 closed (the same shrink-the-
        # denominator trick one level down). With a scope, the threshold
        # is strict (``>=``): one drill table per active campaign. Without
        # one we keep the old 2× slack so a narrow single-ad audit isn't
        # tripped by a search-term table doubling the count.
        n_drill_tables = len(_DRILL_TABLE_RE.findall(part))
        if combo is not None:
            n_active_authoritative = len(combo['active_ids'])
            if (
                n_active_authoritative > 0
                and n_drill_tables < n_active_authoritative
            ):
                _attr(
                    combo_label,
                    f'[内容] 「{head}」声称 drill {drilled}/{active}（权威 active '
                    f'集 {n_active_authoritative} 个），但本节只有 '
                    f'{n_drill_tables} 个含「建议」列的逐-campaign 表——这是页面'
                    '清单(manifest)不是逐活动 drill。必须像 Amazon 那样：每个 '
                    'active campaign 给出 产品/广告组、逐关键词或逐 target 的表格'
                    '(含 出价/eCPC/ROAS/建议)，而不是只列 活动ID|花费|ROAS。',
                )
        elif active > 0 and n_drill_tables * 2 < active:
            # Ungrounded fallback (narrow single-ad audit with no scope):
            # keep the old 2× slack. Without a scope the ``active`` is the
            # agent's self-reported total, so we can't tighten without
            # admitting the same shrink-the-denominator trick one level
            # down. The gap text names the ungrounded case so the agent
            # knows the bar would rise if it ships a scope.
            _attr(
                combo_label,
                f'[内容] 「{head}」声称 drill {drilled}/{active}（无 AUDIT_SCOPE，'
                '按报告自报的 active 数），但本节只有 '
                f'{n_drill_tables} 个含「建议」列的逐-campaign 表。提供 '
                'AUDIT_SCOPE 后这条会按权威 active 集合收紧到一活动一表。',
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
            # Snapshot the gap list length so the per-campaign search-
            # term gaps appended by ``_check_campaign_blocks`` can be
            # mirrored into the combo bucket for D6. The function emits
            # O(constant) entries per combo (missing / mismatched /
            # no_target_table), so the diff is small.
            _gaps_before_blocks = len(gaps)
            _check_campaign_blocks(
                part,
                head,
                tol,
                gaps,
                floor=floor,
                active_ids=combo['active_ids'] if combo else None,
                contradictions=contradictions,
            )
            if combo_label is not None:
                for new_gap in gaps[_gaps_before_blocks:]:
                    _combo_gaps.setdefault(combo_label, []).append(new_gap)
            if combo is None:
                # No authoritative set for a section that just claimed a
                # denominator: the per-campaign checks above ran on
                # shape-matched headings, i.e. on whatever the agent chose
                # to write. Demand the baseline instead of trusting it.
                _attr(
                    combo_label,
                    f'[基线] 「{head}」声称 drill {drilled}/{active}，但没有'
                    'AUDIT_SCOPE.json 里对应的权威 active 名单——分母和'
                    '逐活动清单都只是报告自己的说法，服务器无法校验。'
                    '先把该 combo 的权威 active 集合落盘到任务根目录的 '
                    'AUDIT_SCOPE.json（Amazon: `ads_bulk.py scope '
                    '<export>.xlsx --platform <p> --country <c>`，取 '
                    'state=enabled 的 Campaign id；noon: 活动列表把内层列表'
                    '滚到底后取全部 `/campaign/details/<id>` 链接，并把状态'
                    'chip 上的 `Live N` 数字写进 total_active），再重新提交。',
                )

    # 1a') Ground-truth coverage (#1 + #2) — only when an AUDIT_SCOPE.json
    #      baseline exists. The authoritative combo list closes the
    #      "new platform silently passes" hole (a combo the store audits
    #      but the report never opened), and the authoritative active-id
    #      set closes the "agent shrinks its own denominator" hole (a real
    #      active campaign with no ``### <id>`` block). Absent scope →
    #      skip entirely (escape hatch: first-time / narrow single-ad
    #      tasks fall back to the self-reported checks above).
    combos = all_combos
    # Combo self-check: ``total_active`` stops the agent shrinking the
    # campaign count WITHIN a combo, but the combo LIST was still entirely
    # its own. A store configured for five marketplaces produced a
    # one-combo scope, and every check below then agreed the report was
    # complete — four marketplaces never audited, no gap raised. The
    # server writes what the store is configured for (AUDIT_TARGETS.json,
    # see ``ad_scope.write_declared_targets``); a declared combo with no
    # scope entry is a gap. Declaring it EMPTY is still allowed — that is
    # the "no live campaigns here" finding, and it lands on the
    # empty-``active_ids`` branch below with its own message.
    #
    # Only an AUDIT owes marketplace coverage. An ads EXECUTION summary
    # binds the same skill (so the same gates apply) but has no combo
    # section and no scope — demanding five marketplaces of it would deny
    # a task that never claimed to audit anything. An audit announces
    # itself either way: it has a scope, or it has combo sections.
    is_audit = bool(all_combos) or any(
        _COMBO_HEADER_RE.search(p.splitlines()[0])
        for p in parts[1:]
        if p.strip()
    )
    for missing_combo in (
        ad_scope.missing_declared_combos(combos, declared) if is_audit else []
    ):
        label = f'{missing_combo["platform"]} {missing_combo["country"]}'
        if task_id is not None:
            _seen_combos.setdefault(task_id, set()).add(label)
        _attr(
            label,
            f'[基线] combo 「{label}」根本没进 AUDIT_SCOPE.json——本店在设置里'
            f'配置了这个市场（见任务目录的 {ad_scope.TARGETS_FILENAME}），'
            '审计必须覆盖它。去把该 combo 的 active campaign 枚举出来追加成'
            '一条 combo 记录（Amazon: bulk 导出 state=enabled 的 Campaign id；'
            'noon: 活动列表滚到底取全部 id，并把 chip 上的 `Live N` 写进 '
            'total_active），再逐个 drill。该市场确实一个在投活动都没有，'
            '就写一条 "active_ids": [], "total_active": 0 的记录说明情况——'
            '可以为空，但不能不写。',
        )
    # Scope self-check: ``active_ids`` is agent-written, so a truncated
    # enumeration would just move the old "shrink the denominator" trick
    # from the prose into the JSON. ``total_active`` is observed
    # independently of the id list (bulk-export enabled-row count / noon's
    # server-rendered ``Live N`` chip), so a disagreement means the
    # enumeration is stale — reject the scope rather than grade against it.
    for combo in combos:
        label = f'{combo["platform"]} {combo["country"]}'
        n = len(combo['active_ids'])
        if task_id is not None:
            _seen_combos.setdefault(task_id, set()).add(label)
        if n == 0:
            # "No live campaigns in this marketplace" is a legitimate
            # finding — but only when the INDEPENDENT count agrees. An
            # explicit ``total_active: 0`` is the noon ``Live 0`` chip /
            # a bulk export with no enabled rows, i.e. observed emptiness;
            # an absent or non-zero total with no ids is a truncated
            # enumeration wearing the same clothes.
            #
            # This branch must not tell a DECLARED combo to delete itself:
            # the delete would re-raise the missing-combo gap above, and
            # the two remedies together were a deadlock with no legal move
            # (caught in review before it reached a live run).
            declared_here = any(ad_scope.same_combo(combo, d) for d in declared)
            if combo['total_active'] == 0:
                # An empty market is the STRONGEST claim in the file: it
                # discharges every per-campaign obligation at once. So it
                # cannot be self-certifying, and two things must hold.
                #
                # (a) It must not hedge. ``exhaustive: false`` means "I
                # audited a subset on purpose"; with an empty id list that
                # reads "my subset was nothing" — zero obligations wearing
                # the narrow-task escape hatch. An empty market IS a
                # completeness claim; you cannot opt out of completeness
                # and assert it in the same entry.
                #
                # (b) It must not contradict recorded history. Observed
                # live, and why this exists: a run that never opened
                # Amazon AE wrote total_active 0 for it while the PREVIOUS
                # audit of the same store had drilled 10 active AE
                # campaigns with real spend. Accepting that ships an audit
                # missing a whole marketplace — the very hole AUDIT_SCOPE
                # closes, moved one level down from "omit the combo" to
                # "declare the combo empty".
                if not combo['exhaustive']:
                    _attr(
                        label,
                        f'[基线] AUDIT_SCOPE 的 combo 「{label}」同时写了 '
                        '"active_ids": [] 和 "exhaustive": false——这两个放'
                        '一起等于「我只审了一部分，而那部分是空的」，是零'
                        '义务的免检牌。「没有在投活动」本身就是一个完整性'
                        '断言：要么去掉 exhaustive（或设 true）并用 '
                        'total_active 0 的独立观测背书，要么列出你实际'
                        '审计的那部分 active id。',
                    )
                    continue
                prior = ad_scope.prior_campaign_tsvs(
                    ad_scope.declared_slug(task_id),
                    combo['platform'],
                    combo['country'],
                )
                if prior and declared_here:
                    _attr(
                        label,
                        f'[基线] combo 「{label}」被判为「无在投活动」'
                        f'（total_active 0），但本店此前的审计在 '
                        f'stores/<slug>/ads/{combo["platform"]}/'
                        f'{combo["country"].lower()}/ 留下了 {prior} 个逐'
                        '活动 TSV——这个市场以前是有活动的，0 是一次回退，'
                        '不能只凭断言。请真的去该市场的广告后台确认：'
                        'Amazon 要单独为这个 marketplace 导出一份 bulk'
                        '（每个 marketplace 是独立广告账户，SA 的导出不能'
                        '代表 AE），确认没有 state=enabled 的行；noon 看'
                        '活动列表状态 chip 是否为 `Live 0`。确认真的清零，'
                        '就在该小节写明依据（导出文件名 / chip 读数 + 这些'
                        '活动大约何时停投）；若其实仍有在投活动，把它们'
                        '枚举进 active_ids 并逐个 drill。',
                    )
                continue
            keep = (
                '该 combo 确实没有 active 活动，就把 total_active 也写成 0'
                '（noon: 状态 chip 显示 `Live 0`；Amazon: bulk 导出没有 '
                'state=enabled 的行），并在报告里保留该小节说明无在投活动。'
                if declared_here
                else '该 combo 确实没有 active 活动就整条删掉，别留空壳。'
            )
            _attr(
                label,
                f'[基线] AUDIT_SCOPE 的 combo 「{label}」的 active_ids 是空的'
                '——空名单等于没有基线，逐活动检查会退回只看报告自己写了'
                '几个块。把该 combo 枚举到的 active campaign id 全部列进去；'
                + keep,
            )
            continue
        if not combo['exhaustive']:
            continue
        total = combo['total_active']
        # Provenance. total_active must say WHERE it came from, and when
        # it names a bulk export the server checks the file itself — the
        # only part of this contract that is verified rather than trusted.
        kind, ref = ad_scope.classify_total_source(
            combo.get('total_active_source', '')
        )
        if total is not None and kind is None:
            _attr(
                label,
                f'[基线] AUDIT_SCOPE 的 combo 「{label}」的 total_active='
                f'{total} 没写出处（total_active_source）。这个数必须是'
                '独立观测来的，不能跟 active_ids 出自同一次解析——两个数'
                '来自同一个地方时，它们相等什么也证明不了（实测：某轮把'
                '12 个活动的市场声明成 4/4 并通过了全部检查）。请补上：'
                'Amazon 写 `"total_active_source": "bulk:<导出文件名>.xlsx"`'
                '（服务端会打开该文件自己数 state=enabled 的活动行核对）；'
                'noon 写 `"total_active_source": "chip:Live N"`，N 为活动'
                '列表状态 chip 上的读数。',
            )
        elif kind == 'bulk' and total is not None and ref in _shared_bulk:
            # The SAME export cited by several combos. A bulk export is
            # PER-MARKETPLACE — one advertiser account, one market — so it
            # can be authoritative for at most one of them, and counting
            # its rows against another market's total is meaningless.
            # Observed live: Amazon AE (8) and AU (4) both cited the SA
            # export because neither has a bulk-operations page of its own,
            # and the naive check told each of them it had "under-declared"
            # against SA's 17 enabled rows. The agent had even written the
            # caveat itself — "服务端如打开该文件预计 0 AE 行".
            #
            # Skip the row-count comparison (that is the invented
            # under-count); the conflict itself is reported ONCE after
            # this loop, naming every combo involved.
            pass
        elif kind == 'bulk' and total is not None:
            observed = ad_scope.count_enabled_in_export(ref)
            # observed is None = could not check (file gone / unreadable).
            # Unverifiable is not a failure; a missing download must never
            # block an otherwise sound report.
            if observed is not None and total < observed:
                _attr(
                    label,
                    f'[基线] combo 「{label}」声明 total_active={total}，但'
                    f'服务端打开 `{ref}` 数到 {observed} 个 state=enabled '
                    '的活动——你少算了。（多算是允许的：窗口内有花费、'
                    '之后被暂停的活动仍该审。少算说明枚举没取全。）请把'
                    f'漏掉的活动补进 active_ids 并逐个 drill，或说明为什么'
                    f'{observed} 里有些不该算在内。',
                )
        if total is None:
            _attr(
                label,
                f'[基线] AUDIT_SCOPE 的 combo 「{label}」缺少 total_active——'
                '这是枚举时独立观测到的 active 总数（Amazon: bulk 导出里 '
                'state=enabled 的行数；noon: 活动列表状态 chip 上的 `Live N` '
                '数字），用来证明 active_ids 没有被截断。补上该字段；'
                '确实只审计部分活动时写 "exhaustive": false。',
            )
        elif total != n:
            _attr(
                label,
                f'[基线] AUDIT_SCOPE 的 combo 「{label}」自相矛盾：'
                f'active_ids 只有 {n} 个，但 total_active={total}——枚举没取全'
                f'（noon 的活动列表是懒加载内层滚动，没滚到底就只有前 ~20 行；'
                'Amazon 要用 bulk 导出的全量 enabled 行）。把内层列表滚到底/'
                f'重新导出，补齐到 {total} 个 id 再提交；确认 {total} 这个数字'
                '本身过时的话，重新读一次 chip / 重新导出并同时更新两处。',
            )
        else:
            # ``total_active == len(active_ids)`` proves internal
            # consistency, NOT observation — and it is trivially true when
            # both numbers come from the same agent-side derivation. Live:
            # a run declared noon SA 4/4 where a verified audit the same
            # day found 8, because its parser silently dropped TSVs it
            # could not read; the gate then graded it against its own
            # shrunken denominator and raised nothing about the shortfall.
            #
            # So cross-check the one independent record the server holds:
            # what the store's OWN prior audits left on disk. Campaigns do
            # get paused, so prior >= current is normal and must not be
            # flagged — only a COLLAPSE (declared active below half of the
            # historical campaign count) is challenged. That threshold
            # leaves the plausible cases alone (21 vs 22, 8 vs 10, 8 vs 9
            # on the same store) and catches the 4-vs-12 shortfall.
            #
            # This is a backstop, not a proof. The real fix is for
            # ``total_active`` to carry its provenance (which export file /
            # which chip reading) so the server can verify it directly.
            prior = ad_scope.prior_campaign_tsvs(
                ad_scope.declared_slug(task_id),
                combo['platform'],
                combo['country'],
            )
            if prior and n * 2 < prior:
                _attr(
                    label,
                    f'[基线] combo 「{label}」只声明了 {n} 个 active，但本店'
                    f'以往审计在 stores/<slug>/ads/{combo["platform"]}/'
                    f'{combo["country"].lower()}/ 留下过 {prior} 个活动的 TSV'
                    f'——不到历史的一半。total_active={total} 只证明它跟 '
                    'active_ids 自己一致，不证明它被独立观测过：两个数来自'
                    '同一次解析时，这个自检是空的（实测：某轮把 12 个活动的'
                    '市场声明成 4 个，因为脚本静默跳过了读不懂的 TSV）。'
                    '请重新独立枚举一次：Amazon 用 bulk 导出 state=enabled '
                    '的行数，noon 把活动列表滚到底并读状态 chip 的 `Live N`，'
                    '然后把 active_ids 和 total_active 一起改成观测值。'
                    f'确实有大量活动已停投（{prior} → {n} 是真的），就在该'
                    '小节写明依据（导出文件名 / chip 读数 + 大致停投时间），'
                    '别让缩水悄悄通过。',
                )
    # One shared bulk export = ONE gap, naming every combo that cites it
    # and the resolution. Global (not ``_attr``) because the conflict is a
    # fact about the SET of combos, matching how the other cross-cutting
    # checks are attributed — and because per-combo attribution let one
    # mis-citation triple the stall metric's distance-to-done.
    for ref in sorted(_shared_bulk):
        cited = _bulk_by_ref.get(ref, [])
        gaps.append(
            f'[基线] `{ref}` 同时被 {len(cited)} 个 combo 当作 '
            f'total_active 的依据：{"、".join(f"「{c}」" for c in cited)}。'
            'bulk 导出是**按市场**的（一个广告账户=一个市场），所以它只能'
            '作为其中**一个**市场的依据。请保留它给它真正导出自的那个市场，'
            '其余市场改成自己的依据：该市场自己的 bulk 导出'
            '（`bulk:<该市场的导出>.xlsx`），没有 bulk 页面时'
            '（如 AE 会 404）写活动列表总数 `list:N` 并说明读自哪个列表。'
            '不用改 active_ids——只改 total_active_source。'
        )

    if combos:
        sections = {
            p.splitlines()[0].strip(): p for p in parts[1:] if p.strip()
        }
        for combo in combos:
            label = f'{combo["platform"]} {combo["country"]}'
            sec = ad_scope.find_combo_section(sections, combo)
            if sec is None:
                _attr(
                    label,
                    f'[完整性] combo 「{label}」尚未开始——报告里没有对应的 '
                    f'`## {label}` 小节（本店按 AUDIT_SCOPE 需要审计该 combo）。'
                    '补上该小节并逐个 drill 其 active campaign。',
                )
                continue
            missing = ad_scope.missing_active_ids(sec, combo['active_ids'])
            if missing:
                n_active = len(combo['active_ids'])
                sample = '、'.join(missing[:8])
                more = '' if len(missing) <= 8 else f' 等共 {len(missing)} 个'
                _attr(
                    label,
                    f'[完整性] 「{label}」按权威 active 名单（共 {n_active} 个）'
                    f'还缺 {len(missing)} 个未 drill 的 campaign：{sample}{more}。'
                    '这些 id 来自枚举时落盘的 AUDIT_SCOPE，不能靠改小 进度 分母'
                    '规避——为每个缺失 id 补出逐-campaign drill 块。',
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
    # Progress = the DISTANCE TO DONE shrank. Distance is unmet gaps
    # plus campaigns still owed, so it falls for either kind of real
    # work: closing a rule/format gap drops the first term, drilling
    # another campaign drops the second. Text churn moves neither.
    #
    # A gap COUNT alone would be wrong — an under-drilled combo emits
    # ONE gap entry whether it is at 3/48 or 47/48, so a slow driller
    # would look stalled. The old metric had the opposite bug: it reset
    # on any >=400-char edit, so an agent rewriting prose against a bar
    # it could not clear was never stalled and looped forever.
    #
    # D6 makes this per-combo. The legacy global counter stays as a
    # fallback for tasks that never wrote a ``## <platform> <country>``
    # section (narrow single-ad audits) so they keep failing open.
    if task_id is not None and track:
        seen = _seen_combos.get(task_id)
        if seen:
            for label in seen:
                combo_distance = len(
                    _combo_gaps.get(label, [])
                ) + _combo_deficit.get(label, 0)
                key = (task_id, label)
                best = _combo_min_distance.get(key)
                if best is None or combo_distance < best:
                    _combo_min_distance[key] = combo_distance
                    _combo_stall_rounds[key] = 0
                else:
                    _combo_stall_rounds[key] = (
                        _combo_stall_rounds.get(key, 0) + 1
                    )
        else:
            distance = len(gaps) + round_deficit
            best = _min_distance.get(task_id)
            if best is None or distance < best:
                _min_distance[task_id] = distance
                _stall_rounds[task_id] = 0
            else:
                _stall_rounds[task_id] = _stall_rounds.get(task_id, 0) + 1

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
    return GateDeny(
        gate=GATE_NAME,
        reason=reason,
        gaps=tuple(gaps),
        contradictions=tuple(contradictions),
    )
