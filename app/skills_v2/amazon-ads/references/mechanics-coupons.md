# Amazon — coupons and the Manage promotions page

Coupons live in Seller Central (`sellercentral.amazon.<tld>`), not the
ad console. A coupon spends money and goes through a review window
before it runs, so create one only with the user's explicit go-ahead.

## Manage promotions

`https://sellercentral.amazon.<tld>/manage/promotions` (the old
`/coupons/dashboard` redirects here) — title "Manage Promotions (N)",
breadcrumb Marketing > Manage promotions and deals.

- Tabs: **Manage promotions | Create promotions | Manage brand tailored
  promotions**.
- Right controls: **Promotion Performance**, **All promotions ⌄** (view
  picker), **Create promotion ⌄**.
- Filters: Search by ASIN/SKU · Promotion type ⌄ · Running/Upcoming ⌄ ·
  Store ⌄ · +2 · Save view · Clear filters. Type chips: **Deals | Coupons
  | Price Discounts | Buy X, Get Y**.
- Columns: Promotion (type | ASINs) · Status · Schedule (Events) ·
  Audience · Promotion terms · Performance · Action (Edit ⌄).

The list is account-wide: rows from several marketplaces appear in one
table, each in its own currency. Filter with **Store ⌄** instead of
switching marketplace — the opposite of the ad console.

`Create promotion ⌄`:

| Menu item | URL |
|---|---|
| Create Coupon | `/workflow/coupons/create` |
| Create Coupons in bulk | `/coupons/bulk-upload` |
| Create Deal | `/merchandising-new/create` |
| Create Deals in bulk | `/promotion-central/manage-in-bulk/` |
| Create Buy X, Get Y | `/promotion-central/bxgy/create` |
| Create Price Discount | `/discounts/create` |

## The four-step coupon wizard

`/workflow/coupons/create` redirects to
`/workflow/coupons/create/<uuid>/step/1` ("NGS Workflows Application"),
with a left rail "Create Coupon" and a progress line ("0 of 4 steps
completed · 0%"):

1. **Store & products** — `Choose store` lists every marketplace the
   account sells on; the product search ("All ASINs") appears only after
   a store is chosen. Controls: `Back ⌄` · `Continue`.
2. **Coupon set up** (unlocks after step 1)
3. **More details**
4. **Review and confirm**

The coupon's marketplace is chosen in step 1, not inherited from the
session, so pick the store the task names even if the session is on
another marketplace. Product rows are selected with a `[role=checkbox]`
div at the far left (not a native input); search works by name, ASIN
or SKU.

## Inputs that may not take a programmatic value

The amount fields (discount value, budget), the start date and
sometimes the title are `kat-*` components that can show a value set by
script while React still treats the field as empty, leaving `Continue`
disabled. Value setters with `input`/`change`/`blur`, the wrapper's
`value` attributes, `fill_input`, and select-all + retype + Tab have all
been seen to fail this way. Real per-keystroke typing after a trusted
click is the way in; a date is set by opening the picker and clicking
the day cell. Radio buttons (`name=discount-type` money-off vs
percentage, `name=combinability`) do accept programmatic clicks.

If the amount fields will not register after a real-keystroke attempt,
do step 1 end-to-end and hand the rest to the user: "click Continue and
enter discount, budget, start date and title" takes them under a
minute.

## Eligibility is not inventory

An active, in-stock SKU can still be missing from the coupon product
search. New listings with no review history may be ineligible, and an
account in a "no new coupons" state returns `No eligible ASINs found`
for every product, including ones with running coupons. On a
multi-marketplace account the same SKU is often eligible in another
marketplace — try the account's other stores (in that marketplace's
currency) before concluding the SKU cannot run a coupon.

## New-launch defaults

Starting values for a new-product coupon (when the user states standing
defaults of their own, record them in `stores/<slug>/notes.md` so every
later run uses them):

- Discount: 10 (marketplace currency).
- Budget: 1000 (same currency).
- Duration: 30 days from today — Amazon's maximum; 31 fails.
- Title: write a fresh short product name rather than copying a sister
  coupon's title, which Amazon flags as a duplicate across accounts.
