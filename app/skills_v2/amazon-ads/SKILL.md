---
name: amazon-ads
description: "Amazon Sponsored Products / Sponsored Brands / Sponsored Display ads + Coupons on Seller Central / advertising console. ONE catalog covering BOTH mechanics (URLs, click paths, modal patterns, kat-* component gotchas, field input ranges) AND workflows (tuning existing campaigns, weekly review, search-term harvest, ACOS improvement). Load this skill BEFORE any browser-use action that creates, edits, captures, archives, or downloads campaigns / ad-groups / keywords / product targets / coupons on amazon.<tld> or advertising.amazon.<tld>. The catalog below points to topical references — load whichever ones the task needs. Defaults to last 30 days for tuning analysis but accepts any user-specified window."
allowed-tools: Bash(browser-use:*)
requires: [amazon-shared]
gates: [ad_changes, ad_coverage]
---

# Amazon Ads — Catalog

Load the `amazon-shared` skill first (Skill tool): marketplace TLD
map, navigation, the Ziniao login challenge loop, and the ad-console
vs seller-central account caveat live there.

## 0. Declare what this task is for

Before any browser action, call `vibe_seller_declare_ad_task`. The
declaration decides how much coverage the server expects from you and
whether you may propose changes at all, so read it off what the person
actually asked for — not off what you expect to find once you look.

| They asked for | `kind` | `scope` |
|---|---|---|
| "审计一下广告" / "review our ads" | `audit` | omit `combos` — whole store |
| "复核 widget-006 在 amazon 的广告" | `audit` | that platform+country, those campaign ids |
| "把这三个词的出价降下来" | `audit` | those campaigns |
| "帮 widget-006 建关键词广告" | `create` | the market you are creating in |
| "执行刚才确认的调整" | `execute` | the campaigns being changed |
| "我们 SA 的 ACOS 大概多少" | `investigate` | the market asked about |

**Numbers or changes?** That is the line between `investigate` and
`audit`. If the person wants a figure — what did we spend, what is our
ROAS, how did last month go — it is `investigate`, however many markets
it spans: you are reading, not proposing. If they want to know what to
change, or asked "is this right?", it is `audit`. Declaring a figures
question as a whole-store audit buys hours of drilling nobody asked for;
declaring a bid review as `investigate` leaves you unable to hand over
the recommendations they wanted.

**`investigate` reads and proposes nothing.** Read the figures from the
console (summary or campaign list, for the window asked) and answer with
`vibe_seller_set_task_result`. No per-campaign drill, no
`CAMPAIGNS.tsv`, no `AD_AUDIT_*.md`, and no `CHANGES.tsv` rows other
than `hold`.

**If you under-declared, correct it upward.** When an `investigate`
turns out to need decisions — raise, lower, pause, negate — call the tool
again with `kind: "audit"` and the same or a narrower scope. That is the
only kind change allowed inside a turn, because it only adds what you
owe. `audit` → `investigate`, adding a marketplace, or widening the
campaign list need a new message from the user. Don't delete
recommendations the person asked for to fit a declaration.

**There is no `edit` kind.** A request to change specific bids is an
`audit` scoped to those campaigns: the user still approves the change
before it is applied.

**The scope trap: omitting `combos` means every marketplace the store
sells on**, and you owe all of them. Omit it only for a genuinely
store-wide request.

**When the request names a product, not a campaign** ("widget-006 的
广告"), declare the market first with `products`, enumerate the campaign
list, find the campaigns carrying that SKU family, then call the tool
again with the same kind and combos plus `campaigns: [...]`. That
narrowing call is accepted within the turn. Refining may only remove
reach. If something seems to need a wider scope, say so in your result
and let the user widen it.

**A new user message that changes the work is a new phase — declare
again.** After a `create`, "now review what you created" is an `audit`
that may name the campaigns you just made. When the user replies with
which suggested changes to make, declare `execute` scoped to those
campaigns.

## What each kind produces

| kind | Deliverable | Reference |
|---|---|---|
| `audit` | `AD_AUDIT_<YYYY-MM-DD>.md` for the user, plus `CAMPAIGNS.tsv`, two TSVs per live campaign, and `CHANGES.tsv` | [`audit-quickref.md`](references/audit-quickref.md) (procedure), [`data-contract.md`](references/data-contract.md) (files and columns) |
| `execute` | Approved changes applied and verified, `EXECUTION_LOG.tsv`, `applied_*` columns filled | [`execution.md`](references/execution.md) |
| `create` | New campaigns, verified live | [`bulk-operations.md`](references/bulk-operations.md), [`mechanics-create.md`](references/mechanics-create.md) |
| `investigate` | The figures asked for, as the task result | this page, §0 |

The server checks the TSVs, never the report's prose, so write the
report in whatever shape reads best for the person and put the numbers
and decisions in the files. For an audit, start with
[`audit-quickref.md`](references/audit-quickref.md) and load other
references only when a step points to them — pre-reading everything
crowds out the work.

`AUDIT_TARGETS.json` (task root, written by the server after you
declare) lists every marketplace the store is configured for. It is the
menu; your declaration picks what you owe from it. A market list in the
task description is prose that may be stale — the declaration is the
source of scope.

## When the recipe doesn't fit — recover, don't stall

The references are worked examples, not the whole space. When a step
meets a shape it didn't expect, reason from the invariants below and take
the next concrete step rather than handing a half-finished task back.
Ask a human only when a credential or permission is genuinely missing.

- **The export is ground truth; the grid is fragile.** Amazon's grids
  are virtualized (about 13 of hundreds of rows exist in the DOM), so
  read campaigns, keywords and search terms from the Bulk Operations
  export or a Search Terms CSV. If you catch yourself scrolling and
  re-reading a grid, export instead.
- **The grid's own Export button is page-scoped.** Campaign Manager's
  inline "导出 / Export → 当前表格数据 / current table data" dumps only
  the ~50 rows on the current page, and the grid's Next Page control
  often refuses to advance. Enumerate from the Bulk Operations download
  ([`mechanics-reading.md`](references/mechanics-reading.md) §2; reuse the newest
  `bulk-*.xlsx` whose window covers yours). After any download, compare
  its campaign-row count with the grid's `aria-rowcount`; a file with
  about 50 rows is the page, not the account.
- **A persisted search filter hides campaigns.** The campaign-search box
  keeps the last term in the browser profile. Clearing it takes a real
  coordinate click on its ✕ (`aria-label="Clear search terms"`); the
  list-level "clear filters" control, setting `.value`, and synthetic
  clicks all leave it applied. Then read `aria-rowcount` — a real clear
  makes it jump. Also clear the default status filter so paused and
  archived campaigns count. Snippet and fallbacks:
  [`mechanics-reading.md`](references/mechanics-reading.md) §1 — load it
  before your first campaign-list read.
- **Data is scoped to the marketplace on screen.** Some accounts show
  one country at a time (top-left `Sponsored ads, <Country>` switcher,
  each country a separate entity); others list several countries with a
  Country column. Check which you have, and confirm every requested
  country is actually in the data you read. A country's absence means it
  lives behind the switcher, not that it has no ads — switch, export it
  separately, and look before you claim a negative. A stored note saying
  a market is empty is a stale snapshot.
- **Build from the export's own structure.** `scripts/ads_bulk.py` has
  `inspect`, `scope`, `clone-campaign`, `bid-update`, `negate` and
  `archive-campaign`; check them before hand-parsing a workbook. For a
  case they don't cover (an SB negative, an unusual entity), copy a
  working row of that kind from the export and change only your values —
  the export shows the exact tokens this account and locale accept.
- **Poll async jobs in short calls.** A bulk export or upload takes
  minutes; poll with a few `sleep 20` + re-open cycles. One long sleep,
  or blocking on a Monitor for page state, looks like a hang.
- **Verify by re-reading ground truth.** A bulk `Success` only means the
  file was accepted. Re-export or re-open the campaign and confirm the
  committed state: enabled, advertising the intended SKU (a cloned
  campaign can still point at the old one), negatives present.

## References

| Reference | Load when |
|---|---|
| [`data-contract.md`](references/data-contract.md) | Writing any ad TSV: file names, paths, columns, store-rule keys and defaults. |
| [`audit-quickref.md`](references/audit-quickref.md) | Running an audit — the procedure end to end. |
| [`output-spec.md`](references/output-spec.md) | Filling the per-campaign TSVs correctly and writing the human report. |
| [`tuning-recommendation-format.md`](references/tuning-recommendation-format.md) | Deciding each row's action: PROTECT, bid floor and step cap, missing margin, zero-sales rows, search-term actions. |
| [`tuning-workflow.md`](references/tuning-workflow.md) | What to read on each campaign tab and how to diagnose it; handling follow-up replies to an audit. |
| [`tuning-thresholds.md`](references/tuning-thresholds.md) | Deriving the store's breakeven / target ACOS, waste and harvest cutoffs. |
| [`tuning-toolbox.md`](references/tuning-toolbox.md) | Choosing a lever (negate, pause, bid, placement, strategy, harvest, budget) and where it lives in the UI. |
| [`tuning-funnel-diagnosis.md`](references/tuning-funnel-diagnosis.md) | Telling a listing problem (CTR, CVR) from an ad problem before touching bids. |
| [`tuning-campaign-types.md`](references/tuning-campaign-types.md) | The campaign isn't SP Manual Keyword: per-type tabs, columns, and which levers apply. |
| [`execution.md`](references/execution.md) | Applying approved changes and recording them. |
| [`bulk-operations.md`](references/bulk-operations.md) | Default path for creating campaigns or changing many bids: export → `ads_bulk.py` → upload → verify. |
| [`mechanics.md`](references/mechanics.md) | Any `browser-use` call against Amazon Ads or Coupons: preconditions, URLs, which marketplace layout you are on, field ranges, wedged-daemon recovery — and the index to the four files below. |
| [`mechanics-reading.md`](references/mechanics-reading.md) | Reading the console: campaign-list filters and `aria-rowcount`, bulk download, date pickers, Targeting / Search terms / Bid adjustments tabs, Brand Analytics. |
| [`mechanics-edit.md`](references/mechanics-edit.md) | Changing a live campaign by clicks: bids, pause, negatives, harvest, placement / strategy / budget, Sponsored Brands edits. |
| [`mechanics-create.md`](references/mechanics-create.md) | Creating a campaign or ad group in the UI (fallback to bulk), verifying the Product Ad, the bulk sheet schema. |
| [`mechanics-coupons.md`](references/mechanics-coupons.md) | Coupons and the promotions wizard. |

## Safety rails

- **Never apply a change the user has not approved.** An audit ends with
  `CHANGES.tsv` and the report; execution happens only in an `execute`
  phase, on rows the user approved.
- **Derive thresholds from this store's data and notes.** Defaults are in
  [`data-contract.md`](references/data-contract.md); a store overrides
  them with `key: value` lines in `stores/<slug>/notes.md`. When the user
  states a different standard, write it there with
  `vibe_seller_write_workspace_file` so later runs use it.
- **Check the field's valid range before proposing a value** (e.g.
  placement modifiers are 0% to +900%, increase-only).
- **Protect what drives orders** — see
  [`tuning-recommendation-format.md`](references/tuning-recommendation-format.md)
  § PROTECT.
- **Per-run captures go to your task directory (CWD)**, never into `stores/` or
  `knowledge/` (`amazon-shared` §5).

## Default analysis window

Last 30 days, unless the user names another window. Pin the same window
on every page you read — campaign list, ad groups, Targeting, Search
terms, Bid adjustments — because each page has its own date picker and
defaults drift. Misaligned windows make layers that should add up
disagree, and lead to wrong recommendations.

## What this skill is not

- Not for other marketplaces — noon has `noon-ads`.
- Sponsored Brands tuning is partially verified; Sponsored Brands Video
  and Sponsored Display are listed-only (see
  [`tuning-campaign-types.md`](references/tuning-campaign-types.md)).
  For inferred types, say so in the report and prefer conservative,
  reversible actions.
