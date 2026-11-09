# Relisting on another marketplace (sharing the ASIN)

The steps after "get the source ASINs" in SKILL.md: working on the target
marketplace, pinning, choosing the operation, and verifying.

Contents:
- Work on the target marketplace
- The template is region-stamped
- Pin the ASIN on every row
- Which operation: shared catalog or not
- The offer
- Verify
- Unified vs separate catalogs

## Work on the target marketplace

A create / partialupdate applies to one marketplace. Run template
download, upload and Check Upload Status all on the target's host (AE →
`sellercentral.amazon.ae/...`, EG → `.eg`, …), and before uploading, and
before every verification read, confirm the session really is on the
target: `js("return ue_mid")` against `scripts/marketplace_ids.py`. What a
page displays follows the session's switcher, not the URL subdomain, so
never infer the marketplace from the URL (see `verification.md`). To move
the session, use the `amazon-shared` skill's marketplace switch; if a host
lands on an account picker ("Select an account"), click the target
account's `<button>` (not the label), then **Select account**.

`bh_upload_flatfile.py` submits only when three things name the same
marketplace: the one you meant (`SC_HOST`'s domain, or
`SC_MARKETPLACE=<id|CC>`), the file's `primaryMarketplaceId` stamp, and the
live `ue_mid`. Two agreeing isn't enough: an SA-stamped file uploaded on an
SA session by an agent that believed it was doing AE agrees with itself
and still lists on the wrong storefront.

Removing a listing from one storefront is not available here (delete is
disabled); if a task needs a SKU's ASIN changed on one marketplace only,
stop and ask the user.

## The template is region-stamped

Generate it on the target with the target store ticked
(`bh_download_template.py` does this and reads the tick back). The manual
path: Download Blank Template → Download Product Spreadsheet ("List
products not currently in Amazon's catalog") → search the product type
with trusted keystrokes (`type_text`, not a JS value set) → Select → tick
the target in "stores where you want to create offers" → Generate. The
default tick is the account's home marketplace, so a template "downloaded
from AE" is stamped SA unless the target got ticked. Other ticked stores
are harmless (see `error-priors.md`). Reusing another marketplace's file
fails with `UPLOAD_AND_DOWNLOAD_MARKETPLACES_DIFFERENT`, even on the right
host.

Browse-node ids are marketplace-scoped, and a multi-store template carries
the primary store's classifications only. To set `recommended_browse_nodes`
for marketplace X, generate the template with X as primary; otherwise
leave it blank and let Amazon classify from the product type. `fill`
hard-fails a cross-primary browse-node spec.

Staging the file needs the file-chooser intercept; the `kat-file-upload`
widget's visible input is a decoy ("Staged: False"). `bh_upload_flatfile.py`
handles it; for a manual upload use the browser-harness skill, "Uploading
a file", Method 2.

## Pin the ASIN on every row

`asin: <existing>` on the row (or `external_product_id` = the ASIN with
`external_product_id_type: asin`) makes Amazon match instead of mint. The
catalog content already exists under the ASIN; you are adding this
marketplace's offer.

Dropping the pin is destructive, never a debugging simplification. A
seller SKU is account-scoped: an unpinned `create` for a SKU that already
sells elsewhere makes Amazon mint a fresh ASIN and re-point the SKU to it
account-wide, orphaning the original with its reviews, ratings and rank
(Amazon never re-issues a retired ASIN). The FNSKU follows the SKU, so FBA
stock looks untouched while the ASIN changed underneath it, and the feed
report calls it a clean create. If a pinned upload fails, fix what the
report names. `fill` refuses an undeclared mint.

## Which operation: shared catalog or not

Check whether the ASIN exists in the target catalog first (open
`amazon.<tld>/dp/<ASIN>`, or read the first upload's report):

- **Shared catalog** (the ASIN resolves on the target): pin it +
  `operation: partialupdate`, adding only this marketplace's offer.
- **Not in the target catalog** (`/dp/<ASIN>` is "Page Not Found", or the
  upload says "the offer cannot be added because the product is not in
  the catalogue … listing a product from one marketplace to another"): an
  offer-only partialupdate can't work, but this isn't a first-time create
  either. Create on the target with the full product data and each row's
  existing ASIN pinned, so the target links the same ASIN and reviews pool
  internationally.

Mint a new ASIN only when the product exists on no marketplace yet, the
user explicitly wants a separate listing, or a pinned create
irrecoverably conflicts (fix what the report names first).

## The offer

Supply the complete offer on each child: `our_price` + `quantity` +
`fulfillment_channel_code`, via the top-level `"marketplace": "<CC>"` and
bare keys so `fill` routes them to the target's block. The channel code
comes from the target template's own valid set. An FBA seller's merchant
quantity is 0 (see `fulfillment-channel.md`).

## Verify

The target ASINs equal the source ones (same ASIN ⇒ shared reviews) —
children especially, since they are the buyable entities whose reviews
pool. If the parent container minted a new ASIN despite matched children,
say so rather than calling the families identical. And the offer is live
in the target's Pricing view.

## Unified vs separate catalogs

A unified pan-regional account (e.g. MENA: SA / AE / EG) shares one
catalog, so a matched ASIN carries its reviews across all of them. Reusing
the same SKU often auto-shares it; pinning makes it deterministic. If the
marketplaces sit on separate catalogs (a different region, or a legacy
single-marketplace login), the ASIN may not be shareable: when Amazon
still 8560s after a correct match, report that a new ASIN is unavoidable
rather than silently forking the reviews.
