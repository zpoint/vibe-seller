---
description: 'Drive Vibe Seller from the AI tool you already use — MCP setup: create one API key, then open the page for your tool (Codex / WorkBuddy / Qoder / Hermes Agent / Pi / Claude), plus troubleshooting.'
---

# MCP setup

Already live in Claude, Codex, WorkBuddy, Qoder, Hermes Agent or Pi? You can now
drive Vibe Seller from those tools: create tasks, follow their progress, and
answer their questions.

Just say something like “look at Store A's ads from last week” in the chat. The
tool creates the task in Vibe Seller, keeps an eye on it, and brings the summary
back to you. Questions the task wants to ask you, and plans that need your nod,
all happen in the same conversation — no more hopping between windows.

Any MCP-capable tool works; all you need is an API key. Where each integration
is configured and how they divide up: see the
[Integrations overview](integrations.md).

## What you can ask

Once connected, just talk to it like a colleague:

- “How many stores does vibe-seller have right now?”
- “Look at Store A's ad spend over the past week and suggest improvements —
  don't change anything yet.”
- “What is that task doing at the moment? How far along is it?”
- “Turn this week's inspection into a weekly Monday-morning run — show me the
  plan first.”

You don't need to remember any commands — the tool figures out what to call.

## What it can and cannot do

- **Can**: create tasks (with attachments), follow progress, answer the task's
  questions, approve or reject plans, confirm generated images, send
  follow-ups, stop / retry / delete tasks, read a task's output files, manage
  the knowledge base, skills and schedules.
- **Cannot**: drive the browser (click, open pages, take screenshots) or edit
  your store directly.

Anything store-facing happens inside a Vibe Seller task, and plan approval,
the ad-scope checks and the rest of the gates work exactly as they do in the
web UI. Routing work through Vibe Seller keeps each store's data isolated and
keeps the AI from going hands-on.

Ad tasks created from an AI tool follow the same rules as ones you create in
the web UI: before any work starts the task must state exactly what it will
check and change; a report that skips marketplaces gets sent back to finish the
job; and a report-only task never touches your ads.

## First: create an API key

In the Vibe Seller web UI, open **Settings → Integrations → API keys** and
click **New API key**:

1. Give it a name you'll recognise later, like `claude-desktop`;
2. Pick an expiry: 30 days, 90 days, 365 days, or never;
3. **Copy the key** right away. It is shown only once — close the dialog and
   it's gone for good.

![Creating an API key in Settings → Integrations](images/mcp_apikey_create_en.png)

The same dialog also hands you ready-to-paste configs, one card per tool: the
"In the app" tab for tools you set up inside the app, "Command line / config
file" for the rest — the same tabs each setup page uses. Set the **server
address** to one the tool can reach first; the configs update as you type:

![The created-key dialog: the key and per-tool configs](images/mcp_apikey_created_en.png)

A few things to keep in mind:

- Keys start with `vst_`: **a tool holding one acts as you, with your
  permissions**.
- Revoke it in the same place whenever you like — if you're done with it or
  suspect it leaked.
- Create a key even if your Vibe Seller runs without login. Turn login on later
  and the tool won't suddenly stop connecting.

## Choose your AI tool

Take the key you just created and open the page for the tool you use:

| Tool | How you connect |
|---|---|
| [Codex](mcp-codex.md) | Fill a form in the app |
| [WorkBuddy](mcp-workbuddy.md) | Paste JSON |
| [Qoder](mcp-qoder.md) | Paste JSON |
| [Hermes Agent](mcp-hermes.md) | Paste JSON |
| [Pi](mcp-pi.md) | Command line |
| [Claude](mcp-claude.md) | Command line |

Using another MCP-capable tool, or after the technical details (the protocol,
all 32 tools, curl examples)? See [Connect an External Agent](agents.md).

## Troubleshooting

**401 Unauthorized**

The key is wrong, revoked, or expired, or its owner is deactivated. Check the
key under **Settings → Integrations → API keys**. A revoked or expired key is
rejected even when the server runs with login off — revoking really revokes.

**405**

The address is wrong, or your Vibe Seller is too old to have the MCP endpoint.
Make sure the address ends in `/api/mcp`, and upgrade Vibe Seller if needed.

**Connection refused, or certificate (TLS) errors**

Check the address and port, and whether the tool's machine can reach Vibe
Seller at all. A server bound to `127.0.0.1` is only reachable from the same
machine; put an HTTPS reverse proxy in front for remote use.

**Tools are listed but every call fails**

Usually a wrong address. All the configs on the tool pages want the address
ending at `/api/mcp`; the only exception is the Claude **plugin**, which takes
just the server root (e.g. `http://localhost:7777`) and appends `/api/mcp`
itself. Also check for a stray trailing slash.

**It answers without creating a task**

It answered from its own knowledge instead of running anything. The Claude
plugin helps a lot here — it requires the agent to report only what the task
really produced. You can also say outright “use vibe-seller to create a task
for this”.
