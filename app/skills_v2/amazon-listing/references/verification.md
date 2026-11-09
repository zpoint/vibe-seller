# Verifying a listing: which marketplace, and is it live

Contents:
- Prove which marketplace the page shows
- Trust inventory, not the feed count
- The Manage Inventory page
- "Missing information / ASIN -" is usually not a failure
- "N/A" on Check Upload Status, and slow queues
- Reporting an image-deferred family
- Copy-only partial updates

## Prove which marketplace the page shows

A seller-central page renders the marketplace the **session** is on,
whatever the URL subdomain says: a `.ae` inventory URL renders SA's
inventory when the header switcher is still on SA. So "the `.ae` page
shows the family" is not evidence that AE has it.

Read the id, not a label: `js("return ue_mid")` returns the live
marketplace id (the full table is `scripts/marketplace_ids.py`), and it is
identical in every console language. Read it on every verification read
and quote it in the result. A family reported live on the wrong
storefront is worse than an unverified one, because nobody goes back to
check it. If it differs from the target, switch marketplace (the
`amazon-shared` skill) and reload before trusting anything on the page.

`bh_listing_status.py` and `bh_upload_flatfile.py` do this check for you
and refuse when `ue_mid` is not the marketplace you named.

A batch-status row is not proof either: on a unified account the batch id
is account-level and shows on every sibling marketplace's status page.

## Trust inventory, not the feed count

The report's "records processed / 0 errors" means the feed was accepted,
not that a live listing exists. A record with errors can still create an
incomplete stub; a clean feed can leave a suppressed listing. Confirm on
Manage Inventory with `bh_listing_status.py`: it returns each SKU's ASIN
off the page's own `div[data-sku]` rows, proves the marketplace by
`ue_mid`, and names the SKUs that are not there. Check that every SKU has
the ASIN you meant and, for a family, that the parent row (the one with
`offer_cell: false`) carries the family's parent ASIN. Then confirm price
and stock in that marketplace's Pricing view — the feed's "N/N successful"
reflects neither.

`skucentral?mSku=` is an unreliable surface: in some sessions it renders
an empty body. If you open it, first confirm it rendered (a `SKU:` header
line in the body text); an empty body is that page failing, not a missing
SKU.

## The Manage Inventory page

- URL `/myinventory/inventory`; header "Manage All Inventory". On an NGS
  session the classic body renders under the `ngstrim` trim.
- Tabs: All listings / Complete drafts / Activate listings / Enhance
  listings / Review listing changes; quick filters plus Listing status /
  Fulfilled by dropdowns and "All filters"; top-right **Add a variation**
  and **Add a product**.
- Row actions sit in a per-row kebab (`button.indicator` inside a
  `kat-dropdown-button` at the row's far right), outside the
  `div[data-sku]` element, so row-scoped queries miss it. Parent rows offer
  Edit listing / Manage images / Copy listing / Analyze for FBA / Delete
  listing / Advertise listing; child rows add Add another condition /
  Change to Fulfilled by Merchant / Send or replenish inventory / Create
  removal order / Print item labels / Close listing / Change shipping
  template.
- Expanding "Variations (N)" opens a "Related to N variations" panel; child
  rows carry an inline price input, "View reference prices", and a
  `+N marketplace` chip on multi-marketplace listings.
- The kebab menu items have not responded to synthetic clicks (the host
  wins the hit-test over its own popup). Use the flat-file helpers and
  `bh_listing_status.py` instead of the menu.

## "Missing information / ASIN -" is usually not a failure

Two benign causes, and re-uploading fixes neither:

1. **ASINs mint asynchronously.** A just-submitted family can show
   `ASIN -` or a "Complete drafts → Submitted: Provide missing
   information" entry for 10–30 minutes. Re-check later; the parent flips
   to `Variations (N)` with real child ASINs on its own. Re-uploading only
   spawns duplicate batches.
2. **Only the main image is missing.** Images are not uploaded from here,
   so a no-image product parks in "provide missing information" for the
   image alone. That is an accepted done state once Manage Inventory shows
   the variation relationship, real child ASINs and correct title / price
   / bullets. Treat it as unfinished only if the processing report names a
   non-image error.

## "N/A" on Check Upload Status, and slow queues

After a submit, the batch's "SKUs successful" column stays `N/A` for
minutes (CREATE/DELETE feeds can sit there a long time), and the widget's
shadow-root text may read "File not uploaded" on a submit that went
through. Once you have a `reference_id`, Amazon accepted the file.

Latency says nothing about your file. A CREATE submitted right after
changes on the same SKUs is the slowest case; 30+ minutes at N/A is
ordinary. While a batch is pending, wait and check Manage Inventory. Don't
rewrite the spec to test a theory, and above all don't remove an ASIN pin
to "see if the pin was the problem": an unpinned create mints a new ASIN,
destroying what you were waiting to restore. If you need to know why,
wait for the processing report and read what Amazon says. Re-upload only
when the downloaded report names a real per-SKU error.

## Reporting an image-deferred family

A SUCCESS (OTHER) whose only error is the missing main image means the SKU
is created, priced and stocked but search-suppressed until the seller adds
an image. Report it as that ("N children created, linked, priced, stocked
— suppressed pending main image"), not as "live". The inventory row reads
"Search suppressed" / "No image available" and the feed reads 0/N
successful; both are expected for this state.

Any other SUCCESS (OTHER) / "Action required" error is not done, even
though the SKU appears in inventory — e.g. **100476** ("Provide an Item
Name ≤75 chars to use Item Highlights") when `title_differentiation` was
filled on a long title. Fix the field the report names (Item Highlight is
optional — clear it; the colour belongs in `color_name`) and re-upload.

## Copy-only partial updates

A partialupdate of title / bullets / description / keywords is proved by
two things together: the batch's report reads "Your changes were applied
without any errors" for every SKU (`parse-feedback`, 0 errors), and
`bh_listing_status.py` shows the new title on every SKU. Manage Inventory
shows titles only, and the per-SKU edit page has no stable URL
(`/listing/edit?asin=` is "page not found"), so the report's per-SKU
"applied" line is the evidence for the other fields. Don't go hunting for
a page that reads bullets back.
