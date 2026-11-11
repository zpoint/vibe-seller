---
name: review-collect
description: "Collects product ratings and full customer-review history from Amazon and noon for every product in a store's catalog, and writes them as versioned JSON (reviews/v1: one file per product plus a _MANIFEST.json index) under the task-local reviews/ dir. Read-only: it never posts, replies to, edits or deletes anything. Use for tasks that collect, gather or refresh product reviews or ratings, build a review dataset, or audit customer ratings across a store. Collects full history every run (dedup by review id) for the (platform, country) combos in the store's metadata.json."
allowed-tools: Bash(browser-use:*)
requires: [amazon-shared]
gates: [review_completeness_review, review_output_gate]
---

# Review Collect

This skill **collects**; it does not analyse or recommend. The output is
a machine-readable dataset that a downstream sync reads and upserts into
a production database, so the job is to make it complete and correct for
every product the store sells.

Load the `amazon-shared` skill (Skill tool) for Amazon marketplaces,
sign-in and navigation, and `noon-shared` for noon.

## What you produce

All paths are relative to the task CWD (`reviews/` is task-local run
output, not shared workspace data):

- `reviews/<platform>/<country>/<product_id>.json` — one per product: the
  current rating read off the page this run plus the full review history.
- `reviews/_MANIFEST.json` — per `(platform, country)`, the enumerated
  `expected` product set and the `collected` set with files written.
- `./REVIEW_COLLECT_<YYYY-MM-DD>.md` — a short cover (per-combo totals and
  progress). Pass its path as `result_file` to `vibe_seller_set_task_result`.

Write all of these with the built-in **Write** tool. `reviews/` is inside
your CWD, not behind the `stores/` symlink, so
`vibe_seller_write_workspace_file` is the wrong tool here.

The exact shapes are in [`references/output-spec.md`](references/output-spec.md);
the procedure, selectors and parallelism are in
[`references/collect-quickref.md`](references/collect-quickref.md). Read
both before starting.

## Read-only

You open and read review pages. You never post, reply to, vote on or
report a review, edit a listing, or change anything on Amazon or noon. If
the task asks you to respond to reviews, say that is out of scope.

## Scope

Collect every `(platform, country)` listed in
`stores/<slug>/metadata.json` → `platforms.{amazon,noon}` unless the task
narrows it. That file, not the DB `countries` column, is current.

## Enumerate first, then collect

| Platform | product universe (`expected`) | reviews (newest first) |
|---|---|---|
| **amazon** | All Listings Report per country (load the `amazon-reports` skill) → every ASIN | `https://www.amazon.<tld>/product-reviews/<asin>/?sortBy=recent`, paged to the end, plus a `&filterByStar=one_star,two_star` sweep. `product_id` = ASIN. |
| **noon** | Seller Center catalog per country (load the `noon-listing` skill), every page → product ids, and each product's `seller_sku` in the same pass | the product page's reviews section, sorted by date. `product_id` = noon product id. |

Some marketplaces render in Arabic or Spanish; extract by structure
(stars, dates, counts), not by English labels.

## Ratings are read, never inferred

- Each file's `rating` and `rating_count` come from that product's own
  rating widget on the page you opened this run — not from a sibling
  variant, another country, a previous run, or a file found elsewhere on
  disk. Whether noon shares ratings across variants or countries changes
  over time, so copying one across silently ships a wrong number.
- **A page you couldn't read is a gap, not a zero.** Write no file for it
  and leave it out of `collected`. A blank `rating: 0` file looks exactly
  like a genuine zero and overwrites a real rating downstream. A genuine
  zero (the right product rendered with no rating summary) is
  `rating: 0, rating_count: 0, no_ratings: true`.
- **noon's identity is noon's own:** `product_id` is the noon product id
  and every noon file carries that variant's `seller_sku`. Don't resolve
  noon products to Amazon ASINs, and never let two noon variants share
  one identity.
- noon's header shows "N Ratings" and "M reviews": `rating_count` = N;
  the `reviews` array holds the M written reviews. If the page shows
  M > 0 but you extracted none, the extraction failed — retry.

## Finishing

Write each product's file and update the manifest before moving on —
the files on disk, not your memory, are the record. When you set the
result, the server checks `reviews/` against the manifest: for every
`expected` id there must be a valid file (non-null `rating`, a `reviews`
array, `collected_at`, and `seller_sku` for noon). If anything is short
it replies with the missing combos and malformed files; keep the files
you have, collect just those, and set the result again. Partial progress
is fine between rounds; the task is done when every enumerated product
is collected.

Afterwards, write today's ISO date to `last_review_collect_date` with
`vibe_seller_set_schedule_state`. It is for cross-run reporting only and
never skips a product — every run collects full history.
