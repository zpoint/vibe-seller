# Workspace

> User guide: [Knowledge & Skills](knowledge.md)

## Workspace AI Assistant

Conversational chat on the Workspace page for organizing knowledge via natural language.

- **Backend**: `WorkspaceAgentSession` (subclass of `AgentSession`) in `app/ai/workspace_assistant.py` — SSE-only (no DB persistence), auto-allows all control requests
- **Manager**: `WorkspaceAssistantManager` — per-user sessions with own semaphore (MAX_CONCURRENT=2), separate from task agent concurrency
- **SSE events**: `ws_assistant_message`, `ws_assistant_turn_done` (TURN end — one `result` per turn; the CLI process stays alive across turns), `ws_assistant_done` (PROCESS exit only — stop/crash/EOF) — independent of task events. Consumers clear the UI's running state on the turn event; the process event is the backstop. The hosted-MCP `workspace_assistant(send)` collector ends its wait on the same pair
- **Busy vs alive are two facts**: the session tracks `turn_in_flight` (a delivered message awaits its `result`); `GET /status` reports `running` = busy with a turn and `alive` = the process is up and input-capable (routinely alive while not running — the next send then reuses it, context and all)
- **API**: `POST /api/workspace/assistant/message`, `POST /stop`, `GET /status` in `app/routers/workspace_assistant.py`
- **System prompt**: `app/prompts/workspace_assistant.md` with `{stores_list}` placeholder injected at runtime
- **Skill tools over a restricted stdio MCP**: the assistant launches `python -m app.mcp_server --tools vibe_seller_list_skills,vibe_seller_save_skill,vibe_seller_import_skill` from a PER-SESSION `--mcp-config` artifact (`data/system-prompts/<session>.mcp.json`), never the shared workspace-root `.mcp.json` — store-less tasks write that file with their full task toolset and their own task id (two writers, one file). The `--tools` allowlist is enforced twice (intersection on `tools/list` AND dispatch refusal), and a restricted session needs no task id. Whether the launch also passes `--strict-mcp-config` follows the profile's `load_global_mcp` through the one shared decision `claude_backend_utils.mcp_launch_flags` — identical to the task launches (globals off → strict; globals on → the session file merges with the user's servers). Skill writes go through the skills manager — never hand-written files — so the slug/cap/symlink/built-in/overwrite gates and lockfile provenance always apply; a URL or local-dir install uses `vibe_seller_import_skill` (see § Skills System)
- **Frontend**: `WorkspaceAssistantView.tsx` — default view when entering Workspace; file editor shown when a file is selected; "+ Tell AI" button returns to chat

## Knowledge System

3-level knowledge hierarchy:

### L1: Builtin Knowledge (repo-synced)

- Source: `app/knowledge/` in package → synced to `~/.vibe-seller/knowledge/project/`
- Content: Universal rules (Amazon URLs, Ziniao behavior, skill environment)
- Read-only: Only maintainer commits change L1
- Sync: `knowledge_sync.fetch()` on startup, remote check async on task start
- Catalog: `knowledge/project/CATALOG.md` (maintainer-edited, one row per file)

### L2: Cross-Store Relationships (local, user/AI-editable)

- Location: `~/.vibe-seller/knowledge/` (excluding `project/`)
- Content: Cross-store relationships (跟卖, shared brands, shared suppliers), plus any user-created files
- Catalog: `knowledge/CATALOG.md` — **includes L1 entries + L2 files** (AI-generated, daily catalog-sync task)

### L3: Per-Store Knowledge (local, agent-generated)

- Location: `~/.vibe-seller/stores/<slug>/` (all files under the store directory)
- Content: ALL store-specific knowledge (selectors, SKU lists, shipment info, STORE.md, notes.md, etc.)
- Private to each store — NOT shared between stores
- Catalog: `stores/<slug>/CATALOG.md` — **includes L2 entries + all store files** (AI-generated, daily catalog-sync task)

### Catalog Accumulation

Each catalog level includes all entries from the level below. Agents read **one** catalog file:
- Store tasks → `stores/<slug>/CATALOG.md` (L3, contains L1+L2+L3)
- No-store tasks → `knowledge/CATALOG.md` (L2, contains L1+L2)

### Catalog Format: 3 Columns

All three catalog levels use the same shape:

```
| File | Relevance | Summary |
|---|---|---|
| knowledge/project/common/noon-sites.md | noon | Noon Seller Center portal domains (login/welcome/store/...) |
| knowledge/project/common/amazon-sites.md | amazon | Seller Central URLs for 23 countries... |
| stores/my-store/notes.md |  | Empty/stub — knowledge accumulates here |
```

**Relevance** tags a row by platform (`amazon`, `noon`, comma-separated for multiple). Empty Relevance means cross-platform / optional.

The agent prompt (`design_system.md`, Phase 1 — Knowledge Recall) tells the agent to read every row tagged with a platform the task touches before opening that platform; untagged rows are optional. This is prompt guidance — no hook enforces it (`app/ai/catalog_read.py` only records whether the catalog was read, for the PreToolUse plugin-gate signature). The L2/L3 sync prompts (`CATALOG_DESC_L2`/`CATALOG_DESC_L3` in `app/prompts/__init__.py`) preserve the column when copying rows from L1 → L2 → L3. Earlier versions dropped Relevance at L2 generation; that broke the contract.

The catalog's **structure** is enforced server-side: a write to `knowledge/CATALOG.md` (L2) or `stores/<slug>/CATALOG.md` (L3) through `PUT /api/workspace/file` — the route behind the agent's `vibe_seller_write_workspace_file` — is validated by `app/workspace/catalog_validation.py` before it lands: accumulation (L2 carries every L1 row, L3 every L2 row, Relevance unchanged), completeness (every tier file exactly once — a filename only sets which rows are REQUIRED; a row naming an existing store file is the writer declaring it knowledge, and is honoured whatever it is called, so a user's `PROMO_RULES.md` can be cataloged), isolation (no other store's files; machine files — `*.json`, `*.lock`, `*.cache` — are the one name-based refusal: the server wrote them, they are not prose), format (header, 3 columns, tier path prefixes, Relevance tokens known to the workspace) and the stub contract (a substantive file must be summarized). A violation is a 400 naming the rows to fix — that message is the sync agent's redo instruction.

```
~/.vibe-seller/
├── knowledge/
│   ├── project/              ← L1 (synced from package)
│   │   ├── CATALOG.md        ← L1 catalog (hand-maintained)
│   │   └── common/*.md
│   ├── CATALOG.md            ← L2 catalog (L1 + L2 entries)
│   ├── notes.md              ← L2 file
│   └── amazon/PL.md          ← L2 file
└── stores/
    └── my-store/
        ├── CATALOG.md         ← L3 catalog (L1 + L2 + L3 entries)
        ├── STORE.md           ← L3 file
        ├── notes.md           ← L3 file
        └── amazon/PL/*.md     ← L3 files
```

### Knowledge Sync

- `knowledge_sync.fetch()`: Copies L1 from package to workspace (runs at startup). Only files listed in `app/knowledge/MANIFEST.txt` are synced; falls back to `rglob` if MANIFEST is missing. `__pycache__` is always excluded.
- Remote sync: the central sync scheduler's daily `knowledge` job — a conditional-GET probe, a download only when the repo moved (`app/sync/jobs.py`)
- Manual sync: `POST /api/sync/knowledge/run` (remote only — local sync happens at startup)
- Sync state: `GET /api/sync/status` (the `knowledge` job)
- Files tracked via `MANIFEST.txt` in `app/knowledge/`
- `get_structured()` filters `__pycache__` from knowledge listings (both project and local)

### Agent Knowledge Writing

Knowledge is kept correct **in-task**, not in a post-task phase. The
"Keeping knowledge correct" section of `app/prompts/design_system.md`
tells the agent, once its result is recorded and before it ends the turn,
to rewrite stale facts in place (the current value only — no "was stale"
notes or dates), to add what a different task would need on its first
try, and to write nothing when it learned nothing. Writes go through
`vibe_seller_write_workspace_file`.

`knowledge/` and `stores/` are the agents' **only** memory: Claude Code
auto-memory is off for every agent (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`,
set by `ProfileManager.get_env_for_profile`), and
`app/workspace/claude_memory_cleanup.py` purges the auto-memory folders
at boot. A store's `metadata.json` (its
platform/country list) is written by the server, never the agent:
`app/store_metadata.py:sync_store_metadata` records the task's
platform/country after each store task.

Where knowledge goes:

| What | Where | Level |
|---|---|---|
| Cross-platform rules | `knowledge/project/common/*.md` | L1 (READ-ONLY — synced from source, agents must NOT write) |
| Cross-store relationships | `knowledge/notes.md` | L2 |
| Platform/country selectors | `stores/<slug>/<platform>/<COUNTRY>/notes.md` | L3 |
| Store data (SKU, shipment) | `stores/<slug>/<platform>/<COUNTRY>/<name>.md` | L3 |
| General store notes | `stores/<slug>/notes.md` | L3 |
| New L3 topic files (when an existing file doesn't fit) | `stores/<slug>/<topic>.md` (lowercase topic name) | L3 |

**L3 naming convention — knowledge vs task output.** L3 holds knowledge:
procedural, transferable across runs, organized by topic. It is NOT for
per-run task output (audit reports, improvement plans, captured metric
tables). The naming below sets the sync agent's default — a
name that looks like run output exempts a file from FORCING a row,
but it never forbids one: the writer declares what a row is (see
after the table):

| Use | Naming | Examples | Path |
|---|---|---|---|
| **Knowledge** (lives at L3) | lowercase, topic-named, no dates | `notes.md`, `browser-tips.md`, `fbn-quirks.md`, `cases-process.md` | `stores/<slug>/<topic>.md` |
| **Per-run output** (does NOT live at L3) | ALL_CAPS, dated, report-y | `*_PLAN_*.md`, `*_REPORT_*.md`, `*_AUDIT_*.md`, `*_2026-04-29.md` | task CWD (`~/.vibe-seller/tasks/<task-id>/<filename>.md`) |

The L3 catalog regen agent (`CATALOG_DESC_L3` in `app/prompts/__init__.py`)
scans every file under `stores/<slug>/`: a lowercase topic name is
included as knowledge by default, and a name that looks like run output
(ALL_CAPS / dated) is opened before it is skipped — durable facts or a
rule the user set (the user's own `PROMO_RULES.md`) are knowledge and
get a row, a one-run numbers dump is left out and named in the result.
An existing row the sync did not write is a declaration it must keep,
not silently drop. Only machine files — `metadata.json`, `*.json`,
`*.lock`, `*.cache` — are refused a row outright: the server or a tool
wrote them and no reader reads them. The location rule is the other
defense: the in-task system prompt directs report writes to the task
CWD / `store-data/`, so per-run output should not land at L3 at all.

### One-time knowledge cleanup after an upgrade

`app/knowledge_contract.py`. A harness change makes notes written before
it misleading — a note describing how the platform's checks used to work
is read before the skills are, and acted on as fact. The base prompt
keeps such notes from being written and tells an agent to delete one it
meets; the stock an upgrade leaves behind is cleared **once per scope**,
inside the nightly catalog-sync task (scope `knowledge` for the global
L2 run, `store:<id>` for a store's L3 run). The contract each scope was
last cleaned to lives in server-owned `app_settings`; a scope whose
record is older than `KNOWLEDGE_CONTRACT` gets one extra instruction in
its catalog task and runs even when its catalog is current. The record
is written only when that task COMPLETES — a failed run (out of credits,
a crash) leaves the scope owing, so the next catalog sync runs it again.

While a cleanup runs, its writes are held to two rules, enforced at the
`vibe_seller_write_workspace_file` boundary and re-checked after the
run: **deletion only** — every line kept must already exist, in order
(nothing added or reworded; a store-rule line like `acos_no_lower: 20`
is a user-set standard and is never deleted) — and **its own tier** —
the global run edits `knowledge/` but not the read-only
`knowledge/project/`, a store's run edits `stores/<slug>/` only. A file
changed by more than deleting lines is restored from the pre-run
snapshot, unless someone else wrote it through the endpoint meanwhile —
their edit is not undone. A run that restored anything leaves the scope
owing and is retried by the next catalog sync.

## Skills System

Reusable agent procedures with scripts, bundled in `app/skills_v2/` and synced to `~/.vibe-seller/.claude/skills/`. Skills must be direct children of `skills/` for Claude Code auto-discovery. (`app/skills/` is the frozen legacy tree for clients on browser-use 0.12 — never add files there; see [CLAUDE.md § Skill hosting across release lines](https://github.com/zpoint/vibe-seller/blob/main/CLAUDE.md#skill-hosting-across-release-lines) and [docs/browser-use-0.13-migration.md](browser-use-0.13-migration.md).)

- **Three-tier sync**: local package → workspace `skills/`, remote GitHub via `MANIFEST.txt`, driven by the central sync scheduler's daily `skills` job (plus the Sync button) — see `app/sync/scheduler.py`
- **User skills**: created via UI at `~/.vibe-seller/.claude/skills/{slug}/` (same directory as built-in, distinguished by `.sync_meta.json`)
- **Four doors, ONE install path**: a skill gets in via the UI create form, an in-task agent (`vibe_seller_save_skill` text authoring, `vibe_seller_import_skill(path | url)`), the hosted MCP (`manage_skill` `begin_upload`→`import(upload_id)`, or `import(url=…)`), or the workspace assistant (same restricted MCP). Every ARCHIVE source — uploaded, packed, or fetched — ends in the same composition `skill_install.import_staged`: skills-tree lock → ONE thread hop (strict extract → slug derive → `save_skill_blocking(replace=True)` → consume upload) → auto-commit outside the lock. No source has its own copy of it
- **Presigned archive upload** (external agents): `manage_skill(begin_upload)` → raw `PUT` of a `.tar.gz`/`.tar`/`.zip` of the skill dir to the signed one-shot `put_url` (`PUT /api/skill-uploads/{upload_id}`, `app/skill_uploads.py`) → `manage_skill(import, upload_id=…)`. The file BYTES never pass through the agent's LLM (no token cost, no base64 corruption, no emit-size ceiling); the archive is extracted strictly (regular files only, safe names, no case/Unicode-normalization collisions, one common top-level dir stripped, `SKILL.md` at the root) and installed byte-identical, executable bits taken from the archive. The slug derives from the SKILL.md frontmatter `name`
- **Local directory from a task** (`vibe_seller_import_skill(path=…)`): the in-task MCP process runs on the AGENT's machine, so IT walks and packs the directory (`app/skill_pack.py`: `SKILL.md` required at the root, symlinks escaping the directory refused by name, in-dir links dereferenced, non-regular entries refused, caps pre-checked) and POSTs the archive to `POST /api/workspace/skills/import-local` as raw bytes. The SERVER never reads a caller-named path — the bytes arrive exactly like a presigned upload, guarded the same way; `origin_url` defaults to the path, so the skill lists as `imported`
- **URL source** (`vibe_seller_import_skill(url=…)` / `manage_skill(import, url=…)` / assistant): the SERVER fetches it — guards in `app/skill_fetch.py`: http(s) only, no userinfo; the host must resolve to a PUBLIC address (private/link-local/reserved refused; loopback only under the same `VIBE_ALLOW_LOOPBACK_WEBHOOKS` e2e switch the webhook stubs honor); redirects are followed manually with the address guard re-applied per hop; timeouts and streamed size caps. A GitHub-style `tree`/`blob` URL of a skill folder is walked through the git-trees + git-blobs API with `recursive=1` (exec bits from tree modes; submodules and symlinks refused; a directory the server did not expand fails loudly — a dropped entry can never go unreported); any other URL is fetched as the archive it already is (magic bytes decide, never the extension). `origin_url` defaults to the URL → lists as `imported`
- **Inline writes stay small**: `PUT /api/workspace/skills/{slug}` (and `update`) carry text `files` (stored as UTF-8), `files_bin` (base64) and `executables` for tiny text-only skills and edits — a real skill import goes through one of the paths above
- **Replace vs merge vs overwrite**: `import` (`replace: true`) swaps the tree in wholesale — files the call does not carry are deleted, and a failure leaves the old skill untouched; `update` merges (files given are written, the rest stays). Replacing an EXISTING user skill is refused without `overwrite: true`, so a hand-made skill is never wiped silently
- **Write contract**: paths and sizes are validated before any byte lands (no `..`/absolute/backslash, no `SKILL.md` collision, no symlink escapes; per-file/total/count caps) — the rules live in `app/workspace/skills_manager.py`
- **Dep auto-install**: skill `requirements.txt` deps are auto-installed into the shared workspace venv (`~/.vibe-seller/.venv/`) during sync
- **Source tracking**: `get_structured()` returns `source: 'builtin' | 'imported' | 'custom'`. Synced skill names tracked in `.sync_meta.json`; imported skills tracked in `skills.lock.json`
- **Reserved slugs & create collisions**: names starting with `_` are rejected; `create_skill` (UI, hosted MCP, any door) additionally refuses a slug that already exists (naming `update`/`import overwrite=true` as the intended doors — the overwrite gate cannot be bypassed through create), a built-in (synced) slug, and a name that slugifies to the empty string (e.g. pure CJK — `dir / ''` IS the skills dir)
- **API**: `POST /api/sync/skills/run`, `GET /api/sync/status`, `POST /api/workspace/skill`, `PUT /api/workspace/skills/{slug}`, `POST /api/workspace/skills/import-local` (archive body packed by the in-task MCP process), `POST /api/workspace/skills/import-url` (server fetches) — see [api.md](api.md)
- **Frontend**: skills panel in Workspace sidebar with sync button, builtin badges, and skill creation form

### Ads skill routing

The `amazon-ads-api` skill is not in this repo: it ships from the bound
ads service, which versions it (a content hash). It is pulled once a store is authorized, at
boot, and by the sync scheduler's daily `ads-skill` job (after the
GitHub `skills` job) and the Sync button; the bundle is
downloaded only when the service's version differs. A pulled bundle is
data: anything but documentation is dropped (`app/ads_skill.py`) — no
`gates/*.py` or scripts from a remote source ever run here. Revoking the
last authorized store removes the skill.

**A change reaches a machine the day it ships, not the day after.** The
service names its skill version in the `X-Skill-Version` header of
*every* answer it gives, and `app/ads_client.call` reads it there — the
one path every request to the service takes, the agent's
`vibe_seller_ads_call` tool included. A version that differs from what
this machine installed triggers the `ads_skill` job
(`reason=stale`) in a background task: never awaited by the call that
noticed, never able to fail it, and at most once per remote version per
ten minutes, so a bundle download that keeps failing cannot turn every
agent call into a sync. The two paths the refresh itself calls,
`/skill/version` and `/skill/bundle`, are not read: their header is the
version being installed, and noticing it would make every refresh start
the next one. A missing header is no information, not "up to date" — the
daily job stays the backstop, and is what a service older than the
header relies on.

**What a refresh changes is the next task, never a running one.** The
skill is copied into a task workspace once, at launch, under the
skills-tree lock (`app/workspace/skills_tree`), and the swap the refresh
does is taken under that same lock — so an agent mid-run keeps reading
the skill it started with. The version held in memory (`AppSettings` on
first read, `sync_bundle` and `remove` after that) is what makes one
header cost no database read.

Each task sees one ads skill: `amazon-ads-api` for a store authorized on
the service, `amazon-ads` (browser) for any other store or no store, both
for a task spanning both. Claude Code discovers skills up to the git
root, so each task folder is made its own git repo
(`_make_skill_root`) and gets only its routed skills; `CLAUDE.md` is
still read from parents. A routed-away skill is also refused at load time
by the PreToolUse hook (`app/ads_routing.py`). When the store cannot be
read, routing falls back to the browser skill — the one every store can
use.

Routing decides what a task *can* load; the prompt says which path it is
*on*. Once any store is authorized, an authorized store's task is told
to work its Amazon ads over the API even where its task or plan names the
console; any other store's task is told only that the API refuses it. A
no-store task — a schedule's planner, an all-stores orchestrator — sees
each store labelled `ads: API` or `ads: not authorized`. This matters for
a fanout: its plan is written once and handed verbatim to every store.
Until a store is authorized none of this is said.

An unauthorized store hears a fact, never a skill name: it may sell on
noon or on nothing recorded, and telling it to use the Amazon browser
skill made it treat any ad request as Amazon ad work and run that skill's
full audit procedure. `tests/e2e/test_ads_mixed_fanout.py` runs one
schedule over an API store and a console store against a fake service.

## Run Data (`store-data/`)

Per-store **run artifacts** (reports, captures, exports) live outside the
knowledge tree so they never surface as knowledge (catalog injection, the
UI knowledge tab, agent knowledge writes):

```
~/.vibe-seller/
├── stores/<slug>/                  # curated knowledge — flat files only
└── store-data/<slug>/<area>/       # run data, git-tracked
    ├── <YYYY-MM>/<dated file>      # dated artifacts, bucketed by run month
    └── <working file>              # cross-run files (workbooks, cursors)
```

The layout contract is stated in `design_system.md` (every task's system
prompt): dated outputs go to `store-data/<slug>/<area>/<YYYY-MM>/`; agents
read prior runs' outputs from there before re-collecting. A boot-time,
marker-gated migration (`app/workspace/store_data_migrate.py`, called from
`ensure_init()` in the app lifespan) upgrades old workspaces in place:
subdirectories found under `stores/<slug>/` are run data by definition and
move to `store-data/<slug>/`; loose dated files bucket into the month
derived from the file's own name. The UI mirrors the agent view — one
entry per store with knowledge files plus a run-data section
(`/api/workspace/structured`); binary artifacts are served via
`/api/workspace/file/raw`.

## Per-Task Workspace Isolation

Each task runs in an isolated working directory at `~/.vibe-seller/tasks/{task_id}/`. Shared resources are symlinked so agents can read/write them while task-specific files (downloads, scripts, temp data) stay isolated.

**`prepare_task_workspace(task_id, *, clean=False)`** in `app/workspace/manager.py`:

1. Creates `~/.vibe-seller/tasks/{task_id}/`
2. Symlinks shared resources into the task directory:
   - `.claude` — copied from `~/.vibe-seller/.claude` (skills, settings; excludes `.venv`, `__pycache__`). The `browser-use` skill is copied for **every** task, including no-store (orchestrator) tasks — they now have the store-less `web` browser (see [docs/browser.md](browser.md)) and need the skill's CLI reference.
   - `knowledge` → `~/.vibe-seller/knowledge`
   - `stores` → `~/.vibe-seller/stores`
   - `store-data` → `~/.vibe-seller/store-data`
   - `CLAUDE.md` → `~/.vibe-seller/CLAUDE.md`
3. If `clean=True`: wipes the task directory first (used on retry for a fresh start)

Called automatically by `ClaudeCodeBackend.run()` before launching the agent subprocess. On retry, called with `clean=True` (best-effort — failures are logged, not raised).

### Symlink Write Caveat

Claude Code's `--add-dir` is set to the task directory only. The Read tool resolves symlinks and returns absolute paths in results (e.g., `~/.vibe-seller/stores/...` instead of `tasks/{id}/stores/...`). Some models reuse these resolved paths for Write, which silently fails because the absolute path is outside `--add-dir` scope.

**Solution**: The MCP `vibe_seller_write_workspace_file` tool takes a relative path (e.g., `stores/<slug>/CATALOG.md`) and writes through the workspace manager API, bypassing Claude Code's path restrictions. Any task that writes to `stores/` or `knowledge/` must use this MCP tool — not the built-in Write tool.

**Note**: The `Edit` tool (unlike `Write`) can modify previously-read files through resolved symlink paths, bypassing `--add-dir` scope. This means `Edit` can write to L1 files, and those edits do **not** go through the workspace manager API or the `write_file()` guard. The `write_file()` guard only protects writes performed via the workspace manager / MCP write tool; prompt-level guidance is the primary mitigation against accidental L1 edits via `Edit`.

### Agent-Facing MCP Tools (selected)

Defined in `app/mcp_server.py`; every tool proxies an HTTP call to the backend. Full list is in the source.

| Tool | Purpose |
|---|---|
| `vibe_seller_write_workspace_file` | Write through symlinks (see caveat above) |
| `vibe_seller_email_info` / `vibe_seller_send_email` / `vibe_seller_sync_email_now` | Per-store IMAP email DB paths, send, force-sync |
| `vibe_seller_get_schedule_state` / `vibe_seller_set_schedule_state` | Cross-run cursor for scheduled tasks (e.g. email watermark) |
| `vibe_seller_list_chat_bots` | List configured chat bots (WeCom 企业微信 / DingTalk 钉钉 / Feishu 飞书 / Slack) as `[{id, name, platform, can_send_file, max_message}]`. Webhook URLs and secrets are stripped so nothing secret reaches the LLM. Formerly `vibe_seller_list_wecom_bots` |
| `vibe_seller_send_chat_message` | Post to a group via `{bot_id, content, msgtype?}`. `msgtype` accepts `text` (default) or `markdown`. The markdown dialect and size limit differ per platform (WeCom: reduced subset, 4096 B; DingTalk: title auto-derived, 20000 B; Feishu: standard markdown as a card, 20000 B; Slack: Block Kit, 12000 chars) — the pre-send check refuses oversized messages without a network call.
Keyword security (DingTalk 自定义关键词 / Feishu 关键词) is handled
entirely server-side: the agent never sees or passes the keyword.
Proxies `POST /api/chat-bots/{id}/send`. Formerly `vibe_seller_send_wecom_message` |
| `vibe_seller_send_chat_file` | Send a local file `{bot_id, path}` — WeCom bots only (20 MB). Proxies `POST /api/chat-bots/{id}/send-file`. Formerly `vibe_seller_send_wecom_file` |

### Adding a Built-in Skill

1. Create `app/skills_v2/my-skill/SKILL.md` with YAML frontmatter (`name`, `description`)
2. Add implementation files (scripts, `requirements.txt`)
3. Update `app/skills_v2/MANIFEST.txt` with relative paths to all files
4. Sync copies files to `~/.vibe-seller/.claude/skills/my-skill/`

## Optional Integration Bundles

Some vendors (the official WeCom / Feishu / DingTalk / Google Workspace CLIs) ship their own large pool of SKILL.md files. We do **not** vendor them into `app/skills_v2/` — that would bloat every agent's skill index and pull in externally-copyrighted content. Instead, each is an opt-in integration an admin connects in Settings → Integrations, managed end to end by `app/cli_integrations/` (below).

### Workplace CLIs (WeCom / Feishu / DingTalk / Google Workspace)

Implementation: `app/cli_integrations/` (framework: `manager.py`, `paths.py`, `skills.py`, `login.py`, `download.py`; one module per platform: `wecom.py`, `feishu.py`, `dingtalk.py`, `gws.py`). Endpoints under `/api/integrations/cli` (see [api.md](api.md)); UI: `frontend/src/components/settings/WorkplaceCliPanel.tsx` (Google Workspace has its own card, `google`, with the same rows). User docs: [workplace-cli.md](workplace-cli.md), [google-workspace.md](google-workspace.md).

Vibe Seller **manages the CLI itself** — the Windows installer ships no Node.js, and a CLI the user installs later is invisible to an already-running server (its PATH is a launch-time snapshot). A platform module (`CliIntegration`, `base.py`) only declares: the current version, where the binary for this OS/arch is and how to verify it, where the official skills of that version are, how to read the login state, and the login steps. Everything else is shared.

| | WeCom `wecom-cli` | Feishu `lark-cli` | DingTalk `dws` | Google Workspace `gws` |
|---|---|---|---|---|
| Binary | npm `@wecom/cli-<plat>` tarball, `dist.integrity` (sha512); npmjs → npmmirror | GitHub release archive → npmmirror binary mirror; `checksums.txt` sha256 | GitHub release asset → Gitee; `checksums.txt` sha256 | GitHub release `google-workspace-cli-<target>` (Linux: the static musl build); the asset's own `.sha256`; no mirror. Version from npm `@googleworkspace/cli` |
| Skills | repo `main` (`skills/wecomcli-*`) — the command set is served at run time, the repo has no tags | tag `v<ver>` (`skills/lark-*`); fallback: open.feishu.cn's digest-verified hosted archives | release `dws-skills.zip` (`multi/dingtalk-*`), checksum-verified | tag `v<ver>` (`skills/gws-*`), only `shared` + the services signed in for (`SkillLayout.keep`: drive, gmail, sheets, docs, calendar) |
| Login | `auth init --noninteractive --no-browser --output-qrcode` (QR + scan link, binds a smart bot) | `config init --new` (create the app) then `auth login --recommend --json` (device flow) | `auth login --device --no-browser --recommend` (device code; org admin must enable CLI access) | one-time setup: the person's own Desktop-app OAuth client JSON, uploaded (`apply_setup` → `client_secret.json`); then `auth login -s drive,gmail,sheets,docs,calendar` (Google link; redirect to `http://localhost:<port>`, relayed by paste-back when the browser is elsewhere) |
| Status | `auth show` (local) | `auth status` (local; exit 3 = not configured) | `auth status --format json --readonly` (local) | `auth status` (local files, then one token refresh + userinfo: `expired` shows a revoked / 7-day testing-app sign-in) |
| Isolation env | `WECOM_CLI_CONFIG_DIR` | `LARKSUITE_CLI_{CONFIG,DATA,LOG}_DIR` + no update/skills notifier | `DWS_CONFIG_DIR` + `DWS_KEYCHAIN_DIR`, `DWS_NO_UPDATE_CHECK`, `DINGTALK_DWS_AGENTCODE`; macOS `DWS_DISABLE_KEYCHAIN` | `GOOGLE_WORKSPACE_CLI_CONFIG_DIR`, `..._KEYRING_BACKEND=file` (the OS keyring entry is one per OS user, shared with a personal gws), `..._TOKEN=''`, `CLOUDSDK_CONFIG` (gws runs `gcloud` itself); `env_unset` drops `..._CLIENT_ID/_SECRET/_CREDENTIALS_FILE`, `GOOGLE_WORKSPACE_PROJECT_ID` |

**State lives on disk, nowhere else** (`paths.py`): `tools/<key>/install.json` names the current version, `tools/<key>/versions/<version>/` holds that build, `tools/<key>/state/` holds the CLI's config + login (private to this `VIBE_HOME` — never the user's personal CLI login), and `.claude/skills/<key>/` is the umbrella. *Installed* = the manifest's binary exists; *enabled* = installed + umbrella. There is no DB flag.

**One dir per version, never an in-place swap, never a move**: a running CLI holds its executable (and `dws` a native library it unpacks next to itself) open, which Windows refuses to rename — and right after the `--version` check the antivirus holds the fresh files open for seconds too, so even moving a just-checked staging dir into place fails (`Access is denied`). A build is therefore written straight into its own `versions/<version>/`, checked there, and committed by flipping `install.json`, the only thing anyone resolves a binary through; a dir the manifest does not name is never on a PATH, and a failed attempt's dir is removed or replaced on the next install; the previous version is kept (a task started before the update still has it on its PATH, and Linux/macOS would let us delete it from under that task), older ones are pruned.

**Install** (`manager.install`): latest version → candidate URLs (official host, then mirror) fetched through `system_proxy.urlopen` (the proxy/direct path that reaches each host) → checksum → `--version` must succeed here → land + flip manifest. An enabled integration also gets the skills of the new version.

**Update is automatic** (`manager.update`, run by the DAILY `workplace_clis` sync job, source `external`; there is no update button). Only INSTALLED platforms are touched — the job's precondition is "some `install.json` exists", so an install with nothing connected never asks a vendor. A platform moves when the vendor's latest differs from the installed version (a withdrawn release is followed back), through the same verified install. Its skills are reconciled separately: the umbrella records the version it was built from (`.cli-version`, swapped in with it), and an enabled platform whose record differs from the binary gets the matching skills — so a run whose binary moved but whose skills download failed is healed by the next run, not left documenting the old version until the next release. Each platform fails alone (reported in the run's `error`) and keeps the version it runs.

**Login** (`login.py`): each step is one CLI run with stdin closed; a per-platform parser turns output lines into the page's view (`url`, `user_code`, `qr`, `message`, `hint`). One `LoginSession` per platform owns the child process — cancel, timeout, restart and server shutdown kill its whole tree. `enable_after` lets the session enable the platform itself on success, so closing the page mid-login still finishes; the view's `finishing` stays true from the sign-in until that enable returned, and the page polls until it drops (reading the row in between showed "signed in, the AI can't use it" and stopped there). `login_steps(status)` are the steps of a sign-in starting from `status` — a platform that is signed in already signs in again ("Sign in again": another account, or a fresh sign-in); only what the status says exists (Feishu's app) is skipped. Every vendor CLI re-runs its flow when asked. The view and the QR are admin-only: whoever scans signs the machine in.

**Enable** (`manager.enable`): refused unless `auth_status` is usable — an agent must never be the one to log in. Fetches the skills of the INSTALLED version, keeps `<prefix><name>` dirs, renames them to `<name>/` under the umbrella, rewrites `../<prefix>x/` relative links to `../x/`, and writes the umbrella `SKILL.md` (rules + catalog). The rules forbid the platform's login / update / skill-install commands (`forbidden_commands`) — the official skills tell an agent to run several of them — and say they override the sub-skills. Staging is `.claude/.sync-staging` (excluded from task copies, swept at boot); the swap is `skill_swap.swap_in`.

**Agent reach** (`registry.enabled_bin_dirs` / `enabled_env`, wired in `apply_agent_venv_path`): only ENABLED platforms put their version dir on the agent's PATH (above the inherited PATH, so the managed build beats a personal npm install) and their env switches into the agent's env — enabling one, two or three platforms adds exactly those. Below them and above the inherited PATH sits `tools/_guard/` (`registry.ensure_guard`): an erroring `wecom-cli` / `lark-cli` / `dws` / `gws`, so a DISABLED platform's name never falls through to a personal install on the machine (which would act as that person's own login) — the same pattern as the browser-use guard. Disable removes only the umbrella; binary and login stay.

**What a platform can add to the contract** (`base.py`, used by `gws`): `env_unset` — inherited vars removed from every run, the server's and the agent's (an empty value is not "unset" for every CLI); `network_targets` — hosts the server's own runs (status, sign-in) must reach, so they take `system_proxy.checked_download_env(targets)`; `setup` / `needs_setup` / `apply_setup` — a one-time thing only the person can make (Google's sign-in key), done on the page as a form, `POST /{key}/setup`; the sign-in refuses with `needs_setup` until then; `LoginStep.paste_back` — the approval comes back as a redirect to `LoginView.callback` (a local port): the page offers a box for the address a browser on ANOTHER computer ended on, and `LoginSession.paste_back` relays exactly that one request to that one port (`http`, `localhost`/`127.0.0.1`, the callback's port, `code=` or `error=` present), never through a proxy; the CLI's exit decides the outcome (gws drops the connection unanswered on a refusal); `SkillLayout.keep` — keep only the sub-skills a predicate accepts.

**The earlier Google Workspace switch** (a `gws` the person installed and signed in to themselves) is retired once at boot (`gws_legacy.migrate`): its umbrella (`.claude/skills/gws` without `.cli-version`) and the `google_workspace_enabled` setting go, and if either said "on", the row shows `gws_moved` (`paths.write_notice`, cleared by `enable`). A personal gws and its sign-in are never read or changed; the `gws` guard keeps an agent from reaching it.

Tests: `tests/unit/test_cli_integrations_{core,login,gws}.py`, `tests/workflow/test_wf_cli_integrations.py`, `tests/workflow/test_wf_gws_integration.py` (fake platforms from `tests/fixtures/fake_cli_integration.py`), `tests/integration/test_cli_integrations_real.py` (the real CLIs, downloaded per OS; CI runs it on Linux, macOS and Windows), `tests/e2e/test_workplace_cli_e2e.py`, `tests/e2e/test_gws_e2e.py`.
