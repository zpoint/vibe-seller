---
name: amazon-ads
description: "Amazon Sponsored Products / Sponsored Brands / Sponsored Display ads + Coupons on Seller Central / advertising console. ONE catalog covering BOTH mechanics (URLs, click paths, modal patterns, kat-* component gotchas, field input ranges) AND workflows (tuning existing campaigns, weekly review, search-term harvest, ACOS improvement). Load this skill BEFORE any browser-use action that creates, edits, captures, archives, or downloads campaigns / ad-groups / keywords / product targets / coupons on amazon.<tld> or advertising.amazon.<tld>. The catalog below points to topical references — load whichever ones the task needs. Defaults to last 30 days for tuning analysis but accepts any user-specified window."
allowed-tools: Bash(browser-use:*)
---

# Amazon Ads — Catalog

> **PREREQUISITE:** Read `../amazon-shared/SKILL.md` for marketplace
> TLD map, hamburger-menu navigation, sign-in / Ziniao / OTP handling,
> and the ad-console vs seller-central account caveat.

This skill is a **catalog**. The actual content lives in topical
references in `references/`. Load whichever ones apply to the task.

## What this skill produces

For tuning tasks ("review the ads", "improve ACOS", "audit"): one
**audit report** covering **every campaign** in the analysis window,
and — on user follow-up — execution of approved actions against
that report. The audit and follow-up are split into 4 phases
specified in `tuning-workflow.md`:

```
Phase 1: Discover  →  manifest of every campaign + mechanical state
Phase 2: Drill     →  per-campaign data section (active campaigns)
Phase 3: Compose   →  full report (header + sections + checklist)
Phase 4: Apply     →  on user reply: execute approved rows OR re-emit
```

Phase 1–3 are read-only. Phase 4 is the only phase that clicks
state-modifying buttons, and only after explicit per-row user
approval. Every campaign appears in the report (active = full
section; inactive = one line citing the mechanical reason). There
is no "skipped because tight" — see [`tuning-workflow.md`
"Mechanical state taxonomy"](references/tuning-workflow.md#mechanical-state-taxonomy).

## Workflow references — the "what to do" thinking

| Reference | Load when |
|---|---|
| [`tuning-workflow.md`](references/tuning-workflow.md) | User asks to tune ads, improve ACOS, "review last month's ads", harvest search terms, lower bids on losers, weekly ad review, "why is X campaign burning money", or any ongoing-campaign refinement task. |
| [`tuning-campaign-types.md`](references/tuning-campaign-types.md) | A campaign isn't SP-Manual-Keyword. The skill defaults to SP-Manual-Keyword; for SP-Auto / SP-Manual-Product / Sponsored Brands / Sponsored Brands Video / Sponsored Display, this reference has the per-type sidebar tabs, Targeting-tab columns, and lever-applicability matrix observed on a live merchant account. Pair with `tuning-workflow.md` Phase 3 — that phase branches on type. |
| [`tuning-thresholds.md`](references/tuning-thresholds.md) | Need to derive per-store thresholds (breakeven ACOS = margin %, target ACOS = 0.7 × breakeven, protect-zone, waste/harvest cutoffs). Always heuristic, never hardcoded. |
| [`tuning-toolbox.md`](references/tuning-toolbox.md) | Picking the right lever — 8 levers + 2 advanced (dayparting, structural splits) disabled by default. Ordered surgical-first (search-term negate / harvest, per-keyword bid trim) → blanket-last (bidding strategy, pause campaign). For which levers apply per type, see `tuning-campaign-types.md`. |
| [`tuning-funnel-diagnosis.md`](references/tuning-funnel-diagnosis.md) | Distinguishing listing-side problems (low CTR = image / title; low CVR = PDP / price / reviews) from ad-side problems (ACOS) before reaching for a bid lever. Bad CTR is not an ad-tuning problem. |
| [`tuning-recommendation-format.md`](references/tuning-recommendation-format.md) | Composing the per-campaign output table at the end of a tuning session — header table → per-campaign data → per-problem subsections with per-entity data tables. Targeting-first, placement-second. Data table shape varies by type — see `tuning-campaign-types.md`. |

## Mechanics reference — the "how to click" lookup

| Reference | Load when |
|---|---|
| [`mechanics.md`](references/mechanics.md) | Any time you're about to issue a `browser-use` call against Amazon Ads or Coupons. Sections: § 0 preconditions, § 1 URLs, § 2 reading existing campaigns, § 3 creating a new campaign, § 4 bulk download / upload, § 5 coupons, § 6 wedged-daemon recovery, § 7 per-store conventions, § 8 reading playbook for tuning (Bid Adjustments date-range alignment rule, Search terms tab, bidding-strategy edit, daily-budget edit), § 9 scope. |

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
