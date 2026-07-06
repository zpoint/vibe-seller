# Ad-audit QUICKREF — the whole procedure on one page

This is the lean end-to-end. Follow it top to bottom. Load a heavy
reference **only when a step says to** — do NOT pre-read every file
(that buries the model and causes shortcutting). The contract for the
finished report is [`output-spec.md`](output-spec.md); read that first,
then run this.

## Loop shape (important)

You build the report **across several rounds**, not in one pass: do a
batch, call `vibe_seller_set_task_result("./AD_AUDIT_<date>.md")`, and the
server's completeness reviewer replies with a short **"what's still
missing"** list. Fix the top gaps, write to the file, call
set_task_result again. Repeat — the report converges.

**But the task is NOT done until EVERY active campaign is drilled** — the
`进度` line of every `## <Platform> <Country>` section must read
`drilled <A>/<A>` (D == A). The server **blocks finishing while any
active campaign is still un-drilled** — this includes simply ending your
turn, not just `set_task_result`. So keep drilling and re-submitting
until D == A for every combo. **Do NOT stop early, and never defer
campaigns to a "next round" / "next audit"** — there is no next round;
finish them now. "Missing this round" is fine only as an *intermediate*
state on the way to a full drill, never as the final report.

## Step 0 — scope + scaffold with append-markers

Read `stores/<slug>/metadata.json` → `platform_countries`. Audit each
(platform, country) it lists (e.g. Amazon <cc1>/<cc2> + noon <cc1>/<cc2>;
or just a single Amazon marketplace for a single-market store). 30-day
window. Create
`./AD_AUDIT_<YYYY-MM-DD>.md` with the header.

**Scaffold every section up front, each with a unique append-marker.**
For each (platform, country) write its `## <Platform> <Country>` heading,
its `进度` line, and ONE marker line you will append against:

```
## noon EG
**进度**: drilled 0/46 active (70 total, 5 pages)
<!-- INSERT: noon EG -->
```

The marker is how you append reliably (see Step 2). Do this for all
combos before drilling so every section has a stable, unique anchor.

## Step 1 — enumerate EVERY campaign (per platform, country)

Completeness is the #1 thing the reviewer checks. Get the FULL active
set before drilling.

- **Amazon**: open Campaign Manager via the in-page menu (not a typed
  URL). **Clear the "Find a campaign" search box** (a stale term hides
  most campaigns) and set the date to 30 days. The grid virtualizes —
  do NOT count DOM rows; use **Bulk Operations**. On that page, **reuse
  the newest existing export first** — walk shadow roots for the
  `<a download …/bulk-operations/download/…xlsx>` links and, if the
  newest one's filename date range covers your window, just click it
  (no modal, no 5–15 min wait). Only generate a fresh export
  (`Download campaigns` — a shadow-DOM button, not a plain `<button>`)
  when no recent file covers the window. Then parse the XLSX for the
  full list. Selectors + the reuse/shadow-DOM details: `mechanics.md` §2d.
  - **⚠️ VERIFY the export's window before trusting its metrics.** The
    filename is `bulk-<entity>-<START>-<END>-<epoch>.xlsx`; `<START>-<END>`
    is the metric window. **A 2-day export (e.g. `…-20260701-20260702-…`)
    is NOT a 30-day analysis** — on a short window sales haven't attributed
    yet, so `spend > 0, sales = 0, ACOS = 0`, which reads as "great" but is
    just "no data yet". Confirm `<START>-<END>` spans your requested window
    (≥ ~28 days for "30 天"); if it doesn't, generate a fresh 30-day export
    (Campaign Manager date picker defaults to **2 days** — set it to 30
    first). Never label the report "最近30天" off a 2-day file.
- **noon**: open the Ad Manager home. The list is **paginated ~15/page**
  — page through ALL pages and union the campaign ids. Click the
  page-number anchor with dispatched events (the chevron doesn't work):
  `var a=document.querySelector('li.ant-pagination-item-N a'); a.dispatchEvent(new MouseEvent('mousedown',{bubbles:true})); a.dispatchEvent(new MouseEvent('mouseup',{bubbles:true})); a.dispatchEvent(new MouseEvent('click',{bubbles:true}));`
  then wait ~3s and re-extract `a[href*="/campaign/details/"]`. Details:
  load `../../noon-ads/SKILL.md` §3.

Then write the **进度 line** for that section (the reviewer reads it):
`**进度**: drilled <D>/<A> active (<T> total, <P> pages)` — `<A>` is the
true active count you just enumerated.

**Also persist the authoritative active set to `./AUDIT_SCOPE.json`** (task
root) as you enumerate each combo — this is the ground truth the server
checks report coverage against (you cannot pass by shrinking `<A>`; every
listed id must get a drill block). Append one entry per combo:

```json
{"combos": [
  {"platform": "amazon", "country": "SA",
   "active_ids": ["600000000001", "600000000002"]},
  {"platform": "noon", "country": "AE", "active_ids": ["C_DEMO0001"]}
]}
```

`active_ids` = every **active** campaign id you enumerated (Amazon: the
`state=enabled` Campaign ids from the bulk export — `ads_bulk.py scope
<export>.xlsx` prints them; noon: the campaign ids unioned across all
pages). `<A>` in the 进度 line must equal `len(active_ids)` for that
combo. If you never establish a scope (a one-off "create/investigate a
single ad" task), just omit the file — the server won't demand a full
drill.

## Step 2 — drill EACH active campaign, build the report with `Edit`

You process **one active campaign at a time**, and for each one you do
**two tool calls only: `Read` then `Edit`** (no scripts — see the box).

**The drill is TWO layers per campaign — targets AND search terms —
both on the SAME 30-day window.** A campaign is drilled only when BOTH
TSVs exist: `stores/<slug>/ads/<platform>/<country>/<id>.tsv` (targets)
and `<id>.searchterms.tsv` (customer queries). For each campaign **in
the active set you enumerated in Step 1**:
- **Both TSVs exist** (from a prior round) → `Read` them, then `Edit`
  the block into the report. No re-drill, no browser.
- **Missing either** → capture it:
  1. *Targeting layer*: campaign detail → per-keyword / per-target
     table (noon Manual: Targets tab).
  2. *Search-term layer* (REQUIRED — the actual customer queries):
     **Amazon: Search Terms page → Export CSV button**, then parse the
     downloaded CSV. The on-screen grid is virtualized (~13 rows
     visible of often 200+) — Export is the ONLY full-coverage method.
     Set the date range BEFORE exporting. **noon: Customer Queries
     tab** (Manual and Auto).
  3. *Reconcile*: search-term spend/clicks totals must match the
     targeting totals within ~15%. Write the machine-checkable line
     into the block:
     `搜索词对账: 定向花费 <币> X / 点击 A = 搜索词花费 <币> Y / 点击 B (✓)`
     A mismatch = the two pages are on different date windows (the
     30d-vs-7d bug) — re-pin both and recapture; never submit a ✗.
  4. `vibe_seller_write_workspace_file` BOTH TSVs (full search-term
     set in `.searchterms.tsv`, not just the top rows).

The campaign's report block = targeting table (+合计 row) + top-20-by-
spend search-terms table + the 对账 line. **Every term with impressions
gets its own row** — never fold live terms into `其余 N 个` (the
reviewer rejects collapse rows with traffic; all-zero filler may
collapse but must say `0 展示`). SD-type campaigns with no search-term
report write `无搜索词报告`.

**Append with the section's marker — this is the ONLY anchor that never
drifts.** To add a campaign's block, `Edit` with
`old_string = "<!-- INSERT: noon EG -->"` and
`new_string = "<campaign block>\n<!-- INSERT: noon EG -->"`. The marker
moves down, your block lands above it, and the next append matches the
same marker. **Never** anchor an `Edit` on the previous campaign's table
text or the `进度` line — those vary and you'll get *"String to replace
not found"*, the error that stalled noon at 3/46. If an `Edit` ever
fails to match, `Read` the file to get the exact current text, then
retry against the marker — do NOT give up and do NOT switch to a script.

After appending, `Edit` the `进度` line to bump `<D>` by one. **Only ever
touch campaigns in the active set** — ignore TSVs on disk for campaigns
NOT in your active enumeration (paused/archived leftovers). `<D>` must
converge to `<A>`, **never exceed it**: `drilled > active` means you
dumped non-active campaigns and the server rejects it ([越界]).

> **Build the report with the native `Read`+`Edit` tools, one campaign
> at a time. NEVER write a python/bash script that loops over the TSVs
> and emits the report.** `Edit` is an exact string-replacement that
> *adds* one campaign's block onto the existing report (like editing
> code), so prior campaigns are never touched and `<D>` only ever goes
> up. A script that re-emits the whole `AD_AUDIT.md` is the banned
> anti-pattern — it both drops campaigns (the lossy rewrite that
> collapsed Amazon US 31→2) and sweeps in non-active leftovers (the
> dump that produced `drilled 105/56`). Reading a TSV with `Read` and
> appending it with `Edit` is the ONLY accepted way to grow the report.
> Yes, that's one `Read`+`Edit` pair per campaign even for ~140
> campaigns — do them; it is not "too long". Don't sample, don't
> batch-generate — the reviewer counts D vs A and names the shortfall.

## Step 3 — recommendations (the 4 bid rules)

The recommendation column MUST obey (thresholds `acos_no_lower` default
30, `scale_roas` default 5 — the single source is `ad_rules.py`; a
store's `notes.md` may override, e.g. `scale_roas: 6`):
0. **⚠️ `ACOS = 0`/blank means ZERO SALES, not "good".** `spend > 0` +
   `orders = 0` ⇒ Amazon prints `ACOS = 0.00` — worst case (effective
   ACOS ∞), a money-loser → `降价/暂停`, never `维持/表现良好/<5%`. The
   rules below apply ONLY when `orders ≥ 1`. Compute ACOS = spend÷sales;
   sales=0 ⇒ write `0 转化，花费全部浪费`, never a `<5%` placeholder.
1. **ACOS < `acos_no_lower`% (and `orders ≥ 1`) → never lower the bid.**
   Only Hold or raise. High bid-vs-suggested is not a trim reason.
2. **ACOS ≥ `acos_no_lower`% → trim allowed**, but new bid never ≤
   actual CPC.
3. **ROAS > `scale_roas` converter → raise** (or state why not: bid at
   suggested-high / high impression share / budget-capped / low search
   volume). Never a bare Hold on a winner.
4. Zero-order waste → negate the search term (not a bid cut).

(Deeper lever selection: load `tuning-toolbox.md` only if needed.)

## Step 4 — submit + converge (the server IS the reviewer)

Call `vibe_seller_set_task_result("./AD_AUDIT_<date>.md")` every **3–5
drilled campaigns** — the server's completeness reviewer replies with
exactly what's still missing. **Do NOT spawn your own format-review
subagent and do NOT iterate on format polish** — the server check is
the only review that counts, and the gap that actually blocks you is
almost always *quantity* (D < A), which only more drilling fixes.
Review-loop iterations that don't raise any `<D>` are wasted rounds (3
no-progress submits = the server stops listening and accepts a partial
report — don't burn them on cosmetics).

**Converge — don't restart:**

- **Grow the report with `Edit`, never regenerate it.** When a gap says
  "noon EG drilled 13/46" (or a `[回退]` regression), open ONLY the
  not-yet-drilled campaigns, write each one's TSV, and `Edit`-append each
  campaign's block into its section (bumping `进度 <D>`). You are *adding*
  to the existing `AD_AUDIT.md`, not rewriting it.
- **NEVER `python … Write` the whole report from memory.** After context
  compaction your memory of earlier drills is incomplete, so a from-
  memory rewrite silently drops campaigns you already did — that is the
  exact bug that collapsed Amazon US 31→2. `Edit` touches only the lines
  you name, so prior campaigns are safe. (If you ever need to recover a
  section, the per-campaign TSVs on disk are the durable backup — read
  the missing ones and `Edit` them back in.)
- Each round D must go UP, never down — the server rejects any combo
  whose D regressed below a prior round (`[回退]`). Repeat until the gap
  list is empty (or the server accepts after the round cap). Each round
  *completes onto the last*; you only start a fresh report on a task
  retry, never mid-task.

## Step 5 — export the PDF (once the report is complete)

After every combo is `drilled A/A` and the server accepts the result,
render the report to a PDF for the user (better than reading raw
markdown):

```bash
PY=<project-venv>/bin/python3            # needs `markdown` (requirements.txt)
S=<skills>/amazon-ads/scripts/md_to_pdf.py
$PY "$S" ./AD_AUDIT_<date>.md            # writes AD_AUDIT_<date>.pdf next to it
```

It renders CJK + tables via headless Chrome (throwaway profile — safe to
run while the store browser is up) and fails loudly if the PDF has no
pages. Mention the `.pdf` path in your result summary.

## Reference index (load on demand only)

- `output-spec.md` — the report contract (read first).
- `mechanics.md` — exact Amazon selectors, column layouts, bulk flow.
- `noon-ads/SKILL.md` — noon pagination, drill tabs, span structure.
- `tuning-toolbox.md` / `tuning-thresholds.md` — lever choice, margins.
- `tuning-recommendation-format.md` — table column format.
- Others (`tuning-workflow`, `tuning-campaign-types`, `funnel-diagnosis`,
  `reviewer-loop`, `format-anchor`) — only if a specific question arises.
