# Cancelling an FBA inbound workflow or shipment

Both actions below are irreversible. Get the user's go-ahead through
`AskUserQuestion` before the final click, and always re-read afterwards.

## A confirmed shipment — from the wizard's Step 3

The Shipping Queue is the obvious place to look and the answer is not
there. Dead ends: the API's `cancelInboundPlan` (refuses SEND plans), the
new Shipments page row menu (`Track` · `Download SKU list` ·
`Send again`), and the old Shipping Queue (`/gp/ssof/shipping-queue.html`)
`Next steps` menu (`Download SKU list` · `Send it again`).

The route is the workflow's own Step 3:

```
…/fba/sendtoamazon/print_labels_step?wf=<workflowId>
   → 「Delete shipment and charges」   a[data-testid="void-btn"]
     href /fba/sendtoamazon/workflow/void?wf=<workflowId>
```

It sits under the `Workflow ID` line at the very foot of the page, below
the fold — scroll to it.

1. Click `a[data-testid="void-btn"]` (a JS `.click()` is enough). It is a
   real navigation: the URL becomes `/workflow/void?wf=…`, which renders
   the wizard page again with a dialog on top.
2. Tick the dialog's checkbox — *"I confirm that any FBA box ID labels and
   shipping labels generated for this workflow will be destroyed"*. Until
   you do, `kat-button[label="Delete shipment and charges"]` is disabled.
3. Click that button. The shipment moves to `Canceled`.

If a click seems to do nothing, check the URL and take a screenshot
before theorising: the dialog's paragraph and long checkbox label are
easy to miss with a DOM probe that keeps only short leaf text.

Verify in the Shipping Queue: a cancelled shipment reads `Cancelled`
there, with `Last updated` at the deletion time. The page you clicked on
may still say `Ready to ship` afterwards.

## An unconfirmed draft — effectively cannot be voided

`Void this workflow` sits at the foot of Step 1 beside `Workflow ID` and
`Created:`, only on a draft that hasn't gone past Step 1. Clicking it
opens `/fba/sendtoamazon/workflow/void?wf=…` with a dialog
(`Are you sure? Voiding a workflow cannot be undone.` / `Cancel` ·
`Void workflow`); `Void workflow` then redirects to the Shipping Queue,
which reads like success. Checked afterwards by loading
`confirm_content_step?wf=<id>` and comparing the footer's `Workflow ID:`,
voided drafts were still alive.

- A draft past Step 1 has no void entry point at all (Step 1 redirects
  forward, `print_labels_step` redirects back, the header pencil is
  `Rename workflow`).
- `Active workflows` (a header panel, not a URL) is not a reliable census;
  drafts that load by id can be missing from it.

Unconfirmed drafts accumulate and are harmless — no shipment, charges or
inventory commitment. Don't claim you voided one and don't spend a run
trying; if the task asked for it, report that the portal offers no
working void for drafts.
