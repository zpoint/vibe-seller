---
name: amazon-fba-inbound
description: "Amazon platform only. Load before acting on any task that creates, quotes, confirms or cancels an FBA inbound shipment through Send to Amazon, including the cross-border SEND (Seller Export and Delivery) programme, which no Amazon API exposes. Covers the wizard's step order, the stable Katal selectors, the lookalike traps, and how to cancel a confirmed shipment."
requires: [amazon-shared]
---

# Amazon FBA inbound — Send to Amazon, end to end

Creating an inbound shipment, quoting it, confirming it, printing box
labels, and cancelling one. Covers both the ordinary path (your own
carrier or an Amazon-partnered domestic carrier) and the cross-border
**SEND** programme.

Load the `amazon-shared` skill first with the Skill tool — it owns
marketplace hosts, the sign-in challenge loop and the Ziniao/OTP/passkey
rules. This skill assumes you are signed in.

**Confirming or cancelling a shipment commits inventory and freight
charges and cannot be undone.** Before pressing `Accept charges and
confirm shipping` or `Delete shipment and charges`, show the user what
you are about to commit (account, SKUs, units, boxes, mode, carrier,
amount) and get a go-ahead through `AskUserQuestion`, unless the task
already gave explicit approval for exactly that.

## Done when

- A shipment id of the form `FBA<alnum>` exists on the page or in the
  Shipping Queue — not a claim that the wizard "was completed".
- The account and marketplace on the page are the ones the task named.
- SKUs, unit counts, box count and destination match the request; a
  split, re-boxed or partial shipment is reported, not silently accepted.
- If a freight ceiling was given, the accepted quote is at or under it,
  and the amount + currency are reported.
- For a cancel: a re-read shows the shipment `Cancelled`, not just a
  clicked button.

Verify by re-opening the workflow URL (or the Shipping Queue) after
confirming and reading back the shipment id, destination FC, box and
unit counts and the accepted cost; a screenshot of the confirmation is
a useful visual record.

## 0. Why a browser

For ordinary FBA inbound an API exists, but **SEND is portal-only**: the
SP-API refuses SEND plans by name (`GetInboundPlan is not supported for
the cross border Seller Export and Delivery program`) and
`listInboundPlans` omits them. A plan created through the API is an
ordinary FBA plan from birth, and nothing at the transport step converts
it.

The trap: SEND's partner carriers **do** appear in the API's
`listTransportationOptions`, but picking one books that company as a
carrier *you* arranged — no Amazon freight, tracking or partnered price
(`freightInformation: null`). The shipment name proves nothing either
way (`FBA STA (…)` and `FBA SEA (…)` both occur on genuine SEND
bookings). A genuine SEND booking reads on Step 3 like
`Carrier: Amazon Partnered (<partner>)`, `Shipping mode: …`, `Amazon
partnered carrier fees: <currency> <amount>`. If the task wants SEND,
use the portal; say in your report which one you booked.

## 1. Confirm the account before you click anything

The first words of `document.body.innerText` are the account nickname and
the marketplace:

```
<account-nickname> <Marketplace Name> New Seller Central EN Help ...
```

Compare them against the task. A browser profile's name is not evidence
of the account it holds — profiles get renamed. A shipment created in the
wrong account declares goods inbound to someone else's inventory and
cannot be taken back, so if the page shows a different account, stop and
say so. `window.ue_mid` is the **marketplace** id, not a merchant id —
don't use it as an account check.

## 2. The wizard

The Supply-chain workspace lists three sibling links: **Send to Amazon**
(`/fba/sendtoamazon`), **Send to AWD** and **Move from AWD**. This skill
is only about the first.

```
Step 1  …/fba/sendtoamazon/confirm_content_step?wf=<workflowId>    choose inventory
Step 2  …/fba/sendtoamazon/confirm_shipping_step?wf=<workflowId>   quote + confirm
Step 3  …/fba/sendtoamazon/print_labels_step?wf=<workflowId>       box labels
```

An in-progress workflow is resumable at its URL; `&preselected=<code>`
appears once a carrier has been chosen. The `H4` headings are
`Step 1: Choose inventory to send`, `Step 1b – Pack individual units`,
`Step 2: Confirm shipping`, `Step 3: Print box labels`. Step 2 cannot be
reached until Step 1 is clean.

### Start your own workflow

Opening the wizard **resumes the account's current workflow**, which may
be weeks old and hold SKUs someone else staged. Step 1 names it at the
top (`Current workflow  STA (<date>, <time>)` beside `Active workflows`).
Unless the task handed you a workflow id, click
`kat-button[label="Start new"]` first, and read the footer before your
first edit — a non-zero `SKUs ready to send` count on a workflow you
didn't create is somebody else's work.

### Step 1 — choose inventory

Two tabs, `All FBA SKUs` and `SKUs ready to send (N)`. Columns:
`SKU details · Packing details · Information/action · Quantity to send`.
A row reads:

```
<listing title>
SKU: <your-msku>       ASIN: <asin>    FBA Storage Type: Standard-size
Product Category:       Prep not required   Unit labeling: By seller - Print SKU labels
More inputs
```

- The table paginates (25 a page); match the row by its `SKU: …` text and
  work from that element's bounding box.
- **Packing details** is a `kat-dropdown` (`Individual units` /
  `Create new case pack template`). Leave it on `Individual units` unless
  the task says otherwise.
- A row has `kat-input[name="numOfUnits"]` (`Units`) and
  `kat-input[name="numOfBoxes"]` (`Boxes`). A **Bulk edit** panel at the
  top has its own pair with the same labels — match by bounding-box `y`
  against your row.
- No checkbox is needed; entering a quantity activates the row.

Per SKU, in this order — committing a row locks Step 1, so the category
must be saved first:

1. **Set the product category and press `Save`** (below).
2. **Type the quantity into that row's `Units` field**: coordinate-click
   it, then `type_text`.
3. **Click that row's own `Ready to pack` button.** It appears inside the
   row, to the right, only after a quantity is entered; it is the click
   that commits the row.
4. **Read the footer**: `SKUs ready to send: 1 (10 units)`. That is the
   page confirming it took the number.

#### Type into these fields; don't assign `.value`

Every `kat-input` here keeps its real `<input>` in a shadow root and is
React-controlled. Assigning `.value` — on the element or through the
"native setter" — can make the field *show* your digits while the app
never hears about them, and a reload shows the row empty. Because the
input sits in a shadow root, `fill_input`'s CSS selector can't reach it:
coordinate-click the field and send real input with `type_text` (use
`fill_input` on a light-DOM input).

**The footer is the only proof.** If it still reads `0 (0 units)`, the
number did not land, however convincing the input looks.

#### What a row still owes: one `kat-link` per requirement

| Link label | What it is |
|---|---|
| `Product category required` | the customs classification, blank by default |
| `Prep and labeling details needed` | prep + who labels the units |
| `Add packing line` | split the SKU across packing groups |

`More inputs` is not where these live — that `kat-expander` has no
slotted content. Click the link that names what is missing, on **the row
that names your SKU** (every row has its own copy; take the link nearest
your MSKU's `y`, not the first in document order).

#### The Product Category gate

A blank category is an "unconfirmed SKU", and it surfaces two steps
later: Step 2 says `To proceed with Amazon Partnered Carrier, please
provide product category on Step 1` and keeps `Get shipping cost`
disabled, because partnered cross-border freight is customs-declared.

The modal is `.packing-template-modal`, titled `Select Product Category`;
its header restates `SKU: … ASIN: …` — read it to confirm you opened the
right row. The vocabulary is three radios (`Electronics`,
`Sensitive Product`, `General`), with `General` preselected.

**A row showing `Product Category: General` may have nothing stored** —
`General` is also the default the row renders before any choice. So for
every SKU, open the modal, choose, and press `Save`, even when the radio
already shows what you want. Once `Ready to pack` is clicked, Step 1
redirects forward to Step 2 and the category can no longer be set; a
workflow committed without one has to be abandoned and restarted.

A SKU whose ASIN cell is empty and which says `The SKU for this product
is unknown or cannot be found` has no listing behind it — stop and name
the SKU; that is a catalog problem, not a wizard one.

#### Leaving Step 1

The foot of the page totals and gates the step:

```
SKUs ready to send: N (N units)      Total prep and labelling fees: <currency> 0.00
Please review SKUs with errors or unconfirmed SKUs
Workflow ID: wf…    Void this workflow    Created: …
```

The advance control's label depends on progress (`Ready to pack` with a
disabled `Confirm and continue`, or `Pack individual units`), so read
what is on the page. It stays disabled while the review line shows.
Check the totals match the task, press the advance control, then on
Step 1b enter the boxes with their weights and dimensions.

### Step 2 — confirm shipping

Step 2 restates the scope (`SKUs: N  Units: N  Ship from: …`); check it
against the task here, where a mismatch is still cheap. Two cards:

- **`Amazon SEND`** — Amazon resells cross-border freight; the quote
  includes duties and taxes.
- **`Non-partnered carrier`** — you arrange the freight.

Each shipment block reads:

```
Ship to: <FC code> - <address>
Fulfillment capability: Standard
Boxes: N  SKUs: N  Units: N  Volume: N CBM  Weight: N kg
SKUs that need labeling by seller: N (N units)
```

then `Shipment contents` · `Ship date` · `Shipment service` ·
`Export licence (EOR)` · `Import license (IOR)` · `Confirm Shipping Cost`
· `Provide shipping parties and Customs information`.

`Get shipping cost` and `Accept charges and confirm shipping` are both
disabled on load, and the delivery-mode and carrier cards don't exist in
the DOM until a quote is fetched. Order:

1. verify account + marketplace (§1) and the SKU/unit totals
2. set `Ship date` (§3)
3. pick `Shipment service`, then EOR and IOR (radios arrive with
   defaults — read `.checked` and change only what the task asks)
4. press `Get shipping cost` and wait for the pricing round trip
5. pick the delivery mode, then a carrier card. On a SEND lane the
   choices are typically air (about two weeks) and ocean consolidation
   (about two months); each card shows a total in the marketplace
   currency including duties and taxes — read the live figures
6. fill shipping parties + customs information
7. get the user's go-ahead (see top), then press
   `Accept charges and confirm shipping`
8. Step 3 prints the box labels — save them to the task directory; they
   go on the cartons

If a freight ceiling was given and every quote exceeds it, stop and
report the quotes. Don't accept one anyway, and don't silently pick the
cheapest mode when the task named a different one.

## 3. Selectors and traps

Katal ids are generated per render (`katal-id-29`, …) — never key on
them. Buttons are `<kat-button>` with no id; identify them by `label`.

| What | Handle |
|---|---|
| Shipment service | `input[type=radio][name="service_selection"]` |
| Export licence (EOR) | `input[type=radio][name="export_license"]` |
| Import licence (IOR) | `input[type=radio][name="import_license"]` |
| Ship date | `kat-date-picker#crossBorderSendByDatePicker` (`data-testid="cross-border-ship-date-picker"`) |
| Fetch the quote | `kat-button[label="Get shipping cost"]` |
| Confirm | `kat-button[label="Accept charges and confirm shipping"]` |
| Account + marketplace | first line of `document.body.innerText` |

- **A feedback widget repeats once per section**, each with its own
  `kat-button[label="Cancel"]` and a disabled `kat-button[label="Submit"]`
  at the same screen position. On Step 1 the wizard's own buttons are
  only `Pack individual units`, `Start new` and `Go to Shipping Queue`; on
  Step 2, `Get shipping cost` and `Accept charges and confirm shipping`.
  A disabled `Submit` is always the widget.
- **No field on this wizard is a light-DOM input.**
  `document.querySelectorAll('input')` finds only the header search box.
  Every field that matters lives in a shadow root: drive them by
  coordinates and typing.

### The ship date

`kat-date-picker#crossBorderSendByDatePicker` is a stable id. Its shadow
root has no `<input>`, so there is nothing to type into, and day cells
can't be located by DOM query. `Get shipping cost` stays disabled while
its `value` attribute is empty, so `value` is what to read back:

```python
js("return document.getElementById('crossBorderSendByDatePicker').getAttribute('value')")
# e.g. '15/08/2026' — the picker's locale format (DD/MM/YYYY on en-AE)
```

Setting it is a visual step: coordinate-click the field to open the
calendar, `capture_screenshot()` and look at which days are live (the
rest render greyed), coordinate-click one, then read `value` back — the
DOM read is what confirms it, not the highlighted cell. This needs a
model that can view images; without one, ask the user to set the date
through `AskUserQuestion` rather than guessing coordinates.

**A requested date that is greyed out is not a failure.** Only a narrow,
moving window of days is selectable (Sundays are greyed too); take the
earliest live day, and report the date you actually set. Don't compute a
substitute.

## 4. Cancelling

See [references/cancel.md](references/cancel.md). In short: a
**confirmed** shipment is cancelled from its Step 3 page
(`Delete shipment and charges`, behind a required checkbox), not from the
Shipping Queue; an **unconfirmed draft** cannot be reliably voided — the
void dialog redirects as if it worked while the draft stays alive — so
don't claim you voided one and don't spend a run trying. Cancelling a
confirmed shipment is irreversible: confirm with the user first.

## 5. Reporting

Report the shipment id (`FBA…`), destination FC, box and unit counts,
mode and carrier, and the accepted amount with its currency. If any
differ from the request — Amazon split the shipment, chose a different
FC, or re-boxed — say which, rather than reporting success.
