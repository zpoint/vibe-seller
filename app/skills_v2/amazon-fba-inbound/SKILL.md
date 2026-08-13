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

### The trap: the SEND partners *are* in the option list

The carriers Amazon names as its SEND partners **do** come back from
`listTransportationOptions`, by `carrier.alphaCode`. Picking one does
**not** book a SEND shipment — it books that company as a carrier *you*
arranged. Same haulier, different product, and the difference is
everything: no Amazon-arranged freight, no Amazon-issued tracking, no
partnered price, and the shipment then waits on you for pallet or
tracking details.

Verified end to end on a live account: confirming a SEND partner's
`USE_YOUR_OWN_CARRIER` option produced a shipment with
`freightInformation: null` and no quote — Amazon arranged and priced
nothing.

**The shipment's name tells you nothing about which one you have.** That
API-confirmed non-SEND shipment was auto-named `FBA STA (…)`, and a
genuine SEND shipment booked through the portal on the same account
hours later was *also* named `FBA STA (…)` — while older genuine SEND
shipments there read `FBA SEA (…)`. `STA` is just "Send To Amazon", and
the `SEA`/`STA` difference is naming history, not product. Reading the
name as evidence is how a real SEND booking gets mistaken for the trap.

**Litmus test, and it is the only reliable one:** call `getInboundPlan`
on the plan. A SEND plan is refused **by name** —
`GetInboundPlan is not supported for the cross border Seller Export and
Delivery program` — and an ordinary one reads back. `listInboundPlans`
agrees: a SEND plan appears under no status (`ACTIVE`, `SHIPPED`,
`VOIDED` are the only ones it accepts), so a confirmed plan you cannot
find in any of them is a SEND plan. It also tells you *when* the
difference is decided — the plan is one kind or the other from creation,
so no choice at the transport step converts it.

What a genuine portal-booked SEND shipment reads on Step 3, for
comparison: `Carrier: Amazon Partnered (<partner>)`,
`Shipping mode: Ocean Shipping - LCL`,
`Amazon partnered carrier fees: AED <amount>`. An Amazon-partnered fee and
a shipping mode are the positive signal; `freightInformation: null` is
the negative one.

So: if the task wants SEND, use the portal. If it wants one of those
carriers as your own forwarder, the API is fine — just don't confuse the
two, and say in your report which one you booked.

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

**Step 1 and Step 2 are two different pages**, and confusing them wastes a
run — Step 2 cannot be reached until Step 1 is clean:

```
Step 1  …/fba/sendtoamazon/confirm_content_step?wf=<workflowId>    choose inventory
Step 2  …/fba/sendtoamazon/confirm_shipping_step?wf=<workflowId>   quote + confirm
```

An in-progress workflow is resumable at either URL; `&preselected=<code>`
appears once a carrier has been chosen. Four `H4` steps:

| Step | Heading | Page |
|---|---|---|
| 1 | `Step 1: Choose inventory to send` | `confirm_content_step` |
| 1b | `Step 1b – Pack individual units` | " |
| 2 | `Step 2: Confirm shipping` | `confirm_shipping_step` |
| 3 | `Step 3: Print box labels` | " |

Once packed, Step 2 restates the scope — `SKUs: N  Units: N  Ship from: …`.
Check it against the task there; it is cheaper than discovering a mismatch
after a confirmation.

### Step 1 — choose inventory (verified live 2026-08-12, `sellercentral.amazon.ae`)

Two tabs, `All FBA SKUs` and `SKUs ready to send (N)`. The table is
`SKU details · Packing details · Information/action · Quantity to send`, and a
row reads:

```
<listing title>
SKU: <your-msku>       ASIN: <asin>    FBA Storage Type: Standard-size
Product Category:       Prep not required   Unit labeling: By seller - Print SKU labels
More inputs
```

- **Packing details** is a `kat-dropdown` offering `Individual units` or
  `Create new case pack template`.
- A selected row carries **two** number fields plus that dropdown:
  `kat-input[name="numOfUnits"]` (label `Units`) and
  `kat-input[name="numOfBoxes"]` (label `Boxes`). A **Bulk edit** panel at the
  top of the table has its own pair with the same labels — match by bounding-box
  `y` against your row, or you will be typing into the bulk panel.

#### The recipe that works, driven by hand on 2026-08-12

Three agent runs failed on this step; ten minutes of driving it directly settled
it. Per SKU, in this order:

0. **`Start new` first**, unless you were handed a workflow id — see above. This
   is worth its own numbered step because it is skipped so easily: the workflow
   you land on can be **weeks old and already hold staged SKUs**. On 2026-08-13 a
   visit to `confirm_content_step` landed on `STA (27/07/2026, 10:14 AM)` with
   `SKUs ready to send: 3 (390 units)` on it, and editing began before anyone
   noticed. **Read the footer and the `Current workflow` label before your first
   click**; a non-zero count is somebody else's work.
1. **Find the row.** The table paginates (25 a page), so the SKU you want may not
   be on the first one. Match the row by its `SKU: …` text and work from that
   element's bounding box.
2. **Set the product category and press `Save`** (see below) — before the
   quantity, because committing the row locks Step 1.
3. **Type the quantity into that row's `Units` field.** Coordinate-click it, then
   `type_text`. No `.value` (below).
4. **Click that row's own `Ready to pack` button.** It does not exist until a
   quantity is entered, and it appears **inside the row**, to the right — there is
   no page-level equivalent, and this is the click that commits the row.
5. **Read the footer**: `SKUs ready to send: 1 (10 units)`. The tab label
   (`SKUs ready to send (1)`) moves with it. That is the page confirming it took
   the number.

**No checkbox is required** — the row's tick box stays unticked through all of
this; entering a quantity is what activates the row. And a SKU that already reads
`Product Category: General` needs nothing doing to it; only the rows showing
`⚠ Product category required` do.

#### Type into these fields. Never assign `.value`.

**Every `kat-input` on this page is a web component whose real `<input>` lives in
a shadow root** — the same trap §3 records for the ship date, and it applies to
every quantity here. Assigning `.value` on the element, or reaching through to
the inner input with React's "native setter", tells Katal nothing: the field may
even *show* your digits while the app never hears about them.

**The footer is the only proof.** `SKUs ready to send: N (N units)` is the page
saying it accepted the number. If it still reads `0 (0 units)`, the number did
not land, however convincing the input looks — and a reload will show the row
empty, because the server was never told.

Burned on 2026-08-12: one run spent ~70 steps and 25 minutes on a single
quantity, trying element `.value`, the native-setter trick, triple-click plus
`dispatchEvent`, and the bulk-edit panel. All of it read back fine locally and
none of it persisted. Do it the way a person does — coordinate-click the field,
then `type_text` real key events — and read the footer to confirm.
- `Product Category` / `Prep` / `Unit labeling` appear only once a row is
  selected.

**What a row still owes you is a `kat-link`, one per requirement** — this is the
part that is easy to hunt for in the wrong place:

| Link label | What it is |
|---|---|
| `Product category required` | the customs classification, blank by default |
| `Prep and labeling details needed` | prep + who labels the units |
| `Add packing line` | split the SKU across packing groups |

**`More inputs` is not where these live.** That `kat-expander` has **no slotted
content** — opening it and looking for a category dropdown finds nothing, and
`document.querySelectorAll('kat-dropdown')` on this page only ever answers the
per-row `Individual units` control. Click the link whose label names what is
missing. The row's own text shows the current value (`Product Category: General`),
so the *label* says "required" while the *text* shows what it holds — do not read
the visible word as proof it is set.

**Match the link to the row that names your SKU** — every selected row has its
own copy, so "click the `Product category required` link" in document order opens
the modal for whichever SKU happens to come first. Find the element whose text is
your MSKU, take its bounding box, then take the link on that row (nearest by `y`),
the same way the Actions column has to be matched on the Shipments page.

The category modal is `.packing-template-modal`, titled `Select Product
Category`, and **its header restates the row it belongs to** — `SKU: …`,
`ASIN: …`, `FBA Storage Type: …`. Read that header before filling anything in; it
is the cheapest possible check that you opened the right row's modal.

- The foot of the page totals it and gates the step:

```
SKUs ready to send: 3 (150 units)      Total prep and labelling fees: AED 0.00
Please review SKUs with errors or unconfirmed SKUs
Workflow ID: wf…    Void this workflow    Created: …
```

- **The advance control's label depends on how far the rows have got, so read
  what is on the page instead of reaching for a fixed one.** With rows selected
  but not yet confirmed it is `Ready to pack` plus a disabled
  `Confirm and continue`; once they are confirmed the page offers
  `Pack individual units` (also disabled until the rows are clean). Both
  observed on 2026-08-12, on two workflows of the same account.
- Whichever it is, **it stays disabled while that review line shows**, so a
  selected SKU carrying an unmet requirement is what stops the whole run — not
  anything on Step 2.

### The Product Category gate — an empty field here surfaces as a Step 2 error

`Product Category` is routinely **blank** on a SKU that is otherwise perfectly
healthy (verified: a healthy SKU, blank category). A
blank one is an "unconfirmed SKU", and the cost is paid two steps later:
Step 2 answers

```
To proceed with Amazon Partnered Carrier, please provide product category on Step 1
```

and leaves `Get shipping cost` **disabled**, so no quote of any kind can be
fetched. Amazon needs the classification because a partnered cross-border
shipment is customs-declared freight.

So when a quote cannot be fetched, **go back to Step 1 and read the rows** —
the answer is nearly always a blank category there, and it is fixable in the
`More inputs` expander of that row.

**The whole vocabulary is three radio buttons**, and the modal comes up with the
third already filled in:

```
Select Product Category   —   SKU: <your-msku>   ASIN: <asin>
   ○ Electronics     ○ Sensitive Product     ● General
                                        [Cancel]  [Save]
```

**A row that displays `Product Category: General` has nothing stored.** That is
the trap, and it cost two wrong conclusions in a row: `General` is both a
legitimate choice *and* the default the row renders before any choice is
recorded, so it reads exactly like a value that is already set. It is not.
Verified 2026-08-13 on a workflow holding a single SKU, which showed
`Product Category: General` throughout: with the ship date set, the service
picked and both licences chosen, Step 2 still refused with
`To proceed with Amazon Partnered Carrier, please provide product category on
Step 1` and kept `Get shipping cost` disabled.

So for every SKU in the shipment: **open the modal, choose, and press `Save`** —
even when the radio already shows what you want. Pressing `Save` is the only
thing that records it. `⚠ Product category required` and a bare `General` are
the same state wearing different clothes.

**Do it before you commit the row.** Once `Ready to pack` is clicked, Step 1 is
closed to you: `confirm_content_step?wf=…` redirects *forward* to Step 2, the
`Step 1: Confirmed inventory to send` header is not a link back, and the
`View contents` dialog is read-only with no category in it. A workflow committed
without a saved category is a dead end — void it and start again. So the order
is **category → Save → quantity → `Ready to pack`**.

**A SKU Amazon cannot resolve at all is a different thing and is not fixable
here**: its ASIN cell is empty and it says
`The SKU for this product is unknown or cannot be found` (seen live). No category can be set on a SKU with no listing
behind it. Stop and name the SKU — that is a catalog problem, not a wizard one.

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
| Ship date | `kat-date-picker#crossBorderSendByDatePicker` (`data-testid="cross-border-ship-date-picker"`) |
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
  "click the Submit button" step hits that widget, not the wizard. Step 1
  carries **five** copies (verified), so of the 13 `kat-button`s on that page
  only three belong to the wizard: `Pack individual units`, `Start new` and
  `Go to Shipping Queue`. On Step 2 the wizard's are `Get shipping cost` and
  `Accept charges and confirm shipping`. **A disabled `Submit` is always the
  widget, never the wizard.**
- **The ship date has a stable handle, and one honest state.** It is
  `kat-date-picker#crossBorderSendByDatePicker` — a real id, not a generated
  `katal-id-*`, so key on it. Two things worth knowing before you fight it: its
  shadow root contains **no `<input>`** (so there is nothing to type into by
  selector), and **`Get shipping cost` stays disabled while its `value`
  attribute is empty**. So `value` is the thing to read back — an on-screen
  calendar and a highlighted day prove nothing, and a run on 2026-08-12 spent
  most of its budget clicking day cells that never landed. Verify
  `document.getElementById('crossBorderSendByDatePicker').getAttribute('value')`
  is your date, then check the button.

  **A requested date that is greyed out is not a failure — take the earliest
  selectable day instead and say which one you took.** The calendar is the
  authority on what Amazon will accept, and the rule behind it is not worth
  reverse-engineering: it moves. On 2026-08-12 the 13th, 14th and 15th were
  offered and the 16th was not; on the 13th the 15th had already gone and the
  earliest was the 17th. Sundays are greyed too. So do not fail a run because the
  task's date is unavailable, and do not compute a substitute — read the picture
  and click the first day that is live. This is the one field where moving a value
  the task gave you is correct, because the page is refusing it; report the date
  you actually set.

  **What works**, done by hand on 2026-08-13: coordinate-click the field to open
  the calendar, **screenshot it**, and coordinate-click the day cell you read off
  the picture. `value` then reads e.g. `15/08/2026` (the `locale="en-AE"` format,
  DD/MM/YYYY). What does **not** work: `Input.insertText` into the focused field
  (the picker has no inner `<input>` to receive it), and locating day cells by DOM
  query. Only a narrow window of days is selectable — the rest render greyed — so
  read which ones are live off the screenshot rather than assuming your date is
  offered.
- **No field on this wizard is a light-DOM input.**
  A light-DOM sweep finds almost nothing to work with:
  `document.querySelectorAll('input')` returns only the header search box,
  and the page's one `<select>` is the footer language switcher. Every
  field that matters — the ship date, and every `kat-input` quantity on
  Step 1 — lives inside a component's shadow root. Drive them by
  coordinates: screenshot, click, **type**. Assigning `.value` is the
  single most expensive mistake available on this page (see Step 1),
  because it appears to work.

## 4. The order matters

`Get shipping cost` and `Accept charges and confirm shipping` are
**both disabled on load**, and the delivery-mode and carrier cards **do
not exist in the DOM yet** — they are rendered only after the quote is
fetched.

### Building a workflow from scratch

**Opening the wizard does not give you a new workflow — it resumes the account's
current one.** Step 1 says so at the top: `Current workflow  STA (12/08/2026,
04:54 PM)` beside an `Active workflows` link. So a task with no workflow id that
simply navigates to `confirm_content_step` lands in **whatever somebody left
half-built**, and every SKU and quantity already on that page was chosen by
someone else.

**Click `kat-button[label="Start new"]` first.** Verified on 2026-08-12: a job
carrying no workflow id landed on `wf…E` — three SKUs and 150 units staged
by an earlier session — and began setting a product category on *its* SKUs
instead of the job's one SKU × 10. The agent's own note at the time was
*"a workflow already exists … or it was just created by landing on this URL"*,
which is exactly the ambiguity to resolve before touching anything: if the page
names a current workflow you did not create, it is not yours.

Then, on your own workflow:

1. pick each SKU the task names and type its `Quantity to send`
2. leave `Packing details` on `Individual units` unless the task says otherwise
3. open each selected row's `More inputs` and fill what is blank — **the
   `Product Category` above all**, because a blank one costs you the quote two
   steps later (§2)
4. check the footer reads `SKUs ready to send: N (N units)` with the task's
   totals and that the `Please review SKUs with errors…` line is **gone**
5. press `Pack individual units` — it is disabled until 4 holds
6. Step 1b: enter the boxes, their weights and dimensions
7. then Step 2, below

Then, on Step 2:

1. verify account + marketplace (§1), and that Step 2's SKUs/units match
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

**Voiding an unconfirmed draft does not appear to work at all, and the
attempt looks like a success.** Do not claim you voided one.

`Void this workflow` sits at the foot of the page beside the `Workflow ID`
and `Created:` timestamp — but only on a draft that has not gone past
Step 1. Clicking it navigates to `/fba/sendtoamazon/workflow/void?wf=…`,
which renders the wizard with a small dialog on top
(`Void this workflow` / *"Are you sure? Voiding a workflow cannot be
undone."* / `Cancel` · `Void workflow`). Clicking `Void workflow` then
redirects to the Shipping Queue, **which reads exactly like success and
is not**.

Verified 2026-08-13 against four workflows, checked afterwards with a
criterion fixed in advance — load `confirm_content_step?wf=<id>` and
compare the footer's `Workflow ID:` to the one requested. **All four were
still alive**, including two whose dialog had been driven to completion:

```
wf…A  ALIVE  (4 SKUs)     wf…C  ALIVE  (1 SKU)
wf…B  ALIVE               wf…D  ALIVE
```

Two further traps around this:

- **A draft that has gone past Step 1 has no void entry point at all.**
  Not on Step 1 (which redirects forward to Step 2), not on Step 2, not
  on `print_labels_step` (which redirects back), and not behind the
  header icons — the pencil is `Rename workflow` and the circle is an
  info popover. Proven with a 5000px-tall viewport and a full-text search
  that found zero occurrences of void/cancel/delete/abandon/discard in
  the page's 60 lines.
- **`Active workflows` is not a reliable census either.** It is a panel
  opened from the header, not a URL, and workflows that demonstrably load
  by id were missing from it — so absence there is not evidence of
  deletion.

The practical consequence: unconfirmed drafts accumulate and stay. One
live account carried **31** of them, most weeks old, several with real
quantities on them. Treat a stray draft as untidy but harmless — it holds
no shipment, no charges and no inventory commitment — and do **not**
burn a run trying to remove one.

A **confirmed** shipment is different and really is removable — see
below.

**A confirmed one is undone from the wizard's own Step 3, not from the
Shipping Queue** — this is worth knowing before you go looking, because
the queue is the obvious place and the answer is not there:

```
…/fba/sendtoamazon/print_labels_step?wf=<workflowId>
   → 「Delete shipment and charges」   a[data-testid="void-btn"]
     href /fba/sendtoamazon/workflow/void?wf=<workflowId>
```

It sits under the `Workflow ID` line at the very foot of the page, below
the fold — scroll to it. Verified 2026-08-12 against a confirmed SEND
shipment, where **every other route was a dead end**:

- `cancelInboundPlan` — refuses a SEND plan, like `getInboundPlan`
- the new Shipments page, row menu — `Track` · `Download SKU list` ·
  `Send again`, no cancel
- the old Shipping Queue (`/gp/ssof/shipping-queue.html`), `Next steps`
  menu — `Download SKU list` · `Send it again`, no cancel

**The link opens a modal, and the modal has a required checkbox.** This is
the whole sequence — three steps, all ordinary:

1. click `a[data-testid="void-btn"]` (a JS `.click()` is enough). It is a
   real navigation: the URL becomes `/workflow/void?wf=…`, which renders
   the wizard page again **with a dialog on top of it**.
2. tick the dialog's checkbox — *"I confirm that any FBA box ID labels and
   shipping labels generated for this workflow will be destroyed"*. Until
   you do, the dialog's own
   `kat-button[label="Delete shipment and charges"]` reads `disabled`.
3. click that button. The shipment moves to `Canceled`.

**Do not conclude "the click did nothing" from a DOM probe that filters by
text length.** That is how an hour was lost on 2026-08-12: the dialog was
on screen the whole time, but a probe collecting only leaf nodes with
short text skipped both its paragraph and its long checkbox label, so it
looked like the link was inert and unautomatable. It was not. When a click
seems to do nothing, **screenshot before theorising** — and note the URL,
which had changed all along.

Always re-read after cancelling: a clicked button is not a cancelled
shipment, and the page you clicked on still said `Ready to ship`
afterwards. The Shipping Queue is where to *verify* — a killed shipment
reads `Cancelled` there, with its `Last updated` stamped at the deletion.

## 6. Reporting

Report the shipment id (`FBA…`), the destination fulfilment centre, box
and unit counts, the mode and carrier chosen, and the accepted amount
with its currency. If any of those differ from the request — Amazon split
the shipment, chose a different FC, or re-boxed — say which, rather than
reporting success.
