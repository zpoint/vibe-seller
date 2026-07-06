# Ad-audit OUTPUT SPEC — the report contract

This is the single definition of "done" for an ad-audit report. The
skill writes to it; the server's completeness reviewer
(`app/ai/stop_gates/ad_completeness_review.py`) checks against it at
`set_task_result` and returns the list of what's still missing each
round. **Partial is accepted** — you don't have to be perfect in one
pass; fix what the reviewer reports and re-submit. The report improves
each round until the gaps are gone.

## Per (platform, country) section — required shape

For every audited `(platform, country)`, the report MUST contain a
`## <Platform> <Country>` section with, in order:

1. **A discovery line stating the TRUE active count**, in the exact
   machine-checkable form:

   `**进度**: drilled <D>/<A> active (<T> total, <P> pages)`

   - `<A>` = active/Live campaigns you found AFTER full enumeration
     (Amazon: cleared search + bulk export; noon: paginated ALL pages).
   - `<D>` = how many of those you've drilled per-campaign so far.
   - `<T>`/`<P>` = total campaigns / pages seen (proves you enumerated).
   - The reviewer reads this line. If `<D> < <A>`, it reports the gap
     and names the missing campaign ids. Record `<A>` honestly from the
     enumeration step — under-reporting it is the failure we are
     closing.

2. **A header table** — one row per ACTIVE campaign:
   `| id | name | type | spend | sales | orders | ACOS | ROAS | status |`

3. **One per-campaign drill block per active campaign**, each with a
   keyword/target table whose recommendation column obeys the bid rules
   below. The block heading MUST contain the campaign id so the
   reviewer can match it to its TSV.

4. **Inactive campaigns**: one line each (id + state), not drilled.

## Per-campaign drill block — required shape

Each `### <campaign id> | <name> | …` block MUST contain, in order:

1. **Targeting table** — every keyword/target individually (bid /
   suggested range / clicks / spend / orders / ACOS-or-ROAS / CPC /
   建议), plus a **合计 row** whose totals match the campaign header.
2. **Search-terms table** — the ACTUAL customer queries
   (Amazon: Search Terms page → **Export CSV** (the ONLY full-coverage
   method — the on-screen grid is virtualized and shows ~13 rows);
   noon: Customer Queries tab). Report the **top ~20 by spend** with
   `搜索词 | 来源关键词 | 匹配 | 点击 | 花费 | 订单 | 销售额 | ROAS |
   建议`, state the total term count, and write the FULL set to the
   search-terms TSV (below). **Every term with impressions > 0 is its
   own row** — never fold live terms into a `其余 N 个` row (a collapse
   row is only allowed for all-zero-impression filler and must say
   `0 展示`). The reviewer rejects collapse rows with traffic.
3. **The reconciliation line** — machine-checkable, same 30-day window
   on BOTH pages:

   `搜索词对账: 定向花费 <币> X / 点击 A = 搜索词花费 <币> Y / 点击 B (✓/✗)`

   `X/A` = targeting-table totals; `Y/B` = search-term-report totals.
   The reviewer parses this line and rejects the campaign when spend or
   clicks differ by more than `reconcile_tolerance` (default 15%,
   `ad_rules.py`). A mismatch means the two pages were read on
   DIFFERENT date windows (the 30d-vs-7d bug) or the term capture is
   incomplete — re-pin both pages to the same window and recapture.
   Campaign types with no search-term report (e.g. Sponsored Display)
   write `无搜索词报告` instead; zero-click campaigns may write
   `无点击，无搜索词`.

## The optimizer's bar (read this first)

You are this store's ads optimizer. Every 建议 cell must be a call a
real human optimizer would sign — matched to THAT row's data, not a
template. The whole playbook is the ordinary one:

- 有花费/点击但零效果 → **否定/暂停**（auto 活动否定即移除该定向）
- 效果差（高 ACOS / ROAS 低于盈亏线，且数据足够）→ **降价或禁用**
- 效果好 → **维持或加码**
- 数据太少 → **先诊断为什么少，再决定**。零数据 ≠ 一律「观察」：
  - 出价远低于竞争区间（如 0.10 vs 建议价/品类实际 CPC 1~3）导致
    整组拿不到点击的——「观察」永远观察不到任何东西。要么**提高出价
    到能拿到流量的水平**（建议价区间或该品类 CPC 附近，写明目标价
    与依据）真正测试这组定向，要么承认不想投这组、**暂停/并入**
    其他活动。一个 0.10 出价挂 30 天零点击的活动，「维持观察」是
    伪装成建议的不作为。
  - 出价本身合理（在建议区间内）、纯粹搜索量小/刚启动 →
    此时「维持观察」才是诚实的建议（写明原因）。
- **跨层联动：来源关键词的出价决定要按「否定之后」的经济性算**。
  关键词级 ACOS 爆表often是 Phrase/Broad 吃进来的垃圾搜索词造成的；
  这些词本报告已经在搜索词层否定了，下个周期流量会回归到核心词。
  此时再砍来源词出价是对好词的双重惩罚。先用搜索词表把「将被否定
  的词」的花费/销售从关键词汇总里剔除，按剔除后的 ACOS/ROAS 决定
  出价：剔除后健康 → 维持（写明「ACOS 高源于已否定的 X 个垃圾词，
  否定后预计回归」）；剔除后仍差 → 才降/停。来源词动作变化时，
  依赖它的提取/承接标签也要联动改。
- **动作头就是决定**。建议单元格的第一个动词是操作者执行的动作。
  出价被地板锁死且表现差到该停的行，动作头就写 暂停定向词（出价已低
  于 CPC×1.1 地板且 ROAS X 亏损），不要写 维持（…）再在备注里藏一句
  「建议暂停」——扫表的人只看动作头，藏起来的决定等于没做。
- **活动级判决**：当一个活动整体亏损且行级动作（地板锁死无法降价、
  主力词被停后活动失去意义等）解决不了出血时，必须给出活动级建议
  （暂停/重组/并入），不能只给一排「维持」让店主自己猜。
- **Auto 活动用否定剪枝，不要一刀切暂停整组**。Auto（close-match /
  loose-match / 自动定向）活动的搜索词里只要有转化词（订单>0、ROAS
  尚可），正确做法是：**否定零单浪费搜索词**、保留 auto 组继续承接
  转化词、否定后再观察整体 ROAS 是否回升（浪费花费剔除后通常会升）。
  整组 ROAS 偏低 often 是被一堆零单词拖的，不是 auto 组本身该停。
  **只有当该 auto 组的搜索词没有任何转化、或剔除浪费后仍亏损时，
  才暂停整组**。注意跨层自洽：一旦写了「暂停该自动定向」，它名下所有
  「维持——auto 定向承接」的搜索词就全部失去承接（组停了就不再承接），
  这是自相矛盾——要么不停组（剪枝即可），要么这些转化词改为提取为
  独立 Exact 定向词（救出来）。
- 汇总建议与行级建议必须一致（汇总说否定的，行里就是否定）

The sections below pin the machine-checkable details (thresholds,
formats, dimensions); they are backstops for the judgement above,
not a substitute for it.

## Bid rules (the recommendation column MUST obey these)

(Thresholds `acos_no_lower` (default 30) and `scale_roas` (default 5)
are the single source in `ad_rules.py`; a store's `notes.md` may
override — see tuning-thresholds.md.)

- **⚠️ `ACOS = 0` / 空白 is NOT "low ACOS = good" — it means ZERO SALES.**
  When `spend > 0` but `orders = 0` / `sales = 0`, Amazon prints
  `ACOS = 0.00` (and `ROAS = 0`). That is the WORST case — spend with no
  return, effective ACOS **∞** — not a healthy sub-threshold campaign.
  **The `ACOS < threshold` rule below does NOT apply when `orders = 0`.**
  Such a campaign is a money-loser: verdict is `降价/暂停` (or negate the
  wasted search terms), never `维持/表现良好`. Compute ACOS as
  `spend ÷ sales`; if `sales = 0`, write it as **`0 转化，花费 <币> X 全部
  浪费 (ACOS ∞)`**, never `<5%` or any placeholder. **Every ACOS value in
  the report must trace to captured `spend` AND `sales`** — a qualitative
  guess like `<5%` is a gap, not a metric.
- **ACOS < `acos_no_lower`% → never LOWER the bid** (only when
  `orders ≥ 1` — see the zero-sales rule above). Only `Hold` or
  `提高/raise`. Bid-above-suggested alone is NOT a reason to trim.
- **ACOS ≥ `acos_no_lower`% → a trim is allowed**, but the new bid must
  never be ≤ actual CPC (floor = `max(actualCPC×1.1, suggested_low)`).
- **ROAS > `scale_roas` converter (≥1 order) → raise (or state a
  concrete reason not to**: bid already at suggested-high / high
  impression share / budget-capped / low search volume). Don't park
  winners on a bare `Hold`.
- Zero-order waste → negate the search term, not a bid cut.

## TSVs per active campaign

Two TSVs per drilled active campaign, written right after drilling that
campaign, before the next (survives compaction):

- `stores/<slug>/ads/<platform>/<country>/<campaign_id>.tsv` — the
  targeting/keyword table.
- `stores/<slug>/ads/<platform>/<country>/<campaign_id>.searchterms.tsv`
  — the FULL search-term set (every row of the Export CSV / Customer
  Queries, not just the top-20 shown in the report).

The reviewer cross-checks TSVs on disk against the drill blocks; a
claimed drill with no TSV (or a TSV with no block) is a gap.

## Report-level requirements

- `# 广告优化建议 — <store> — <date>` header + analysis window.
- A `## 汇总建议` section at the end with REAL content (the reviewer
  rejects a header-only / marker-only summary): per-combo
  spend/sales/ROAS totals, the 5–10 highest-impact actions of this
  audit (each with magnitude + basis), ordered by impact.
- No leftover `<!-- INSERT: … -->` scaffold markers — every slot must
  be filled and its marker removed before the final submit.
- Prose/recommendations in **Chinese** (identifiers, metric names,
  search terms stay verbatim). Keep narrative tight — the TSVs carry
  the bulk structured data; the report needs the manifest + per-campaign
  tables + recommendations, not walls of text.

## 建议列格式 (reviewable actions)

Every raise/lower recommendation must state **how much** and **why** —
a bare 「提高出价」/「降低出价」/「加投」 is unreviewable and the
reviewer flags it:

**Targeting-keyword rows** (the ~10 biddable keywords):

- **Magnitude**: a target bid (`提高至 0.96`, `降至 1.20`) or a
  percentage (`下调 10%`). Compute the target from that row's own
  出价/ACOS/ROAS — not a blanket number.
- **Basis**, tagged rule or assumption:
  - rule-based: `（ACOS 41%>30 规则）`, `（ROAS 9>5 加投赢家规则）`
  - assumption-based: `（主力订单来源，ROAS 偏低也保留——假设）`
- Example row:
  `| wireless mouse | 0.80 | 9.0 | 提高至 1.00（ROAS 9>5 加投赢家规则） |`
- 维持 needs no magnitude; 否定/暂停 are binary. High-ROAS holds still
  need their justification (scale-winners rule).

**Search-term rows are a DIFFERENT DIMENSION — never bid verbs.**
A search term is what the user typed; it has no bid of its own (the
bid lives on the targeting keyword that caught it), so 「加投 25%」/
「提高出价」 on a search-term row is meaningless. The only valid
search-term actions:

- **否定** — high-spend zero-order term.
- **维持** — a converting term whose SOURCE keyword is healthy. The
  broad/phrase source keeps catching it; no action needed on the
  term itself (`维持——来源 Broad 词承接，ROAS 8.5`).
- **维持观察** — sample too small to judge.
- **提取为定向词 (Exact)** — RARE, conditional. Extraction is a
  RESCUE move, not a default: harvest a converting term into its own
  Exact keyword ONLY when its SOURCE keyword is being cut (its
  targeting-row action is 降/停/否定) — you save the good traffic
  before downgrading the bad source. State suggested bid for the NEW
  keyword (≈ the term's actual CPC × 1.1–1.25) + basis, e.g.
  `提取为定向词（Exact，建议出价 0.95——ROAS 5.66>5；来源 Broad 词
  ACOS 41% 已降档）`.
  - **NEVER extract when the source keyword is healthy** (提高/维持)
    — blanket extraction bloats the targeting list into a mess and
    self-competes with the source. The reviewer flags it.
  - **NEVER "extract" a term identical to its source keyword** — it
    already IS a targeting keyword (just at Broad/Phrase). The
    reviewer flags it.

The reviewer flags: any bid verb inside a search-term table
(wrong dimension); extraction with a healthy source; identity
extraction; extraction without a suggested bid.

## Workspace hygiene

The task directory is the DELIVERABLE space — it holds the report and
nothing else. Scratch material goes elsewhere:

- Throwaway analysis scripts → `/tmp/` (never the task dir).
- Backups: don't create `.bak` copies of the report — the platform
  snapshots it; a stale `.bak` invites a stale restore.
- Before the final `set_task_result`, remove anything in the task dir
  that isn't the `AD_AUDIT_<date>.md` itself.

## Quality requirements the reviewer enforces

These are checked at `set_task_result` — get them right or the reviewer
lists them as gaps:

1. **Every (platform, country) is drilled the SAME way — noon included.**
   every noon (country) must look like Amazon: per active campaign → product /
   ad-group + a **per-keyword / per-target table with a 建议 column**
   (bid / eCPC / ROAS / recommendation). A page-manifest (just
   `活动ID | 类型 | 花费 | ROAS`) is NOT a drill — the reviewer counts
   tables with a 建议 column per section and flags a section that claims
   drills but has none. Do not write `drilled 46/46` over a manifest.
2. **Clean search-term / target data.** Customer search terms are either
   a readable keyword OR an **UPPERCASE ASIN** (a product-page placement,
   e.g. `B0XXXXXXXX`) — never a lowercased `b0…` string and never a raw
   DOM attribute (`asin-expanded="…"`, `aria-label=…`). Include the match
   source, clicks, spend, orders, ROAS columns. The reviewer flags raw
   DOM / lowercased-ASIN leakage.
3. **No deferring in-scope work.** This session has all platforms open
   with time across rounds — do it now, don't write "待下次 audit",
   "无法获取", "代表性样本", or "需 Brand Registry OTP":
   - **Brand Analytics ASIN report is accessible WITHOUT OTP** (Seller
     Central → Brands → Brand Analytics; the brand auto-fills). Pull it.
   - **Cross-platform / same-SKU comparison** (a SKU's Amazon vs noon
     performance; the same SKU across SP campaigns) — do it this session
     using the TSVs you've written under `stores/<slug>/ads/`.
   The reviewer flags these excuse phrases.

## What "missing is acceptable" means

The reviewer never demands perfection in one shot. Each `set_task_result`
it returns a concise diff: which `(platform,country)` are under-drilled
(`D<A`, with the missing ids), which sections are absent, which
recommendations violate a bid rule. Address the top gaps, re-submit, and
the diff shrinks. After it converges (or the round cap), the best report
is accepted.
