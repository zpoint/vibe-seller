# Review-collect QUICKREF — the whole procedure on one page

Follow top to bottom. Load a heavy reference **only when a step says
to**. The contract for the finished dataset is
[`output-spec.md`](output-spec.md) — read that first, then run this.

## Loop shape (important)

You do NOT have to collect everything in one pass. Do your best, call
`vibe_seller_set_task_result("./REVIEW_COLLECT_<date>.md")`, and the
server's completeness reviewer replies with a short **"what's still
missing"** list. Fix the top gaps, write the files, call set_task_result
again. Repeat — the dataset converges. Missing is acceptable each round;
only *progress* matters.

## Step 0 — scope

Read `stores/<slug>/metadata.json` → `platforms.{amazon,noon}`. Collect
every `(platform, country)` it lists (e.g. amazon <cc1>/<cc2> + noon <cc1>/<cc2>; or
just amazon MX for an MX-only store) unless the task narrows it. Note the
`<slug>` — every output path uses it.

## Step 1 — enumerate the product universe (per platform, country)

Completeness is the #1 thing the reviewer checks. Get the FULL product
set per combo BEFORE collecting reviews, and write it into the manifest's
`expected`.

- **Amazon**: pull the **All Listings Report** (load
  `../amazon-reports/SKILL.md` for the hamburger-menu report nav +
  download flow). Parse it for every ASIN sold in that country. That ASIN
  set is `expected` for `(amazon, <country>)`.
- **noon**: open the noon catalog (load `../noon-listing/SKILL.md`) and
  union every product id across all catalog pages (paginated — page to
  the end). That id set is `expected` for `(noon, <country>)`.

Write `_MANIFEST.json` now with each combo's `expected` filled and
`collected: []`. Use `vibe_seller_write_workspace_file`.

## Step 2 — collect each product's reviews, newest-first, to the end

Process products **in the `expected` set you enumerated**. For each, open
its reviews page **sorted by date (newest first)** and page through ALL
history, then write one `<product_id>.json` (the `reviews/v1` shape) and
add the id to that combo's `collected`.

**Amazon** (`product_id` = ASIN):
1. Open the reviews page (newest first). The overall rating + rating
   count are on this page header. (TLD map: `../amazon-shared/SKILL.md` §1.)
   ```bash
   browser-use <<'PY'
   new_tab("https://www.amazon.<tld>/product-reviews/<asin>/?sortBy=recent")
   wait_for_load()
   print(page_info())
   PY
   ```
2. Extract each review block via `js("return …")` returning JSON:
   stars, date, title, body, verified flag, variant, and the review id
   (`data-hook="review"` carries an `id`; fall back to a stable hash of
   `author|date|title`). Page via the **Next page** control
   (`js("document.querySelector('li.a-last a')?.click()")`) until there
   is no next page; bump `review_pages_fetched`.
   ```bash
   browser-use <<'PY'
   print(js("""
     return JSON.stringify(Array.from(document.querySelectorAll('[data-hook=review]')).map(function(r){
       return {
         id:     r.id,
         stars:  r.querySelector('[data-hook=review-star-rating] .a-icon-alt')?.textContent.trim(),
         date:   r.querySelector('[data-hook=review-date]')?.textContent.trim(),
         title:  r.querySelector('[data-hook=review-title]')?.textContent.trim(),
         body:   r.querySelector('[data-hook=review-body]')?.textContent.trim(),
       };
     }));
   """))
   PY
   ```
3. Also sweep `&filterByStar=one_star,two_star` so the low-star set is
   fully captured even if the recent-sort tail is long.
4. A non-English marketplace (e.g. local-language storefronts such as
   Arabic- or Spanish-language sites): the DOM structure is identical —
   extract by element structure (star aria-value, the dated review node),
   NOT by matching English text labels.

**noon** (`product_id` = noon product id):
1. Open the product page, scroll to the reviews section, switch the sort
   to newest/by-date (verify the exact control on the live page — it has
   moved between noon redesigns), page/scroll through all reviews.
2. Extract the same fields. Record `review_pages_fetched` (or scroll
   batches). Load `../noon-shared/SKILL.md` if the page structure is
   unfamiliar.
3. **Emit the `asin`**: resolve the noon product to its matching Amazon
   ASIN (you enumerated the Amazon catalog in Step 1 — match by
   MSKU/SKU/title) and write it as the top-level `asin` field. The
   downstream consumer stores noon ratings against that ASIN; a noon
   file without `asin` is dropped.

Then `vibe_seller_write_workspace_file`
`store-data/<slug>/reviews/<platform>/<country>/<product_id>.json` and append
the id to the combo's `collected` in `_MANIFEST.json`. **Write the file
before moving to the next product** (survives context compaction — a
written file is the durable record; an in-memory list is not).

### Idempotent re-runs

Full history every run. A re-run re-pages everything and **upserts** the
JSON, deduping reviews by `id`. Writing a product that already has a file
just refreshes it — never an error, never a duplicate.

## Step 3 — parallelize (carefully)

Open **N browser windows concurrently** to collect N products at once.

- Start the concurrency cap at **2**, confirm browser-use drives both
  windows without wedging, then raise toward ~4–6 only if stable. Anti-bot
  rate-limiting at the platform is the real ceiling — back off (lower the
  cap, add a short pause between products) the moment you see CAPTCHAs,
  throttle pages, or empty extracts.
- **Self-heal**: a window can wedge on a stale tab. If a `js()` read
  returns empty or a page hangs, re-navigate that product's URL with a
  fresh `new_tab(...)` + `wait_for_load()` and retry; don't abandon the
  product — a missing file is a named gap.
- This is the new, riskier part vs the sequential ad-audit. The vertical
  slice (one store, one ASIN, then 2-window) validates it before scaling.

## Step 4 — submit + converge (the server IS the reviewer)

Call `vibe_seller_set_task_result("./REVIEW_COLLECT_<date>.md")` every
handful of products. The server's completeness reviewer replies with
exactly which combos are under-collected (`collected < expected`, with
the missing ids) and which product files are missing/malformed.

**Converge — don't restart:**

- The written JSON files and `_MANIFEST.json` persist. Keep them. Each
  round, open ONLY the not-yet-collected products, write their JSON, add
  their ids to `collected`, and re-submit.
- Never regenerate the dataset from memory — after compaction your memory
  of earlier products is incomplete. The on-disk files are the durable
  record; build on them.
- A round that adds nothing new several times in a row stalls the gate
  (it then accepts the partial). Don't burn rounds on the MD cover — the
  gap that blocks you is almost always *quantity* (collected < expected),
  which only collecting more products fixes.

## Step 5 — cursor (light, non-gating)

After converging, write `last_review_collect_date` (today's ISO date) to
`schedule_state` via `vibe_seller_set_schedule_state`. This is for
cross-run reporting only — it does NOT skip any product next run (full
history every run; the per-product JSON is the dedup store). Use this one
canonical key — do not invent variants.

## Reference index (load on demand only)

- `output-spec.md` — the `reviews/v1` data contract (read first).
- `../amazon-shared/SKILL.md` — TLD map, login/OTP, navigation.
- `../amazon-reports/SKILL.md` — All Listings Report nav + download.
- `../noon-shared/SKILL.md` / `../noon-listing/SKILL.md` — noon login,
  page structure, catalog enumeration.
- `../browser-use/SKILL.md` — the heredoc interface: `new_tab`,
  `wait_for_load`, `page_info`, `js`, `fill_input`, `type_text`,
  `press_key`, `click_at_xy`, `capture_screenshot`; extraction via
  `js("return …")` returning JSON.
