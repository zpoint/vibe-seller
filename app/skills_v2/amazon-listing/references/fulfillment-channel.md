# Fulfillment channel: FBM (`DEFAULT`) vs FBA, and switching a listing to FBA only

A listing's offer carries one `fulfillment_availability` entry **per
channel**, and an FBA seller can end up with an FBM offer they never
asked for. This page is how to spot one, and how to remove it.

## What the channels mean

| `fulfillment_channel_code` | channel | quantity |
|---|---|---|
| `DEFAULT` (flat file: `fulfilment by merchant (default)` on some marketplaces) | **FBM / merchant-fulfilled**: the seller ships it | typed by the seller |
| `AMAZON_<REGION>` (e.g. `AMAZON_NA`, `AMAZON_EU`, `AMAZON_SA` — the code is the *region's*, so a Middle-East marketplace can read `AMAZON_EU`) | **FBA**: Amazon ships it from an FC | none on the entry; Amazon derives it from FC stock |

**`DEFAULT` with a quantity above 0 is a live FBM offer**, whatever the
seller thinks of themselves as. It sits idle while FBA has stock, and
**becomes the offer buyers see the moment FBA stock runs out**. It ships on
the seller's own shipping template (often a `legacy-template-id` nobody
configured), so the detail page shows symptoms like:

- "International shipping" / "Ships from outside <country>" / a long
  delivery estimate on a product the seller only sells through FBA;
- an order that lands as **merchant-fulfilled** and has to be shipped by
  hand (or cancelled, which costs account health).

`DEFAULT` present with **quantity 0** (or no quantity) is harmless. That
is the normal remnant on a listing that was converted to FBA.

## How it gets there

Any create path that puts a quantity on the `DEFAULT` group writes an FBM
offer: the flat file's `fulfillment_availability#N.fulfillment_channel_code
= DEFAULT` + `quantity` (the template-round-trip example does exactly
this), or the web wizard's "Quantity" field. Converting the SKU to FBA
afterwards **adds** the `AMAZON_*` entry. It does not zero the `DEFAULT`
quantity, so the listing becomes dual-channel without anyone noticing.

So for an FBA seller, create with `quantity: 0` on the merchant group.
`fill` enforces this: a merchant quantity above 0 is refused unless the
spec declares `"fulfillment": "FBM"`. For listings created before that
guard existed, zero the quantity (below) and verify.

## Detect

**SP-API** — `GET /listings/2021-08-01/items/{sellerId}/{sku}?marketplaceIds=<MKT>&includedData=attributes,summaries`
and read `attributes.fulfillment_availability`:

```json
[{"fulfillment_channel_code": "AMAZON_EU"},
 {"fulfillment_channel_code": "DEFAULT", "quantity": 100}]   ← FBM offer, live when FBA is empty
```

To sweep a whole account, page `GET /listings/2021-08-01/items/{sellerId}`
(`searchListingsItems`, `includedData=attributes`, `pageSize=20`,
`pageToken` from `pagination.nextToken`) and flag every item whose
`DEFAULT` entry has `quantity > 0`. Do it **per marketplace**: the
attribute is marketplace-scoped, and one marketplace of a unified account
can be clean while its sibling is not.

**Seller Central** — Manage Inventory, filter Fulfilled by =
*Merchant*. A SKU that also shows FBA stock columns is dual-channel.

## Fix via SP-API (verified live)

`PATCH /listings/2021-08-01/items/{sellerId}/{sku}?marketplaceIds=<MKT>`:

```json
{"productType": "<the item's productType, from summaries>",
 "patches": [{"op": "replace",
              "path": "/attributes/fulfillment_availability",
              "value": [{"fulfillment_channel_code": "AMAZON_EU"},
                        {"fulfillment_channel_code": "DEFAULT", "quantity": 0}]}]}
```

- **Keep the `DEFAULT` entry and set `quantity: 0`.** Do not just leave it
  out. A `replace` whose value **omits** `DEFAULT` comes back
  `ACCEPTED` with no issues and **changes nothing**: the entry and its
  quantity are still there minutes later and `lastUpdatedDate` does not
  move. `replace` merges entries by channel code, the same way a
  `purchasable_offer` replace merges its sub-attributes. Setting quantity 0 landed
  within ~10 s.
- **Carry every other entry back unchanged** (each `AMAZON_*` code the
  read returned), so the FBA channels are restated, not dropped.
- **Do not `delete` the attribute** to get rid of FBM. A delete on an
  offer attribute can take the whole offer down, and the listing then
  stops being buyable.
- **`ACCEPTED` is not done.** Read the item back until `DEFAULT` shows
  quantity 0, and check `offers` still holds the price and `issues` is
  empty. If it does not land within ~2 min, the patch did not take, so
  investigate rather than resubmit blindly.
- Snapshot `attributes.fulfillment_availability` before patching so the
  change can be put back exactly.
- **Patch one SKU, verify it, then do the rest.**

## Fix via Seller Central (not yet verified live; confirm on the page)

Manage Inventory → the SKU → **Edit** → *Offer* tab → set the merchant
**Quantity** to 0, or change *Fulfillment channel* to "Amazon will ship
and provide customer service". For many SKUs, Manage Inventory's bulk
action *Change to Fulfilled by Amazon* does the conversion. Afterwards
re-check with the Detect step above, because the UI accepting the
change does not prove the merchant quantity is gone.

## Afterwards

- A merchant-fulfilled **order already placed** is not affected by the
  fix. It still has to be shipped or cancelled.
- With FBM gone and FBA at 0 stock, the listing shows *Currently
  unavailable* until FBA stock arrives. That is the expected state for
  an FBA-only seller, not a broken listing.
