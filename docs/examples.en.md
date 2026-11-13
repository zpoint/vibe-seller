---
description: "Examples: five complete seller scenarios, step by step — where to click, what to paste, and what the finished run looks like. A daily inbox digest to WeCom, a weekly ACOS check, a listing sweep across every store, a daily sales report, and exporting a Ziniao store's financial data with Codex for analysis."
---

# Examples

Each example is something sellers do again and again. The pages take you from
where to click to what the finished run looks like, with a screenshot of every
screen and task descriptions you can copy and paste. You don't have to read the
rest of the docs first.

Before you start, this machine needs a working AI profile and at least one
store. If it doesn't have those yet, go through the [Quickstart](quickstart.md)
first.

## The five examples

- [Check the mailbox every day and send what matters to the WeCom group](example-email-wecom.md)
  No more digging through the inbox first thing: mail that needs a reply,
  supplier and platform notices, and noise, sorted into three buckets, each with
  one suggested next step, in the group at 09:00. About 10 minutes to set up.

- [Check ads every week, propose fixes above 30% ACOS, and alert me](example-ads-acos.md)
  Monday morning brings a list of everything over your ACOS line, with spend,
  orders and a proposal per row. Changes wait for your confirmation — it touches
  the ad account only after you say so. About 15 minutes to set up.

- [Sweep every store's listings once a week](example-listing-check.md)
  One trigger, one task per store: inactive and suppressed items, missing main
  images and missing required attributes, listed by store, then merged into one
  cross-store list. About 5 minutes to set up.

- [Export yesterday's sales report every day and post it to the group](example-daily-report.md)
  At a fixed time each day it exports yesterday's sales data, keeps the CSVs in
  the task files, and sends the summary plus the files to the group. About 5
  minutes to set up.

- [Export a Ziniao store's financial data with Codex and analyse it](example-mcp.md)
  One sentence in Codex: Vibe Seller opens Store A in Ziniao and downloads the last
  3 monthly transaction reports; Codex fetches them and works out sales, each fee,
  refunds and net payout per month. About 5 minutes to set up.

## What these examples use

| Capability | What it does here | Where to set it up | Details |
|---|---|---|---|
| Scheduled tasks | Creates a task on time, every time, from the same frozen plan | **Tasks** in the sidebar → **Scheduled** | [Scheduled Tasks](schedules.md) |
| Mailboxes | The AI reads, sorts and drafts, per mailbox send permission | **Settings → Email** | [Email integration](email.md) |
| Chat bots | Posts conclusions and files to a group (out only) | **Settings → Integrations → Chat bots** | [Chat bots](chat-bots.md) |
| Ads API | Ad data straight from the API instead of clicking through pages | **Settings → Integrations → Vibe Seller Ads API** | [Amazon Ads API setup](ads-api.md) |
| API keys | Lets an outside AI tool work this server | **Settings → Integrations → API keys** | [MCP setup](mcp.md) |

One task can stack several of them. That Monday ad check, for example, reads the
mailbox linked to the store, queries the store's own ad authorization, and posts
to a shared group bot. Who owns each integration — the store, you, or this
machine — is in the [Integrations overview](integrations.md).

## What these examples don't do

- Chat bots only send out: the AI cannot read what people wrote back in the group.
- Tasks don't push notifications to a group on their own. It posts only when your
  description asks it to, or when you tell it to afterwards.
- Ad changes always wait for your confirmation. The listing sweep reports problems
  and changes nothing — you decide what to fix.
