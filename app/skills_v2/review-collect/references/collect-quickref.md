# Review-collect procedure

- [Step 0 — scope](#step-0--scope)
- [Step 1 — enumerate the product universe](#step-1--enumerate-the-product-universe)
- [Step 2 — collect each product](#step-2--collect-each-product)
- [Step 3 — parallelise](#step-3--parallelise)
- [Step 4 — set the result and fill gaps](#step-4--set-the-result-and-fill-gaps)

The finished dataset's contract is [`output-spec.md`](output-spec.md).
Browser mechanics (heredoc helpers, `--worker N`) are in the
`browser-harness` skill.

## Step 0 — scope

Read `stores/<slug>/metadata.json` → `platforms.{amazon,noon}` and
collect every `(platform, country)` listed unless the task narrows it.

## Step 1 — enumerate the product universe

Get the full product set per combo before collecting any reviews, and
write it as the manifest's `expected` — completeness is measured against
it.

- **Amazon:** download the All Listings Report for each country (load
  the `amazon-reports` skill for the report and download flow). Every ASIN
  sold in that country is `expected` for `(amazon, <country>)`.
- **noon:** page through the Seller Center catalog to the end (load the
  `noon-listing` skill) and take every product id. In the same pass build
  the full product id → `seller_sku` map: the partner SKU lives only in
  Seller Center — it is not on the public product page, and the page's
  LD+JSON `sku` is the noon product id, not the seller SKU. Collecting it
  now means every noon file carries it on the first submit.

Write `reviews/_MANIFEST.json` with each combo's `expected` filled and
`collected: []` (built-in Write tool; `reviews/` is task-local).

## Step 2 — collect each product

For each product in `expected`: open its reviews sorted newest-first, page
through all history, write `<product_id>.json`, then add the id to that
combo's `collected`. Write each file before moving to the next product —
after context compaction the files on disk are the only reliable record.

Every value comes from a page opened in the browser this run. Don't copy
review JSON from anywhere else on disk (`store-data/`, a backup, another
store, an old manifest), and don't stamp a fresh `collected_at` on data
you didn't read live. A product you can't read this run is a named gap
with no file.

### Amazon (`product_id` = ASIN)

1. **Open the reviews page** in the store's default (Ziniao) session,
   which is normally already signed in:

   ```bash
   browser-use <<'PY'
   new_tab("https://www.amazon.<tld>/product-reviews/<asin>/?sortBy=recent")
   wait_for_load()
   print(page_info())
   PY
   ```

   If it lands on `/ap/signin`, complete sign-in as the `amazon-shared`
   skill describes (Ziniao autofills the account; don't ask the user for
   a password), then reload. If sign-in can't be completed, record the
   ASIN as a gap and say so in the result rather than collecting from a
   degraded page.

2. **Read the rating summary from these selectors only** — they are the
   product's own widget. A page-wide `i.a-icon-star` / `.a-icon-alt`
   lookup returns a review row's star on an unrated product (typically
   `5.0`).

   ```bash
   browser-use <<'PY'
   print(js(r"""
     var pick = function (s) { var e = document.querySelector(s); return e ? (e.getAttribute('title') || e.textContent).trim() : null; };
     var pct = function (r) { var a = r.querySelector('[aria-label]'); var m = a && a.getAttribute('aria-label').match(/(\d+)\s*(%|percent)/); return m ? Number(m[1]) : null; };
     var rows = Array.from(document.querySelectorAll('#histogramTable li'));  // 5 rows, 5-star first
     return {
       title:  pick('#productTitle'),
       rating: pick('[data-hook=rating-out-of-text]') || pick('#acrPopover'),               // "4.1 out of 5"
       count:  pick('[data-hook=total-review-count]') || pick('#acrCustomerReviewText'),   // "200 global ratings"
       histogram: rows.length === 5 ? {5: pct(rows[0]), 4: pct(rows[1]), 3: pct(rows[2]), 2: pct(rows[3]), 1: pct(rows[4])} : null,
     };
   """))
   PY
   ```

   Parse the leading number of `rating` and `count`, and copy `histogram`
   exactly as `rating_histogram` (a 0% star on a small count is how a
   consumer learns a stored review was removed).

   - Rating and count must come together; one without the other means
     the read failed — re-open and retry.
   - **All three absent is not yet a zero** — a half-loaded reviews page
     reads the same. Open `https://www.amazon.<tld>/dp/<asin>` and require
     both `#productTitle` present and
     `document.querySelector('input#ASIN').value` equal to the requested
     ASIN (a dp link can land on a sibling variant). Re-read the widgets
     there. Only if that page passes and still has no rating summary
     write `rating: 0, rating_count: 0, no_ratings: true`. Anything else
     (no title, another ASIN, block page, sign-in, timeout) is a gap.

3. **Extract the review blocks** and page to the end. The "Next page"
   control is a link: read its href and `new_tab` it, rather than
   clicking.

   ```bash
   browser-use <<'PY'
   rows = js("""
     return Array.from(document.querySelectorAll('[data-hook=review]')).map(function(r){
       var q = function(s){ var e = r.querySelector(s); return e ? e.textContent.trim() : null; };
       return {id: r.id, stars: q('[data-hook=review-star-rating] .a-icon-alt'),
               date: q('[data-hook=review-date]'), title: q('[data-hook=review-title]'),
               body: q('[data-hook=review-body]')};
     });
   """)
   nxt = js("var a=document.querySelector('li.a-last a'); return a ? a.href : null;")
   print(len(rows), nxt)
   PY
   ```

   Use the review's `id` attribute, or a stable hash of
   `author|date|title` when there is none. One page per invocation keeps
   each call well inside the wrapper's 120 s budget; count pages into
   `review_pages_fetched`.

4. Also sweep `&filterByStar=one_star,two_star` so the low-star set is
   complete even when the recent-sorted tail is long.

5. Non-English storefronts (Arabic, Spanish, …) have the same structure;
   extract by element (star aria values, dated review nodes), not by
   English labels.

### noon (`product_id` = noon product id)

1. Open the product page and read the overall rating and rating count
   from its header — this page, this run. A page that rendered with no
   rating is `rating: 0, rating_count: 0, no_ratings: true`, as on
   Amazon.
2. Scroll to the reviews section, switch the sort to newest/by date
   (find the control on the live page; it moves between redesigns), and
   page or scroll through every review. `rating_count` is "N Ratings";
   the `reviews` array holds the "M reviews" with text. If M > 0 and you
   extracted none, the review cards are there — read them by structure
   (Arabic included) and retry.
3. Write the file with that variant's `seller_sku` from Step 1. Load the
   `noon-shared` skill if the page structure is unfamiliar.

## Step 3 — parallelise

To collect several products at once, run subagents that each drive the
browser with their own slot, `browser-use --worker N` (you assign the
numbers; see `browser-harness` § Parallel subagents). Give each worker a
disjoint list of products and have it write the product files itself;
you update the manifest.

Start with 2 workers and raise toward 4–6 only while it stays stable.
The platform's anti-bot limits are the real ceiling: at the first
CAPTCHA, throttle page or run of empty extracts, lower the count and add
a pause between products. If a worker's tab wedges (empty `js()` reads,
a hanging page), re-open the product with a fresh `new_tab(...)` and
retry rather than dropping it.

## Step 4 — set the result and fill gaps

Write `./REVIEW_COLLECT_<date>.md` and call
`vibe_seller_set_task_result(result_file="./REVIEW_COLLECT_<date>.md")`. The server
checks the files against the manifest and, if anything is short, replies
with the under-collected combos (with missing ids) and malformed files.

- Keep the existing files and manifest; open only the products listed,
  write them, add them to `collected`, and set the result again.
- Don't rebuild the dataset from memory — the files on disk are the
  record.
- The gap is almost always quantity (`collected < expected`), which only
  collecting more products closes; editing the cover doesn't.

When done, write today's ISO date to `last_review_collect_date` with
`vibe_seller_set_schedule_state`. It is for cross-run reporting only and
skips nothing next run.
