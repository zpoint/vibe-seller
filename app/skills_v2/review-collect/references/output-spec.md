# Review-collect OUTPUT SPEC — the `reviews/v1` data contract

This is the single definition of "done". The skill writes to it; the
server gates check against it at `set_task_result`:

- `review_completeness_review` (soft, converges over rounds) returns the
  list of combos still under-collected + product files missing/malformed.
- `review_output_gate` (hard backstop) refuses the result if any
  enumerated product file is missing/malformed, so the downstream
  consumer never reads a half-finished run.

**Partial is accepted each round** — fix what the reviewer reports and
re-submit; the dataset converges. The contract is intentionally generic
and versioned (`reviews/v1`): no downstream-consumer concepts leak in.

## File layout

```
reviews/_MANIFEST.json                       # run index (task-local)
reviews/<platform>/<country>/<product_id>.json  # one per product
```

Paths are **task-local** — relative to your working dir, i.e.
`~/.vibe-seller/tasks/<task_id>/reviews/`. This is per-run scratch that
`retry` wipes, so every run starts from an empty `reviews/` dir; never
write review files to the shared `store-data/` tree.

- `platform` ∈ `amazon`, `noon`. `country` is the lowercase marketplace code
  (e.g. `us`, `eg`, `uk`).
- For `platform=amazon`, `product_id` = the **ASIN** (uppercase, e.g.
  `B0EXAMPLE1`); no extra identity field is needed.
- For `platform=noon`, `product_id` = the **noon product id** and the
  JSON MUST also carry a top-level **`seller_sku`** — noon's OWN
  seller/partner SKU for that listing (each colour / size variant has a
  distinct one). That is noon's native identity, and the rating you write
  is **noon's rating for that noon product** — never mapped onto an Amazon
  ASIN. This skill is platform-agnostic: it does not know or care how a
  private consumer later relates noon and Amazon data, so do **not** emit
  an `asin` for noon, and never let two different noon products collapse
  onto one identity (each variant is its own product with its own rating).

Write every review file with the built-in **Write** tool to the
task-local `reviews/...` path above. Do **not** use
`vibe_seller_write_workspace_file` for review files — that targets the
shared workspace; review dumps are per-run task-local data. (The
symlink-write caveat only applies to the shared `stores/`, `knowledge/`,
`store-data/` trees, which `reviews/` is not.)

## `<product_id>.json` — required shape

```json
{
  "schema": "reviews/v1",
  "store_slug": "example-store",
  "platform": "amazon",
  "country": "us",
  "product_id": "B0EXAMPLE1",
  "asin": "B0EXAMPLE1",
  "collected_at": "2026-06-17T09:12:00Z",
  "rating": 4.1,
  "rating_count": 218,
  "sort": "recent",
  "review_pages_fetched": 17,
  "reviews": [
    {
      "id": "R3ABC...",
      "author": "Ahmed",
      "rating": 2,
      "date": "2026-05-12",
      "title": "Stopped working after a week",
      "body": "…",
      "verified": true,
      "variant": "Black / L"
    }
  ]
}
```

**The gates REQUIRE these, per file** (a file missing/violating any is
counted missing/malformed and named in the diff):

1. `rating` — the product's current overall rating **read off the page
   this run**, a **non-null number** (e.g. `4.1`). A product page with no
   rating yet must still carry a number — use `0` and set
   `rating_count: 0` (not `null`).
2. `reviews` — an **array** (may be empty `[]` for a product with a
   rating but no written reviews; never omit the key).
3. `collected_at` — a truthy ISO-8601 UTC timestamp of when this file
   was collected. It must be from **this run**: the gate treats a file
   whose `collected_at` predates the run's start as **stale** (= not
   collected) and denies it. You cannot pass by preserving a previous
   run's files — re-open the page and read the current rating.
4. `seller_sku` (**noon only**) — noon's own seller/partner SKU for the
   product, its native per-variant identity. Amazon files don't need it
   (`product_id` is the ASIN).

The other keys are part of the contract and should be filled, but the
gates key on the three above:

- `id`: the platform review id if exposed; else a **stable hash of
  `author|date|title`** so re-runs dedup deterministically. The
  downstream consumer dedups on `(product_id, country, id)`.
- `rating` per review is the integer stars (1–5).
- `verified`, `variant`: best-effort; omit a key you genuinely can't read
  rather than guessing.
- `review_pages_fetched`: how many review pages you paged through —
  records where Amazon/noon caps history (accept the cap, record it).

## `_MANIFEST.json` — the run index the reviewer parses

```json
{
  "schema": "reviews/v1",
  "store_slug": "example-store",
  "collected_at": "2026-06-17T09:12:00Z",
  "combos": [
    {
      "platform": "amazon",
      "country": "us",
      "expected": ["B0EXAMPLE1", "B0AAA11111", "B0BBB22222"],
      "collected": ["B0EXAMPLE1", "B0AAA11111"],
      "reviews": 437,
      "pages": 31
    },
    {
      "platform": "noon",
      "country": "us",
      "expected": ["N12345"],
      "collected": ["N12345"],
      "reviews": 12,
      "pages": 2
    }
  ]
}
```

- `expected` — the **full enumerated universe** for that combo from
  Step 1 (Amazon: every ASIN in the All Listings Report; noon: every
  product id in the catalog). This is the analog of ad-audit's `active`
  count — record it honestly; under-reporting it is the failure the gate
  closes. The reviewer denies until `collected` covers `expected`.
- `collected` — the product ids for which a well-formed `<product_id>.json`
  exists. Add an id here **after** its JSON is written.
- `reviews` / `pages` — running totals for the combo (informational).

The reviewer cross-checks: for every id in `expected`, the file
`<platform>/<country>/<id>.json` must exist and carry the three required
keys. A `collected` shorter than `expected`, or any expected file
missing/malformed, is a named gap.

## `REVIEW_COLLECT_<date>.md` — the result cover

A short Markdown summary in the **task dir** (not the store dir), passed
to `vibe_seller_set_task_result`. One `## <Platform> <Country>` section
per combo with its totals and a progress line, then a one-paragraph
summary:

```
# 评论采集 — example-store — 2026-06-17

## Amazon US
**进度**: collected 42/42 products (437 reviews, 31 pages)

## noon EG
**进度**: collected 1/1 products (12 reviews, 2 pages)

## 汇总
共采集 43 个商品、449 条评论。最低评分商品：B0XXX (2.7★, 18 条)…
```

The MD is a cover for humans; the gates validate the JSON on disk, so a
fabricated MD over a half-finished dump is still denied — collect the
files, don't pad the report.

## Workspace hygiene

The JSON dataset lives in the task-local `reviews/` dir alongside the
`REVIEW_COLLECT_<date>.md` report. Throwaway scripts → `/tmp/`, never the
task dir. Remove scratch files before the final `set_task_result`.
