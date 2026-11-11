# Review-collect output spec — the `reviews/v1` data contract

- [File layout](#file-layout)
- [`<product_id>.json`](#product_idjson)
- [`_MANIFEST.json`](#_manifestjson)
- [`REVIEW_COLLECT_<date>.md`](#review_collect_datemd)
- [What the server checks](#what-the-server-checks)

The contract is generic and versioned (`reviews/v1`): a downstream sync
walks these files and upserts ratings and reviews into a production
database, so a missing or wrong file silently drops or overwrites real
data there.

## File layout

```
reviews/_MANIFEST.json                          # run index
reviews/<platform>/<country>/<product_id>.json  # one per product
```

Paths are relative to the task CWD
(`~/.vibe-seller/tasks/<task_id>/reviews/`). A retry wipes the task
workspace, so every run starts from an empty `reviews/` and every file in
it was written this run. Write them with the built-in **Write** tool;
`vibe_seller_write_workspace_file` targets the shared `stores/` /
`knowledge/` / `store-data/` trees, which `reviews/` is not part of.

- `platform` ∈ `amazon`, `noon`. `country` is the lowercase marketplace
  code (`us`, `eg`, `uk`, …).
- `amazon`: `product_id` = the ASIN (uppercase, e.g. `B0EXAMPLE1`).
- `noon`: `product_id` = the noon product id, and the file also carries a
  top-level `seller_sku` — that variant's own seller/partner SKU (each
  colour/size has a distinct one). The rating is noon's rating for that
  noon product; don't emit an `asin` for noon, and never let two noon
  variants collapse onto one identity.

## `<product_id>.json`

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
  "rating_count": 200,
  "rating_histogram": {"5": 70, "4": 10, "3": 10, "2": 0, "1": 10},
  "sort": "recent",
  "review_pages_fetched": 17,
  "reviews": [
    {
      "id": "R3ABC...",
      "author": "A. Customer",
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

Required per file:

1. **`rating`** — a non-null number read from the product's own rating
   summary this run (selectors: `collect-quickref.md` § Step 2), never
   from the first star icon on the page: a product without ratings still
   renders review rows (e.g. from other marketplaces), and a page-wide
   star lookup returns one of those.
   - A product page that rendered as the right product and has no rating
     summary is a genuine zero: `rating: 0`, `rating_count: 0`,
     `no_ratings: true`. `no_ratings` is your statement that you checked;
     a `0` without it is rejected.
   - `rating > 0` with `rating_count: 0` is rejected — a rating nobody
     gave was read off something else.
   - **A page you could not read gets no file.** A timeout, block page,
     sign-in wall, empty render or wedged tab is a gap that the next round
     fills. A blank zero written in its place looks identical to a real
     zero and overwrites the product's real rating downstream. A run of
     blanks sharing one `collected_at` is the signature of a browser lost
     mid-sweep, not of real data.
2. **`reviews`** — an array, `[]` when the product has ratings but no
   written reviews. Never omit the key.
3. **`collected_at`** — ISO-8601 UTC time this file was read.
4. **`seller_sku`** — noon only.

Fill the rest as well:

- `rating_count` — the ratings count (on noon, "N Ratings", not "M
  reviews").
- `rating_histogram` — percent per star as the page shows it, keys
  `"5"`…`"1"`, integers 0–100. Omit it where the page has none; when
  present its shape is checked.
- review `id` — the platform's id if exposed, else a stable hash of
  `author|date|title`; consumers dedup on `(product_id, country, id)`.
- review `rating` — integer stars 1–5. `verified`, `variant` —
  best-effort; omit a key you can't read rather than guessing.
- `review_pages_fetched` — pages walked, which records where the
  platform caps history.

## `_MANIFEST.json`

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
      "reviews": 400,
      "pages": 30
    },
    {
      "platform": "noon",
      "country": "ae",
      "expected": ["N12345"],
      "collected": ["N12345"],
      "reviews": 10,
      "pages": 2
    }
  ]
}
```

- `expected` — the full enumerated universe for the combo (Amazon: every
  ASIN in the All Listings Report; noon: every catalog product id).
  Record it honestly: it is what completeness is measured against.
- `collected` — ids whose well-formed file exists. Add an id after its
  file is written.
- `reviews` / `pages` — running totals, informational.

## `REVIEW_COLLECT_<date>.md`

A short cover in the task CWD, passed to `vibe_seller_set_task_result`:
one `## <Platform> <Country>` section per combo with its progress line,
then a short summary.

```
# 评论采集 — example-store — 2026-06-17

## Amazon US
**进度**: collected 40/40 products (400 reviews, 30 pages)

## noon AE
**进度**: collected 1/1 products (10 reviews, 2 pages)

## 汇总
共采集 41 个商品、410 条评论。最低评分商品：B0EXAMPLE1 (2.7★, 20 条)…
```

## What the server checks

At `vibe_seller_set_task_result` the server reads `reviews/` from disk —
not the cover — and, for every id in each combo's `expected`, requires
`<platform>/<country>/<id>.json` to exist and satisfy the required keys
above. If `_MANIFEST.json` is missing, a combo's `collected` is short of
`expected`, or a file is missing or malformed, it replies with the named
gaps. Keep the files you have, collect only what is listed, and set the
result again. Partial progress is accepted between rounds; the run is
complete when every enumerated product has a valid file.

Keep throwaway scripts out of `reviews/`; remove scratch files from the
task CWD before the final result.
