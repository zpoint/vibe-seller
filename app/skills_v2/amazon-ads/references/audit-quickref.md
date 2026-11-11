# Ad audit — the procedure

Shared by `amazon-ads` and `noon-ads`. File names, paths and columns
are defined once in [`data-contract.md`](data-contract.md); this page
says how to produce them. Load other references only when a step points
to them.

1. [Declare, then read the menu](#1-declare-then-read-the-menu)
2. [Pin the window](#2-pin-the-window)
3. [Enumerate every campaign → CAMPAIGNS.tsv](#3-enumerate-every-campaign--campaignstsv)
4. [Drill each live campaign → two TSVs](#4-drill-each-live-campaign--two-tsvs)
5. [Decide → CHANGES.tsv](#5-decide--changestsv)
6. [Write the report and submit](#6-write-the-report-and-submit)
7. [PDF copy](#7-pdf-copy)

## 1. Declare, then read the menu

Call `vibe_seller_declare_ad_task` first (`amazon-ads` SKILL §0 explains
kind and scope). The server then writes `./AUDIT_TARGETS.json`:
`{"combos": [{"platform": "amazon", "country": "SA"}, …]}`, every
marketplace the store is configured for. Your declaration picks what you
owe from that menu: the combos you declared, or all of them if you
declared none. Stay inside the declaration even when the menu is longer —
auditing marketplaces the user didn't ask about is work nobody wanted. If
you think the scope is wrong, say so in the result.

An empty menu (`"combos": []`) means the store has no marketplace
configured. Audit the one marketplace the console shows (its switcher,
else its currency) under this skill's platform. Leave store settings and
`metadata.json` alone.

## 2. Pin the window

Default: last 30 days, unless the user named another. Write down the
exact start and end dates and use them on every page and export; they go
into `window_start` / `window_end`.

Check an export's window before trusting its metrics. Amazon's bulk file
is named `bulk-<entity>-<START>-<END>-<epoch>.xlsx`, and Campaign
Manager's date picker defaults to two days. On a two-day window sales
haven't attributed yet, so spend with zero sales looks like ACOS 0 —
that is "no data yet", not "great". Generate a fresh export when the file
doesn't span your window.

## 3. Enumerate every campaign → CAMPAIGNS.tsv

Get the full list for each owed marketplace before drilling anything.

**Amazon.** Open Campaign Manager through the in-page menu. Clear the
campaign-search box with a real click on its ✕ and clear the status
filter (`mechanics-reading.md` §1), then note the grid's `aria-rowcount` — the
account's true total. Enumerate from the Bulk Operations export, not the
grid or its inline Export button (`mechanics-reading.md` §2). Reuse the newest
existing `bulk-*.xlsx` if its window covers yours; otherwise request a
fresh one with the search-term boxes ticked, so the same workbook carries
the `SP Search Term Report` / `SB Search Term Report` sheets you need in
step 4. `python3 .claude/skills/amazon-ads/scripts/ads_bulk.py scope
<export> --platform amazon --country <CC>` prints the enabled campaign
ids and their count — a quick cross-check that every live campaign has a
row. Per-country accounts need one export per country.

**noon.** The Campaigns tab is a lazy-loaded inner-scroll table. Read the
`Live N` / `All N` chip, scroll the list's own container until the
`/campaign/details/` link count stops growing and equals the chip, and
enumerate live — a leftover export under-counts. Selectors and snippet:
the `noon-ads` skill (campaign enumeration).

**Writing `CAMPAIGNS.tsv`** (task dir). One row per campaign, inactive
ones included, with the campaign's real name — the console shows one on
every row and the export carries `Campaign Name`; a reader can't act on
a bare id. `state` is the console's own state. `drill_status`:

- `drilled` — both per-campaign files written (step 4);
- `inactive` — not live in the window, not drilled;
- `blocked` — live but you could not read it; `note` says why.

Every live campaign ends as `drilled` or `blocked`. A paused campaign
that spent inside the window may be drilled too — its spend is part of
the period. A marketplace with no campaigns gets one row with an empty
`campaign_id` and a `note`. Write the file as you enumerate and update
`drill_status` as you go, so progress survives context compaction.

Auditing a subset on purpose (the declaration names campaigns) is fine:
list those campaigns.

## 4. Drill each live campaign → two TSVs

A drill is two layers on the same window: every target (keyword, product
target, auto-target group) and the full search-term set. Both files go
to `store-data/<slug>/ads/<platform>/<country>/` via
`vibe_seller_write_workspace_file`. Write them right after drilling each
campaign, before the next — a long audit gets compacted, and only what is
on disk survives.

How to fill the columns (paused targets included, `ad_group`,
`source_target`, zero-sales rows, missing clicks): [`output-spec.md`](output-spec.md)
§ Filling the TSVs. What to look at on each tab:
[`tuning-workflow.md`](tuning-workflow.md).

Sources, in order of preference:

- **Amazon:** the bulk export from step 3. Targets come from the keyword
  / product-targeting rows, search terms from the `SP/SB Search Term
  Report` sheets, both filtered by `Campaign ID` — one file for both
  layers means the windows match by construction. Fallback per campaign:
  the Search Terms page's Export CSV with the date range set first (the
  grid shows about 13 rows). Sponsored Display has no search-term
  report; write the header-only file and say so in the report.
- **noon:** the Targets tab (Manual) and the Customer Queries tab's
  Export (the on-page table shows only the top 15). Details in
  `noon-ads`.

**Check the layers add up before moving on.** Search terms are part of
the targeting spend. The server checks, on both platforms:

- search-term spend is at least `noon_reconcile_floor` (0.85) of the
  targeting total — below that is a gap;
- search-term spend is at most `1 + reconcile_tolerance` (1.15) times
  the targeting total — above that is a contradiction;
- the targeting total matches the campaign's spend in `CAMPAIGNS.tsv`
  within `reconcile_tolerance` of it (or 1.00, whichever is larger) — a
  larger difference is a contradiction.

What to do when they don't:

- Search terms short of the targeting total: the capture is incomplete or
  the windows differ — re-pin both and recapture (noon: use the Export).
- Search terms above the targeting total: impossible for one campaign.
  The usual cause is a `state=enabled` filter that dropped paused targets
  from the targeting layer; otherwise two campaigns' figures were joined
  under one id. Re-read both layers for the same `Campaign ID`.
- If it still won't reconcile, mark the campaign `blocked` with the
  numbers in `note`, and propose no bid changes for it.

**Before overwriting last run's file**, read it. Carry forward
`applied_*` values on rows you are not changing — they are the record of
what was done and drive the cooldown. If a target's bid or state differs
from last time with no `applied_*` record behind it, someone changed it
outside these runs; mention it in the report.

## 5. Decide → CHANGES.tsv

Apply [`tuning-recommendation-format.md`](tuning-recommendation-format.md)
to every drilled row, using the store's thresholds
([`tuning-thresholds.md`](tuning-thresholds.md), overrides in
`stores/<slug>/notes.md`). Write one `CHANGES.tsv` row (task dir) per
proposed change, with the row's `acos`, `roas` and actual `cpc` so the
reasoning can be checked. Add a `hold` row when the hold itself needs
explaining — a high-ROAS row you are not raising, or a target inside its
cooldown — with the reason. Columns and the store rules the server
checks: [`data-contract.md`](data-contract.md).

Mapping to `action`:

| Decision | `action` | Notes |
|---|---|---|
| raise / lower a target's bid | `raise` / `lower` | `target_bid` required |
| pause a target | `pause` | |
| negate a search term or product placement | `negate` | `target` = the term or ASIN; `match_type` = `exact` / `phrase` for keyword negatives, the placement type otherwise |
| keep, with a reason worth recording | `hold` | |

Proposals that don't fit a target row — harvesting a search term as a
new keyword, a budget or placement-modifier change, a bidding-strategy
switch, pausing a whole campaign — go in the report as a separate list
for the user to approve, not in `CHANGES.tsv`.

An `investigate` task writes no `CHANGES.tsv` rows other than `hold`.

## 6. Write the report and submit

Write `AD_AUDIT_<YYYY-MM-DD>.md` in the task dir, for the person
([`output-spec.md`](output-spec.md) § The report). Then call
`vibe_seller_set_task_result("./AD_AUDIT_<date>.md")` — the path, not a
summary.

The server reads the TSVs and replies with anything that doesn't hold up:
a live campaign without both files, a marketplace far smaller than its
recent history with no note, layers that don't add up, a change that
breaks a store rule. Fix the data (usually by drilling or recapturing),
update the report, and submit again. Gaps you genuinely can't close are
delivered as caveats; rule violations on `CHANGES.tsv` are about money
and have to be fixed.

Your CWD is the task dir. Keep its top level to the deliverables
(report, `CAMPAIGNS.tsv`, `CHANGES.tsv`, PDF); put scratch scripts and
captures in a subfolder of it. The per-campaign TSVs live under
`store-data/` ([`data-contract.md`](data-contract.md)).

## 7. PDF copy

Once the result is accepted, render a PDF for the user:

```bash
PY=python3   # the agent venv's python; install requirements.txt if `markdown` is missing
S=.claude/skills/amazon-ads/scripts/md_to_pdf.py
$PY "$S" ./AD_AUDIT_<date>.md            # writes AD_AUDIT_<date>.pdf next to it
```

It renders CJK and tables through headless Chrome with a throwaway
profile (safe while the store browser is up) and fails loudly on an
empty PDF. Run it once and mention the `.pdf` path in your result; the
`.md` is the deliverable, so don't iterate on the PDF's layout.
