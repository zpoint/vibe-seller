# Workspace

## Workspace AI Assistant

Conversational chat on the Workspace page for organizing knowledge via natural language.

- **Backend**: `WorkspaceAgentSession` (subclass of `AgentSession`) in `app/ai/workspace_assistant.py` — SSE-only (no DB persistence), auto-allows all control requests
- **Manager**: `WorkspaceAssistantManager` — per-user sessions with own semaphore (MAX_CONCURRENT=2), separate from task agent concurrency
- **SSE events**: `ws_assistant_message`, `ws_assistant_done` (independent of task events)
- **API**: `POST /api/workspace/assistant/message`, `POST /stop`, `GET /status` in `app/routers/workspace_assistant.py`
- **System prompt**: `app/prompts/workspace_assistant.md` with `{stores_list}` placeholder injected at runtime
- **Frontend**: `WorkspaceAssistantView.tsx` — default view when entering Workspace; file editor shown when a file is selected; "+ Tell AI" button returns to chat

## Knowledge System

3-level knowledge hierarchy:

### L1: Builtin Knowledge (repo-synced)

- Source: `app/knowledge/` in package → synced to `~/.vibe-seller/knowledge/project/`
- Content: Universal rules (Amazon URLs, Ziniao behavior, skill environment)
- Read-only: Only maintainer commits change L1
- Sync: `knowledge_sync.fetch()` on startup, remote check async on task start
- Catalog: `knowledge/project/CATALOG.md` (maintainer-committed, generated via `generate_catalog.sh`)

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

The agent prompt (`design_system.md`) treats Relevance as a contract, not a hint:

> For every platform the task touches, the agent MUST read every catalog row whose Relevance column contains that platform before opening any URL on that platform. Empty-Relevance rows are read by judgment.

This is the design fix for "agent cherry-picked the catalog and skipped `noon-sites.md`" — Relevance turns mandatory reads into a deterministic rule. The L2/L3 sync prompts (`CATALOG_DESC_L2`/`CATALOG_DESC_L3` in `app/prompts/__init__.py`) preserve the column when copying rows from L1 → L2 → L3. Earlier versions dropped Relevance at L2 generation; that broke the contract.

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
- Remote sync: Fetches from `KNOWLEDGE_REPO_URL` (GitHub raw) when commit changes & >24h cooldown
- Manual sync: `POST /api/workspace/knowledge/sync` (remote only — local sync happens at startup)
- Sync metadata: `GET /api/workspace/knowledge/sync-meta`
- Files tracked via `MANIFEST.txt` in `app/knowledge/`
- `get_structured()` filters `__pycache__` from knowledge listings (both project and local)

### Agent Knowledge Writing (Reflection)

Agents update knowledge after tasks via `app/prompts/reflection.md`, delivered as a Stop hook block reason post-execution:

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
tables). The boundary is enforced by the filename:

| Use | Naming | Examples | Path |
|---|---|---|---|
| **Knowledge** (lives at L3) | lowercase, topic-named, no dates | `notes.md`, `browser-tips.md`, `fbn-quirks.md`, `cases-process.md` | `stores/<slug>/<topic>.md` |
| **Per-run output** (does NOT live at L3) | ALL_CAPS, dated, report-y | `*_PLAN_*.md`, `*_REPORT_*.md`, `*_AUDIT_*.md`, `*_2026-04-29.md` | task CWD (`~/.vibe-seller/tasks/<task-id>/<filename>.md`) |

The L3 catalog regen agent (`CATALOG_DESC_L3` in `app/prompts/__init__.py`)
applies this discriminator: it scans every file under `stores/<slug>/`,
indexes lowercase topic-named files as knowledge, and skips ALL_CAPS /
dated filenames as leaked task output. This is defense-in-depth — the
in-task system prompt (`app/task_runner_context.py`) directs report
writes to the task CWD, but if a leak ever lands at L3 anyway, the
catalog won't promote it to "first-class store knowledge".

## Skills System

Reusable agent procedures with scripts, bundled in `app/skills/` and synced to `~/.vibe-seller/.claude/skills/`. Skills must be direct children of `skills/` for Claude Code auto-discovery.

- **Three-tier sync**: local package → workspace `skills/`, remote GitHub via `MANIFEST.txt`, on-demand with 24h cooldown
- **User skills**: created via UI at `~/.vibe-seller/.claude/skills/{slug}/` (same directory as built-in, distinguished by `.sync_meta.json`)
- **Dep auto-install**: skill `requirements.txt` deps are auto-installed into the shared workspace venv (`~/.vibe-seller/.venv/`) during sync
- **Source tracking**: `get_structured()` returns `source: 'builtin' | 'imported' | 'custom'`. Synced skill names tracked in `.sync_meta.json`; imported skills tracked in `skills.lock.json`
- **Reserved slugs**: names starting with `_` are rejected by `create_skill()`
- **API**: `POST /api/workspace/skills/sync`, `GET /api/workspace/skills/sync-meta`, `POST /api/workspace/skill`
- **Frontend**: skills panel in Workspace sidebar with sync button, builtin badges, and skill creation form

## Run Data (`store-data/`)

Per-store **run artifacts** (reports, captures, exports) live outside the
knowledge tree so they never surface as knowledge (catalog injection, the
UI knowledge tab, reflection writes):

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

**`prepare_task_workspace(task_id, *, store_id=None, clean=False)`** in `app/workspace/manager.py`:

1. Creates `~/.vibe-seller/tasks/{task_id}/`
2. Symlinks shared resources into the task directory:
   - `.claude` — copied from `~/.vibe-seller/.claude` (skills, settings; excludes `.venv`, `__pycache__`). When `store_id is None` (non-store/orchestrator task), browser-only skills like `browser-use` are excluded from the copy.
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
| `vibe_seller_list_wecom_bots` | List configured WeChat Work bots as `[{id, name}]`. Webhook URLs are stripped so no secrets reach the LLM |
| `vibe_seller_send_wecom_message` | Post to a WeCom group via `{bot_id, content, msgtype?}`. `msgtype` accepts `text` (default) or `markdown`; WeCom markdown is a reduced subset (no tables/images) with a 4 KB per-message hard limit. Proxies `POST /api/wecom-bots/{id}/send` |

### Adding a Built-in Skill

1. Create `app/skills/my-skill/SKILL.md` with YAML frontmatter (`name`, `description`)
2. Add implementation files (scripts, `requirements.txt`)
3. Update `app/skills/MANIFEST.txt` with relative paths to all files
4. Sync copies files to `~/.vibe-seller/.claude/skills/my-skill/`

## Optional Integration Bundles

Some third-party SDKs (e.g., the Google Workspace `gws` CLI) ship their own large pool of SKILL.md files. We do **not** vendor them into `app/skills/` — that would bloat every agent's skill index and pull in externally-copyrighted content. Instead, such bundles are opt-in integrations gated by an admin toggle in Settings → Integrations.

### Google Workspace (`gws`)

Implementation: `app/workspace/gws_integration.py`. Endpoints: `GET /api/settings/google-workspace/status`, `POST /api/settings/google-workspace/{enable,disable}` (admin only).

**Prereqs** (checked by `check_status()`):

- `gws` binary on `$PATH` (from `npm install -g @googleworkspace/cli` or a release binary).
- `gws auth status` returns 0 — user ran `gws auth login` or set `GOOGLE_APPLICATION_CREDENTIALS`.

**Install flow** (`install_skills()`):

1. Run `gws generate-skills --output-dir <tmpdir>` to produce the upstream skill folders.
2. Apply the allowlist `GWS_SUBSET` (19 Amazon-seller-focused entries covering Sheets, Drive, Gmail, Docs, Calendar + helpers). Everything else (persona-\*, recipe-\*, chat/meet/keep/etc.) is discarded.
3. Fold each `gws-<name>/` into a sub-dir `gws/<name>/` under a single **umbrella skill** at `~/.vibe-seller/.claude/skills/gws/`. Rewrite all `../gws-<y>/SKILL.md` cross-refs to `../<y>/SKILL.md` so sibling paths still resolve under the new hierarchy.
4. Generate `gws/SKILL.md` — the umbrella — with Claude Code frontmatter and an inline catalog pointing at each sub-SKILL.md.
5. Atomically swap into place.

**Why umbrella layout**: Claude Code auto-indexes only direct children of `.claude/skills/`. The flat alternative (19 sibling `gws-*/` folders) would add 19 entries (~350 always-on tokens) to every agent context. The umbrella reduces this to one entry (~20 tokens); the agent reads `gws/SKILL.md` on invoke and opens individual sub-SKILL.md files via the Read tool as needed.

**Uninstall** (`uninstall_skills()`): `shutil.rmtree` on `.claude/skills/gws/`. Idempotent. Sibling skills (browser-use, amazon-invoice, user-created) are untouched.

**Sync safety**: `skills_sync.fetch()` only manages dirs that have a matching source in `app/skills/` — `gws/` has no package source, so startup sync never deletes it. The `skills_sync.is_gws_installed()` flag (in `sync_meta.json`) is set by the enable endpoint as a hint for future extensions.
