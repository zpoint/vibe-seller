---
description: "Example: say one sentence in Codex; Codex calls Vibe Seller to create a store task that downloads last month's sales report, then fetches the report files and analyses the data itself."
---

# Have Vibe Seller download a report from Codex, then analyse it

Say one sentence in a Codex chat, and Codex calls Vibe Seller to create a task on
your store: the task signs in to Seller Central with the store's own browser and
downloads last month's sales report. When the task is done, Codex fetches the
report files, reads the data and analyses it the way you asked.

Vibe Seller does the part in the seller center — signing in and downloading the
report; what to do with the files is up to Codex.

About 5 minutes to set up. Claude, WorkBuddy, Qoder and the other tools work the
same way.

## Before you start

- A store whose browser backend is bound and can sign in to Seller Central (see
  [Stores and browsers](stores.md));
- Codex connected to Vibe Seller (see [Connect Codex](mcp-codex.md)).

## 1. Say it in Codex

Start a new chat and paste this, with your own store name:

> Use Vibe Seller to create a task for Store A that downloads last month's sales
> report. When the task is done, fetch the report files, total sales and orders
> by SKU, and list the top ten.

## 2. What happens while it runs

1. **Creates the task**: Codex finds Store A in Vibe Seller and creates a task
   on that store;
2. **The task runs**: it opens the reports page in Seller Central and downloads
   last month's report into the task's files. Downloading Amazon reports is
   already covered by the built-in reports skill — you don't have to teach it;
3. **Waits**: Codex waits in the chat until the task finishes. If the task needs
   an answer from you or a plan approved, the question shows up in the chat;
4. **Fetches the files and analyses them**: when the task is done, Codex pulls
   the report files, reads the data, totals it by SKU and lists the top ten in
   the chat.

![Demo: driving Vibe Seller from Codex — waits sped up](images/mcp_codex_demo_en.gif)

## What you get after it runs

- **In the Codex chat**: the analysis, and the report files it used;
- **On the Vibe Seller Tasks page**: a new store task, just like one you created
  by hand. Open it to see the run and the result; the **Generated Files** panel
  at the bottom holds the downloaded reports — download one, or take everything
  with **Download All (ZIP)**.

Want a different cut — by week, or against the previous month? Keep asking in
the same chat: Codex already has the files, so no new task is needed.

## FAQ

**It answered, but didn't actually create a task?**

It answered from its own knowledge. Say "create a Vibe Seller task" explicitly.

**Not every report was downloaded — how do I get the rest?**

In the same chat, ask Codex to send a follow-up to the **same task**, e.g.
"download the report you missed"; the task continues from its last result.
Don't **Re-run** — that clears the task's workspace and the files already
downloaded go with it.

**Can the AI skip my confirmation and change my store directly?**

No. It can only work by creating tasks, and plan approval and the other checks
are exactly the same as in the web UI.

**Can't connect, or getting 401?**

See [MCP setup · Troubleshooting](mcp.md#troubleshooting).
