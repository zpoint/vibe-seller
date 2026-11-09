# Connect an external agent

Vibe Seller can expose itself to **any external AI agent** — Claude Code,
Codex, Tencent WorkBuddy, Hermes Agent, Pi, anything that speaks MCP over HTTP. The agent
gets a URL and a token, and from then on it can create tasks against your
stores, follow them, answer the task agent's questions and review plans.

The agent acts **through tasks**. It never drives the browser itself, so
every gate on the server (plan approval, ad-scope declarations, the
completeness review) still applies. See
[What the agent can and cannot do](#4-what-the-agent-can-and-cannot-do).

## 1. Mint an API key

In the Vibe Seller web UI: **Settings → Integrations → API keys →
Create API key**.

Give it a name you will recognise later (e.g. `claude-code`), pick an
expiry (never by default, or 30 / 90 / 365 days), and copy the API key.
It is shown **once** — the server keeps only a hash. If you lose it,
revoke it and mint another.

The key starts with `vst_`. Treat it like a password:

- it acts as **you**, with your role, on every server it is used against;
- it has no scopes — it can do what you can do;
- revoke it from the same page when you are done, or when you suspect it
  leaked.

An API key has its owner's full role on everything except **user
management and credentials**: the mutating `/api/users` routes,
`/api/auth/me/password`, `POST /api/auth/tokens` (minting — a leaked
key can never mint its own never-expiring replacement) and
`POST /api/auth/refresh` all answer 403 to a `vst_` credential, so
revoking a key cuts everything it could have planted. Listing and
revoking keys stay open to it. Mint always from a signed-in browser
session, and manage users from the web UI — so treat the key like
the password anyway: it can still create and run tasks.

If the server runs with `auth_required` off, a key still works (and
still gets revoked for real) — mint one anyway, so turning auth on later
does not lock your agent out.

## 2. Point an agent at it

The endpoint is **`POST /api/mcp`** on your server, and the token goes in
an `Authorization: Bearer vst_…` header.

### Claude Code — as a plugin (recommended)

The repo ships a plugin with the MCP server and a skill that teaches the
agent how to use it.

```bash
# from a clone of the vibe-seller repo
claude plugin marketplace add /path/to/vibe-seller
claude plugin install vibe-seller@vibe-seller
```

Claude Code asks for two values:

| Option | What to enter |
| --- | --- |
| **Vibe Seller server URL** | The base URL, e.g. `http://127.0.0.1:7777` or `https://vibe.example.com`. No trailing slash, no `/api/mcp` suffix — the plugin appends `/api/mcp` itself. |
| **API key** | The `vst_…` key you minted. Stored in the credential store, not in `settings.json`. |

The plugin lives in the `agent/` directory of this repo:
[`agent/.claude-plugin/plugin.json`](https://github.com/zpoint/vibe-seller/blob/main/agent/.claude-plugin/plugin.json),
[`agent/.mcp.json`](https://github.com/zpoint/vibe-seller/blob/main/agent/.mcp.json),
[`agent/skills/vibe-seller/SKILL.md`](https://github.com/zpoint/vibe-seller/blob/main/agent/skills/vibe-seller/SKILL.md).

### Claude Code — plain `claude mcp add`

No plugin, just the server. The skill is then absent, so the agent has
the tools but not the guidance on how to use them well.

```bash
claude mcp add --scope user --transport http vibe-seller \
  http://HOST:7777/api/mcp --header "Authorization: Bearer vst_YOUR_TOKEN"
```

`--scope user` matters: without it the server registers for the current
folder only, and Claude desktop's Code tab in other folders won't see
it.

### Codex

`~/.codex/config.toml`:

```toml
[mcp_servers.vibe-seller]
url = "http://HOST:7777/api/mcp"
http_headers = { "Authorization" = "Bearer vst_YOUR_TOKEN" }
```

`http_headers` is Codex's static-header map for streamable-HTTP
servers, sent on every request. Don't use `bearer_token_env_var` +
`export VIBE_SELLER_TOKEN=…` here: the Codex desktop app does not
inherit shell exports, so the token never reaches it.

### WorkBuddy

`~/.workbuddy/mcp.json`:

```json
{
  "mcpServers": {
    "vibe-seller": {
      "url": "http://HOST:7777/api/mcp",
      "headers": {
        "Authorization": "Bearer vst_YOUR_TOKEN"
      }
    }
  }
}
```

After saving the file, enable/trust the server in WorkBuddy's MCP
settings — and re-trust it after every edit of the file.

> The WorkBuddy config shape comes from a third-party vendor document —
> there is no official Tencent documentation for it. If your WorkBuddy
> build wants a `"type": "http"` key alongside `url` (as Claude Code does),
> add it.

### Hermes Agent

`~/.hermes/config.yaml` (merge into an existing `mcp_servers:` key if
there is one):

```yaml
mcp_servers:
  vibe-seller:
    url: "http://HOST:7777/api/mcp"
    headers:
      Authorization: "Bearer vst_YOUR_TOKEN"
```

Run `/reload-mcp` in a session, then check with
`hermes mcp test vibe-seller`. (`hermes mcp add --auth header` prompts
for the token interactively, so the YAML block is the scriptable route.)

### Pi

Pi has MCP built in:

```bash
pi mcp add vibe-seller --url http://HOST:7777/api/mcp \
  --header "Authorization=Bearer vst_YOUR_TOKEN"
pi mcp list   # → vibe-seller: connected, 32 tools
```

`--header` takes `KEY=VALUE`. The entry lands in `~/.pi/agent/mcp.json`.
Pi exposes MCP tools through its codemode script tool by default, so
calls appear as codemode runs rather than as individual tool names.

### Any other MCP client

Point it at `http(s)://HOST:PORT/api/mcp` with an `Authorization: Bearer
vst_…` header. The transport is **streamable HTTP** (the MCP `http`
transport), and the server answers in stateless/JSON mode, so clients
that require a long-lived SSE stream work but pay for it.

**Send `Content-Type: application/json`.** The endpoint refuses any other
content type with **415** (`Content-Type must be application/json`) —
a JSON-RPC body in a `text/plain` POST is not JSON, and the rule also
keeps a browser page from driving an auth-off server (a cross-site
`text/plain` POST is a "simple request" that skips the preflight).

One JSON-RPC document per POST:

```bash
curl -sS http://127.0.0.1:7777/api/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer vst_REPLACE_WITH_YOUR_TOKEN' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

`initialize` → `tools/list` → `tools/call` are the three calls you need;
a call looks like:

```bash
curl -sS http://127.0.0.1:7777/api/mcp \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer vst_REPLACE_WITH_YOUR_TOKEN' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"list_tasks","arguments":{}}}'
```

## 3. What the agent can do

| Tool | What it is for |
| --- | --- |
| `list_stores` | The stores the token can act on (you need the id before creating a task). |
| `list_tasks` | What is already running, and what has been done. Filters: store, status, limit. |
| `get_task` | One task in full: status, plan, result, error, pending question, recent steps. |
| `get_task_log` | The debug log: the full step log (paginated) plus the conversation. |
| `wait_task` | Block until the task moves, asks something, offers a plan, or finishes (~45 s, capped at 50 s so it returns before the client's 60 s call timeout). |
| `create_task` | Start a task: description or title, optional store, optional plan mode, optional AI profile, optional attachments (base64, ≤ 10 MB each). |
| `get_pending_questions` | The question the task agent is asking, if any. |
| `answer_question` | Your answer to that question, keyed by the full question text. |
| `review_plan` | Approve a plan, or reject it with feedback so the agent re-plans. |
| `image_request` | See and resolve an image-generation confirm card (get / confirm / cancel). |
| `send_message` | A follow-up on an existing task, optionally with attachments. |
| `stop_task` | Halt a running task (it ends `failed`). |
| `retry_task` | Re-run a task from scratch (the plan, transcript and workspace are wiped). |
| `delete_task` | Remove a task and its workspace. Refused while the task or one of its children is running. |
| `task_files` | The files a task produced: list, read (inline text or download URL + blob), ZIP URL. |
| `read_knowledge` | Read the store's `knowledge/` and `stores/` files. Read-only. |
| `workspace_file` | Read and edit the workspace the web UI edits: files, git history / version / reset, structured view, store profiles, knowledge sync. |
| `manage_skill` | Manage the server's agent skills: list / get / create / update / **begin_upload** / **import** / delete / sync. |
| `workspace_assistant` | Drive the workspace assistant (organize knowledge): send (waits for the reply), status, stop. |
| `list_schedules` | The schedules on this server, with their plan status and whether they can fire. |
| `create_schedule` | Create a scheduled task (a prompt on a recurrence) — paused by default; pass `paused: false` to arm it at creation. |
| `update_schedule` | Edit a schedule, pause/resume it, or re-plan it. |
| `delete_schedule` | Delete a schedule (its fired tasks are kept; an in-flight planner is stopped, never left parked). |
| `run_schedule` | Fire a schedule now, outside its cron. Refused while its plan is not ready; fires a paused schedule too (get your user's go-ahead first). |
| `manage_store` | Stores: list/get/create/update/delete, and a store's browser bookmarks (Settings → Stores). |
| `browser` | Start a browser session a task uses (store main / aux / store-less web); the Ziniao Windows launcher download URL. |
| `manage_ziniao_account` | Ziniao accounts: CRUD, list their browser profiles, force-restart in WebDriver mode. |
| `manage_profile` | AI profiles: CRUD, set the default, validate (one tiny real model call), provider presets. |
| `app_settings` | Server settings, Google Workspace enable/disable/status, the vision (kie.ai) key. |
| `manage_email_account` | Email accounts: CRUD, IMAP/SMTP test, real send, sync-now, store links, per-store mail DB info. |
| `manage_chat_bot` | Chat-bot webhooks (WeCom/DingTalk/Feishu/Slack): CRUD, test, real send, small file send. |
| `manage_ticktick` | TickTick/Dida365: status, OAuth start (URL for a human), projects, configure, disconnect. |

Every result that carries a task also carries `status`, `status_meaning`
and `next_actions` — what the status means and which tools are valid in
it *right now*. Schedule results carry `plan_status`,
`plan_status_meaning`, `fires`, `next_actions` and the plan text itself.

### Plan mode

`create_task` takes a `plan_mode` flag. In plan mode the task stops after
writing a plan and waits for `review_plan` before it touches anything; in
auto mode it starts working immediately. A task with **no store** is always
plan mode — the server forces it.

### Schedules

A schedule runs a prompt on a timetable. Every user-created schedule is
plan-mode: a plan-only task writes the plan once, and the schedule fires
with that frozen plan (no planning step at fire time).

**A schedule whose plan is not `ready` never fires** — not on its
timetable, not via `run_schedule`, not ever. Every schedule result says
so in plain words: `plan_status`, `fires` and `plan_status_meaning`.

**The plan is accepted by the server, not by a human.** The plan-only task
auto-approves at `ExitPlanMode`, so `plan_status: ready` means "the server
took the plan the planner wrote" — nobody has read it yet.
`create_schedule` therefore creates the schedule **paused by default**
(pass `paused: false` only when your user already told you to go ahead —
the web UI creates active schedules, but an armed schedule fires on its
own the moment the plan freezes). Show your user the plan text from the
result, then resume it with `update_schedule(paused: false)`. If the
pause step itself fails, the tool deletes the schedule it just created —
nothing half-armed is left behind.

**A task fired by its schedule skips the review.** Its `planned` status
means the frozen plan is auto-executing (the queue picks it up) — the
result says so, does not offer `review_plan`, and rejecting or messaging
such a task would discard the approved plan and re-plan with automatic
approval. Follow it with `wait_task` like any running work. (A
non-scheduled task's `planned` is the interactive checkpoint: use
`review_plan` there — and remember a plain message to a `planned` task IS
a rejection.)

**`run_schedule` fires a paused schedule too** (the web UI's Run-now
button is not disabled by the pause). Get your user's go-ahead before
firing a paused one; resuming is the move when they have already approved
the plan.

Editing a schedule's prompt makes its plan `stale` — it stops firing until
`update_schedule(replan: true)` authors a new one.

### Answers are keyed by the full question text

`answer_question` takes a map from the **full question text exactly as the
server returned it** to the answer. The short header does not match, and
neither does a paraphrase — a near miss is delivered as an empty answer
and the task waits forever. The server does the matching; you only have to
pass the text through unchanged.

### Attachments

`create_task` and `send_message` accept `attachments`:
`[{filename, mime_type, content_base64}]`, up to 10 MB each, at most
10 per call, types png / jpeg / gif / webp / pdf. They are handed to
the SAME upload routes the web UI uses — create-time files land in
the task workspace before the agent starts (its prompt names them),
chat files ride along with the message and reach the agent when it is
delivered. The round trip is real: the task agent reads the files
from its own workspace.

The 10 MB per-file ceiling is deliberately tighter than the web UI's
100 MB: the UI's multipart upload streams to disk, while an MCP
attachment is base64 inside the JSON body the endpoint parses in
memory (one request is capped at 64 MB total). A larger file goes
through the UI, or any HTTP file path the agent can reach.

### Files a task produced

`task_files(action=list)` names the outputs of a run; `action=read`
returns a text file (≤ 256 KB) inline, and anything else as an
**absolute download URL** on the server — a `vst_` bearer works on it
— plus a base64 `blob` for binaries up to 5 MB. `action=zip_url`
hands back the everything-in-one-ZIP URL the web UI's download button
uses.

### The image confirm gate

When a task agent wants to generate a product image it must ask
first: the generation tool **parks** and the server holds a confirm
card (prompt, model). `wait_task` surfaces it as
`reason=needs_operator` with a `pending.kind=image_confirm` block
while the task itself stays `running`, and the guidance names
`image_request`. Resolve it with `image_request(action=confirm)` —
optionally with your user's edited `prompt` / `model`, exactly what
the web card's confirm does — or `action=cancel`. A card is a
checkpoint: show the proposal to your user unless they already told
you to proceed.

### Workspace, skills and the assistant

`workspace_file` reads and edits the knowledge the web UI's Workspace
view edits (`knowledge/`, `stores/`, `.claude/skills/`): the tree,
file write/delete, per-file git `history` / `version` / `reset`, the
structured view, store-profile scaffolding and the knowledge sync.
Writes land in the same workspace git history a UI write does — and
**are attributed**: a commit made by a signed-in caller (web UI or
token) is authored by that caller and its message carries a
`Via: session` / `Via: api_token` trailer, while the task agents' own
knowledge commits keep the server's agent identity. `history` shows
the author, so "who changed this note" always has an answer.
`read_knowledge` stays as the read-only view over the same knowledge.

`manage_skill` manages the server's agent skills (the instructions
its task agents load): list, read, create, update (full SKILL.md plus
extra files), **import**, delete, and the built-in sync. Built-in
skills are read-only (`source=builtin` in the listing).

`import` is how a skill that lives on the agent's own machine (a
codex skill in `~/.codex/skills/`, say) becomes a Vibe Seller skill —
via a **presigned archive upload**, so the skill's bytes never pass
through the agent's LLM (no token cost proportional to file size, no
corruption risk on long base64, no size ceiling an LLM could not emit
in the first place):

1. `manage_skill(action=begin_upload)` returns `{upload_id, put_url,
   expires_at, max_bytes, formats, curl}` — the `put_url` is
   HMAC-signed, single-use and short-lived (~10 min); the signature
   IS the authorization, so no API token ever appears in a shell
   command.
2. The agent runs the `curl` in its own shell: `tar czf` (or `zip`)
   of the skill directory, raw `PUT` of the archive bytes
   (`multipart/form-data` with one `file` field works too — it must
   declare `Content-Length`; a chunked body is refused with 411). The
   format is sniffed from magic bytes (`.tar.gz` / `.tar` / `.zip`)
   and the size cap is enforced before or while the body is read.
   The result also carries `put_path` (path + signed query): when the
   `put_url` origin differs from the MCP URL the agent is configured
   with (a reverse proxy), it `PUT`s to its own MCP origin +
   `put_path` — the path is the contract, the origin is a hint.
3. `manage_skill(action=import, upload_id=…)` extracts the archive
   strictly and installs it through the same `save_skill` write path
   every other save uses — one validation/install contract. Regular
   files only (no symlinks, hardlinks, devices or FIFOs), safe
   relative names, no duplicates, one common top-level directory
   stripped, `SKILL.md` required at the resulting root (valid UTF-8),
   executable bits taken from the archive, everything stored
   byte-identical. The slug derives from the SKILL.md frontmatter
   `name` when not given, and `origin_url` records provenance (the
   skill then lists as `imported`).

Inline `files` / `files_bin` / `executables` arguments remain for
TINY text-only skills and edits (and the web UI's merge path);
carrying a real skill — or any binary — through tool arguments is
exactly what this flow replaces.

`import` **replaces** the skill directory wholesale — the tree is
staged and swapped in, so files the new version does not carry are
deleted and a failure mid-write leaves the old skill untouched.
Replacing a user skill that ALREADY exists under that slug is refused
(`a skill named X already exists (source=…)` …) unless the call
carries `overwrite: true` — a hand-made skill is never destroyed
silently; the agent confirms with its user first and re-imports
deliberately. `update` **merges**: it writes the files given and
leaves the rest of the skill alone. Built-in skills are refused by
both.

`workspace_assistant` drives the per-user workspace assistant — a
chat agent that organizes knowledge files. `send` waits (bounded) for
the reply and returns it; a slow turn times out and
`action=status` reports `running` plus the assistant's last reply
(its answers are streamed live, not stored in the DB).

### Server administration (the Settings pages)

The `manage_*` / `browser` / `app_settings` tools wrap the Settings pages:
stores, Ziniao accounts, AI profiles, app settings + Google Workspace +
the vision key, email accounts, chat bots, TickTick. Every call runs the
same REST route the web UI calls, so:

- **Role checks are the UI's.** Admin-only routes (settings update, GWS
  enable/disable, vision key) answer 403 for a member's token, exactly as
  they do in the browser.
- **Secrets are write-only.** Passwords, API keys (including profile env
  keys such as `ANTHROPIC_AUTH_TOKEN`), webhook URLs, signing secrets and
  the kie.ai key are accepted on create/update and **never returned** —
  reads carry a mask (`****` plus the last 4 characters; a bare `****`
  for URLs) or a `has_secret` / `*_set` flag. Even the routes that used
  to feed the edit form raw (the single chat-bot GET) come back masked
  over MCP.
  A masked value sent back is recognised as an echo of the read and the
  server substitutes the stored secret, so read → edit one field →
  update is safe; a mask when nothing is stored is an error (send the
  real secret or omit the field). Profile `env` updates MERGE with the
  stored env — omitted keys stay, a null deletes a key.
- **The same guarantee covers plain REST.** The token also authenticates
  on every `/api/...` route, and NO caller — API token, session (web
  UI), or no-auth — receives a stored secret from ANY of them: one
  guard (`app/rest_secret_guard.py`) masks secret-classified fields in
  every JSON response with the same classifier MCP uses, and restores
  masked (or empty) echoes sent to the update routes
  (`PUT /api/profiles/{id}`, `PUT /api/chat-bots/{id}`,
  `PUT /api/vision/config`, email/Ziniao account updates). So there is
  no bypassing the masks by swapping the MCP tool call for `curl` with
  the same bearer — and the web UI's edit forms read the same masks
  too (the admin's Show/Copy goes through `POST /api/secrets/reveal`,
  which refuses tokens; see docs/api.md).
- **Payloads mirror the UI's**, defaults included: a store create gets
  `platforms=['amazon']`, `countries=['US']`; a Ziniao create defaults
  `socket_port` to 16851; an email create auto-discovers IMAP when no
  host is given.
- Some actions do real things to the world: `manage_email_account(send)`
  sends a real email (refused from a mailbox whose `allow_send` — the
  "Email sending permission" switch in Settings → Email — the user
  turned off; it is on by default; the switch is
  interactive-only, so no token or agent credential can change it),
  `manage_chat_bot(send|test)` posts to the group,
  `manage_profile(validate)` spends one tiny real model call,
  `browser(start_store, force=true)` relaunches the Ziniao client. The
  tool descriptions say so; treat them like the buttons they mirror.
- `manage_ticktick(authorize)` returns an OAuth URL **only a human can
  finish** — hand it to the user and poll `status` until connected.

The `browser` tool starts (or reuses) a browser session for tasks to use;
it does not let the agent drive the browser. There is still no
click/navigate/screenshot in the external toolset, on purpose.

## 4. What the agent can and cannot do

**Can**: create tasks (with attachments), follow them, answer questions,
review plans, resolve image-confirm cards, send follow-ups, stop and
retry, read a task's full log and output files, read and edit the
workspace knowledge the UI edits, manage skills, drive the workspace
assistant — and, acting as the key's owner, manage the Settings pages
(stores, browsers, Ziniao accounts, AI profiles, app settings, email
accounts, chat bots, TickTick). Not users or API keys.

**Cannot**: drive a browser (click, navigate, screenshot) or write into
your store directly — `browser` only starts a session for tasks to use.
The store-facing agent inside Vibe Seller is the only thing that touches
the store, and it runs behind the server's gates.

If you want the raw browser, that is the `debug-store` skill on the
server machine, and it is a developer tool, not an external-agent feature.

> The **in-task** MCP server (`app/mcp_server.py`) is a different thing
> entirely: it is the stdio MCP that runs *inside* a task, it needs a
> `--task-id`, and it exposes tools such as `set_task_result` and
> `declare_ad_task`. It is never exposed over the network — an outside
> agent holding it could forge a task's result or walk around the
> server's review gates.

## 5. Troubleshooting

**401 Unauthorized.** The token is wrong, revoked, expired, or belongs to
a deactivated user. Check the key's status in Settings → Integrations →
API keys. A
revoked or expired `vst_` token is rejected even when the server runs
with `auth_required` off — that is deliberate, so revoking really
revokes.

**405 on `/api/mcp`.** The server answered, but with "Method Not Allowed":
the path reached the SPA's static-file fallback instead of an MCP route.
That means this server has no `/api/mcp` endpoint — it is older than the
feature, or you pointed at the wrong path. The endpoint is `/api/mcp`,
under `/api/`.

**Connection refused / TLS errors.** Check the host and port, and whether
the server is reachable from the machine the agent runs on. A server bound
to `127.0.0.1` is only reachable from the same machine; put it behind a
TLS reverse proxy for remote use.

**The tools are listed but every call fails.** The `url` you configured
usually ended in `/api/mcp` already, or had a trailing slash. The URL
should be the **base** URL only.

**The agent answers without creating a task.** It answered from its own
knowledge instead of running the task. That is the one failure mode the
skill works hardest against: if you use the plugin, the skill tells the
agent never to fabricate a result, and the plugin's evals check that it
reports the task's own numbers and nothing else.
