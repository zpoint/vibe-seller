---
description: "Example: every day at 09:30, export this store's previous-day sales report, keep the CSVs per marketplace in the task files, and send the summary plus the files to the WeCom group. The files are also archived in the store's Run Data."
---

# Export yesterday's sales report every day and post it to the group

Every day at 09:30 Vibe Seller uses this store's own browser to get into Seller
Central, exports yesterday's sales data, saves it as CSV per marketplace in the
task files, and sends the summary — revenue, orders, refunds, anything unusual —
together with those CSVs to your WeCom group.

Setup takes about 5 minutes; after that it runs every day by itself.

## What you need first

- A store with its browser backend bound and able to sign in to the seller
  console (see [Stores & Browsers](stores.md));
- A **WeCom** group bot: file delivery works on WeCom only today — DingTalk,
  Feishu and Slack bots can send text but not files (see
  [Chat bots](chat-bots.md)).

## 1. Add the group bot

Exactly the same as [step 1 of the mailbox example](example-email-wecom.md#1-add-the-group-bot):
**Settings → Integrations → Chat bots** → **Add Bot**, paste the Webhook URL,
**Save**, then **Test**.

## 2. Create the schedule

On the **Tasks** page in the sidebar, **select this store** on the left (reports
are exported per store, and a store schedule has no mode to choose), switch to the
**Scheduled** sub-tab and click **New Schedule** — the dialog is titled "New
Schedule for {store}".

**Title**: "Daily sales report"

**Description** (copy this block, and replace the group name with yours):

> Export yesterday's sales report for this store, save it as CSV per marketplace
> in the task files, then send the CSVs plus a short summary (revenue, orders,
> refunds, anything unusual) to the "Ops group" bot.

**Frequency**: every 1 days; **At time**: 09:30; **Timezone**: the zone
you work in — the schedule fires in that zone.

![The New Schedule form: every 1 day 09:30, store scope so no mode to pick](images/example-report-schedule_en.png)

Click **Create**. The AI drafts the plan right away (export order lines →
summarise by SKU → post to the group), freezes it on the schedule, and the status
becomes **Ready**; every later day runs that plan — see
[The plan is authored once, at creation](schedules.md#the-plan-is-authored-once-at-creation).

Exporting Amazon reports is something the built-in report skill already knows:
Business Reports, FBA, tax documents and ad reports — the entry points and the
download steps are covered, so you don't have to teach it.

## 3. Several stores: one schedule each

Per-store schedules are the clearest way to do reports: create a "Daily sales
report" under each store and each posts its own files to the group. **All Stores ·
Fan out** covers every store in one schedule too, but each store's task sends its
own files, so the group gets N messages — with many stores, separate schedules
read better. Scopes are compared in
[Two scopes and the execution modes](schedules.md#two-scopes-and-the-execution-modes).

## What you get after it runs

Open that round's run on the **Tasks** page: the result is numbers, and a file
list.

![The finished run: yesterday's revenue, orders, refunds, an anomaly, two CSVs, posted to the Ops group](images/example-report-result_en.png)

At the same time the WeCom group gets the summary with both CSVs under it. You can
also take them from the **Generated Files** panel at the bottom of the task, or
click **Download All (ZIP)**.

## Where the files live

- **This run's files** are in the task's working directory (on the server:
  `~/.vibe-seller/tasks/<task id>/`); the result report usually names the paths;
- **Important outputs are archived by the AI** into this store's **Run Data**,
  bucketed by month — open the **Knowledge** page, pick the store, and browse or
  download from there. When a later run needs the same data, it reads the files
  already there instead of scraping page by page again; that's why report tasks
  get faster over time, see [Run Data](knowledge.md#run-data).

## Questions this example gets

**DingTalk / Feishu / Slack groups don't get the files?**
File delivery is WeCom-only today (5 bytes to 20 MB per file). On other platforms
the summary still arrives; take the files from the task or from the store's Run
Data.

**Does Re-run throw away the report it already downloaded?**
Yes. **Re-run** clears this task's conversation and working directory, and the
files it produced go with it. To build on the last result, use **Continue** or
just send a follow-up, e.g. "re-summarise the US file by SKU".
[Continue, retry, stop, delete](using-tasks.md#continue-retry-stop-delete) covers
the difference.

**The report didn't export, or the numbers look wrong?**
The result report says which step it didn't complete. The usual causes: the store's
browser lost its login, the report page changed, or the date range wasn't what you
wanted. First confirm you can get into the console with that store's browser, then
ask in the same task to redo that step.

**Weekly or monthly instead, or making sure no day is skipped?**
Set the frequency to every 1 weeks / months and state the date range in the
description. Between runs of the same schedule the AI keeps its own cursor —
"which day the report got to" — and picks up where it stopped, with nothing for
you to configure: see [Memory across runs](schedules.md#memory-across-runs).
