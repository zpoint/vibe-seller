---
name: debug-store
description: "Developer-only skill for poking at vibe-seller stores from outside the task system — inspect the SQLite DB, drive a store's browser-use wrapper directly via the CDP proxy, manage VIBE_TASK_ID for stable sessions, recover wedged daemons, find seller SKUs, and authenticate without the JWT cookie path. Load when the user says 'debug a store', 'why is store X broken', 'check the wrapper for store X', 'I need to drive Ziniao directly without creating a task', or anything that touches the runtime store layer outside the normal task → agent flow."
---

# Debug Store Infrastructure (developer-only)

This is the inside-out view of vibe-seller stores: how to inspect and
drive the runtime — the database, the browser-use wrapper, the CDP
proxy, the per-store Ziniao session — *without* going through the task
system, the web UI, or the JWT-cookie auth wall.

This is for the platform builder, not for end-user agents running tasks.
For end-user/agent flows (running campaigns, etc.), see `amazon-ads` and
`new-product-launch`.

## What you can do without a task

The vibe-seller per-store wrapper is just a shell script that talks to
a long-running CDP-mux proxy. The wrapper auto-starts the proxy and
the browser via an internal API call (with a baked-in `ai_bot` JWT,
so you don't need user creds). All you need is:

1. A fresh `VIBE_TASK_ID` env var (lowercase UUID; first 8 chars must
   be hex).
2. The store slug (look it up in `~/.vibe-seller/data/vibe_seller.db` —
   `stores.name`, then slugify; or just list `~/.vibe-seller/bin/` for
   the directories that actually exist).

Then `~/.vibe-seller/bin/<slug>/browser-use open <url>` works.

## DB cheatsheet

```bash
DB=~/.vibe-seller/data/vibe_seller.db
sqlite3 "$DB" .tables
sqlite3 "$DB" "SELECT id, name, browser_backend, platforms, countries FROM stores;"
sqlite3 "$DB" ".schema stores"
sqlite3 "$DB" "SELECT id, username, email, role FROM users;"
sqlite3 "$DB" "SELECT store_id, status, current_platform, current_country,
                      active_tab_count, cdp_port, chrome_pid
               FROM browser_sessions;"
sqlite3 "$DB" "SELECT id, status, substr(result,1,120), substr(error,1,120)
               FROM tasks WHERE created_at > datetime('now','-1 hour')
               ORDER BY created_at DESC LIMIT 10;"
```

The `stores` table has no `slug` column — the wrapper directory
(`~/.vibe-seller/bin/<slug>/`) is where the actual usable name lives.
`name` in DB is the user-facing label and may differ from the slug
when names contain spaces / non-ascii. Match by ID when scripting.

The user table is seeded on first boot with two entries:

- a human admin whose username/email/password come from the
  `ADMIN_USERNAME` / `ADMIN_EMAIL` / `ADMIN_PASSWORD` env vars
  (defaults: `admin` / `admin@vibe-seller.local` / `admin`). After
  the first login the user can change these, so don't assume
  the seeded values are still current — read them from the DB.
- `ai_bot` (id `00000000-0000-0000-0000-000000000002`, email
  `ai@vibe-seller.local`, `password_hash='disabled'`) — its JWT is
  baked into the wrapper for `browser/start` API calls. **Interactive
  login for `role='ai_bot'` is rejected by `auth.py`**, so don't try
  to use this account for HTTP API calls. Use it only as the
  internal-token subject (which the wrapper already does for you) or
  follow Option A below to create your own admin user.

User-created accounts may also exist alongside these two seeds.
`password_hash` is bcrypt; the literal `disabled` means the account
cannot log in regardless of role.

## Wrapper architecture in 30 seconds

Each store has a generated wrapper at `~/.vibe-seller/bin/<slug>/browser-use`.
The wrapper:

1. Resolves `SESSION` from `VIBE_TASK_ID`:
   - With `VIBE_TASK_ID` set → `<slug>-<first 8 hex>`
   - Without → `<slug>` (the global session)
2. Validates the session name against `^<slug>(-aux|-[0-9a-fA-F]{8})?$`.
3. Auto-starts the CDP proxy + Ziniao Chrome by calling
   `POST http://127.0.0.1:7777/api/stores/<store_id>/browser/start`
   with a hardcoded `ai_bot` JWT. Polls until 9222 responds.
4. Injects `--cdp-url ws://127.0.0.1:9222/client-<VIBE_TASK_ID>` so the
   browser-use daemon connects through the mux proxy as a unique client.
5. Execs the real `browser-use` binary with the rest of the args.

The wrapper blocks `--profile`, `--cdp-url`, and `--connect` flags
because those break the mux. `--headed` is also blocked (managed by
the wrapper). For non-seller-center pages, use
`--session <slug>-aux`. **Behavior depends on backend:**

- **Ziniao-backed stores** (`browser_backend=ziniao`) — the `-aux`
  session bypasses the CDP-mux proxy and connects to a Ziniao
  Chrome instance directly. Useful for non-seller-central pages
  (public Amazon dp pages, third-party sites) without burning the
  main session's tab.
- **Chrome-backed stores** (`browser_backend=chrome`) — there is no
  aux exemption. ALL sessions including `-aux` still go through the
  CDPMuxProxy. The `-aux` name is just a convenient secondary client.

Check `stores.browser_backend` in the DB to know which case you're in
before designing a debug flow that assumes "aux = direct Chrome".

## VIBE_TASK_ID — the recurring footgun

A fresh `VIBE_TASK_ID` per real task is what the runtime gives every
task agent. Outside the task system, **rotate it manually after**:

- Every long pause (> 15 min idle, especially if the user resumed
  after sleeping for hours).
- Any time you see `Error: Session '<slug>-<8hex>' is already running
  with different config.` — means the daemon for that ID has stale
  CDP config.
- Any time `browser-use sessions` shows the daemon for your ID with
  `CONFIG=?` (TTY-detection failure mode).
- Any time eval calls return `TimeoutError: timed out` or
  `Client is stopping` — daemon has wedged.

Rotate:

```bash
NEW="$(uuidgen | tr '[:upper:]' '[:lower:]')"
echo "export VIBE_TASK_ID='$NEW'" > /tmp/vs_session_env.sh
. /tmp/vs_session_env.sh
~/.vibe-seller/bin/<slug>/browser-use sessions
~/.vibe-seller/bin/<slug>/browser-use open <some-url>
```

## Reviving a wedged daemon

```bash
# 1. List daemons to find the wedged one
~/.vibe-seller/bin/<slug>/browser-use sessions

# 2. Force-kill (the wrapper's `close` subcommand may itself hang)
pkill -9 -f "skill_cli.daemon.*<slug>-<8hex>"

# 3. Rotate VIBE_TASK_ID and re-open
NEW="$(uuidgen | tr '[:upper:]' '[:lower:]')"
export VIBE_TASK_ID="$NEW"
~/.vibe-seller/bin/<slug>/browser-use open about:blank
```

The Ziniao Chrome process and the CDP mux proxy survive daemon kills —
they're per-store, not per-task. Don't kill them unless you've tried
everything else.

To verify the CDP proxy is actually responding:

```bash
curl -sf -m 2 http://127.0.0.1:9222/json/version | head -1
ps aux | grep -E "ziniaobrowser|cdp_mux_proxy" | grep -v grep
```

## Skipping JWT-cookie auth

The vibe-seller HTTP API (`/api/tasks`, `/api/stores`, etc.) gates
everything on a JWT cookie. From a Claude Code shell, you don't have
that cookie. Two ways to work around without touching the user's
admin password:

### Option A — temp admin user, login via API, then deactivate

```bash
# 1. Generate password + bcrypt hash
PW=$(python3 -c "import secrets;print('tmp_'+secrets.token_hex(8))")
HASH=$(./.venv/bin/python3 -c "from app.password import hash_password;print(hash_password('$PW'))")  # run from repo root

# 2. Insert temp user (admin role)
TMP_UID="taskbot-$(uuidgen)"
TMP_USERNAME="taskbot_tmp_$(date +%s)"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
sqlite3 ~/.vibe-seller/data/vibe_seller.db \
  "INSERT INTO users (id, username, email, password_hash, role, is_active,
                      plan_mode_default, debug_mode, default_profile_id,
                      created_at, updated_at)
   VALUES ('$TMP_UID','$TMP_USERNAME',NULL,'$HASH','admin',1,1,0,'default',
           '$NOW','$NOW');"

# 3. Login → cookie
curl -s -c /tmp/vs_cookie.txt -H 'Content-Type: application/json' \
  -X POST http://localhost:7777/api/auth/login \
  -d "{\"identifier\":\"$TMP_USERNAME\",\"password\":\"$PW\"}"

# 4. Use the cookie for any API call
curl -s -b /tmp/vs_cookie.txt http://localhost:7777/api/stores

# 5. Cleanup — but only if no tasks reference the user (FK = restrict)
sqlite3 ~/.vibe-seller/data/vibe_seller.db \
  "UPDATE users SET is_active=0 WHERE id='$TMP_UID';"
# OR DELETE if no tasks were created with created_by=this user.
```

The `users.is_active=0` flag blocks future logins without violating the
`tasks.created_by` FK. This is the safest cleanup if you created any
tasks with the temp account.

### Option B — drive the wrapper directly (no API auth needed)

If your only goal is to drive a store's browser, you don't need the
HTTP API at all. The wrapper itself authenticates the
`browser/start` call with the `ai_bot` JWT baked into the script.
Just rotate `VIBE_TASK_ID` and call `browser-use open <url>`.

This is the right choice when:

- You're inspecting a single store from outside (debugging, verifying
  a workflow before turning it into a task).
- You don't need the per-task workspace isolation.
- You're OK with sharing the global `<slug>` session (or pinning a
  per-shell `<slug>-<8hex>` session via `VIBE_TASK_ID`).

This is the WRONG choice when:

- You want isolation across concurrent runs (use real tasks).
- You need the agent's MCP tools (`vibe_seller_write_workspace_file`,
  catalog injection, etc.) — those only exist inside a real task.

## Calling the HTTP API

Once you have a cookie file (Option A) — assume `COOKIE=/tmp/vs_cookie.txt`
and `BASE=http://localhost:7777` below — every authenticated route is
just `curl -b "$COOKIE" "$BASE/<path>"`. Use `-c "$COOKIE"` on the
login call (writes), `-b` everywhere else (reads).

The API surface (full list in `docs/api.md`) breaks into a few groups:

| Group | Common routes | Use for |
|---|---|---|
| Auth | `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout` | Cookie lifecycle |
| Stores | `GET /api/stores`, `GET /api/stores/<id>`, `POST /api/stores/<id>/browser/start`, `POST /api/stores/<id>/browser/stop` | Store metadata + per-store browser lifecycle |
| Tasks | `POST /api/tasks`, `GET /api/tasks`, `GET /api/tasks/<id>`, `POST /api/tasks/<id>/messages`, `POST /api/tasks/<id>/stop` | Create / list / inspect / message / stop tasks |
| Schedules | `GET /api/schedules`, `POST /api/schedules`, `POST /api/schedules/<id>/run` | Cron-style routines |
| Events | `GET /api/events/stream` (SSE), `GET /api/events/recent` | Live + historical event feed |

Keys to remember:

- Body is always JSON; set `-H 'Content-Type: application/json'`.
- All IDs are UUIDs (cookie lookup not exposed in URL).
- Read endpoints accept `?limit=` / `?status=` filters; check
  `docs/api.md` for the canonical query shape per route.

### Discover the IDs you need

```bash
# List stores → pick the one you want by name
curl -s -b "$COOKIE" "$BASE/api/stores" | jq '.[] | {id,name,browser_backend,platforms,countries}'

# List recent tasks for a store
curl -s -b "$COOKIE" "$BASE/api/tasks?store_id=<store_id>&limit=10" \
  | jq '.[] | {id,title,status,created_at}'

# Inspect a single task
curl -s -b "$COOKIE" "$BASE/api/tasks/<task_id>" | jq
```

`jq` is optional but makes the JSON readable. The DB cheatsheet
(`SELECT id, name FROM stores;`) is the offline equivalent — same
data, no auth needed for read.

## Triggering a task via API

Tasks are the primary unit of agent work. A `POST /api/tasks`
creates one, and (when `plan_mode=false` — the default) the runtime
auto-runs it: PENDING → RUNNING → COMPLETED. Plan mode goes through
DESIGNING / PLANNED first and waits for explicit `/run`.

### Minimal request

```bash
curl -s -b "$COOKIE" -H 'Content-Type: application/json' \
  -X POST "$BASE/api/tasks" \
  -d '{
    "title": "<short imperative — what the agent should do>",
    "description": "<optional context — country, marketplace, dates>",
    "store_id": "<store_uuid>",
    "ai_profile_id": "<profile_id>",
    "plan_mode": false
  }'
```

The response is the new task row including `id`. Save it:

```bash
TASK_ID=$(curl -s -b "$COOKIE" -H 'Content-Type: application/json' \
  -X POST "$BASE/api/tasks" \
  -d "$(jq -n --arg t "..." --arg d "..." --arg s "$STORE_ID" --arg p "$PROFILE" \
        '{title:$t,description:$d,store_id:$s,ai_profile_id:$p,plan_mode:false}')" \
  | jq -r .id)
echo "$TASK_ID"
```

### Field reference

| Field | Required | Notes |
|---|---|---|
| `title` | yes | Short imperative; this becomes the agent's primary instruction. |
| `description` | no | Free-form context — country (`NOON EG`, `Amazon US`), date range, prior-task references. |
| `store_id` | no\* | Required for store-scoped work. Omit for non-store tasks (always plan mode). |
| `ai_profile_id` | no | Defaults to the user's `default_profile_id`. Override per-run (e.g. `claude_code`, `minimax`, `kimi`) — check `app/ai/profiles.py` for the live list. |
| `plan_mode` | no | `false` (default) = auto-run. `true` = plan-then-execute (PENDING → DESIGNING → PLANNED → wait for `/run`). |
| `parent_task_id` | no | Chains tasks. The new task inherits store + workspace from the parent. |
| `schedule_id` | no | Set only when creating a one-off ad-hoc fire of an existing schedule; normal task creation should leave this null. |

### Watching it run

```bash
# Quick status poll
curl -s -b "$COOKIE" "$BASE/api/tasks/$TASK_ID" \
  | jq '{status,error,result_len:(.result|length),updated_at}'

# Or tail directly from SQLite (faster, no auth dance)
sqlite3 ~/.vibe-seller/data/vibe_seller.db \
  "SELECT status, length(result), error, datetime(updated_at,'localtime')
   FROM tasks WHERE id='$TASK_ID'"

# Live event stream (SSE) — every state transition + agent message
curl -N -b "$COOKIE" "$BASE/api/events/stream?task_id=$TASK_ID"
```

Per-message stream from the `task_messages` table is the richest
view of what the agent is doing — useful when you want to see
`tool_use` / `thinking` events without the SSE overhead:

```bash
sqlite3 ~/.vibe-seller/data/vibe_seller.db \
  "SELECT datetime(created_at,'localtime'), role, length(content)
   FROM task_messages WHERE task_id='$TASK_ID'
   ORDER BY created_at DESC LIMIT 20"
```

### Sending a follow-up message mid-run

If the agent stalls or you want to redirect it:

```bash
curl -s -b "$COOKIE" -H 'Content-Type: application/json' \
  -X POST "$BASE/api/tasks/$TASK_ID/messages" \
  -d '{"content":"<your follow-up — e.g. resume from where the run stalled>"}'
```

This is the same path the UI uses when the user types in the task
detail panel. The agent receives the message as a user turn.

### Stopping a runaway task

```bash
curl -s -b "$COOKIE" -X POST "$BASE/api/tasks/$TASK_ID/stop"
```

`stop` flips status → `cancelled` and signals the agent backend to
terminate. If the daemon doesn't honor it within ~30 s, fall back to
`pkill -9 -f "skill_cli.daemon.*$TASK_ID"` — and then mark the row
manually:

```bash
sqlite3 ~/.vibe-seller/data/vibe_seller.db \
  "UPDATE tasks SET status='cancelled', error='manual kill', updated_at=datetime('now')
   WHERE id='$TASK_ID' AND status IN ('running','pending');"
```

### When NOT to use the API

- **Iterating on a workflow before encoding it as a task** — drive
  the wrapper directly (Option B above) until the steps are stable.
- **Smoke-testing a single browser-use command** — no task overhead
  is justified.
- **Anything where you'd be staring at a single page of output** —
  the task system is built for unattended runs; for attended work
  the wrapper is faster.

## Finding seller SKUs without the catalog

The vibe-seller catalog (`~/.vibe-seller/knowledge/`) is a curated set
of facts; it does NOT contain a per-product SKU index. To find a
specific seller-side SKU when you need one (e.g. for bulk-upload Product
Ad rows):

```bash
# Get parent SKU for an ASIN — use the inventory page UI
~/.vibe-seller/bin/<slug>/browser-use open \
  "https://sellercentral.amazon.com/myinventory/inventory?searchKey=<ASIN>&status=all"
# The "Parent SKU:" label in the page text is the parent SKU.

# Get child SKUs by drilling down to SKU central
~/.vibe-seller/bin/<slug>/browser-use open \
  "https://sellercentral.amazon.com/skucentral?mSku=<parent-sku>&condition=New"
# This page has Sales/Pricing/Inventory tabs. The variations expand
# is in the "Inventory" tab on most SKUs.
```

Faster path: bulk-download the existing campaigns from the store and
read the `Sponsored Products Campaigns` sheet, filter Entity=Product Ad,
look at the `SKU` column — that's the seller's canonical SKU naming
scheme for that store. See `amazon-ads` § 2d for the bulk download
flow; the resulting file lands in `~/.vibe-seller/downloads/<slug>/`.

## The Ziniao download dir

Files downloaded by Ziniao (CSV, XLSX, PDF) land in either:

- the Ziniao-managed native dir (persistent) —
  `~/Library/Application Support/ziniaobrowserdatas/ziniao browser/<slug>/`
  on macOS; under `%LOCALAPPDATA%\ziniaobrowser` / Chrome's default
  `~/Downloads` on native-Windows — or
- `~/.vibe-seller/downloads/<slug>/` (per-store, the reliable one).

Recent bulk-export XLSX files land in the second path because the CDP
mux proxy pins Chrome's download path there via `Browser.setDownloadBehavior`
(`cdp_mux_proxy.py` / `cdp_mux_upstream.py`) — **verified working on
native-Windows too**, not just macOS. List most recent first:

```bash
ls -lt ~/.vibe-seller/downloads/<slug>/ | head -10
```

**Download-trigger gotcha (verified 2026-07-02, Ziniao/native-Windows).**
The pinned path works, but a page's *visible* download link is often a
`<p>`/label, not the clickable `<a>` — a coordinate/element click on the
label silently fires **no** download and the dir stays empty, which reads
as "the download dir is broken" when it isn't. Trigger the real anchor by
`href` (JS `.click()` on `a[href*="…/download/…"]`, or `browser-use open
<href>` — the latter returns a benign `net::ERR_ABORTED` but the file
still lands). See `amazon-ads` mechanics § 2d for the worked example.
Downloaded files carry Amazon's real name (`bulk-<entity>-<dates>-<ts>.xlsx`),
never a fixed `BulkSheetExport.xlsx` — glob the newest `*.xlsx`, don't
assume a name.

## Capturing data → temp dir, never knowledge

Per project policy: when you scrape live-store data (campaign keywords,
coupon configs, ASINs, prices, screenshots), save to `/tmp/<task-slug>/`.
**Never** under `~/.vibe-seller/knowledge/` — that path is for codified,
reusable facts about Amazon/Noon/etc., NOT per-run private business
data. The catalog gets synced into agent contexts, so private data
there leaks across sessions.

Skill files (this skill, `amazon-ads`, etc.) document the *patterns* of
what worked, not the per-run captures.

## Common confusions worth flagging early

- **"slug" vs "name":** the wrapper directory `~/.vibe-seller/bin/<slug>/`
  uses a slugified version of the store name. The DB `stores.name`
  column may have spaces or non-ascii. They are NOT the same string
  for some stores. The slug is what `bin/` and `downloads/` use.

- **Unified multi-marketplace accounts:** one Amazon seller-id, several
  marketplaces. Inventory differs (~10–20% of ASINs are listed on
  some but not others). Listing-status enums differ between marketplaces
  (read the live filter dropdown — one marketplace may omit a value
  another includes). Custom Reports's All-Listings TSV is byte-identical
  across the marketplaces' subdomains (account-level). Stranded Inventory is a single pool. When debugging
  a specific listing, always specify which marketplace; "the inventory
  for store X" is ambiguous.

- **Two Amazon accounts per store:** the seller-central account
  (sellercentral.amazon.com) and the advertising account
  (advertising.amazon.com) can be on different underlying Amazon
  accounts with different emails — even though SSO usually bridges
  them. When debugging an "I logged in but it shows the wrong
  account" issue, check the email in the Ziniao password-fill dialog
  and the `entityId` in the ad-console URL — both indicate which
  account is actually active.

- **Browser sessions table != actual running daemons:** `browser_sessions`
  in DB is what the queue-scheduler thinks is running. The actual
  daemon processes are visible via `ps aux | grep skill_cli.daemon`.
  These can drift apart (DB says `idle`, daemon is wedged; or DB says
  `running`, daemon was killed). Trust `ps` for "what is actually
  running"; trust DB for "what task-queue-scheduler will do next".

- **The `aux` session for non-seller-center sites:** when you need to
  visit `amazon.com/dp/<ASIN>` (public product page) for a probe
  without disrupting the main seller-central session, use
  `--session <slug>-aux`. It's a Chrome-direct (no CDP proxy) session
  that the wrapper allows even in task-mode.

## Things this skill is NOT

- Not for end-user Amazon Ads / Coupons / Listings work — that's
  `amazon-ads` + `new-product-launch`.
- Not for noon — see `noon-seller`.
- Not for the catalog/knowledge system itself (loading, sync, etc.) —
  that's a different layer.
