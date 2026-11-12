---
description: Integrations overview — what Vibe Seller can connect to, where each one is configured, whether it follows a store, a person or the whole install, and what the AI is allowed to do with it.
---

# Integrations Overview

Vibe Seller itself only schedules and executes. Getting into a store, reading mail, posting to a group or making an image all need an external service hooked up. This page answers three things: **what you can connect**, **where you configure it**, and **how they differ from each other**.

Configuration lives in three places: `Settings → Integrations` (five cards, in this order: ads, API keys, chat bots, Google Workspace, TickTick), `Settings → Email` (its own tab), and `Settings → AI` (image generation is the card at the bottom).

## What you can connect

| Integration | What it does | Where you set it up | What it attaches to | What the AI may do | Details |
|---|---|---|---|---|---|
| **MCP access (API keys)** | Lets **another** AI tool (Claude Code, Codex, WorkBuddy, Qoder…) drive this server in return: create tasks, follow up, answer questions, review plans | `Settings → Integrations → API keys` → "**New API key**" | Follows the person — every key acts as whoever created it, with that person's permissions | Same as its owner, no finer tiers; except the things only the web UI allows (managing users, minting more keys, revealing saved passwords) | [MCP access](mcp.en.md) |
| **Vibe Seller Ads API** | Ads work without clicking a browser: reports, bids, budgets, structure. Amazon stores only for now | `Settings → Integrations → Vibe Seller Ads API`, then "**Authorize**" on each store's row under "Stores" | Follows the store — one authorization per store (two marketplaces of one store need two), and only an admin can authorize or revoke; this install's service key is minted by the service on first use, you never type one | Reads and writes, but only inside the advertiser account this store authorized; changes land only on rows you approved | [Ads API setup](ads-api.en.md) |
| **Email (IMAP / SMTP)** | Inbox syncs to local storage every 5 minutes; the AI searches mail, sorts it, drafts replies and sends them | `Settings → Email` → "**+ Add Email**" | The mailbox rows are install-wide (who added one is only a record), then linked to stores from the mailbox card or `Settings → Stores`; which mailboxes a task can see depends on what its store is linked to | **Read**: the synced mail. **Send**: the "**Email sending permission**" switch on each mailbox card, on by default — only a signed-in human can flip it, the AI cannot | [Email setup](email.en.md) |
| **Chat bots (WeCom / DingTalk / Feishu / Slack)** | Pushes task results and alerts into a group channel (sending files works on WeCom only) | `Settings → Integrations → Chat bots (WeCom / DingTalk / Feishu / Slack)` → "**Add Bot**" | Shared across the install, not per store; the platform is detected from the webhook URL you paste | Outbound only: it can post messages and files; reading the group back isn't wired up | [Chat bots](chat-bots.en.md) |
| **Dida365 / TickTick** | The AI can see your lists and write to-dos into them | `Settings → Integrations → Dida365 / TickTick` → the five-step wizard, ending with "**Authorize**" | One connection for the whole install; you may pin one default list, and if you don't the AI reaches all of your projects | Reads and writes your own lists and tasks | [TickTick](ticktick.en.md) |
| **Google Workspace** | Sheets, Drive, Gmail, Docs and Calendar as a bundle: 19 sub-skills installed under one umbrella | `Settings → Integrations → Google Workspace` → switch on "**Enable**". The gws CLI has to be installed and signed in on this machine first, and only an admin can flip this | One switch for the whole install; every task shares the same authorization | Whatever you granted, read and write — there are no per-product switches | [Google Workspace](google-workspace.en.md) |
| **AI image generation** | Product hero images and infographics, or editing an image against a reference | `Settings → AI → Vision (Image Generation)` → paste the kie.ai API key → "**Save**". Only an admin can enter the key | One key for the whole install | Only on request inside a task, and nothing is generated until you press "Generate" on the review card | [Below](#ai-image-generation) |

## How they differ

### Two ways to read mail: Email vs Google Workspace

- **Email** speaks plain IMAP / SMTP: any mailbox works (163, QQ, Gmail, self-hosted), the AI reads the copy that was synced down locally, and sending is governed by a switch you can set mailbox by mailbox. Right for "I just have this support inbox to work through".
- **Google Workspace** uses Google's own authorization and installs a bundle of command-line powers: not just mail, but writing docs, creating calendar events, reading sheets — live from the API rather than a cache. The price: Google accounts only, a CLI to install on the machine, one install-wide switch, and no per-capability toggle to turn things off.
- You can configure the same Gmail address on both sides — they are two independent roads that don't know about each other: one is a synced local copy of the inbox, the other calls Google live. So if you want the AI to reach that mailbox through exactly one door, connect only one of them; the other stays usable as long as it's set up.

### One is an exit, one is an entrance: chat bots vs MCP access

- **Chat bots** point outward: the task posts its result to the group, people read it there — but nobody can drive Vibe Seller through the bot.
- **MCP access** points inward: the AI tool already in your hand connects and creates tasks, asks follow-ups, answers questions and reviews plans for you. Each key it uses is pinned to one user identity and carries that person's full permissions.
- Neither of these is the toolset the agent inside Vibe Seller uses while working — three separate channels, none impersonates another.

### API or browser: the Ads API vs ad tasks in a store browser

- Ads themselves — reports, targeting, search terms, bid and budget changes — are far faster and steadier through the API than by having the AI click through the console ([Ads API setup](ads-api.en.md) has the 147-minute vs 11-minute measurement).
- Everything in the seller center that isn't ads — coupons, report downloads, account health and appeals, category work — only exists on the web, so that still runs through the store's browser. A store you never authorized falls back to the browser route for ads too.

### What follows a store, a person, or the machine

- **Per store**: the browser backend and its profile (Ziniao account or persistent Chrome profile — see [Stores & Browsers](stores.en.md)), the Amazon Ads authorization, and the mailboxes linked to the store.
- **Per person**: API keys for MCP access.
- **Per machine (whole install)**: mailboxes, chat bots, Dida365 / TickTick, Google Workspace, the image-generation key.
- In practice: a second person signing in has to create their own API key, while mail, bots, lists, Workspace and image generation are one shared set for the machine. Ads authorization hangs off the store rather than the person and only an admin can grant or revoke it — two people sharing a store authorize it once, through the admin.

## AI image generation

**What it is**: inside a task the AI can propose "make a lifestyle shot of this product" or "swap the background on this reference image". It runs on kie.ai's unified API (Nano Banana-class models), so one key reaches every model in the catalog.

**Where you set it up**: `Settings → AI → Vision (Image Generation)`. The card shows a status pill — "Not configured", or "Key set" with the last four characters. Its own wording: "kie.ai API key for AI product-image generation (Nano Banana). Required before the agent can generate main images or infographics." Paste the key into the masked field and hit **Save**. **Only an admin can set it**; everyone else sees "Only an admin can set the Vision API key."

With no key, the image tool isn't offered to the agent at all — asked for an image, it will only send you to this page. And this card really is just the key: you don't pick a model or see prices here, all of that lives on the review card below.

**Every step needs your nod**: the AI only proposes; it can't generate on its own. The flow is: the AI drafts a prompt and picks a model → a "**Review image generation**" card appears in the conversation — the prompt is editable ("Prompt (edit before generating)"), the model can be swapped ("Model"), reference images can be attached ("Reference images"), and the card shows roughly what this image costs → you press "**Generate**" and it starts ("Generating image… this can take 1–2 minutes"). "**Cancel**" generates nothing and costs nothing. Whatever you edited wins over what the agent proposed. The finished image shows inline in the task stream ("Generated image") and is saved in that task's workspace. The confirmation is enforced server-side, so it holds whether the task runs in auto mode or plan mode.

**Where it stops**: this feature only creates new files — it never edits the original image sitting on your disk; if the AI needs to touch a local file, that's the file route, not this one.

> The technical side (endpoints, model catalog, syncing, pricing) is in [the image-generation section of the developer docs](vision.md).

## Several integrations in one task

They don't compete; the usual pattern is stacking them in a single scheduled task, for example a Monday-morning one:

> "Go through last week's buyer mail for the acme store, pull out negative reviews and refund requests; for the SKUs with ads running, check that search term's spend and conversions in the ads console; post the conclusion to the WeCom group and put anything that needs following up into TickTick."

That single task uses: the store's linked **mailbox** (read) → the **Ads API** (through this store's own authorization) → a **chat bot** (push the summary) → **TickTick** (write the to-dos).

What you can stack depends on the task's scope: a task bound to a store gets that store's browser, ads authorization and linked mailboxes; an "All stores" task only has the generic web browser plus the shared integrations (email, bots, TickTick, Google Workspace, image generation) and can't get into any store's backend.
