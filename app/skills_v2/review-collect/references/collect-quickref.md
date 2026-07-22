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
- **noon**: open the noon **Seller Center catalog** (load
  `../noon-listing/SKILL.md`) and union every product id across all
  catalog pages (paginated — page to the end). That id set is `expected`
  for `(noon, <country>)`. **In this same pass, capture each product's
  `seller_sku` (partner SKU) and build the full noon-id → `seller_sku`
  map up front** — do NOT defer it to per-product later. The `seller_sku`
  lives ONLY in the Seller Center catalog: it is **not** on the public
  product page, and it is **not** the page's LD+JSON `sku` (that value is
  the noon product id / NSKU, not the seller SKU). Grabbing every
  product's `seller_sku` here — while you're already paging the catalog —
  is what lets each noon file carry it on the first submit; a noon file
  without `seller_sku` fails the gate and forces a wasted converge round.

Write `reviews/_MANIFEST.json` now with each combo's `expected` filled
and `collected: []`. Use the built-in **Write** tool — the `reviews/`
dir is task-local (inside your working dir), NOT the shared workspace,
so do **not** use `vibe_seller_write_workspace_file` for review files.

## Step 2 — collect each product's reviews, newest-first, to the end

Process products **in the `expected` set you enumerated**. For each, open
its reviews page **sorted by date (newest first)** and page through ALL
history, then write one `<product_id>.json` (the `reviews/v1` shape) and
add the id to that combo's `collected`.

**Amazon** (`product_id` = ASIN):
1. Open the reviews page (newest first) in the store's default browser —
   the Ziniao browser is already signed in, so storefront pages load
   without a separate login. The overall rating + rating count are on
   this page header. (TLD map: `../amazon-shared/SKILL.md` §1.)
   ```bash
   browser-use <<'PY'
   new_tab("https://www.amazon.<tld>/product-reviews/<asin>/?sortBy=recent")
   wait_for_load()
   print(page_info())
   PY
   ```
   **If it redirects to sign-in (`/ap/signin`)** — the default (Ziniao)
   session is normally already signed in; if it isn't, complete the
   sign-in (`../amazon-shared/SKILL.md` §2 login loop). If no account is
   pre-filled, **STOP and ask the operator via `AskUserQuestion`** for
   the account (email + password) — never bake credentials into this
   skill, and never loop. Sign in with whatever the operator provides,
   then reload the reviews page. Do **NOT** silently fall back to a
   degraded page and leave the product uncollected: if you truly cannot
   sign in, record the ASIN as a NAMED gap (still uncollected in the
   manifest), never a stale file.
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
1. Open the product page. **Read the overall rating + rating count from
   this page's header and record them as `rating`/`rating_count`** — the
   value on the page you opened, never copied from a sibling variant,
   another country, or a previous run. Then scroll to the reviews
   section, switch the sort to newest/by-date (verify the exact control on
   the live page — it has moved between noon redesigns), and page/scroll
   through all reviews.
2. Extract the same fields. Record `review_pages_fetched` (or scroll
   batches). Load `../noon-shared/SKILL.md` if the page structure is
   unfamiliar.
3. **Emit noon's own identity — `seller_sku`, not an Amazon ASIN.** Write
   the noon **`seller_sku`** (the seller/partner SKU of THIS noon listing;
   each colour / size variant has its own) as a top-level field. That is
   noon's native id and the rating is keyed on it. Do **not** resolve the
   product to an Amazon ASIN, and never let two noon products share one
   identity — each variant is its own product with its own rating. (This
   skill is platform-agnostic; relating noon to Amazon is a private
   consumer's job, not yours.)

Then use the built-in **Write** tool to write
`reviews/<platform>/<country>/<product_id>.json` (task-local, NOT
`vibe_seller_write_workspace_file`) and append the id to the combo's
`collected` in `reviews/_MANIFEST.json`. **Write the file before moving
to the next product** (survives context compaction — a written file is
the durable record; an in-memory list is not).

### Idempotent re-runs

Full history every run. Your task-local `reviews/` dir starts **empty**
every run (retry wipes the workspace), so there is nothing to "resume" —
you collect the full set fresh each time. Within a run, re-writing a
product you already did this run just refreshes it — never an error.

**Freshness is earned, not stamped. Collect LIVE — never copy.** Every
product's `rating`/`reviews` MUST come from opening its page in the
browser THIS run. Do **NOT** read, copy, `cp`, or script pre-existing
review JSON from anywhere else on disk (e.g. `store-data/…/reviews`, a
backup, another store, a `_MANIFEST` that claims "already collected")
into your `reviews/` dir, and do **NOT** write a file with a
freshly-stamped `collected_at` whose rating/reviews you did not read off
the live page this run. A file's existence proves it was written this
run; it does **not** prove the data is real — copying stale content with
a new timestamp is the exact failure this design forbids. If a page
genuinely can't be read this run, report that product as a NAMED gap;
never fabricate or copy a file for it.

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
- `../browser-harness/SKILL.md` — the heredoc interface: `new_tab`,
  `wait_for_load`, `page_info`, `js`, `fill_input`, `type_text`,
  `press_key`, `click_at_xy`, `capture_screenshot`; extraction via
  `js("return …")` returning JSON.
