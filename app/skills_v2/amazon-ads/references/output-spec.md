# Ad-audit OUTPUT SPEC — the report contract

This is the single definition of "done" for an ad-audit report. The
skill writes to it; the server's completeness reviewer
(`app/ai/stop_gates/ad_completeness_review.py`) checks against it at
`set_task_result` and returns the list of what's still missing each
round. **Partial is accepted** — you don't have to be perfect in one
pass; fix what the reviewer reports and re-submit. The report improves
each round until the gaps are gone.

## Scope is fixed by `AUDIT_TARGETS.json` — read it FIRST

Which marketplaces the audit owes is **not** your call, and it is **not
the task description's call either**. Before you start, the server writes
`AUDIT_TARGETS.json` at the task-workspace root — `{"combos": [{"platform": "amazon", "country": "SA"}, …]}`,
every marketplace the store is configured for in Settings. Read it at
the START of Phase 1 and let it drive the enumeration loop: **every
combo it lists needs its own `AUDIT_SCOPE.json` entry AND its own
`## <Platform> <Country>` section here.**

**A market list in the task title, description or plan does NOT narrow
this.** Those are prose, often written once and reused across stores, and
they go stale the moment a store adds a marketplace in Settings —
`AUDIT_TARGETS.json` is regenerated from that config on every run, so it
is the only list that is current. Seen live: a weekly schedule whose
description said "SA+AE" ran for a store since configured for SA+AE+AU;
the agent read the prose, announced it would skip AU, and would have been
denied for a missing combo it had been told twice to skip. If the two
disagree, `AUDIT_TARGETS.json` wins and the description is out of date —
audit every declared combo and note the discrepancy in the report rather
than silently dropping a market.

A combo with genuinely no live campaigns is still written down — an
entry with `"active_ids": []` and `"total_active": 0`, plus its section
saying so. 可以为空，但不能不写：omitting a declared combo is a `[基线]`
gap that blocks submission.

**`total_active` must record its provenance.** A combo that declares
`total_active` also needs a `"total_active_source"` naming WHERE that
number was read — `"bulk:<export filename>.xlsx"` (Amazon) or
`"chip:Live N"` (noon). Reason: `total_active == len(active_ids)` only
proves the two numbers agree, not that either was observed, and it is
trivially true when both come from the same parse. Observed live: a run
declared a 12-campaign marketplace as `4/4` because its script silently
dropped TSVs it couldn't read, and every check passed. Missing the field
while `total_active` is present is a `[基线]` gap; the exact shape and
what the server does with each form are in
[`audit-quickref.md`](audit-quickref.md) Step 1.

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

   **`name` must be the campaign's real name — never the id copied over.**
   The ad console shows a name on every row of the campaign list and the
   bulk export carries `Campaign Name`, so `name == id` never means "this
   campaign has no name"; it means the column was not read. A reader
   handed a bare 15-digit id cannot tell which ad it is, and recognising
   the ad is the first step of every decision they make. The server checks
   this: a combo where most campaigns have `name == id` is a `[名称]` gap.
   Write the same name into each `### <id> | <name> | <type>` heading.

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

   **One aggregate row is NOT a drill — the rejected shape.** A
   targeting table whose ONLY row restates the campaign total (first
   cell matching 合计 / 总计 / 汇总 / 整体活动 / 定位层汇总 / overall /
   total) is rejected as `[定向层]`. 把活动级数字抄进一张带 建议 列的表
   里不算下钻——出价、暂停、加投都是逐个关键词 / 逐个定向组的决策，
   汇总行里没有可执行的对象。Every active campaign's block needs REAL
   per-keyword rows (SP Auto: one row per auto-target group; noon Auto,
   which has no Targets tab: one row per Customer-Query-derived
   target). A trailing 合计 row is fine as a footer — it just cannot be
   the only row. 该活动的定向页确实没有数据时，在块内写「无数据」。

   **Include PAUSED targets that spent in the window — never filter by
   `state=enabled`.** This layer answers "where did this campaign's money
   go over the 30 days", and where the money went does not depend on what
   a target's state is *now*: a keyword that was paused yesterday still
   spent before it was paused, and that spend is still in the campaign
   total. Filtering to enabled makes the targeting layer under-count, so
   the search-term layer reads HIGHER than it — which cannot happen, since
   a campaign's queries cannot cost more than the campaign. You then have
   an arithmetically impossible reconciliation and will go looking for a
   platform defect that does not exist.

   Measured on one bulk export, same 30-day window, three campaigns: ALL
   targeting rows sum exactly to the campaign row and exactly to the
   search-term layer. Enabled-only gives 1.10x, 1.26x and 13.3x instead;
   the worst dropped 92% of that campaign's spend (3 of its 4 targets were
   paused). Every paused row is present in the export with correct
   figures — the filter lost the data, not the platform.

   Self-check: the targeting 合计 must equal the campaign row's spend. If
   it does not, rows are missing, and a `state` filter is the first
   suspect.

   **Carry the AD-GROUP level.** A campaign is not flat: Amazon's bulk
   export has `Ad Group ID` / `Ad Group Name` on every target row, and one
   campaign in a single live account had 3 ad groups (25 ad groups across
   23 campaigns, 1-63 targets each). Bids, budgets and negatives are set
   per ad group, so a table that flattens them loses the level the user
   acts on. Add a `广告组` column to the targeting table (Amazon: the
   export's `Ad Group Name`; noon: the SKU — see below) and keep rows of
   the same group together.

   **noon groups by SKU, not by ad group.** Its per-campaign export has
   sheets `(Product) Campaign | Sku | Target | Placement | Queries`; there
   is no ad-group concept. Use `Sku` as the group column and say so in the
   header. Do NOT invent an ad group for noon.

2. **Search-terms table** — the ACTUAL customer queries
   (Amazon: Search Terms page → **Export CSV** (the ONLY full-coverage
   method — the on-screen grid is virtualized and shows ~13 rows);
   noon: Customer Queries tab). Report the **top ~20 by spend** with
   `搜索词 | 广告组 | 来源关键词 | 匹配 | 点击 | 花费 | 订单 | 销售额 |
   ROAS | 建议`, state the total term count, and write the FULL set to the
   search-terms TSV (below).

   **What a search term hangs off differs by platform — do not fake the
   one you don't have.** Amazon's `SP/SB Search Term Report` carries
   `Ad Group ID`, `Keyword ID`, `Keyword Text` and `Match Type` on every
   row, so each query IS attributable to the exact target that matched it
   — fill `广告组` and `来源关键词` from those columns. noon's
   `(Product) Queries` sheet carries only `Campaign Name`, `Sku` and
   `Query`: a noon query CANNOT be attributed to a target. For noon put
   the SKU in `广告组`, write `—` in `来源关键词`, and never guess a
   source target. **Every term with impressions > 0 is its
   own row** — never fold live terms into a `其余 N 个` row (a collapse
   row is only allowed for all-zero-impression filler and must say
   `0 展示`). The reviewer rejects collapse rows with traffic.
   **The `建议` column must name the EXACT action, not a category.** The
   console renders your recommendation as the pre-selected button, so an
   ambiguous verb forces whoever renders it to guess — and a guess about
   money is not theirs to make. Use exactly one of:

   - targeting rows: `提高至 <bid>` / `下调至 <bid>` / `暂停` / `维持`
   - search-term rows **that are keyword queries** — the action AND the
     match type, because both directions have one:
     `添加为关键词（精准|词组，建议出价 X）` (older spelling `拓词` is still
     read) / `否定关键词（精准|词组）` (older `否定精确` / `否定词组` still
     read) / `维持`
   - search-term rows **that are category / product placements** — the
     match column says `Subcat`, `Category`, `Product`, an ASIN, or the
     like: `否定投放` / `维持`

   Bare `否定` is NOT acceptable on a KEYWORD search-term row: phrase-
   negating and exact-negating a query have very different blast radius,
   and only the audit knows which one the data supports. Likewise a
   converting query that deserves its own keyword must say so explicitly —
   nothing downstream can infer that from `维持`.

   **State the match type when ADDING a keyword too, not only when
   negating.** Both carry one, and the blast radius argument is the same in
   both directions: added as 精准 the new keyword serves only that exact
   query; as 词组 it also absorbs every query containing it, taking traffic
   from the broad target that surfaced it. The console has to pre-select
   something, so when you leave it unstated it falls back to 精准 (the
   narrower option) and marks the row `报告未指定` — a visible admission
   that the page chose, not you. Do not make that the normal case.

   **But match type is a property of keywords, so that rule stops at a
   category/product placement.** A `Subcat` or ASIN row has no phrase-vs-
   exact variant to choose between and cannot be promoted to a keyword —
   negating it just drops that placement, so `否定投放` is the whole
   action and `拓词` does not apply. Do not invent a match type to satisfy
   the keyword rule: writing `否定精确` on a category row states a
   distinction the platform does not have, and the console executes what
   the action head says.

3. **The reconciliation line** — machine-checkable, same 30-day window
   on BOTH pages:

   `搜索词对账: 定向花费 <币> X / 点击 A = 搜索词花费 <币> Y / 点击 B (✓/✗)`

   `X/A` = targeting-table totals; `Y/B` = search-term-report totals.
   The reviewer parses this line and grades the SPEND pair — in two
   directions, and NOT symmetrically (next paragraph). Clicks are
   advisory: Amazon's search-term report strips invalid clicks, so click
   totals can legitimately diverge on a perfect same-window read.
   Campaign types with no search-term report (e.g. Sponsored Display)
   write `无搜索词报告` instead; zero-click campaigns may write
   `无点击，无搜索词`.

   **对账 是两个检查，不是一个。** 搜索词花费只可能是定向花费的一
   *部分*——每个搜索词的花费本来就已经计在定向层里了——所以「偏低」
   和「偏高」是两种完全不同的失败，服务端也按两种处理：

   - **`Y` 低于下限 → `[对账]`，普通的「没取全」。** The capture missed
     rows. Amazon's floor is **85%** of targeting spend
     (`1 - reconcile_tolerance`, default 15%, `ad_rules.py`). **noon now
     uses the same 85% floor.** The old 40% was calibrated on Customer-
     Queries readings taken off the on-page table, which renders a fixed
     top-15 with no paginator — so it measured a UI cap, not the
     platform. Via that tab's **Export**, noon's two layers agree
     exactly (an Auto campaign matching across ~10k query rows and a
     Manual one across ~400). Usual causes, in order: the search-term
     capture is incomplete (noon: you read the 15-row tab instead of the
     Export), or the two pages were read on DIFFERENT date windows (the
     30d-vs-7d bug) — use the Export and re-pin both pages to the same
     window. 这一条跟别的 gap 一样，实在补不上时最终会放过。
   - **`Y` 超过 `X × 1.02` → `[对账·不可能]`，一个矛盾。** 不是精度
     问题，是不可能。Measured on a live account (one bulk export, both
     layers, same 30-day window, 17 enabled SP campaigns): ratio min
     0.998, median 1.000, max 1.000 — 15 of 17 exactly 1.000, the other
     two off only by two-decimal rounding, clicks tracking identically.
     noon's captured layers likewise never exceed 1.000. The 2% ceiling
     is that live maximum plus headroom for rounding and currency
     formatting, nothing more.

     **The usual cause is a `state=enabled` filter on the targeting
     layer.** Spend made before a target was paused still counts in the
     campaign total, so dropping paused rows under-counts the targeting
     side and the search-term side then reads higher. Measured on three
     campaigns: all targeting rows sum exactly to the campaign row and to
     the search-term layer; enabled-only turns that into 1.10x, 1.26x and
     13.3x (the worst losing 92% of the campaign's spend). **Check first
     whether the targeting 合计 equals the campaign row's spend**, then
     look for another explanation.

     Only after that: the targeting table of campaign A joined to the
     search terms of campaign B (wrong `Campaign ID`), or the two figures
     taken from different accounts / different exports.

   **Missing CLICKS is not a reason to quarantine the whole campaign.**
   A page that renders the clicks column as 0 (the AE Manual targeting
   page does this under an unstable load) costs you `CPC = spend ÷ clicks`
   and therefore the `CPC×1.1` floor — so **bid** recommendations for that
   campaign are unreliable. It costs you nothing else. Pausing a target
   that spent with zero conversions, or negating a wasteful query, needs
   no CPC at all, and those are usually the most valuable actions in the
   block.

   So when spend reconciles but clicks are missing, do NOT write the
   whole-campaign 「数据不可信 + 请勿执行」. Drop only what actually
   depends on the missing column:

   - **A LOWER is unsafe** — `下调至 X` is floored at `CPC × 1.1`, and with
     no clicks there is no CPC. Write `维持（点击列未渲染，CPC 无法成立，
     降价地板算不出来）` instead. The tell is visible in the text itself:
     a row whose bid is 2-4 printing `新出价不低于 CPC×1.1=0.05` has
     divided by zero clicks, and 0.05 is not a floor.
   - **A RAISE is fine** — `提高至 X` triggers on `ROAS ≥ 5`, and ROAS is
     sales ÷ spend. Both columns rendered; only clicks did not. Keep it.
   - **Pause / negate / 拓词 are fine** — they stand on spend, orders and
     conversions, never on CPC. Keep them; they are usually the most
     valuable actions in the block.
   - add one line to the block: `⚠️ 本活动点击列缺失：降价建议不可用
     （CPC 地板算不出），提价/暂停/否定建议仍然有效`.

   Never print a `CPC×1.1=…` floor derived from zero clicks. If clicks are
   0 and spend is not, say the floor is unavailable rather than quoting a
   number that came from dividing by nothing.

   Seen live: three Amazon AE campaigns carrying AED ~1,100 of spend were
   quarantined whole for exactly this, one of them reconciling at
   1.000×. Every pause and negate recommendation in them was sound and
   became unexecutable along with the bid advice.

   **⚠️ 「不可能」这一条不吃 stall fail-open。** Every other gap
   eventually fails open when the agent can't finish it; this one keeps
   refusing, because the number would otherwise ship straight into bid
   recommendations. 只有两个合法答案：

   1. **Confirm the targeting layer is not filtered by `state=enabled`**
      (合计 == the campaign row's spend), then **re-read both layers for
      the same `Campaign ID`** and fix the figures. (The
      Amazon bulk export carries BOTH layers in one workbook — read them
      off the same file for the same campaign and this cannot happen.)
   2. **在该活动块里声明这个活动不可信**，用一行同时说明「数据不
      可信」*和*「本活动的出价建议请勿执行」，例如：
      `⚠️ 数据不可信：本活动两层对账矛盾，请勿执行本活动的出价建议`
      **半个免责声明不算。**「数据有偏差，仅供参考」只写了前半句，
      那些带 建议 列的行读起来依然是可执行的动作——而那正是这条检查
      要防的结果。

   两个都不做，服务端会在交付的报告最前面加一条警告横幅、点名这些
   活动。所以它永远不可能「看起来干干净净」地交付。

   **All four numbers, or it does not count.** The reviewer parses this
   exact shape. A line that substitutes prose for the numbers — `待导出`,
   `需回采当前 30 天窗口`, `定向花费 X（TSV）→ 当前 30 天 Y（需回采对齐）` —
   is **an admission that this layer was never captured**, and is rejected
   as a format gap (`[搜索词·格式]`), not silently accepted. If you cannot
   fill in `Y/B`, the layer is not done: go back to the search-term page,
   pin it to the same 30-day window as the targeting table, and read it.

   **Never source `Y/B` from a pre-existing TSV on disk.** A TSV written
   on an earlier date covers an earlier window, so it cannot reconcile
   against a fresh targeting read — and the mismatch is not fixable by
   re-labelling it. Re-drill the search-term page live for the window you
   are auditing. (Live failure this prevents: an agent extracted the layer
   from 6-week-old TSVs, wrote its own `✗ — 窗口不对齐` on every line, then
   spent 11 review rounds insisting the reviewer was misparsing instead of
   re-drilling.)

   **`无搜索词报告` is a claim about the campaign TYPE, not an excuse.**
   It means "this campaign type has no search-term report at all" (SD).
   Writing it next to a note that the data still needs fetching — e.g.
   `无搜索词报告（需从 Search Terms 页面导出全量 CSV）` — contradicts itself
   and is rejected. Either the report does not exist (say only that), or
   it exists and you must go read it.

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

- **⚠️ `ACOS = 0` / blank, when `spend > 0`, is NOT "low ACOS = good" —
  it means ZERO SALES.** (With `spend = 0`, `ACOS = 0` is a benign zero.)
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

### The TSV schema is FIXED — same columns every time

These files are the machine-readable artifact: the console renders from
them, the execution task acts on them, and the next audit diffs against
them. So the header is not yours to choose.

**Tab-separated. First line is the header, exactly these columns, in this
order.** Targeting (`<campaign_id>.tsv`):

```
ad_group	target	match_type	state	bid	currency	clicks	spend	orders	sales	acos	roas	suggestion
```

Search terms (`<campaign_id>.searchterms.tsv`):

```
ad_group	search_term	source_target	match_type	currency	clicks	spend	orders	sales	roas	suggestion
```

Rules that make these readable by something other than the run that
wrote them:

- **Tabs, never pipes.** A pipe-delimited file read as a TSV is one
  column wide and every figure in it is lost.
- **`currency` is its own column** — `SAR`, `AED`, `A$`. Never fold it
  into a header name (`spend_SAR`, `Spend (AED)`): a reader looking for
  `spend` then finds nothing, and the column it needs changes per market.
- **`ad_group`** is the Amazon ad group, or the SKU on noon (which has no
  ad group). Never blank.
- **`source_target`** is which target the query matched — available on
  Amazon (`Keyword ID` / `Keyword Text` in the search-term report), `—`
  on noon, whose Queries sheet cannot attribute a query to a target.
- **`state`** is the target's own state, and paused targets with spend in
  the window BELONG here (see the targeting-table rule above).
- **`suggestion`** repeats the report's `建议` verbatim, so the two never
  drift.
- **One row per item, no collapse rows.** No `其他 N 个` / `其余…`; a
  zero-impression filler row must say `0 展示` and carry zero metrics.

Observed live, before this was pinned: **22 different targeting headers
and 12 search-term headers across one store's files** — `target/match/
entity/…`, `targeting_group/status/…`, `row_id/status/match_type/…`,
English title-case, Chinese, and two pipe-delimited. The agent's own
summary script then mis-summed the layer and it had to recompute, because
nothing could rely on a column being where it was last time.

The reviewer cross-checks TSVs on disk against the drill blocks; a
claimed drill with no TSV (or a TSV with no block) is a gap.

## Report-level requirements

- `# 广告优化建议 — <store> — <date>` header + analysis window, as the
  report's **first line**. Nothing above it — in particular **never a
  `Status:` line**: `Status: ok | gaps | incomplete` is the REVIEW file's
  format (`REVIEW_<date>_iterN.md`, read by the reviewer gate), and a
  report that opens with it hands the reader an internal gate token
  instead of a title. Observed live: an audit shipped with `Status: gaps`
  as its literal first line, above the H1.
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
