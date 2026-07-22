---
name: review-collect
description: "Collect product ratings + full customer-review history from Amazon (Seller Central / storefront) and noon, for every product in a store's catalog, and dump them as generic versioned JSON under the task-local reviews/ dir. Read-only: NEVER posts, replies to, edits, or deletes anything on Amazon or noon — it only reads review pages. Load this skill for any task that says collect/gather/refresh product reviews or ratings, build a review dataset, or audit customer ratings across a store. Produces one JSON per product (reviews/v1 contract) + a _MANIFEST.json index the server completeness reviewer checks. Defaults to FULL review history every run (idempotent, dedup by review id); accepts a per-(platform,country) scope from the store's metadata.json."
allowed-tools: Bash(browser-use:*)
requires: [amazon-shared]
gates: [review_completeness_review, review_output_gate]
---

# Review Collect — Catalog

> **PREREQUISITE:** Read `../amazon-shared/SKILL.md` for the marketplace
> TLD map, hamburger-menu navigation, sign-in / Ziniao / OTP handling,
> and the ad-console vs seller-central account caveat. For noon, read
> `../noon-shared/SKILL.md` for login + page structure.

This skill **collects** — it does not analyze or recommend. The output
is a machine-readable dataset (one JSON per product + a manifest) that a
downstream consumer ingests. Your only job is to make that dataset
**complete and well-formed** for every product the store sells.

## What this skill produces

For every `(platform, country)` the store covers, and every product in
that combo's catalog:

- **One JSON per product**:
  `reviews/<platform>/<country>/<product_id>.json` (task-local; the
  `reviews/v1` contract — current rating + full review history).
- **One run index**:
  `reviews/_MANIFEST.json` — per combo, the enumerated
  `expected` product set and the `collected` set with files written. The
  server's **completeness reviewer** parses this to tell you what's still
  missing each round.
- **One short Markdown summary**: `./REVIEW_COLLECT_<YYYY-MM-DD>.md` in
  the task dir — combo totals + the manifest progress line. This is the
  result you pass to `vibe_seller_set_task_result`.

The JSON dumps are the deliverable; the MD is just a human-readable
cover. Write JSON via `vibe_seller_write_workspace_file` (the only tool
that writes through the `stores/<slug>` symlink).

## Safety — read-only

This is a **read-only** skill, exactly like the ad-audit Layer-1 collect
step. You **open and read** review pages. You **never**: post a review,
reply to a review, vote/report a review, edit a listing, or change
anything on Amazon or noon. If a task asks you to respond to reviews,
stop and say that is out of scope for `review-collect`.

## START HERE — two files, then run

Do NOT pre-read every reference (it buries the model and causes
shortcutting). Read just these two, then execute:

1. **[`output-spec.md`](references/output-spec.md)** — the data contract
   (the exact `reviews/v1` JSON + manifest shape "done" means). The
   server reviewer checks against it.
2. **[`collect-quickref.md`](references/collect-quickref.md)** — the
   entire procedure on one page (enumerate → drill each product's reviews
   newest-first → write JSON → update manifest → converge). Load a heavy
   reference only when a step there tells you to.

Then write the dumps and call
`vibe_seller_set_task_result("./REVIEW_COLLECT_<date>.md")`. The server's
completeness reviewer replies with a short "what's still missing" list
(combos under-collected + malformed product files) and converges over
rounds — **partial is accepted each round**, just fix the top gaps and
re-submit until it returns nothing.

## Scope — read it from metadata.json, not the DB

The combos to collect come from
`stores/<slug>/metadata.json` → `platforms.{amazon,noon}` (the
per-platform country lists). Do NOT trust the stale DB `countries`
column. Collect every `(platform, country)` listed there unless the task
narrows it.

## Two platforms, two DOMs

| Platform | enumerate the product universe | reviews page (sort newest-first) |
|---|---|---|
| **amazon** | All Listings Report (`../amazon-reports/SKILL.md`) → the ASIN universe per country | `https://www.amazon.<tld>/product-reviews/<asin>/?sortBy=recent` — page to the end; `&filterByStar=one_star,two_star` guarantees the bad-comment set is captured. `product_id` = **ASIN**. |
| **noon** | noon catalog (`../noon-listing/SKILL.md`) → product ids per country | noon product page → reviews section, sorted by date. `product_id` = **noon product id**. Verify the sort control during the run. |

TLD map: `../amazon-shared/SKILL.md` §1. Some marketplaces render the
DOM in a non-English locale (e.g. Arabic, Spanish) — extract by structure
(stars, dates, counts), not by matching English labels.

## noon specifics — read the real rating off each page

**Never infer a noon rating from a rule.** Whether a rating looks shared
across a product's colour/size variants (or across country sites) is not
fixed — noon changes it, and a baked-in "they share it, so copy it" rule
silently ships a wrong number. For every noon file you write: **open that
product's page, read the rating + rating count shown on it, and write
those exact on-page values.** Never copy a rating from a sibling variant,
another country, or a previous run — read it fresh off the page you are
writing.

- **noon's identity is noon's own — never Amazon's.** `product_id` is the
  noon product id; also emit **`seller_sku`** — noon's seller/partner SKU
  for that listing (each colour / size variant has a distinct one). Write
  noon's rating against that noon-native id. Do **not** resolve or emit an
  Amazon ASIN, and never let two different noon products collapse onto one
  identity — each variant is its own product with its own file and its own
  on-page rating. (How a private consumer later relates noon to Amazon is
  not this skill's concern.)
- If a product page won't load (or shows 0 when it clearly has ratings),
  record it as a gap and leave the prior file — never write a bad value
  and never backfill it from another page.
- The header shows TWO numbers: **"N Ratings"** (everyone who starred)
  and **"M reviews"** (those who also wrote text). Use the **ratings**
  number (N) as `rating_count`; collect the **M written reviews** into
  the `reviews` array. N and M differ — a product can have many ratings
  but few or zero written reviews.
- **Extract the review bodies — not just the summary rating.** Open the
  reviews section, page/scroll it, and read each written review's text,
  author, date, and star into a review object. If the page shows **M > 0
  written reviews but you extracted fewer (or zero)**, extraction FAILED
  (the bodies are on the page — read the review cards by structure,
  Arabic included): re-open the reviews section and retry. (When the page
  genuinely shows **0 written reviews**, an empty `reviews` array is
  correct even though `rating_count` is non-zero — don't retry forever.)

## Full history, parallel, idempotent

- **Full review history AND a fresh rating every run.** Sort newest-first
  and page through to the end. There is no early-stop cursor — re-runs
  page everything again and **dedup by `review id`** (stable hash of
  `author|date|title` when the platform exposes no id). The per-product
  JSON is the dedup store; writing it twice is a no-op upsert. **Re-read
  the current rating off the page every run** — never carry forward a
  previous run's `rating`/`collected_at`; the gate flags a file whose
  `collected_at` predates this run as stale (= not collected).
- **Parallelize** across products with a small concurrency cap (start at
  2, raise toward ~4–6 only if stable) to avoid anti-bot rate-limiting.
  See `collect-quickref.md` for the multi-window pattern and the
  self-heal note (browser-use can wedge a tab — the quickref covers
  recovery).
- A single cursor key `last_review_collect_date` (ISO date) is written to
  `schedule_state` via `vibe_seller_set_schedule_state` for cross-run
  reporting only — it does **not** gate collection.

## The converge loop (the server IS the reviewer)

You do not have to collect everything in one pass. Do your best, call
`vibe_seller_set_task_result("./REVIEW_COLLECT_<date>.md")`, and the
server's completeness reviewer replies with exactly which combos are
under-collected and which product JSONs are missing/malformed. Fix the
top gaps, re-submit. The dataset converges. **Missing is acceptable each
round** — only progress matters; a re-submit that collects nothing new
several rounds in a row is what stalls the gate.

The files you write **this run** persist across rounds — keep them and
each round open only the products still uncollected this run. But files
left by a **previous** run are stale: a new run re-reads every product's
page, it does not inherit the last run's ratings. Preserving old files
and re-reporting them as "collected" is exactly what the freshness gate
now rejects.
