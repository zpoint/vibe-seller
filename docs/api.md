# API Routes

All routes prefixed with `/api/`. When `auth_required` is enabled (see Settings), all routes except `/api/auth/status`, `/api/auth/login`, `/api/health`, and `/api/sse` require JWT auth via httpOnly cookie. When `auth_required` is disabled (default), all routes return the default admin user without authentication.

A request may also carry a personal API token (`vst_…`) as `Authorization: Bearer vst_…`. Such a token is always checked against the token table — an unknown, revoked or expired one is a **401 even when `auth_required` is off**. Any other bearer value keeps the JWT/cookie behaviour described above.

External agents connect over the MCP endpoint at `/api/mcp` with a `vst_` API token — see [Connect an external agent](agents.md).

## `auth.py` — Authentication

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/auth/status` | **Public.** Returns `{ auth_required: bool }` |
| POST | `/api/auth/login` | Login with `identifier` (username or email) + `password`; sets httpOnly cookie. If `identifier` contains `@`, matches by email; otherwise by username. |
| POST | `/api/auth/logout` | Clear auth cookie |
| POST | `/api/auth/refresh` | Roll the session cookie forward by another 24h window (the frontend keepalive calls it on real activity). **Refused (403) for API-token callers** — a `vst_` credential must never be traded for a session JWT that outlives the token's revocation. |
| GET | `/api/auth/me` | Return current user (includes `username`, nullable `email`) |
| PATCH | `/api/auth/me/password` | Change own password (`current_password` + `new_password`) |
| PATCH | `/api/auth/me/profile` | Update own profile: `username`, `name`, `email` (nullable, `EmailStr`), `plan_mode_default`. Admin can change email; send `null` to clear email. |

### Personal API tokens

A personal API token lets an external agent (Claude Code, Codex, …) call
the API as the user who minted it — the token carries the owner's role,
there are no scopes. The plaintext (`vst_` + 32 random bytes) is shown
**once**, in the creation response; only its SHA-256 hash is stored.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/auth/tokens` | List the caller's own live tokens: `[{ id, name, prefix, created_at, last_used_at, expires_at }]`. Never contains the secret; revoked tokens are omitted. |
| POST | `/api/auth/tokens` | Create a token. Body `{ name, expires_in_days? }` — `expires_in_days` defaults to `90`, `null` means "never expires" (1–3650 otherwise). Returns the token fields **plus `token`** (the plaintext, shown once). The `ai_bot` role cannot mint tokens (403), and neither can an API token (403 — a token must never beget another credential). |
| DELETE | `/api/auth/tokens/{id}` | Revoke one of the caller's own tokens. Another user's token id, an unknown id and an already-revoked id all return **404** (never 403 — that would confirm the id exists). |

**API tokens never read secrets back, on MCP or REST** — and neither
does anyone else: see [Stored secrets are write-only](#secretspy--stored-secrets-are-write-only-for-every-caller)
below. Every request passes through one guard
(`app/rest_secret_guard.py`, the same classifier as the MCP redaction
layer): any JSON response any caller receives has its
secret-classified fields masked — including the REST routes the web
UI's edit forms read (the profile list's `env` with
`ANTHROPIC_AUTH_TOKEN`, the chat-bot single GET's `webhook_url` +
`secret`). Non-JSON responses (SSE, file downloads) pass untouched.
The write side holds too: a mask a caller sends back through an
update route (`PUT /api/profiles/{id}`, `PUT /api/chat-bots/{id}`,
`PUT /api/vision/config`, email/Ziniao account updates) is recognised
as an echo of the read and the stored secret is kept; a mask on a
create-style route is a 400, never a stored `****`.


## `secrets.py` — Stored secrets are write-only, for every caller

No route returns a stored secret in a normal response — not to a
member session, not to an admin session, not to a `vst_` token, not
to the no-auth default admin. One middleware
(`app/rest_secret_guard.py`, the same classifier as the MCP redaction
layer) masks secret-classified fields out of every JSON response:
list/get/create/update answers carry the mask (`****` + last 4; a
webhook URL is masked whole, `****`) or a `has_secret`/`*_set` flag,
and non-secret neighbours (a profile's `ANTHROPIC_BASE_URL`) stay
readable. SSE streams and file downloads pass through untouched.

The write side of the same contract: a form that still holds the
mask — or an EMPTY secret field on an update (an untouched password
box posts `''`) — means **unchanged**; the guard restores the stored
value before the route runs. A mask on a create-style route (nothing
stored to keep) is a 400 naming the field. A member may OVERWRITE a
secret with a fresh real value; they can never read the stored one.
`POST /api/profiles/validate` takes an optional `profile_id` for the
same purpose: the Settings edit form names the profile it is about to
save, and masked keys in the probe env are restored against that
profile's stored env (a create probe sends the real key).

The two deliberate exceptions, curated and drift-guarded: minting an
API token (the fresh plaintext is shown to its creator exactly once)
and the reveal endpoint below.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/secrets/reveal` | **Admin only**, and an interactive admin at that: members get 403 for every credential class, and ANY `vst_` API-token caller gets 403 (an admin's token included — tokens never read secrets). Body `{resource, id, field}` → `{resource, id, field, value}`. Resources: `profile` (`field` = one secret-classified env KEY, e.g. `ANTHROPIC_AUTH_TOKEN`), `chat_bot` (`webhook_url` / `secret`), `email_account` (`password`), `ziniao_account` (`password`), `vision` (`kie_api_key`; `id` unused), `dida365` (`client_secret`; `id` unused). Unknown resource/field, a missing id, or a field with nothing stored → 404. Every successful reveal writes ONE log line — `secret revealed: user=… role=… resource=… field=… via=…` — and NEVER the value. The response is `Cache-Control: no-store`. This powers the admin "Show / Copy full key" buttons next to masked fields in the UI. |


## `app_settings.py` — App Settings

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings` | Return app settings (public). Also carries server capabilities that are not stored settings: `max_agent_concurrency`, `default_schedule_timezone`, `install_id`, and `max_upload_size` (bytes, from `MAX_UPLOAD_SIZE` in `app/uploads.py` — the browser reads this instead of hardcoding a cap; see [Upload limits](#upload-limits)) |
| PUT | `/api/settings` | Update settings (admin only). Keys: `auth_required`, `max_agent_concurrency` (int, 1-10, live-updates semaphore), `default_schedule_phase_mode` (`fanout` \| `single` — default mode pre-selected when creating new all-stores schedules), `default_schedule_timezone` (IANA name, validated via `ZoneInfo`; GET seeds with `get_server_timezone()` when unset), `task_retention_days` (int, 0-3650, default 30; controls the daily auto-cleanup job — 0 disables it. See [docs/tasks.md § Task deletion + auto-cleanup](tasks.md#task-deletion--auto-cleanup)), `google_workspace_enabled` |
| GET | `/api/settings/google-workspace/status` | Report `gws` binary presence, auth status, version, and whether the umbrella bundle is enabled/installed |
| POST | `/api/settings/google-workspace/enable` | Admin only. Validates prereqs (400 if gws missing or unauthenticated), runs `gws generate-skills`, installs the 19-skill umbrella at `.claude/skills/gws/`, sets `google_workspace_enabled=true` |
| POST | `/api/settings/google-workspace/disable` | Admin only. Idempotent. Removes `.claude/skills/gws/` and clears the flag |

## `users.py` — User Management (admin only)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/users` | List all users |
| POST | `/api/users` | Create user (`username` required, `name`, `email` optional/validated, `password`, `role`) |
| PUT | `/api/users/{id}` | Update user (supports `username`, `email`; send `email: null` to clear) |
| DELETE | `/api/users/{id}` | Delete the user: their API tokens are deleted with them, owned events are reassigned / activity detached, and the row is removed (hard delete — the UI's "delete" is not a deactivation). |

## `stores.py` — Store Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stores` | List all stores |
| POST | `/api/stores` | Create a store |
| GET | `/api/stores/{store_id}` | Get store by ID |
| DELETE | `/api/stores/{store_id}` | Delete a store |

## `tasks.py` — Task Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/tasks?store_id=&include_archived=&parent_task_id=&status=&limit=` | List tasks, newest first (filter by store, archived, parent task, or status; `limit` caps the count). `status`/`limit` are the filters the hosted MCP `list_tasks` tool uses — they live in the route so both callers run the same query. |
| POST | `/api/tasks` | Create task (auto mode by default; `plan_mode=true` for plan mode; optional `parent_task_id` to create a sub-task — sub-tasks must have a `store_id`). Task provenance (`source`) is derived server-side from the request context (`subtask` when `parent_task_id` is set, else `ui`) — it is not a request field and any body value is ignored. |
| GET | `/api/tasks/{task_id}` | Get task by ID |
| DELETE | `/api/tasks/{task_id}` | Cascade-delete a task and its full subtree (children, grandchildren, …). Removes dependent rows (`task_steps`, `task_messages`, `task_attachments`, `task_logs`, `screenshots`), drops the on-disk workspace at `~/.vibe-seller/tasks/{task_id}/`, and best-effort stops any running agent. Refuses with 409 if any task in the subtree is in `designing` / `running` — caller must Stop first. Idempotent: returns `{ok: true}` even when the task does not exist. Cleanup logic lives in `app/task_delete.py` and is shared with the auto-cleanup job. |
| POST | `/api/tasks/{task_id}/retry` | Retry failed/completed task (clears plan_history, wipes workspace, and deletes the prior run's `ad_task_declarations` + `report_run_declarations` with its messages — a declaration belongs to the run, and a retried run has no files from it). For a schedule-owned plan-mode task with a ready plan, the frozen plan is re-seeded so the retry executes it directly. A plan-only task (`is_plan_only`) is excluded from that re-seed: retrying the planner re-authors the plan instead of executing it. |
| POST | `/api/tasks/{task_id}/start` | Queue task via scheduler |
| GET | `/api/tasks/{task_id}/steps` | Get task steps |
| POST | `/api/tasks/{task_id}/agent/start` | Start Claude Code agent |
| POST | `/api/tasks/{task_id}/agent/stop` | Stop agent |
| POST | `/api/tasks/{task_id}/design` | Start design agent |
| POST | `/api/tasks/{task_id}/execute-plan` | Execute planned task (plan mode only) |
| POST | `/api/tasks/{task_id}/questions/answer` | Answer agent question (batch) |
| POST | `/api/tasks/{task_id}/result` | Record task result summary (agent-only via MCP `vibe_seller_set_task_result`). Does NOT transition status; `_auto_run_task` handles that. |
| POST | `/api/tasks/{task_id}/error` | Record unrecoverable task error (agent-only via MCP `vibe_seller_set_task_error`). Does NOT transition status; `_auto_run_task` sees `task.error` during cleanup and transitions to FAILED. |
| POST | `/api/tasks/{task_id}/ad-declaration` | Declare what THIS PHASE of an ad task is for, before any browser work (agent-only via MCP `vibe_seller_declare_ad_task`). Body: `{kind, scope}` where kind ∈ audit/change/create/execute/investigate — `change` is a change the USER named ("lower widget-006 to 1.00"): no coverage, no store-rule judgement on that row, still confirmed; a request to change bids you pick is a one-campaign `audit`. There is no `edit`. `scope` = `{combos[], campaigns[], products[]}`; **absent `combos` means every marketplace the store sells on**. Append-only, one row per declaration. Like every check, one round: within a turn, narrowing at the same kind or correcting `investigate` → `audit` is always accepted (`ad_declaration.supersedes`); any other re-declaration is answered 409 once, and the same call again is recorded as declared. `execute` normally follows a user reply to a declared `audit` or `change` (this task's or its parent's); without one it is 409 once, and on a second call the frozen changes are posted to the conversation marked as not confirmed by the user. On accept, mirrored to `AD_TASK.json` in the task workspace for the stop gates (the row stays authoritative). |
| GET | `/api/tasks/{task_id}/ad-declarations` | Every declaration this task has made, oldest first — one row per phase (`seq`), with `kind`, `scope`, `user_turn`, `created_at`. The sequence is the task's phase history: a task that created a listing and then audited its ads has two declarations, one per phase. |
| POST | `/api/tasks/{task_id}/report-declaration` | Declare that this task IS a monthly report-download run, and for which month (agent-only via MCP `vibe_seller_declare_report_run`). Body: `{month: "YYYY-MM"}` — the only thing declared; the expected file list stays server-derived (`app/deliverables/manifest.py`, from the store's capabilities and the calendar), because a denominator the run authors is the one that once reported "9 of 9" against a set of 19. Store-scoped only (400 otherwise: an orchestrator has no store row to derive from). The month must be one that has ENDED — the derived reporting month, or an earlier one for a back-fill the user asked for; the current or a future month is 400, and the refusal names the expected month. Append-only like the ad declaration, and every declared month is owed, so re-declaring can shed nothing. Answer carries `expected_files`, the same list the check will grade against. |
| GET | `/api/tasks/{task_id}/report-declarations` | Every report run this task declared, oldest first (`seq`, `month`, `created_at`). |
| GET | `/api/tasks/{task_id}/schedule-state/{key}` | Read a cross-run cursor persisted by a prior run of the same schedule (agent-only via MCP `vibe_seller_get_schedule_state`). Scope resolved server-side from `task.schedule_id`; non-scheduled tasks → 400. Response omits `schedule_id` so the MCP tool output cannot leak it to the agent. Keys must match `[A-Za-z0-9_.-]{1,64}`. |
| PUT | `/api/tasks/{task_id}/schedule-state/{key}` | Upsert a cross-run cursor (agent-only via MCP `vibe_seller_set_schedule_state`). Body: `{value: string}` (non-empty). Uses SQLite `INSERT ON CONFLICT DO UPDATE` so concurrent manual triggers can't race the PK insert. Same scoping / key rules as GET. Typed keys: `email_watermark` must be a unix epoch seconds integer string (regex `^[0-9]{1,15}$`) — ISO timestamps are rejected because lex compare with microseconds / timezone variants is unsafe. |
| POST | `/api/tasks/{task_id}/wake` | Wake a waiting task (optional message) |
| GET | `/api/tasks/{task_id}/messages` | Get agent chat history |
| POST | `/api/tasks/{task_id}/messages` | Send chat message to agent; if task is in `waiting` status, also wakes the task (response includes `woken: true`). On a task at `planned`, the message is a plan REJECTION: the route flips it to `designing` and the text becomes the re-plan feedback — there is no neutral "comment" channel into a planned task. |
| GET | `/api/tasks/{task_id}/files` | List agent-generated files in task workspace (non-symlink, non-dot files in task root) |
| GET | `/api/tasks/{task_id}/files/{filename}` | Download a task output file (path traversal protected) |

`TaskResponse` includes `created_by_name: str | null` — the creator's display name resolved from the users table (defaults to `"admin"` if not found).

`TaskResponse` also carries the usage meter's totals across every run: `cost_usd`, `input_tokens` (uncached), `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `num_turns`, plus the main agent's latest `context_tokens` / `context_window` / `usage_model`, and `cost_basis` (`catalog` = priced from the provider's per-token rates, `included` = plan / free model, `unpriced` = tokens only). The same fields stream live as the `task_usage` SSE event. See [subsystems.md § Task usage meter](subsystems.md#task-usage-meter--price-catalog-model_pricing).

### `POST /api/tasks/{task_id}/result` — what `set_task_result` checks

The submission is retained first (`submitted_result`), so a refusal (400)
annotates it rather than discarding it. Then every check below runs.

Every check gets **one round**, and a submit is refused at most once
(`task_submission.apply_round`): if any check reports for the first time
on the task, the submit is a 400 listing everything every check found;
a check that already had its round never refuses again, so the next
submit is accepted with the findings as caveats (`review_gaps`).
Findings that contradict each other — or a change the user did not
approve — are also marked with a banner at the top of the result, and
of the retained submission, so a run that ends after the refusal still
ships the warning.

1. **Report deliverables** (`app/deliverables/gate.py`): a run that
   DECLARED itself a monthly report download (`vibe_seller_declare_report_run`,
   one month or more) delivered every report the calendar and the store's
   capabilities say that month owed. A task with no declaration is not
   checked at all — folder names, file counts and the shape of the result
   are not obligations.
2. **Skill-declared gates** — only those named in the `gates:` frontmatter
   of a skill the task loaded (bindings persist per task under
   `data/gate_bindings/`). The ad skills (`amazon-ads`, `noon-ads`)
   declare `ad_changes` and `ad_coverage`; `review-collect` declares
   `review_completeness_review` and `review_output_gate` (every enumerated
   product collected into well-formed `reviews/v1` JSON); `amazon-listing`
   declares `upload_verdict` (`app/ai/stop_gates/listing_upload_gate.py`)
   — a listing batch this turn started uploading to the live account
   should have a clean `BATCH_<id>_VERDICT.json`.

   It arms on the upload, never on an earlier step: `bh_upload_flatfile.py`
   writes `UPLOAD_ATTEMPT.json` when it is about to put the file on the
   live page. Downloading or inspecting a template arms nothing, so a run
   that only read a category is no longer refused for an "unverified
   upload".

**Ad gates read fixed-header TSVs, never the report prose.** An audit
writes `CAMPAIGNS.tsv` and `CHANGES.tsv` in the task dir and per-campaign
`<campaign_id>.tsv` / `.searchterms.tsv` under
`store-data/<slug>/ads/<platform>/<country>/`; execution writes
`EXECUTION_LOG.tsv` (parsed by `app/ai/stop_gates/ad_tables.py`). The
column contract the agent follows is
`app/skills_v2/amazon-ads/references/data-contract.md` (`noon-ads`
ships a byte-identical copy) — that file is the
source of truth; do not restate it.

- `ad_changes`: `CHANGES.tsv` obeys the store rules (`ad_rules.py` —
  `acos_no_lower`, `cpc_floor_multiplier`, `scale_roas`,
  `change_cooldown_days`, …; each default is overridable by a
  `key: value` line in `stores/<slug>/notes.md`) — on what an `audit`
  recommends. A `change` phase's rows are the user's own value, so the
  thresholds are not applied to them, only what the columns say (a real
  action word, a target bid for a bid action, a row that moves the way
  its action says); an `investigate` phase
  proposes nothing; every `EXECUTION_LOG.tsv` row matches an approved
  change (bid within 0.01) and the per-campaign `applied_*` columns agree.
  Execution findings and broken tables are contradictions (banner);
  store-rule findings on a proposal are caveats.
- **Approval freeze**: when the phase declares `execute`, its
  `CHANGES.tsv` is copied to `data/approved_changes/<task_id>.tsv`, out of
  the agent's reach. Execution is checked against that copy (this task's
  or its nearest ancestor's), so editing `CHANGES.tsv` mid-execution
  changes nothing. A re-declared `execute` re-freezes; earlier freezes
  are kept, so changes made under them stay approved.
- `ad_coverage`: only an `audit` declaration owes coverage
  (`ad_declaration.owed_combos`) — every owed marketplace has rows in
  `CAMPAIGNS.tsv`, live campaigns are drilled with both files, and
  search terms reconcile with targeting spend. Spend that cannot be true
  (search terms above targeting) is a contradiction.

`vibe_seller_ads_call` (Ads API) is separately scope-checked against the
declaration by `ads_call_refusal` (`app/ai/ad_scope_access.py`).

## `agent_mcp/` — Hosted MCP endpoint (external agents)

Serves the Model Context Protocol so an external agent (Claude Code,
Codex, …) can drive tasks over HTTP. See
[Connect an external agent](agents.md) for the client-side setup.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/mcp` | One JSON-RPC 2.0 message (or a batch) per call. Methods: `initialize`, `tools/list`, `tools/call`, `ping`, `notifications/initialized`. Stateless — no session id, one `application/json` document per call. A notification (no `id`) is answered with **202** and no body. |
| GET | `/api/mcp` | **405** — the endpoint is stateless and offers no server-to-client event stream. |

Auth is the same dependency the REST API uses (`get_current_user`), so a
session cookie, a `vst_` API token as `Authorization: Bearer vst_…`, and a
server with `auth_required` off all behave exactly as they do for the web
UI. A 401 carries `WWW-Authenticate: Bearer`.

**Transport rules** (checked before the body is parsed): the request's
`Content-Type` must be `application/json` (or any `+json` type; parameters
like `charset` allowed), else **415** — this keeps the endpoint out of the
CORS "simple request" class, so a webpage cannot drive an auth-off server
with a `text/plain` POST. `Sec-Fetch-Site: cross-site` is **403**. An
`Origin` header, when present, must be same-origin or in the app's CORS
allow-list, else **403** (non-browser clients send no `Origin` and are
unaffected).

Tools (`tools/call` → `params.name`, `params.arguments`): `list_stores`,
`list_tasks`, `get_task`, `get_task_log`, `wait_task`, `create_task`,
`get_pending_questions`, `answer_question`, `review_plan`,
`image_request`, `send_message`, `stop_task`, `retry_task`,
`delete_task`, `task_files`, `read_knowledge`, `workspace_file`,
`manage_skill`, `workspace_assistant`, `list_schedules`,
`create_schedule`, `update_schedule`, `delete_schedule`,
`run_schedule`, plus the Settings-page tools (`manage_store`,
`browser`, `manage_ziniao_account`, `manage_profile`, `app_settings`,
`manage_email_account`, `manage_chat_bot`, `manage_ticktick`) — one
tool per resource with an `action` enum, so the
list stays usable in a client's context. Every mutating tool calls the
REST route handlers, so the task state machine, the plan lifecycle and the
launch path are the same code the web UI runs. A tool that fails answers
`isError: true` with the reason; a protocol problem (unknown tool, bad
arguments, non-object `params`) answers with a JSON-RPC error.

**Task detail, files and attachments.** `get_task_log` pages the full
step log (offset/limit) and carries the conversation messages — the
debug view behind the summaries. `task_files` wraps the task file
browser (`list` / `read` / `zip_url`): text ≤ 256 KB inline, anything
else an absolute download URL (a `vst_` bearer works there) plus a
base64 blob for binaries ≤ 5 MB. `create_task` / `send_message`
accept `attachments: [{filename, mime_type, content_base64}]` (≤ 10 MB
each) handed to the same upload routes the UI uses — the deferred
create → upload → start chain, and the chat staging →
promote-on-delivery flow respectively.

**Image confirm gate.** A task agent's image generation parks on a
confirm card; `image_request` (get / confirm / cancel) resolves it
through the same routes the web card uses, `wait_task` surfaces the
card as `needs_operator` with a `pending.kind=image_confirm` block
(the task stays `running`), and the guidance names `image_request`
while one is showing.

**Workspace, skills, assistant.** `workspace_file` (actions `tree`,
`read`, `write`, `delete`, `history`, `version`, `reset`,
`structured`, `store_profile`, `knowledge_sync`, `knowledge_status`,
`knowledge_meta`) wraps the workspace routes over the roots the UI
edits (`knowledge/`, `stores/`, `.claude/skills/`) — path confinement
is enforced up front (no `..`, no backslash, no absolute) with the
route's own guard as the second line; writes land in the same
workspace git history a UI write does. **Workspace commits are
attributed** (`app/workspace/actor.py`): a commit made while a
signed-in caller's request is on the stack is authored by that caller
(`name <name@users.vibe-seller.local>`) with a `Via: session` /
`Via: api_token` / `Via: service` / `Via: no_auth` trailer in the
message; the task pipelines' own knowledge commits (and anything
outside a request) keep the agent identity — the orchestrator entry
points drop the caller's context (`reset_ledger`), so a task launched
by a token never files the agent's edits under the token owner's
name. `history` reports the author. `manage_skill` wraps the
skills routes (list / get / create / update / begin_upload / import /
delete / sync / sync_meta). `import` installs a whole skill from
another machine through a **presigned archive upload**: `begin_upload`
returns a signed single-use `put_url` (plus a ready-to-run `curl`),
the agent `PUT`s a `.tar.gz` / `.tar` / `.zip` of its skill directory
to `PUT /api/skill-uploads/{upload_id}` from its own shell — the file
bytes never pass through the agent's LLM — and `import(upload_id=…)`
extracts the archive strictly and installs it (slug derived from the
SKILL.md frontmatter `name`, `origin_url` records provenance).
`import` also takes a **`url`** instead of an `upload_id` — a GitHub
`tree`/`blob` URL of a skill folder or a direct archive URL — which
the SERVER fetches (http(s) to public addresses only, loopback only
under the e2e test flag, redirects re-checked, streamed caps; guards
in `app/skill_fetch.py`) and installs through the same staged path,
recording `origin_url` (default: the URL) so the skill lists as
`imported`. Passing the URL verbatim matters: the server resolves
every supported form itself.
`import` replaces the skill directory wholesale, `update` merges, and
replacing an existing user skill is refused without `overwrite: true` —
see [workspace.md § Skills System](workspace.md#skills-system) for the
write contract and
[agents.md § Workspace, skills and the assistant](agents.md#workspace-skills-and-the-assistant)
for the external-agent flow. Inline `files` / `files_bin` arguments
remain for tiny text-only skills and edits only.
`workspace_assistant` wraps the
assistant routes; `send`
also waits (bounded, default 45 s) for the reply on the same SSE
events the UI reads, and `status` reports the last reply held in
memory (assistant answers are not persisted to the DB).
**Settings-page tools.** Each `manage_*` tool validates its per-action
required fields and answers a tool error naming the missing one; unknown
keys are rejected rather than silently dropped (`app_settings(update)`
checks its body against the route's own `ALLOWED_KEYS`). Secrets are
write-only through one shared redaction layer
(`app/agent_mcp/redact.py`): passwords, API keys (profile env keys
included), bot webhook URLs, signing secrets and the kie.ai key are
accepted on write and returned masked (`****` + last 4) or as a
`has_secret`/`*_set` flag — including on the chat-bot `get`, whose REST
route used to return the raw URL to the edit form. The same guarantee
covers plain REST for EVERY caller (see "Stored secrets are
write-only" above): the edit-form routes answer masks to tokens and
sessions alike, and the admin reads the real value only through
`POST /api/secrets/reveal`. A drift test
(`tests/unit/test_agent_mcp_redact.py`) fails when a new field appears on
one of the wrapped response models unclassified. Real-world side effects
are named in the tool descriptions: `manage_email_account(send)` sends a
real email, `manage_chat_bot(test|send)` posts to the group,
`manage_profile(validate)` spends one tiny real model call, and
`manage_ticktick(authorize)` returns an OAuth URL only a human can finish.

**Schedule semantics.** A user-created schedule always plans first: a
plan-only task (`plan_task_id` in the result) authors the plan, and when
it finishes the plan is frozen onto the schedule (`plan_status=ready`) —
the planning task executes nothing. Every schedule result carries
`plan_status`, `plan_status_meaning`, `fires` and `next_actions` (from
`app/agent_mcp/lifecycle.py`), plus the frozen plan text itself
(`plan`, `plan_truncated`, `plan_full_text_where`) — the agent has to be
able to SHOW that plan to its user before the schedule runs it.
`fires` is **false** unless the plan gate is open *and* the schedule is
active: a schedule whose plan is not `ready` never fires, and
`run_schedule` on one is refused with the route's reason verbatim.
`update_schedule` folds the UI's separate routes into one call: the
edit applies first, then `replan: true` (spawns a fresh plan-only
task), then `paused` (→ `/pause` or `/resume`; resuming needs a ready
plan). Editing the prompt makes the stored plan `stale`.

**A schedule's plan is accepted by the SERVER, not by a human.** The
plan-only task auto-approves at `ExitPlanMode`, so `plan_status=ready`
means "the server took the plan the planner wrote", not "someone
reviewed it". The human checkpoint is made by the agent: pass
`paused: true` to `create_schedule` (the default `false` is what the
web UI does) and the tool creates the schedule and pauses it in the
same call — no REST change, no direct DB write; the REST create path
has no `is_active` of its own (`ScheduleCreate` carries none, and the
create route always makes an active schedule), and only `/pause`
deactivates one. While the plan is still `planning` the fire gate
refuses the schedule anyway, so pausing at creation leaves no window.
Then show the plan text and resume it with
`update_schedule(paused=false)` only with the user's go-ahead.

**Task lifecycle guidance.** Every tool result that carries a task —
`create_task`, `get_task`, `wait_task`, each `list_tasks` item,
`retry_task`, `review_plan`, `send_message`, `stop_task`,
`answer_question` — also carries `status`, `status_meaning` and
`next_actions` (from `app/agent_mcp/lifecycle.py`): what the status
means and which tools are valid in it *right now*. `next_actions` is
derived from the same state-machine groups the routes check
(`STOPPABLE`, `RETRIABLE`, `WAKEABLE`, the plan gate), so a named
tool is one the server accepts in that state; `wait_task` is named
only where waiting actually waits — it returns at once on `planned`,
`waiting` and the terminal states. A pending question is a **flag**,
not a status: while one is outstanding the guidance names
`get_pending_questions` and `answer_question` first, whichever status
the task is in. `delete_task` follows the delete route's **subtree**
guard (`app/task_delete.py`): the route refuses with 409 while any
task in the tree is running, so the guidance drops `delete_task` for
a parent whose child is still live — the query is the route's own.
The same table is served as `initialize.instructions` and, generated,
in the packaged skill's `references/flows.md`.

**Profile semantics (mirrors the web UI).** An omitted `profile_id` on
`create_task` uses the owner's default profile (the route resolves it). On
`retry_task`, `send_message` and a `review_plan` reject, an omitted
`profile_id` is sent as **the caller's default profile** — exactly what
the UI's composer does, whose profile picker starts from the signed-in
user's `default_profile_id` — so replaying what a person would click runs
the same model. Pass `profile_id` explicitly to choose another.

`list_tasks` returns at most 50 tasks by default (`limit`, max 200).
`wait_task` long-polls (default 45 s, max 50 s — under the 60 s per-call timeout of Qoder and Codex) and ends early when the
client disconnects. Its `reason` is exactly one of `question` (the agent
asked something — `answer_question`), `needs_operator` (a plan is ready
— `review_plan`; or an image confirm card is showing, distinguished by
the `pending` block with `kind=image_confirm` — `image_request`),
`waiting` (parked on child tasks or a condition —
`send_message` wakes it), `terminal` (`completed`/`failed`), or
`timeout` (nothing happened — call again).

`review_plan` with `approve: true` returns only once the task has left
`planned` (the route hands the executor to the background, so the
transition is asynchronous); its result carries the status it reached.
`send_message` CONTINUES a task (plan, transcript and progress are kept)
and `retry_task` STARTS OVER (they are wiped).

`create_task` omits `plan_mode` by default, so the owner's
`plan_mode_default` applies exactly as in the web UI; the result echoes
the `plan_mode` the task actually got. A task with no store always
plans.

`answer_question` maps the answers onto the **full question text** the
agent reads (the header or a 1-based index are also accepted) — an answer
keyed by the header alone would reach the agent as an empty answer.

This is NOT `app/mcp_server.py`: that module is the *in-task* stdio MCP
(`--task-id`, tools like `set_task_result`), and is never exposed here.

## `schedules.py` — Schedule Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/schedules?store_id=` | List schedules (filter by store) |
| POST | `/api/schedules` | Create a recurring schedule. Optional `phase_mode` (`fanout` \| `single`) only honored when `store_id` is null; store-bound schedules always resolve to `single`. When omitted, server uses `AppSettings.default_schedule_phase_mode`, falling back to `fanout`. `two_phase` is system-only and rejected with 400. Optional `fanout_serial` (bool, default false) runs the fanout's stores one at a time (see [All-Stores Scheduling](subsystems.md#all-stores-scheduling)); 400 when set on a store-bound or `single` schedule. When `plan_mode=true`, the response is returned with `plan_status='planning'` and a fresh `is_plan_only` Task is spawned; the schedule is not eligible to fire until the user approves the plan (see [Plan-at-creation lifecycle](subsystems.md#plan-at-creation-lifecycle)). |
| GET | `/api/schedules/{id}` | Get schedule detail |
| PUT | `/api/schedules/{id}` | Update schedule. Mutable: `title`, `description`, `schedule_type`, `schedule_time`, `schedule_day`, `interval_value`, `timezone`, `ai_profile_id`, `finalize_description`, `fanout_serial` (400 on a store-bound or `single` schedule; omitted leaves it unchanged). `plan_mode`, `phase_mode`, and `store_id` are immutable (400 / 422 respectively). `plan_version` acts as an `If-Match` optimistic-lock token — stale values return 412. On a plan-mode schedule, a `description` change (normalized whitespace) aborts any in-flight planner and flips `plan_status='stale'`; call `/replan` to author a new plan. |
| DELETE | `/api/schedules/{id}` | Delete schedule. An in-flight planning task is stopped and ended as `failed` (`error='Schedule deleted.'`) through the same abort path the prompt-edit and replan flows use — deleting mid-planning never strands a planner parked at `planned` holding an agent slot. |
| POST | `/api/schedules/{id}/pause` | Pause schedule |
| POST | `/api/schedules/{id}/resume` | Resume schedule. Returns 409 if `plan_mode=true` and `plan_status != 'ready'` (gate with the cron fire-gate so APScheduler doesn't fire a blocked schedule). |
| GET | `/api/schedules/{id}/tasks` | List child tasks for schedule |
| POST | `/api/schedules/{id}/trigger` | Manually trigger schedule. Returns 409 if `plan_mode=true` and `plan_status != 'ready'`. All-stores schedules route by `phase_mode`: `fanout`/`two_phase` → one task per active store; `single` → one no-store task. Fires a PAUSED schedule too (the pause gates the cron, not the manual trigger — same as the web UI's Run-now button). Store-bound schedules always create one store-scoped task. Before creating a per-store child task, any prior `waiting` task for the same `(schedule, store)` is cancel-forwarded (FAILED with `error_category='superseded_by_next_fire'`) and a `schedule_waiting_cancelled` SSE event is emitted. |
| POST | `/api/schedules/{id}/replan` | **Idempotent.** Spawn a new `is_plan_only` Task to author the schedule's plan. If a non-terminal planning Task already exists, returns the current response without spawning a duplicate. Returns 400 for non-plan-mode schedules. |

### `ScheduleResponse` fields

All endpoints returning a schedule serialize these fields:

| Field | Type | Notes |
|---|---|---|
| core CRUD fields | — | `id`, `store_id`, `title`, `description`, `plan`, `schedule_type`, `schedule_time`, `schedule_day`, `interval_value`, `timezone`, `is_active`, `is_system`, `phase_mode`, `plan_mode`, `ai_profile_id`, `created_by`, `created_at`, `updated_at` |
| `plan_status` | `'none' \| 'planning' \| 'ready' \| 'stale' \| 'failed'` | Lifecycle of the frozen plan — see [`PlanStatus`](subsystems.md#plan-at-creation-lifecycle). |
| `plan_version` | int | Monotonic counter, bumped on every successful plan commit. Use as `If-Match` when sending PUT. |
| `plan_error` | string \| null | Last planner failure reason (shown in the UI banner on `failed`). Cleared on successful plan commit or on prompt-edit invalidation. |
| `current_planning_task_id` | string \| null | Task ID currently authoring the plan, if any. |
| `next_run` | string \| null | Next APScheduler fire time (ISO). |
| `child_task_count` | int | Count of non-plan-only child tasks. |
| `last_run_status` | string \| null | Status of the most recent non-plan-only child task. |
| `pending_questions_count` | int | Count of child tasks in `waiting` (frontend badges this). |

### `ScheduleUpdate` body

PUT accepts the mutable fields listed in the table above plus
optional `plan_version` (for the If-Match check). Unknown fields
return 422 (`model_config = extra='forbid'`).

On create, if the request body omits `timezone` (or sends `null`), the router resolves it in order: `AppSettings['default_schedule_timezone']` → `get_server_timezone()` (via `tzlocal`, UTC fallback). PUT accepts `timezone` and re-registers the APScheduler job with the new zone. Invalid IANA strings → 400.

## `events.py` — Event Tracking

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/events?status=&store_id=` | List events (multi-status filter, comma-separated) |
| POST | `/api/events` | Create event manually |
| GET | `/api/events/{id}` | Get event |
| PUT | `/api/events/{id}` | Update event fields |
| POST | `/api/events/{id}/status` | Change status with transition validation |
| POST | `/api/events/{id}/confirm` | Confirm draft → open |
| POST | `/api/events/{id}/dismiss` | Dismiss event |
| POST | `/api/events/{id}/sync` | Sync to external backend |
| DELETE | `/api/events/{id}` | Delete event |
| GET | `/api/events/{id}/activities` | List activity timeline |
| POST | `/api/events/{id}/activities` | Add note to timeline |
| GET | `/api/events/backends` | List sync backends |
| POST | `/api/events/backends/configure` | Configure sync backend |

## `email_accounts.py` — Email Account Management

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/email-accounts` | List email accounts |
| POST | `/api/email-accounts` | Create email account (password encrypted; `allow_send` always starts `true` — a body naming it is a 422) |
| PUT | `/api/email-accounts/{id}` | Update email account (not `allow_send` — a body naming it is a 422 pointing at the route below) |
| PUT | `/api/email-accounts/{id}/send-permission` | `{allow_send: bool}` — **interactive-only**: any machine credential gets 403 (see below) |
| GET | `/api/email-accounts/discover` | Auto-discover IMAP settings |
| GET | `/api/email-accounts/discover-smtp` | Auto-discover SMTP settings |
| POST | `/api/email-accounts/{id}/test` | Test IMAP connection |
| POST | `/api/email-accounts/{id}/test-smtp` | Test SMTP connection |
| POST | `/api/email-accounts/{id}/send` | Send email via SMTP. **403** when the account's `allow_send` is off — refused before any SMTP connection, with a message telling the agent to draft instead |
| DELETE | `/api/email-accounts/{id}` | Delete email account |
| GET | `/api/email-accounts/info-by-store/{store_id}` | DB paths + schema for agent MCP tools |
| GET | `/api/stores/{id}/emails` | List linked emails for store |
| POST | `/api/stores/{id}/emails` | Link email account to store |
| DELETE | `/api/stores/{id}/emails/{link_id}` | Unlink email from store |
| POST | `/api/stores/{id}/emails/poll` | Poll emails (deprecated, use background sync) |

**Send permission (`allow_send`).** Every email account carries a
boolean `allow_send`, default `true` (also for accounts that predate
it — `_ensure_added_columns` adds the column as `NOT NULL DEFAULT 1`,
so upgrading changes nothing). The user can turn it off per mailbox;
only an explicit off refuses (`None`, never stored, means the default).
The gate is `ensure_send_allowed` in `app/email/sender.py`, called by
`send_email` and again by the socket-opening `_do_send`, so every send
path — this route, the in-task `vibe_seller_send_email` MCP tool (which
POSTs here), and the external `/api/mcp` `manage_email_account(send)`
(which calls this route in-process) — is refused the same way, and a
future caller of the sender is too.

The user flips it per mailbox with the "Email sending permission"
(「发邮件权限」) switch in Settings → Email, through
`PUT …/send-permission`. That route is in `INTERACTIVE_ONLY_ROUTES`
(`app/interactive_only.py`), whose middleware refuses — by how the
credential is presented — any `Authorization` header (a `vst_` token or
a Bearer JWT), a `vst_` cookie, and the AI-bot JWT as a cookie (what
the in-task MCP server presents). Only the web UI's session cookie and,
on a server with login off, a credential-less request pass. Limit: with
login off, an agent's credential-less request looks the same as the web
UI, so the switch is agent-proof only when login is on. A drift guard
(`tests/unit/test_interactive_only.py`) fails if any other route's body
accepts `allow_send` or any other handler assigns it.

## `profiles.py` — AI Agent Profiles

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/profiles` | List AI profiles. Secret-classified env keys (`*_TOKEN`/`*_KEY`/…) arrive masked (`****` + last 4) for every caller; non-secret keys stay readable. See [Stored secrets are write-only](#secretspy--stored-secrets-are-write-only-for-every-caller) |
| POST | `/api/profiles` | Create AI profile |
| PUT | `/api/profiles/{id}` | Update AI profile |
| DELETE | `/api/profiles/{id}` | Delete AI profile |
| PATCH | `/api/profiles/{id}/set-default` | Set a profile as the user's default. Response: `{'ok', 'default_profile_id', 'schedules_synced'}` — `schedules_synced` counts the caller's pinned schedules re-pinned to the new default (only when the caller's `sync_profile_to_schedules` pref is on; see [subsystems.md § Schedule AI profile resolution](subsystems.md#schedule-ai-profile-resolution)) |
| POST | `/api/profiles/validate` | Probe a profile's endpoint config (no persistence). Optional `profile_id` names the profile being edited: masked secret values in `env` are restored against that profile's stored env before the probe (a create probe sends the real key) |
| GET | `/api/profiles/presets` | Provider presets + per-provider model options |

`POST /api/profiles/validate` takes `{"env": {...}}` and makes one minimal Anthropic `/v1/messages` request against the config's own `ANTHROPIC_BASE_URL` (mirroring Claude Code's auth). It **always returns HTTP 200** — the verdict is in the body: `{"ok": bool, "code": str, "error": str, "reported_model": str | null}`. The Settings UI hits it on save so an unreachable base URL, a wrong/expired key, a wrong protocol, or a retired model id is caught on the config page rather than on the next agent run. See [backend.md § Profile Endpoint Validation](backend.md#profile-endpoint-validation-aiprofile_validationpy).

## `workspace.py` — Workspace & Knowledge & Skills

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/workspace/knowledge/sync-status` | Compare the packaged knowledge tree with the workspace copy (content status — WHEN it synced is `GET /api/sync/status`) |
| POST | `/api/workspace/skill` | Create a new user skill (scaffold: `name`, `description`, `origin_url`) — full content goes through `PUT /api/workspace/skills/{slug}` |
| PUT | `/api/workspace/skills/{slug}` | Create or extend a user-space skill in ONE call. Body `SkillSaveRequest`: `skill_md`, `files` (text, stored as UTF-8), `files_bin` (base64, stored byte-identical), `executables` (which files keep the executable bit), `origin_url` (provenance — the skill then lists as `imported`), `replace`, `overwrite`. `replace: true` swaps in the whole tree — files the call does not carry are deleted, a failure leaves the old skill untouched; the default merges. Replacing an EXISTING user skill is refused without `overwrite: true` (`a skill named X already exists (source=…)` — nothing written). Built-in slugs rejected. Paths/size caps enforced in `app/workspace/skills_manager.py` |
| POST | `/api/workspace/skills/import-local` | In-task only (the MCP process's session token; query `slug?`, `overwrite`, `origin_url`): the raw body is a tar.gz of the skill directory packed by `app/skill_pack.py`. Staged + extracted + installed exactly like a presigned archive upload (`skill_install.import_staged`) — the server never reads a caller-named path |
| POST | `/api/workspace/skills/import-url` | Body `SkillImportUrlRequest` (`url`, `slug?`, `overwrite`): the SERVER fetches the URL (guards + recursive GitHub-tree walk + archive sniffing in `app/skill_fetch.py`; unreachable host → 502 refusal) and installs through the same staged path, recording `origin_url` (default: the URL) |
| GET | `/api/workspace/structured` | Workspace grouped for the UI. One entry per store joining `stores/<slug>/` knowledge `files` with `store-data/<slug>/` run-data `data_files`/`data_path` (run-data sorted newest-first) |
| GET | `/api/workspace/file` | Read a text file (`{path, content}`); decodes utf-8 with GB18030 fallback; 400 with a pointer to `/file/raw` for binary |
| GET | `/api/workspace/file/raw` | Raw bytes with guessed content type. `Content-Disposition: inline` only for non-scriptable types (pdf, png/jpeg/gif/webp); everything else `attachment` (stored-XSS guard) |
| GET | `/api/stores/{id}/bookmarks` | Read Chrome profile bookmarks |

## `sync.py` — Central sync scheduler

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sync/status` | Every registered sync job with its `sync_state` row (`last_run_at`, `last_ok_at`, `version`, `etag`, `status`, `error`, `needs_restart`, `detail`, `cadence`, `enabled`, `running`, `next_run_at`) plus a top-level `needs_restart` |
| POST | `/api/sync/{name}/run` | Run one job now. Manual runs bypass the job's auto-sync toggle and force real work instead of an "unchanged" probe. 404 for an unregistered job |

Replaces the per-module sync routes the workspace router used to carry. See [subsystems.md § Central sync scheduler](subsystems.md#central-sync-scheduler-appsync).

## `skill_uploads.py` — Presigned skill-upload endpoint

| Method | Path | Description |
|--------|------|-------------|
| PUT | `/api/skill-uploads/{upload_id}` | Stream a skill archive (`.tar.gz` / `.tar` / `.zip`) to a presigned upload. POST with `multipart/form-data` (one `file` field) is accepted too; the raw PUT is the documented default. |

This is the raw-`PUT` half of `manage_skill`'s import flow (see
[agents.md § Workspace, skills and the assistant](agents.md#workspace-skills-and-the-assistant)):
`begin_upload` mints the URL, the agent's own shell streams the
archive to it, `import(upload_id=…)` installs it. The staging
pipeline is shared with the in-task import routes —
`POST /api/workspace/skills/import-local` (cookie-authed raw archive
from the MCP process) and `…/import-url` (server-fetched) stage the
same way and end in the same `skill_install.import_staged` install
path. The result carries
`put_path` alongside `put_url` so an agent behind a reverse proxy can
`PUT` to its own MCP origin + `put_path`. Multipart uploads must
declare `Content-Length` (411 otherwise) — Starlette spools file
parts unbounded, so a chunked multipart body is never parsed. **No session and no
API token** — the HMAC signature in the query string (binding upload
id + user + expiry, ~10 min TTL, single use, claimed atomically
before the body is read) IS the authorization, so a `vst_` token
never has to appear in a shell command. The body is streamed to
staging under the server data dir with the compressed size cap
enforced inline (413 past it); bad/expired/reused signatures answer
403, a wrong content-type or an unrecognised archive format (sniffed
by magic bytes) answers 415. `app/skill_uploads.py` owns every rule.

## `workspace_assistant.py` — Workspace AI Assistant

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/workspace/assistant/message` | Send message to workspace AI assistant |
| POST | `/api/workspace/assistant/stop` | Stop workspace assistant session |
| GET | `/api/workspace/assistant/status` | Assistant state: `running` = busy with a turn, `alive` = session process up (alive while not running between turns) |

## `dida365_oauth.py` — TickTick/Dida365 Integration

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/dida365/status` | TickTick/Dida365 connection status |
| POST | `/api/dida365/authorize` | Start OAuth2 flow |
| GET | `/api/dida365/callback` | OAuth2 redirect callback |
| GET | `/api/dida365/projects` | List TickTick projects |
| POST | `/api/dida365/configure` | Save project + MCP path |
| POST | `/api/dida365/setup-mcp` | Auto-install ticktick-mcp |
| DELETE | `/api/dida365/disconnect` | Disconnect integration |

## `chat_bots.py` — Chat Bot Webhooks (WeCom / DingTalk / Feishu / Slack)

Workspace-level webhook configs for group chat bots. Not bound to any
store. Four platforms are supported: **WeCom / WeChat Work (企业微信)**,
**DingTalk (钉钉)**, **Feishu / Lark (飞书)** and **Slack**.

The platform is **derived by the server from the webhook URL host** —
it is never accepted from the client, so a stored bot can never
disagree with its own URL and a signing secret can never be posted to
a host the user did not choose. URLs must be `https` with an exact
host match (`qyapi.weixin.qq.com`, `oapi.dingtalk.com`,
`open.feishu.cn` / `open.larksuite.com`, `hooks.slack.com` /
`hooks.slack-gov.com`) plus the platform's path prefix; anything else
(cleartext, look-alike hosts like `oapi.dingtalk.com.evil.com`,
userinfo tricks like `https://oapi.dingtalk.com@evil.com/...`) → 400.
The single exception: a LOOPBACK host (strict `ipaddress` loopback or
`localhost`) is accepted — plain `http` included — only while the
server process runs with `VIBE_ALLOW_LOOPBACK_WEBHOOKS=1`, which the
offline e2e suite (CI and `docker/e2e`) uses to register a loopback
stub webhook; production leaves it unset. Send endpoints (`/test`,
`/send`, `/send-file`) re-derive the platform from the stored URL and
refuse with 400 when it no longer matches, so a row saved before this
validation cannot send to a non-platform host.

List responses mask `webhook_url` (the URL *is* the secret) and report
only `has_secret`; the single-item GET (and every create/update
response) returns the SAME masks for every caller — `webhook_url` as
`****` and `secret` as `****` + last 4 — because no route returns a
stored secret (see [Stored secrets are
write-only](#secretspy--stored-secrets-are-write-only-for-every-caller)).
The edit form posts the masks back untouched to mean "keep"; the
admin's Show/Copy buttons go through `POST /api/secrets/reveal`. The
keyword is returned in the list too: it is not a secret (the UI shows
it on the row), and it never reaches the agent — keyword handling is
entirely server-side.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/chat-bots/platforms` | Registry metadata the UI renders from: `{name, label, hosts, path_prefix, uses_secret, uses_keyword, security_options, security_required, supports_file, max_message, limits, limit_unit, doc_url}` per platform — `security_options` is the subset of `sign`/`keyword`/`ip` the platform's OWN console offers, `security_required` whether it insists on at least one (DingTalk); the form builds its whole security section from these two |
| GET | `/api/chat-bots` | List bots — `{id, name, platform, webhook_url_masked, has_secret, keyword, can_send_file, created_at, updated_at}`; the mask shows the host + last 4 chars of the secret-bearing part (e.g. `https://hooks.slack.com/...****abcd`) |
| GET | `/api/chat-bots/{id}` | Get one bot for the edit form — `webhook_url` (`****`) and `secret` (`****` + last 4) arrive masked for EVERY caller; the real values leave the server only through `POST /api/secrets/reveal`. `security_mode` names which of the platform's security settings the row uses (`sign`/`keyword`/`ip`, comma-joined; derived from the stored fields for rows that predate it) |
| POST | `/api/chat-bots` | Create `{name, webhook_url, secret?, keyword?, security_mode?}`. `security_mode` is what a platform's rules are checked against: omit it and the settings are read off `secret`/`keyword`; send it to record `ip` (both fields blank on purpose) or to switch a setting off. A platform with `security_required` and no setting → 400 naming the choices; a named setting without its value, or a value for a setting the platform does not offer (WeCom/Slack have none) → 400. `keyword` is appended by the SERVER to any outgoing message that lacks it, so agents and the Test button work unchanged. The response carries the masks, not the values you just posted |
| PUT | `/api/chat-bots/{id}` | Update `name`, `webhook_url`, `secret`, `keyword` or `security_mode`; `null`/blank required fields → 400; changing the URL re-derives `platform` (and drops what the new platform cannot use); a MASK (or an empty `secret`) means "keep the stored value", while a sent `security_mode` is AUTHORITATIVE — settings it does not name are cleared |
| DELETE | `/api/chat-bots/{id}` | Delete bot |
| POST | `/api/chat-bots/{id}/test` | Post a test message to the webhook; optional `{content}` (blank/whitespace → default, which starts with "Vibe Seller" — matters for DingTalk keyword security) |
| POST | `/api/chat-bots/{id}/send` | Post a real message: `{content, msgtype?}`. `msgtype` is `text` (default) or `markdown`; blank `content` / unknown `msgtype` → 400. Pre-send size check per platform (WeCom 4096 B, DingTalk 20000 B, Feishu 20000 B body, Slack 40000 chars text / 12000 markdown) rejects without a network call. Used by agents via the MCP bridge (see `docs/workspace.md`). |
| POST | `/api/chat-bots/{id}/send-file` | Upload a local file and post it as a `file` message: `{path}`. **WeCom only** — other platforms → 400 *before* path resolution. `path` is expanded (`~`) and must be absolute under an allowed root (`/tmp`, `/private/tmp`, or `~/.vibe-seller/downloads`) — anything else → 400 (blocks exfiltrating the DB/secrets/`~/.ssh`). File must be 6 B–20 MB (WeCom limit). Returns `{ok, message}`. Used by agents via the MCP tool `vibe_seller_send_chat_file`. |

### Platform notes

| Platform | Text | Markdown | File | Signing | Keyword | Limits |
|---|---|---|---|---|---|
| WeCom 企业微信 | `{"msgtype":"text","text":{"content":…}}` | `{"msgtype":"markdown","markdown":{"content":…}}` (reduced subset) | Yes (`upload_media` → `media_id` → `msgtype=file`) | — | — | 4096 B / msg |
| DingTalk 钉钉 | `{"msgtype":"text","text":{"content":…}}` | `{"msgtype":"markdown","markdown":{"title":…,"text":…}}` — `title` derived from the first non-empty line | No | Optional 加签: HMAC-SHA256(key=secret, msg=`"<ts-ms>\n<secret>"`) → base64 → URL-encoded, sent as `&timestamp=&sign=` **query** params; combinable with a keyword | Optional 自定义关键词 — server appends it to any message that lacks it | 20000 B / msg, 20 msg/min |
| Feishu / Lark 飞书 | `{"msg_type":"text","content":{"text":…}}` | Interactive card (`msg_type=interactive`, card JSON 2.0, `markdown` element) | No | Optional 签名校验: HMAC-SHA256(key=`"<ts-s>\n<secret>"`, msg empty) → base64 (NOT URL-encoded), sent as **top-level JSON body** `timestamp`/`sign` | Optional 关键词 — server appends it to any message that lacks it | 20 KB body, 100 req/min, 5 req/s |
| Slack | `{"text": …}` with `&<>` escaped | Block Kit `{"text": <summary>, "blocks":[{"type":"markdown","text":…}]}` (falls back to a standard→mrkdwn converter if the block is rejected) | No | — (the URL is the secret) | — | 40000 chars text / 12000 markdown |

Backend senders live in `app/notifiers/` — `base.py` holds the
`PLATFORMS` registry, `detect_platform`, shared error handling and the
size guard; one module per platform (`wecom.py`, `dingtalk.py`,
`feishu.py`, `slack.py`). Signing helpers are pure functions taking an
explicit timestamp (unit-tested against independent reference
implementations). Errors are logged server-side; the response message
never echoes the URL back to the client.

## `sse.py` — Server-Sent Events

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/sse` | SSE stream of all real-time events |

Note: SSE endpoint was renamed from `/api/events` to `/api/sse` to free up `/api/events` for event tracking.

## `browser.py` — Browser / Ziniao Utilities

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ziniao/launcher` | Download ziniao_webdriver.bat |
| POST | `/api/browser/web/start` | Start (or reuse) the store-less orchestrator `web` browser. Lazy-called by the `bin/_web` wrapper on first use; parallels `POST /api/stores/{id}/browser/start`. Accepts `force` for parity (no-op). |

## `system.py` — Server Runtime Metadata

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/system/info` | Return `{ platform: 'mac' \| 'windows' \| 'wsl' \| 'linux', version: str, commit: str \| null }`. `platform` is the host OS; `version` is the package version from `importlib.metadata` (`'0.0.0+dev'` for editable / dev installs); `commit` is the short git SHA when the source tree is a git checkout (useful when `version` is the dev fallback). Frontend fetches this once on mount and uses it as the single source of truth for platform-conditional UI (Ziniao launcher download, install hints, etc.) and the about/footer build stamp. |
| GET | `/api/system/update-check` | Check PyPI for a release newer than the one running here. Dev/local checkouts (`app.update_check.is_dev_version` — setuptools_scm `.devN+g<sha>` builds or the `+dev` fallback) always get `{ dev: true }`, since there's no PyPI release an arbitrary in-between commit corresponds to. Otherwise returns `{ dev: false, update_available: bool, current_version, latest_version?, platform?, upgrade_command?: str \| null, download_url?: str \| null, releases_page_url?, releases?: [{ version, name, body, url, published_at }], in_app_update: bool, highlights: [{ version, zh, en }], notes: [{ version, text }] }` — `upgrade_command` (`vibe-seller upgrade`) on macOS/Linux/WSL vs. `download_url` (GitHub releases page) on native Windows, matching the two install paths in the README. `releases` lists GitHub release notes for every version newer than the installed one (capped at 10, never newer than the installable `latest`). `in_app_update` tells the UI whether this install updates itself (see `app/auto_update.py`); `highlights`/`notes` are the same bodies parsed and merged **by section** across all skipped versions, newest first — one entry per bullet, `zh`/`en` paired 1:1 from a release's `## Highlights` halves, `text` a markdown bullet from `## Notes`; a body that doesn't match the release format becomes one highlight item carrying the whole body (never dropped). Rendered by `useUpdateFlow` / `UpdateAvailableModal` / `ReleaseNotesSections`. |
| GET | `/api/system/update-status` | Any logged-in user. The in-app auto-update state machine (`app/auto_update.py`): `{ supported, state: 'idle' \| 'downloading' \| 'ready' \| 'error' \| 'restarting', version, current_version, downloaded_bytes, total_bytes, error, running_tasks, platform }`. Pure memory read — no network I/O, safe to poll every few seconds. `running_tasks` is the live-agent count a restart would interrupt. |
| POST | `/api/system/update/restart` | Admin only (`403` otherwise). `409` unless the state is `ready`. Applies the staged update: Windows launches the staged `VibeSeller-Setup.exe` detached with `/SILENT /SUPPRESSMSGBOXES /NORESTART` (the installer stops server/tray/browsers and relaunches the tray); macOS/Linux runs the real `vibe-seller upgrade` detached and re-execs the server onto the new install the moment it lands (`app/upgrade_restart.py`). Running tasks are interrupted — the modal warns first. Returns `{ state: 'restarting', version }`. |

## `screenshots.py` — Screenshot Serving

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/screenshots/{screenshot_id}` | Serve screenshot PNG file |

## `attachments.py` — File Attachments

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/attachments/{task_id}` | Upload attachment. `400` on a disallowed type, `413` past the shared cap — see [Upload limits](#upload-limits) |
| GET | `/api/attachments/{task_id}` | List attachments |
| GET | `/api/attachments/file/{attachment_id}` | Download attachment |

### Upload limits

Every multipart upload route shares one contract, defined in
`app/uploads.py`: the same MIME allow-list (PNG / JPEG / GIF / WebP /
PDF) and one ceiling, `MAX_UPLOAD_SIZE` (100MB). It previously lived in
four places — create-task attachments at 10MB, chat staging at 15MB, the
vision reference image at 15MB, and a hardcoded copy in the browser — so
the same file was accepted by one box and refused by the next.

Routes covered: `POST /api/attachments/{task_id}`,
`POST /api/tasks/{task_id}/staged`, and
`POST /api/tasks/{task_id}/image/upload-reference`.

Size is enforced **while streaming**, in two layers:

| Layer | What it bounds | Response |
|---|---|---|
| `UploadBodyLimitMiddleware` (ASGI, `app/uploads.py`) | The request body itself, counted on the receive channel — so a chunked upload with no `Content-Length` cannot spool an unbounded temp file before the route runs | `413` |
| `save_upload()` | Memory while writing; aborts mid-write and deletes the partial file | `413` |

The middleware applies **only to `multipart/form-data`** — it is not a
global request-body cap, so a large JSON `PUT` is unaffected. It is
registered before `CORSMiddleware` so CORS stays outermost and a 413
reaches a cross-origin caller as a readable status rather than an opaque
network error.

The frontend never states the limit itself: `max_upload_size` from
`GET /api/settings` drives both the hint text and the "too large"
message (`frontend/src/uploadLimits.ts`).

## `channels.py` — Message Channels

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/channels/types` | List channel types |
| POST | `/api/channels/configure` | Configure channel |
| GET | `/api/channels/active` | List active channels |
| POST | `/api/channels/{id}/poll` | Poll for messages + extract events |
| POST | `/api/channels/{id}/send` | Send message (read-write channels) |
| DELETE | `/api/channels/{id}` | Remove channel |

## `cron.py` — Cron Jobs

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cron/jobs` | List cron jobs |
| POST | `/api/cron/jobs` | Create cron job |
| DELETE | `/api/cron/jobs/{id}` | Delete cron job |
| POST | `/api/cron/jobs/{id}/pause` | Pause job |
| POST | `/api/cron/jobs/{id}/resume` | Resume job |

## `ziniao_accounts.py` — Ziniao Accounts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ziniao-accounts` | List accounts |
| POST | `/api/ziniao-accounts` | Create account |
| PUT | `/api/ziniao-accounts/{id}` | Update account |
| DELETE | `/api/ziniao-accounts/{id}` | Delete account |
| GET | `/api/ziniao-accounts/{id}/browsers` | List browser profiles (returns structured status JSON on error) |
| POST | `/api/ziniao-accounts/{id}/restart` | Kill + relaunch Ziniao in WebDriver mode (Mac only, requires `running_normal` state) |

## `ads.py` — Amazon Ads API service

A store can be bound to the hosted ads service; vibe-seller holds no Amazon
credential. Nothing is configured: the service address is a constant in
`app/ads_client.py`, and the installation registers itself with the service
(`POST /installations`) the first time the stores are synced. The key it
gets is stored encrypted and never returned. Amazon stores only.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/ads/config` | Whether this installation has registered (`configured`); never the key |
| POST | `/api/ads/stores/sync` | Register if needed, upload the stores and record which are authorized; write an authorized store's marketplaces back onto its `platforms` / `countries` / `platform_countries` (add only). Also how a deployment learns a consent finished; pulls the skill once a store is authorized |
| POST | `/api/ads/stores/{id}/unbind` | Revoke one store's authorization (admin only). Body `{"purge": bool}`: also delete its ad history, unless another installation still manages the same Amazon ads account (`kept_shared`). Removes the skill when no store is left authorized |
| GET | `/api/ads/stores/{id}/auth-url` | A consent link for the store (admin only) |
| POST | `/api/ads/call` | The agent's one door (MCP tool `vibe_seller_ads_call`); the key is added here, never seen by the agent |

`/api/ads/call`:

- **Task scoping.** `task_id` is required. A task bound to a store reaches
  that store only; only a store-less task may name any authorized store.
- **Reserved paths.** `/me`, `/auth-url`, `/assignments`, `/ads-binding`,
  `/installations` belong to a person, not an agent, and are refused.
- **File answers.** A list the service answers as CSV is saved to
  `tasks/<id>/ads-data/`; the agent gets the path, the row count and the
  metadata from the `X-Meta` header.

## `vision.py` — Vision (AI image generation)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/vision/config` | kie.ai key status (masked), available models |
| PUT | `/api/vision/config` | Set the kie.ai key (admin only) |
| POST | `/api/tasks/{id}/image/generate` | MCP-tool entry; confirm-gated generation |
| POST | `/api/tasks/{id}/image/confirm` | Resolve a pending image request (confirm/edit/cancel) |
| GET | `/api/tasks/{id}/image/pending` | Recover a still-pending confirm card (for clients that connect after `image_request` fired) |

See [vision.md](vision.md) for the confirm-gate flow and SSE events
(`image_request`, `image_generated`).

## `main.py` — App-level Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Liveness probe (`{"status": "ok"}`) |
| GET | `/api/version` | `{ version, pending_version }`. `version` is the code this process loaded (fixed per process — never the metadata installed since). `pending_version` is the newer install the server is draining to restart onto, else `null`; see [subsystems.md § Upgrade restart](subsystems.md#upgrade-restart). |
| GET | `/api/plugins` | List active plugins' frontend bundles for the dashboard loader: `[{js_extension_path, requires_early_init}]`. Returns `[]` in an OSS-only install (no frontend plugins). See [backend.md § Plugin Framework](backend.md#plugin-framework). |
