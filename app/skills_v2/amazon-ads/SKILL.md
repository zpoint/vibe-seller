---
name: amazon-ads
description: "Amazon Sponsored Products / Sponsored Brands / Sponsored Display ads + Coupons on Seller Central / advertising console. ONE catalog covering BOTH mechanics (URLs, click paths, modal patterns, kat-* component gotchas, field input ranges) AND workflows (tuning existing campaigns, weekly review, search-term harvest, ACOS improvement). Load this skill BEFORE any browser-use action that creates, edits, captures, archives, or downloads campaigns / ad-groups / keywords / product targets / coupons on amazon.<tld> or advertising.amazon.<tld>. The catalog below points to topical references — load whichever ones the task needs. Defaults to last 30 days for tuning analysis but accepts any user-specified window."
allowed-tools: Bash(browser-use:*)
requires: [amazon-shared]
gates: [ad_completeness_review, ad_negation_allowlist, ad_execution_fidelity]
---

# Amazon Ads — Catalog

> **PREREQUISITE:** Read `../amazon-shared/SKILL.md` for marketplace
> TLD map, version-aware navigation (New Seller Central vs classic;
> navigate by direct URL), the Ziniao login challenge-loop (password /
> OTP / hosted-passkey),
> and the ad-console vs seller-central account caveat.

This skill is a **catalog**. The actual content lives in topical
references in `references/`. Load whichever ones apply to the task.

## When something doesn't fit the recipe — recover, don't stall

The references below are worked examples, not the full space of what
you'll hit. Amazon's UI, campaign types, and account states vary; a step
that assumes one shape will sometimes meet another. **A gap in these
docs is not a stop sign — it's a cue to reason from first principles.**
You are a capable agent; the recipe is a starting point, not a cage.
When a documented step doesn't fit, do NOT hand a half-finished task
back to the user ("please do X manually"). Work the problem:

- **The export is ground truth; the live UI is fragile.** For anything
  data-heavy — reading campaigns, keywords, search terms, deciding what
  to negate — use the **bulk export** (or a Search-Terms CSV), never the
  on-screen grid. Amazon's grids are **virtualized** (only ~13 of
  hundreds of rows exist in the DOM; `innerText` is undefined
  mid-scroll). Scraping them is the single most common way a run wastes
  itself. If you catch yourself scrolling a grid and re-reading, stop —
  export instead.

- **If a command/recipe doesn't cover your case, build it from the
  export's own structure.** `ads_bulk.py` has `inspect` / `clone-campaign`
  / `bid-update` / `negate` / `archive-campaign` — but if your case
  isn't one of them (a campaign type, an entity, a field with no
  helper), open the export, find a row of the kind you need (a working
  example of exactly this on this account), and emit the same columns
  with your values changed. The export **teaches you the exact tokens**
  this account+locale accepts — you never have to invent them. (Worked:
  an SB-video negative isn't in the SP script, so read an SB row and
  build the SB negative sheet from it.)

- **Poll async jobs in separate, short calls — never one long silent
  sleep, and never block on a Monitor for browser/job state.** A bulk
  export or upload takes minutes; check it with a short poll (a few
  `sleep 20` + re-open), returning to the loop between checks so
  progress stays observable. A single 15-minute `sleep` — or blocking a
  `Monitor`/`TaskOutput` waiting for a page/job event — looks like a hang
  and strands the run.

- **Verify by re-reading ground truth, not by trusting "Success".** A
  bulk `Success` status only means the file was accepted; re-export (or
  re-read the campaign) and confirm the committed state — the campaign
  is `enabled`, the Product Ad advertises the *intended* SKU (a cloned
  campaign renamed to the new product can still point at the old SKU),
  the negatives are present.

- **Never exit-in-the-half.** If you're blocked, restate the invariant
  ("I must end this campaign as `enabled` advertising SKU X"), find the
  ground-truth source, and take the next concrete step toward it. Ask a
  human only when a credential/permission is genuinely missing, not when
  a recipe merely ran out.

The references are the map; these principles are how you travel when the
map ends.

## What this skill produces

For tuning / audit tasks ("review the ads", "improve ACOS", "audit"):
**one Markdown report** (`AD_AUDIT_<YYYY-MM-DD>.md`) + **two TSVs per
active campaign** (`stores/<slug>/ads/<platform>/<country>/<id>.tsv`
targets + `<id>.searchterms.tsv` full customer-query set). Every
campaign is drilled in TWO layers on the same date window — targets AND
search terms — proven by a `搜索词对账` reconciliation line the server
reviewer parses.

**START HERE — do NOT pre-read every reference (it buries the model and
causes shortcutting). Just two files, then run:**

1. **[`output-spec.md`](references/output-spec.md)** — the report
   contract (what "done" looks like).
2. **[`audit-quickref.md`](references/audit-quickref.md)** — the entire
   procedure on one page. Load a heavy reference only when a step there
   tells you to.

Then write the report and call
`vibe_seller_set_task_result("./AD_AUDIT_<date>.md")`. The server's
**completeness reviewer** replies with a short "what's still missing"
list (under-drilled countries + bid-rule violations) and converges over
rounds — **partial is accepted each round**, just fix the top gaps and
re-submit until it returns nothing. No separate reviewer subagent or
Stop-hook needed.

## Workflow references — the "what to do" thinking

| Reference | Load when |
|---|---|
| [`output-spec.md`](references/output-spec.md) | **Read first for every audit.** The report contract the server completeness reviewer checks against — per-(platform,country) 进度 line, header table, per-campaign drills, the 4 bid rules, TSV-per-campaign. |
| [`audit-quickref.md`](references/audit-quickref.md) | **The procedure, one page.** Run this top-to-bottom; it points to heavy refs on demand. |
| [`format-anchor.md`](references/format-anchor.md) | _Legacy detail._ Per-campaign table column shape; load only if you need the exact table layout. (The mandatory subagent reviewer-loop is superseded by the server completeness reviewer — partial is accepted, it lists gaps each round.) |
| [`reviewer-loop.md`](references/reviewer-loop.md) | Phase-4 **execution review** only (`EXEC_REVIEW_*`, Stop-hook enforced). For audit reports its Phase-3 format loop is superseded by the server completeness reviewer — don't spawn a review subagent for amazon/noon audits. |
| [`tuning-workflow.md`](references/tuning-workflow.md) | User asks to tune ads, improve ACOS, "review last month's ads", harvest search terms, lower bids on losers, weekly ad review, "why is X campaign burning money", or any ongoing-campaign refinement task. |
| [`tuning-campaign-types.md`](references/tuning-campaign-types.md) | A campaign isn't SP-Manual-Keyword. The skill defaults to SP-Manual-Keyword; for SP-Auto / SP-Manual-Product / Sponsored Brands / Sponsored Brands Video / Sponsored Display, this reference has the per-type sidebar tabs, Targeting-tab columns, and lever-applicability matrix observed on a live merchant account. Pair with `tuning-workflow.md` Phase 3 — that phase branches on type. |
| [`tuning-thresholds.md`](references/tuning-thresholds.md) | Need to derive per-store thresholds (breakeven ACOS = margin %, target ACOS = 0.7 × breakeven, protect-zone, waste/harvest cutoffs). Always heuristic, never hardcoded. |
| [`tuning-toolbox.md`](references/tuning-toolbox.md) | Picking the right lever — 8 levers + 2 advanced (dayparting, structural splits) disabled by default. Ordered surgical-first (search-term negate / harvest, per-keyword bid trim) → blanket-last (bidding strategy, pause campaign). For which levers apply per type, see `tuning-campaign-types.md`. |
| [`tuning-funnel-diagnosis.md`](references/tuning-funnel-diagnosis.md) | Distinguishing listing-side problems (low CTR = image / title; low CVR = PDP / price / reviews) from ad-side problems (ACOS) before reaching for a bid lever. Bad CTR is not an ad-tuning problem. |
| [`tuning-recommendation-format.md`](references/tuning-recommendation-format.md) | Composing the per-campaign output table at the end of a tuning session — header table → per-campaign data → per-problem subsections with per-entity data tables. Targeting-first, placement-second. Data table shape varies by type — see `tuning-campaign-types.md`. |
| [`tuning-history.md`](references/tuning-history.md) | The per-campaign TSV under git that records every observed state (bid, status, suggested range, recent metrics) across audits. Read at Phase 2 to diff scrape against record (catches OBSERVED_DRIFT); read at Phase 3 for recency check before recommending changes (< 7 days since last change → downgrade to Hold); written at Phase 4 after each verified apply (cause-and-effect ledger). One TSV per campaign, written via `vibe_seller_write_workspace_file`; the workspace auto-commits. |

## Mechanics reference — the "how to click" lookup

| Reference | Load when |
|---|---|
| [`bulk-operations.md`](references/bulk-operations.md) | **DEFAULT for creating a campaign or applying bids across keywords/campaigns.** Export → edit with `scripts/ads_bulk.py` → import → verify. Locale-general (positional 52-col schema, template-cloned headers) and guards the ASIN-as-SKU trap in code. The click paths below are the **fallback** — a single tweak, a field with no bulk column, or two failed imports. |
| [`mechanics.md`](references/mechanics.md) | Any time you're about to issue a `browser-use` call against Amazon Ads or Coupons. Sections: § 0 preconditions, § 1 URLs, § 2 reading existing campaigns, § 3 creating a new campaign, § 4 bulk download / upload, § 5 coupons, § 6 wedged-daemon recovery, § 7 per-store conventions, § 8 reading playbook for tuning (Bid Adjustments date-range alignment rule, Search terms tab, bidding-strategy edit, daily-budget edit), § 9 scope. **§3 (UI create) and §4a0 (ag-Grid edits) are the fallback to `bulk-operations.md`.** |

## Safety rails

- **Never auto-execute** any tuning change. Output is always a
  recommendations table; the user confirms each row before any
  click that modifies state.
- **Derive thresholds from this store's data**, not from absolute
  numbers. Computed, not hardcoded.
- **Verify the lever before recommending it.** Every numeric
  recommendation specifies field's current value, proposed value,
  valid range, direction. Never recommend a value the field will
  reject (e.g. negative placement modifiers — Amazon SP only
  allows 0% to +900%, increase-only).
- **Don't kill the goose.** Order-driving keywords / campaigns get
  tagged PROTECT regardless of ACOS. Surface, never auto-cut.
- **Per-run captures → `/tmp/<run-slug>/`** (per `amazon-shared § 5`).

## Default analysis window

**Last 30 days** by default. The user may override with any window
("last 14 days", "March 1 – March 31", "year to date", custom calendar
dates). When set, **pin the same window across every page in the
session** — campaign top-tile, ad-group list, Bid Adjustments,
Search terms, Targeting. Each of those pages has its own independent
date picker; defaults drift. Misaligned dates cause the per-placement
breakdown to not sum to the campaign top-tile and lead to wrong
recommendations.

## What this skill is NOT

- Not for new-campaign creation as a workflow → that's the separate
  `new-product-launch` skill (which uses this skill's mechanics
  reference for click paths).
- Not for non-Amazon marketplaces — different platforms have
  different UI / mechanics; document those in their own skills.
- Sponsored Brands tuning is **partially verified** (campaign +
  ad-group + Targeting tab observed; Bid adjustments tab present
  with "New" badge but cell semantics not yet drilled). Sponsored
  Brands Video and Sponsored Display are **listed-only** — the
  per-type playbook in `tuning-campaign-types.md` calls out which
  rows are verified vs inferred. For inferred-only types, mark
  Confidence accordingly and bias to conservative reversible actions.
