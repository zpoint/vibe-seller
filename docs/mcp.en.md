---
description: Drive Vibe Seller from the AI tool you already use — MCP setup: create an API key, connect the Claude / Codex / WorkBuddy / Qoder desktop apps, verify, try it.
---

# MCP setup

Vibe Seller can expose itself as an MCP service to **the AI tool you already
use** — Claude, Codex, WorkBuddy, Qoder, anything that speaks MCP. Give it a
URL and an API key, and from then on that tool can create tasks for you, follow
their progress, answer the task agent's questions and review plans — the same
things you'd click through in the web UI, but in conversation.

The external agent works **through tasks**. It never drives a browser itself,
so every gate on the server (plan approval, ad-scope declarations, the
completeness review) still applies. For the developer-facing reference
(protocol, 32 tools, curl examples) see
[Connect an External Agent](agents.md).

> CLI users (Claude Code CLI, Codex CLI, …): see [Connect an External
> Agent](agents.md). This page covers the desktop apps only.

## What you can ask

Once connected, just talk to it:

- "How many stores does vibe-seller have right now?"
- "Look at the ad spend for acme over the past week and suggest improvements —
  don't change anything yet."
- "What is that task doing at the moment? How far along is it?"
- "Turn this week's inspection into a schedule that runs every Monday morning —
  show me the plan first."

The tool calls `list_stores`, `create_task`, `get_task` and friends for you;
you never have to remember a tool name.

## What it can and cannot do

| | |
|---|---|
| ✅ Can | Create tasks (with attachments), follow them, answer questions, approve / reject plans, resolve image-confirm cards, send follow-ups, stop / retry / delete tasks, read a task's output files, read and edit the workspace knowledge, manage skills and schedules |
| ❌ Cannot | Drive a browser (click, navigate, screenshot) or write into your store directly — store-facing work only ever happens inside a Vibe Seller task, behind the server's gates |

Ad changes are still held to what the task itself declared, the completeness
review still catches a report that skips marketplaces, and a report-only task
still may not hand out change instructions. None of that is relaxed because
the caller is an external agent.

## First: create an API key

In the web UI: **Settings → Integrations → API keys**. Give it a name you'll
recognise later (e.g. `claude-desktop`), pick an expiry
(30 / 90 / 365 days, or never), and **copy the key** — it is shown once, the
server keeps only a hash. The dialog that pops up also hands you ready-to-paste
configs for each client below.

- The key starts with `vst_`. **Treat it like a password**: it acts as you,
  with your role.
- Revoke it from the same card when you're done, or if you suspect it leaked.
- If the server runs with login turned off, the key still works (and still
  gets revoked for real) — create one anyway, so turning login on later
  doesn't lock your tool out.

## Claude desktop (Claude Code)

### Install

Download the macOS app from [claude.com/download](https://claude.com/download)
and install it.

### Connect

The dialog that hands you the API key also hands you a ready-to-paste
command for Claude Code. Run it once in a terminal — the **Code tab of the
Claude desktop app is Claude Code** and shares the same configuration, so it
shows up in the app afterwards:

```bash
claude mcp add --transport http vibe-seller http://HOST:7777/api/mcp \
  --header "Authorization: Bearer vst_…"
```

> Add `--scope user` to that line: without it the command only registers the
> server **for the current directory**, and Code sessions opened on another
> folder won't see it.

The server address in the dialog is editable (it is prefilled with the
address the page was served from) — change it to one the machine running the
tool can reach.

For a fuller setup, install the `vibe-seller` **plugin**
(`claude plugin marketplace add` + `claude plugin install`, see
[agents.md](agents.md)): the plugin additionally ships a skill that teaches
the agent how to use the tools — when to plan first, how to follow a task,
how to answer questions, how to review a plan, and why it must never
fabricate a result.

> If your server is **reachable from the internet**, the chat app can instead
> use **Settings → Connectors → Add custom connector** with the server URL and
> an `Authorization: Bearer vst_…` request header. That route is brokered from
> Anthropic's cloud, so it cannot reach a `localhost` / LAN server — for a
> local install, use the command above.

### Verify

Start a new Code session and ask "how many stores does vibe-seller have?", or
open the plugin's tool list — seeing `list_stores`, `create_task` and the rest
means it's connected.

### Try it

Try these in a session, in order:

1. "How many stores are in vibe-seller right now?"
2. "Look at acme's ad spend for the past week, give me some optimization
   suggestions — use common-sense defaults, don't ask me — and don't change
   anything yet."
3. "What is this task doing right now, which step is it on?"
4. "OK, follow it through and summarize when it's done."

## Codex desktop

### Install

Download the macOS app from [chatgpt.com/codex](https://chatgpt.com/codex) or
OpenAI's Codex page (Codex now ships inside the ChatGPT desktop app — pick
Codex in the app). First launch asks you to sign in with a ChatGPT account.

### Connect

The Codex desktop app and the Codex CLI share `~/.codex/config.toml`. Paste
the block from the dialog (with your own server address):

```toml
[mcp_servers.vibe-seller]
url = "http://HOST:7777/api/mcp"
bearer_token_env_var = "VIBE_SELLER_TOKEN"
```

⚠️ **A desktop app never sees your shell's `export`.** The
`bearer_token_env_var` route reads an environment variable: it works after
`export VIBE_SELLER_TOKEN=vst_…` in a terminal, but a Dock-launched app does
not inherit that variable and the server will never connect. For the desktop
app, either:

- put the header in the config (recommended — no environment needed):

```toml
[mcp_servers.vibe-seller]
url = "http://HOST:7777/api/mcp"
http_headers = { "Authorization" = "Bearer vst_…" }
```

- or set the variable GUI-wide: `launchctl setenv VIBE_SELLER_TOKEN "vst_…"`
  (then restart Codex).

To run Codex on your own model instead of ChatGPT quota, add a
`[model_providers.…]` block (any OpenAI-compatible / Responses-API provider)
to the same file and point `model_provider` at it.

### Verify

Ask "how many stores does vibe-seller have?" in a Codex session, or check the
tool count next to `vibe-seller` in the MCP Servers list.

### Try it

Try these in a session, in order:

1. "How many stores are in vibe-seller right now?"
2. "Look at acme's ad spend for the past week, give me some optimization
   suggestions — use common-sense defaults, don't ask me — and don't change
   anything yet."
3. "What is this task doing right now, which step is it on?"
4. "OK, follow it through and summarize when it's done."

## WorkBuddy (Tencent)

### Install

Download the macOS app from [workbuddy.cn](https://www.workbuddy.cn) (China
edition) or [workbuddy.ai](https://www.workbuddy.ai) (international). First
launch asks you to sign in.

### Connect

The dialog hands you the WorkBuddy MCP JSON. Put it in either place:

- **File**: write the JSON as-is into `~/.workbuddy/mcp.json`;
- **UI**: **Connectors (连接器) → Custom connectors → MCP Server Management →
  + Add MCP**, paste the same JSON.

```json
{
  "mcpServers": {
    "vibe-seller": {
      "url": "http://HOST:7777/api/mcp",
      "headers": {
        "Authorization": "Bearer vst_…"
      }
    }
  }
}
```

The entry then appears in the list — but it will not connect until you click
**Trust** and flip its switch on. Editing the file invalidates the trust, so
re-trust after any change.

### Verify

MCP Server Management reads `vibe-seller • 32/32 tools enabled`; then ask "how
many stores does vibe-seller have?" in a chat:

![WorkBuddy connected](images/mcp_workbuddy_verify_en.png)

### Try it

Try these in a session, in order:

1. "How many stores are in vibe-seller right now?"
2. "Look at acme's ad spend for the past week, give me some optimization
   suggestions — use common-sense defaults, don't ask me — and don't change
   anything yet."
3. "What is this task doing right now, which step is it on?"
4. "OK, follow it through and summarize when it's done."

## Qoder (Alibaba)

### Install

Download the macOS app from [qoder.com/download](https://qoder.com/download) —
the China edition ships as **Qoder CN**. First launch asks you to sign in to a
Qoder account (Google / GitHub / email sign-up).

### Connect

**Extensions → Connectors → top-right "Add Connector"**, choose the Form or the
JSON tab and paste the config the key dialog gives you (JSON form):

```json
{
  "mcpServers": {
    "vibe-seller": {
      "type": "streamable-http",
      "url": "http://HOST:7777/api/mcp",
      "headers": {
        "Authorization": "Bearer vst_…"
      }
    }
  }
}
```

Confirm with "Preview import", then "Import". The config is saved in
`mcpServers` within **`~/.qoder-cn/settings.json`** (the app's own dialog says
so; the international Qoder / CLI uses `~/.qoder/settings.json` — the two
editions differ).

> Qoder often fires `create_task` with a title and no description — the server
> accepts title-only calls, so you don't need to spell out a description in the
> conversation.

### Verify

**Settings → Extension management → Connectors** shows `vibe-seller • Ready •
HTTP`; expand it for the tool list. Then ask "how many stores does vibe-seller
have?" in a session.

### Try it

Try these in a session, in order:

1. "How many stores are in vibe-seller right now?"
2. "Look at acme's ad spend for the past week, give me some optimization
   suggestions — use common-sense defaults, don't ask me — and don't change
   anything yet."
3. "What is this task doing right now, which step is it on?"
4. "OK, follow it through and summarize when it's done."

## Troubleshooting

**401 Unauthorized.** The key is wrong, revoked, or expired, or its owner is
deactivated. Check the key's status under Settings → Integrations → API keys.
A revoked or expired key is rejected even when the server runs with login off — that is
deliberate, so revoking really revokes.

**405 on /api/mcp.** The server answered "Method Not Allowed": the path hit the
SPA's static-file fallback, so this server has no `/api/mcp` endpoint (too old)
or the URL is wrong. The endpoint is `/api/mcp`, under `/api/`.

**Connection refused / TLS errors.** Check host and port, and whether the tool's
machine can reach the server at all. A server bound to `127.0.0.1` is only
reachable from the same machine; put it behind a TLS reverse proxy for remote
use.

**Tools are listed but every call fails.** The configured `url` usually already
ended in `/api/mcp`, or had a trailing slash. Which value to fill differs per
client — see each section's table above.

**The agent answers without creating a task.** It answered from its own
knowledge instead of running the task. The plugin's skill exists to stop that:
never fabricate a result, report the task's own numbers and nothing else. Bare
MCP (no plugin) is more prone to it.

**Will the key show up in a screenshot?** Config UIs usually mask it; scrub
any visible `vst_…` before capturing. Docs and reports only ever use the
`vst_…` placeholder.
