---
name: amazon-fba-inbound
description: "Amazon platform only. MUST load BEFORE taking any action when the task involves creating, quoting, confirming or cancelling an FBA inbound shipment through Send to Amazon — including the cross-border SEND (Seller Export and Delivery) programme, which no Amazon API exposes. Contains the wizard's step order, the Katal selectors that are stable, the two lookalike traps, and how to void a workflow."
requires: [amazon-shared]
review:
  criteria: |
    - The shipment was ACTUALLY created: a shipment id of the form
      FBA<alnum> exists on the page (or in the shipping queue) — not a
      claim that the wizard "was completed".
    - The account and marketplace shown on the page are the ones the task
      named. A shipment created in the wrong seller account declares goods
      inbound to somebody else's inventory and cannot be undone.
    - SKUs, unit counts, box count and destination match what was asked;
      a partial or re-boxed shipment is reported, never silently accepted.
    - If a freight ceiling was given, the accepted quote is at or under it,
      and the accepted amount + currency are reported.
    - If the task was to cancel, the workflow/shipment reads voided or
      cancelled on a re-read, not merely "the link was clicked".
  verify_by: |
    Re-open the workflow URL (or the Shipping Queue) after confirming and
    read back: the shipment id, destination fulfilment centre, box count,
    unit count and the accepted shipping cost. Screenshot the confirmation.
    For a cancel, re-open and confirm the workflow no longer offers
    "Void this workflow" / the shipment shows as cancelled.
---

# Amazon FBA inbound — Send to Amazon, end to end

Creating an inbound shipment, quoting it, confirming it, printing box
labels, and voiding one. Covers both the ordinary path (your own carrier
or an Amazon-partnered domestic carrier) and the **cross-border SEND
programme**, which is browser-only.

Load `amazon-shared` first — it owns marketplace TLDs, the sign-in
challenge loop, and the Ziniao/OTP/passkey rules. This skill assumes you
are already signed in.

## 0. Why a browser at all

For most inbound work an API exists. For **SEND** it does not, and that
is worth knowing before you look for one:

- `GET /inbound/fba/2024-03-20/inboundPlans/{id}` on a SEND plan answers
  `400 BadRequest: GetInboundPlan is not supported for the cross border
  Seller Export and Delivery program`
- `listInboundPlans` omits SEND plans entirely
- a plan you create through the API is an ordinary FBA plan, so
  `listTransportationOptions` only ever offers
  `USE_YOUR_OWN_CARRIER` — no partnered option appears
- the old v0 partnered flow (`/shipments/{id}/transport`) answers
  `400 This API is deprecated`

Amazon has confirmed the same gap for the sibling AGL programme
(`amzn/selling-partner-api-models` issue 4419: *"We don't support Amazon
Global Logistics Program via the new FBA Inbound v2024 APIs as of
now."*). So for SEND the portal is the only interface, and this skill is
how to drive it.

## 1. Confirm the account BEFORE you click anything

The first words of `document.body.innerText` are the account nickname
and the marketplace:

```
<account-nickname> <Marketplace Name> New Seller Central EN Help ...
```

Read them and compare against the task. **A browser profile's name is
not evidence of the account it holds** — profiles get renamed, and a
profile called after one brand routinely holds another. Creating a
shipment in the wrong account is not a mistake you can take back, so if
the task named an account and the page shows a different one, stop and
say so.

`window.ue_mid` is the **marketplace** id (e.g. `A2VIGQ35RCS4UG`), not a
merchant id — do not use it as an account check.

## 2. The wizard

```
https://sellercentral.amazon.<tld>/fba/sendtoamazon/confirm_shipping_step?wf=<workflowId>
```

An in-progress workflow is resumable at that URL; `&preselected=<code>`
appears once a carrier has been chosen. Four `H4` steps:

| Step | Heading |
|---|---|
| 1 | `Step 1: Confirmed inventory to send` |
| 1b | `Step 1b – Pack individual units` |
| 2 | `Step 2: Confirm shipping` |
| 3 | `Step 3: Print box labels` |

Step 1 restates the scope — `SKUs: N  Units: N  Ship from: …`. Check it
against the task here; it is cheaper than discovering a mismatch after a
confirmation.

Step 2 offers two cards:

- **`Amazon SEND`** — "Available partners …". Amazon resells cross-border
  freight; the quote includes duties and taxes.
- **`Non-partnered carrier`** — you arrange the freight yourself.

Each shipment block reads:

```
Ship to: <FC code> - <address>
Fulfillment capability: Standard
Boxes: N  SKUs: N  Units: N  Volume: N CBM  Weight: N kg
SKUs that need labeling by seller: N (N units)
```

Then, in order: `Shipment contents` · `Ship date` ·
`Shipment service` · `Export licence (EOR)` · `Import license (IOR)` ·
`Confirm Shipping Cost` ·
`Provide shipping parties and Customs information`.

## 3. Selectors: what is stable

**The page is Katal web components and its ids are generated per
render** (`katal-id-29`, `katal-id-40`, …). Never key on them. Buttons
are `<kat-button>` with **no id at all** — identify them by their
`label` attribute.

| What | Handle |
|---|---|
| Shipment service (collection vs drop-off) | `input[type=radio][name="service_selection"]` |
| Export licence (EOR) | `input[type=radio][name="export_license"]` |
| Import licence (IOR) | `input[type=radio][name="import_license"]` |
| Fetch the quote | `kat-button[label="Get shipping cost"]` |
| Confirm | `kat-button[label="Accept charges and confirm shipping"]` |
| Void the workflow | link text `Void this workflow` |
| Account + marketplace | first line of `document.body.innerText` |

Radio groups arrive with defaults already selected — read `.checked`
before changing anything, and only change what the task asks for.

### Two lookalike traps

- **A feedback widget repeats once per section.** Each copy has its own
  `kat-button[label="Cancel"]` and a disabled
  `kat-button[label="Submit"]`, all at the *same* screen position. A
  "click the Submit button" step hits that widget, not the wizard. The
  wizard has exactly two controls: `Get shipping cost` and `Accept
  charges and confirm shipping`.
- **The ship-date control is not a light-DOM input.**
  `document.querySelectorAll('input')` finds only the header search box
  and the language `<select>`; the date field lives inside a component's
  shadow root. Drive it by coordinates — screenshot, click, type. See
  `interaction-skills/shadow-dom.md` and `screenshots.md`.

## 4. The order matters

`Get shipping cost` and `Accept charges and confirm shipping` are
**both disabled on load**, and the delivery-mode and carrier cards **do
not exist in the DOM yet** — they are rendered only after the quote is
fetched. So:

1. verify account + marketplace (§1), and that Step 1's SKUs/units match
2. set `Ship date`
3. pick `Shipment service`
4. pick EOR and IOR
5. press `Get shipping cost` — and wait; this is a pricing round trip
6. **now** pick the delivery mode. On a cross-border SEND lane the
   choices are an air option (roughly a fortnight) and an ocean
   consolidation option (roughly two months), both delivered as small
   parcel; then pick a carrier card. Each card shows a total including
   duties and taxes in the marketplace currency — for example
   `AED 20.00` for one small ocean box versus `AED 90.00` for the same
   box by air (illustrative figures; read the live ones).
7. continue to shipping parties + customs information
8. press `Accept charges and confirm shipping`
9. Step 3 prints the box labels — save them, they are what goes on the
   cartons

If a freight ceiling was given and every quote exceeds it, **stop and
report the quotes**. Do not accept one anyway, and do not silently pick
the cheapest mode when the task named a different one.

## 5. Cancelling

`Void this workflow` sits at the foot of the page beside the
`Workflow ID` and `Created:` timestamp, and abandons a workflow that has
**not** been confirmed.

A workflow that has been confirmed is a real shipment with real freight
booked; cancel it from the Shipping Queue, and expect the API not to help
— `cancelInboundPlan` refuses a SEND plan the same way `getInboundPlan`
does. Always re-read after cancelling: a clicked link is not a cancelled
shipment.

## 6. Reporting

Report the shipment id (`FBA…`), the destination fulfilment centre, box
and unit counts, the mode and carrier chosen, and the accepted amount
with its currency. If any of those differ from the request — Amazon split
the shipment, chose a different FC, or re-boxed — say which, rather than
reporting success.
