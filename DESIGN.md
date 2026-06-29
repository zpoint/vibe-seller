# Vibe-Seller: Team Collaboration Platform for E-Commerce Store Automation

## Context

The user runs multiple e-commerce stores (Amazon, Noon, etc.) and currently uses browser automation via Claude Code skills + ziniao anti-detect browser. The problem: it's too specific and not "AI native." The user wants a vibe-kanban-style collaboration platform where team members create and manage browser-automation tasks organized by store, with intelligent scheduling, template-driven execution, real-time feedback, and hierarchical task decomposition.

**Goal**: Build a new project `vibe-seller` that combines team collaboration UX with browser automation capabilities.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                    React + TS Frontend                   │
│  (Vite, Tailwind CSS 4, react-i18next, react-markdown)  │
└────────────────────────┬────────────────────────────────┘
                         │ REST + SSE
┌────────────────────────┴────────────────────────────────┐
│              Python Backend (FastAPI + asyncio)           │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐ ┌───────────┐ │
│  │ API/SSE  │ │ Scheduler│ │  Browser  │ │  Task     │ │
│  │ Routes   │ │ (queue)  │ │  Manager  │ │ Executor  │ │
│  └──────────┘ └──────────┘ └───────────┘ └───────────┘ │
│  ┌──────────┐ ┌──────────┐ ┌───────────┐               │
│  │ Template │ │ Knowledge│ │ Credential│               │
│  │ Engine   │ │ Store    │ │ Vault     │               │
│  └──────────┘ └──────────┘ └───────────┘               │
└────────────────────────┬────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          │              │              │
    ┌─────┴─────┐  ┌────┴────┐  ┌─────┴─────┐
    │  SQLite   │  │ Browser │  │browser-use│
    │(SQLAlchemy│  │ Backend │  │   CLI +   │
    │  + alembic│  │(pluggable│ │  Chromium │
    │          )│  │ ziniao/ │  │           │
    │           │  │ chrome) │  │           │
    └───────────┘  └─────────┘  └───────────┘
```

---

## AI Agent Architecture

### Design Trade-off: CLI Wrapper vs Own Runtime vs Direct API

| Approach | How it works | Pros | Cons |
|----------|-------------|------|------|
| **CLI Wrapper** (MVP) | Wraps CLI tools (claude/gemini) behind `AIAgentBackend` ABC. Spawns `claude -p` subprocess with `--output-format stream-json`. | Simple, leverages existing CLI tool capabilities, multi-provider support | Depends on CLI being installed, limited control over memory/tool loading |
| **Own Agent Runtime** (future) | Builds own agent framework. Calls LLM APIs directly. Implements tool-calling loop, session memory, streaming. | Full control, unified memory/tool loading, provider-swappable | Complex to build, must maintain tool ecosystem |
| **Direct API Calls** (legacy) | Calls `anthropic.Anthropic().messages.create()` directly | Simplest | No context, no memory, no tools, not extensible |

### MVP Decision: CLI Wrapper → Long-term: Own Runtime

**MVP (now)**: `AIAgentBackend` ABC with `ClaudeCodeBackend` impl that spawns `claude -p`. Simple, works today.

**Long-term goal**: Own agent runtime with unified memory loading, tool registration, multi-provider support.

**Why not stay with CLI wrapper forever?**
- CLI tools load their own memory/context — we can't fully control what the agent knows
- Each CLI has different protocols and capabilities — normalization is fragile
- Own runtime lets us implement custom tool calling natively

### Key Principle: Always Use Agent

ALL LLM interactions route through the agent system, never call APIs directly. This ensures:
- Agent loads workspace memory (store profiles, knowledge, skills) before every interaction
- Event extraction understands user's store context and naming conventions
- Consistent behavior across all LLM-powered features

### Agent Abstraction

```
AIAgentBackend (ABC)              app/ai/base.py
    └── ClaudeCodeBackend          app/ai/claude_backend.py
         ├── run(task_id, prompt, mode)
         ├── stop(task_id)
         ├── submit_answer(task_id, request_id, answers)
         ├── send_message(task_id, message)
         └── is_running(task_id)
```

### Task Execution Flow (Auto Plan+Execute)

```
User creates task
  → POST /api/tasks → status: "pending"
  → Backend auto-starts via _auto_run_task() (no queuing on creation):
    1. BrowserManager.write_browser_config_for_store() — generate browser-use wrapper (no browser start)
    2. knowledge_sync.fetch()             — sync project knowledge
    3. agent.run(mode="plan_then_execute") — single session:
       a. Agent starts with --permission-mode plan (read-only tools)
       b. Agent plans → calls ExitPlanMode → status: "designing"
       c. Backend captures plan → task.plan → status: "planned"
       d. If interactive (not scheduled): wait for user approve/reject
       e. Approve sends SetMode → bypassPermissions → status: "running"
       f. Agent continues executing in same session with full access
    4. Done → status: "completed" or "failed"
```

No manual Start Agent / Design / Execute Plan buttons -- tasks auto-plan and auto-execute on creation.
Tasks auto-start via `_auto_run_task()` on creation. Retries and schedules use `TaskQueueScheduler` which gates on platform/country compatibility (same platform + different country → queued; otherwise concurrent via CDPMuxProxy).

### Knowledge System (Two-Layer)

```
PACKAGE (app/knowledge/)                 LOCAL (~/.vibe-seller/)
  common/ziniao-browser.md                 knowledge/
  README.md                                  project/  ← synced from package + remote
  MANIFEST.txt                               (root)    ← agent-generated
                                           stores/    ← per-store (local)
```

Local package knowledge is synced before every agent run. Remote sync (from GitHub) runs async on task start when commit changes and >24h cooldown. Local knowledge is generated by agents during task execution.

### Skills System (Reusable Agent Procedures)

```
PACKAGE (app/skills/)                    LOCAL (~/.vibe-seller/)
  MANIFEST.txt                             .claude/skills/
  amazon-invoice/SKILL.md                    amazon-invoice/  ← synced from package + remote
  amazon-invoice/generate_invoice.py           SKILL.md
  amazon-invoice/requirements.txt              generate_invoice.py
                                               requirements.txt
                                             my-custom-skill/  ← user-created, never synced
```

Skills are reusable procedures that agents load automatically. Three-tier sync mirrors the knowledge system: local package sync via `importlib.resources`, remote GitHub sync via `MANIFEST.txt` with 24h cooldown, and on-demand sync before tasks.

**Optional integration bundles** skip this sync pipeline entirely. The Google Workspace bundle is installed out-of-band at runtime:

```
(gws binary on $PATH) → gws generate-skills → gws_integration.install_skills()
                                                  ↓
                                             post-process: filter to 19-skill
                                             allowlist (GWS_SUBSET), rewrite
                                             cross-refs, write umbrella SKILL.md
                                                  ↓
                                             .claude/skills/gws/  (single entry)
```

The toggle lives in Settings → Integrations; `skills_sync.fetch()` never touches the `gws/` folder because it has no package source under `app/skills/`. See `app/workspace/gws_integration.py`.

Key design decisions:
- **Shared venv**: skill `requirements.txt` deps are auto-installed into `~/.vibe-seller/.venv/` during sync — no per-skill `.venv/`
- **Source tracking**: `get_structured()` returns `source: 'builtin' | 'imported' | 'custom'`; synced skills tracked in `.sync_meta.json`, imported in `skills.lock.json`
- **Reserved slugs**: names starting with `_` are rejected by `create_skill()`

### Fan-Out Architecture (All-Stores Scheduling)

```
All-stores schedule fires (APScheduler)
  → _run_fanout_job(schedule_id)
  → Generate batch_id (UUID)
  → For each active store:
      → Create Task(store_id, batch_id)
      → Submit to TaskQueueScheduler (concurrent by default)
  → Emit SSE 'fanout_triggered'
```

Design decision: independent tasks per store (not mega-tasks). Each task goes through normal plan_then_execute pipeline. The existing per-store task queue handles concurrency and browser session reuse. The `batch_id` links all tasks from the same trigger for grouping in the frontend.

Data model:
- Schedules with `store_id=NULL` are all-stores (fan-out) schedules
- `Task.batch_id` (VARCHAR, nullable, indexed) — groups tasks from same fan-out trigger

### Channel → Event Flow (Agent-Based Extraction)

```
Channel.poll() → messages → agent.run(mode="extract") → structured events
```

Event extraction routes through the agent backend instead of direct API calls, ensuring the agent has full workspace context.

---

## Tech Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Backend** | **Python 3.11+ (FastAPI + asyncio)** | User is familiar with Python; proven stack for browser automation |
| **Database** | SQLite + SQLAlchemy 2.0 + Alembic | Local-first, no external DB dependency |
| **Frontend** | React 19 + TS + Vite 7 | Modern SPA |
| **Styling** | Tailwind CSS 4 | Utility-first CSS |
| **i18n** | react-i18next | Bilingual support (EN/ZH) |
| **Real-time** | SSE (sse-starlette) | Log streaming, step status, screenshots |
| **Browser** | Pluggable: Ziniao (default) / Chrome / custom | Configurable per store |
| **Async** | asyncio + uvicorn | FastAPI native |
| **Validation** | Pydantic v2 | API types + config validation |

---

## Data Model

### Core Entities

```sql
-- Users (team members)
CREATE TABLE users (
    id              TEXT PRIMARY KEY,       -- UUID
    name            TEXT NOT NULL,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,          -- bcrypt hashed
    avatar_url      TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Stores (e-commerce store = browser profile)
CREATE TABLE stores (
    id              TEXT PRIMARY KEY,       -- UUID
    name            TEXT NOT NULL,          -- "US-Store1", "UK-Store2"
    browser_backend TEXT NOT NULL DEFAULT 'ziniao',  -- ziniao | chrome | custom
    browser_config  TEXT NOT NULL,          -- JSON: backend-specific config (see below)
    platforms       TEXT NOT NULL,          -- JSON: ["amazon", "noon"]
    countries       TEXT NOT NULL,          -- JSON: ["US", "UK", "MX"]
    config          TEXT,                   -- JSON: store-specific settings
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
-- browser_config examples:
--   ziniao:  credentials stored in ziniao_accounts table, linked via ziniao_account_id + browser_oauth
--   chrome:  {"user_data_dir": "/path/to/profile", "executable_path": "/usr/bin/google-chrome"}
--   custom:  {"cdp_endpoint": "http://localhost:9222"}

-- Store credentials (platform login credentials per store)
CREATE TABLE store_credentials (
    id              TEXT PRIMARY KEY,       -- UUID
    store_id        TEXT NOT NULL,
    platform        TEXT NOT NULL,          -- amazon | noon
    country         TEXT,                   -- US | UK | MX | null (all countries)
    username        TEXT,                   -- login username/email (encrypted)
    password        TEXT,                   -- login password (encrypted)
    otp_secret      TEXT,                   -- TOTP secret for 2FA (encrypted)
    extra           TEXT,                   -- JSON: additional auth data (encrypted)
    notes           TEXT,                   -- human-readable notes
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(store_id, platform, country),
    FOREIGN KEY (store_id) REFERENCES stores(id)
);

-- Browser sessions (one per store, managed by system)
CREATE TABLE browser_sessions (
    id              TEXT PRIMARY KEY,       -- UUID
    store_id        TEXT NOT NULL UNIQUE,
    cdp_port        INTEGER,               -- dynamic port from browser
    proxy_port      INTEGER,               -- fixed proxy port for this store
    proxy_pid       INTEGER,               -- CDP proxy process ID
    status          TEXT NOT NULL DEFAULT 'idle',  -- idle | occupied | starting | stopping | error
    current_platform TEXT,                  -- amazon | noon | null
    current_country  TEXT,                  -- US | UK | MX | null
    current_url      TEXT,                  -- last known browser URL
    active_tab_count INTEGER DEFAULT 0,    -- number of active tabs
    started_at      TEXT,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (store_id) REFERENCES stores(id)
);

-- Task templates (predefined step sequences)
CREATE TABLE task_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,          -- "Create Listing", "Ship Inventory"
    description     TEXT,
    platform        TEXT,                   -- amazon | noon | null (any)
    category        TEXT NOT NULL,          -- listing | shipment | pricing | inventory
    steps           TEXT NOT NULL,          -- JSON: ordered step definitions
    is_composite    BOOLEAN DEFAULT 0,      -- true = spawns sub-tasks
    decompose_rule  TEXT,                   -- JSON: how to decompose (e.g., by store)
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Tasks (unit of work, hierarchical)
CREATE TABLE tasks (
    id              TEXT PRIMARY KEY,
    parent_task_id  TEXT,                  -- null = top-level task
    store_id        TEXT,                  -- which store (null for composite parents)
    template_id     TEXT,                  -- which template was used
    created_by      TEXT NOT NULL,         -- user who created it
    assigned_to     TEXT,                  -- user/agent assigned
    title           TEXT NOT NULL,
    description     TEXT,
    platform        TEXT,                  -- amazon | noon
    country         TEXT,                  -- US | UK | MX
    status          TEXT NOT NULL DEFAULT 'pending',
        -- pending | queued | designing | planned | running | waiting | completed | failed
    priority        INTEGER DEFAULT 0,     -- higher = more urgent
    input_data      TEXT,                  -- JSON: task-specific parameters
    plan            TEXT,                  -- JSON: generated execution plan
    result          TEXT,                  -- JSON: execution result
    error           TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (parent_task_id) REFERENCES tasks(id),
    FOREIGN KEY (store_id) REFERENCES stores(id),
    FOREIGN KEY (template_id) REFERENCES task_templates(id),
    FOREIGN KEY (created_by) REFERENCES users(id)
);

-- Task steps (individual steps within a task execution)
CREATE TABLE task_steps (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    step_index      INTEGER NOT NULL,      -- execution order
    name            TEXT NOT NULL,          -- "Navigate to inventory"
    description     TEXT,
    action_type     TEXT NOT NULL,          -- navigate | click | type | wait | screenshot | verify | custom
    action_data     TEXT,                  -- JSON: action parameters
    status          TEXT NOT NULL DEFAULT 'pending',
        -- pending | running | completed | failed | skipped
    screenshot_id   TEXT,
    result          TEXT,                  -- JSON: step result
    error           TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

-- Screenshots (browser state captures)
CREATE TABLE screenshots (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    step_id         TEXT,
    file_path       TEXT NOT NULL,         -- path to PNG on disk (not blob in DB)
    thumbnail_path  TEXT,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

-- Task logs (execution output stream)
CREATE TABLE task_logs (
    id              TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
    log_type        TEXT NOT NULL,          -- info | warn | error | agent | browser | system
    content         TEXT NOT NULL,
    timestamp_ms    INTEGER NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

-- Knowledge files (learned browser procedures)
CREATE TABLE knowledge (
    id              TEXT PRIMARY KEY,
    platform        TEXT NOT NULL,          -- amazon | noon
    category        TEXT NOT NULL,          -- e.g., "inventory_management"
    file_type       TEXT NOT NULL,          -- page | locators | procedure | site_map | common_patterns
    file_name       TEXT NOT NULL,
    content         TEXT NOT NULL,          -- markdown content
    verified_at     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    UNIQUE(platform, category, file_type, file_name)
);

-- System config (key-value settings)
CREATE TABLE system_config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,          -- JSON value
    updated_at      TEXT NOT NULL
);
-- Keys: "encryption_key", "default_browser_backend", etc.
-- Note: Ziniao credentials are stored in ziniao_accounts table, not here.

-- Schedules (recurring task definitions)
CREATE TABLE schedules (
    id              TEXT PRIMARY KEY,
    store_id        TEXT,                  -- FK → stores.id
    title           TEXT NOT NULL,
    description     TEXT,
    platform        TEXT,                  -- Captured during first interactive run
    country         TEXT,                  -- Captured during first interactive run
    plan            TEXT,                  -- Saved plan from first run, reused by all recurring executions
    schedule_type   TEXT NOT NULL,         -- 'daily' | 'weekly' | 'monthly'
    schedule_time   TEXT NOT NULL,         -- 'HH:MM' or 'HH:MM:SS'
    schedule_day    INTEGER,              -- 0-6 for weekly (Mon=0), 1-31 for monthly
    timezone        TEXT NOT NULL,          -- IANA name; router resolves unset → AppSettings['default_schedule_timezone'] → server local
    is_active       BOOLEAN DEFAULT 1,
    plan_mode       BOOLEAN DEFAULT 0,
    ai_profile_id   TEXT DEFAULT 'default',
    created_by      TEXT NOT NULL,         -- FK → users.id
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
-- tasks.schedule_id FK → schedules.id (nullable, ondelete SET NULL)
-- APScheduler uses MemoryJobStore; Schedule table is the sole source of truth.
-- Jobs rebuilt from DB on every startup via rebuild_schedule_jobs().

-- Email accounts (IMAP connections with encrypted passwords)
CREATE TABLE email_accounts (
    id                  TEXT PRIMARY KEY,
    email               TEXT NOT NULL UNIQUE,
    encrypted_password  TEXT NOT NULL,     -- Fernet-encrypted, never exposed in API
    imap_host           TEXT NOT NULL,
    imap_port           INTEGER DEFAULT 993,
    use_ssl             BOOLEAN DEFAULT 1,
    created_by          TEXT NOT NULL,     -- FK → users.id
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);
-- Password encryption: Fernet with key derived from JWT_SECRET via SHA-256.
-- IMAP auto-discovery: hardcoded table for top providers (163, Gmail, QQ, Outlook, Yahoo, etc.)
-- with heuristic fallback (imap.{domain}:993).

-- Store-email links (many-to-many with per-link watermark tracking)
CREATE TABLE store_email_links (
    id                  TEXT PRIMARY KEY,
    store_id            TEXT NOT NULL,     -- FK → stores.id
    email_account_id    TEXT NOT NULL,     -- FK → email_accounts.id
    watermark_date      TEXT,             -- ISO datetime, last successful poll boundary
    last_polled_at      TEXT,             -- When poll() was last called
    seen_message_ids    TEXT,             -- JSON array of Message-IDs for dedup
    created_at          TEXT NOT NULL,
    UNIQUE(store_id, email_account_id)
);
-- Poll endpoint: POST /api/stores/{id}/emails/poll
-- 60s rate limit per link, per-link asyncio.Lock, watermark advances on success.
-- Email context auto-injected into agent prompts for stores with linked emails.
```

---

## Pluggable Browser Backend

The system supports multiple browser backends via a common interface:

```python
# app/browser/base.py
class BrowserBackend(ABC):
    """Abstract browser backend - all backends implement this."""

    @abstractmethod
    async def start(self, browser_config: dict) -> BrowserSessionInfo:
        """Start browser, return session info (cdp_port, etc)."""
        ...

    @abstractmethod
    async def stop(self, info: BrowserSessionInfo) -> None:
        """Stop browser."""
        ...

# app/browser/ziniao.py
class ZiniaoBackend(BrowserBackend):
    """Ziniao anti-detect browser."""
    # One instance per store (not shared)
    # Uses HTTP API on configurable socket_port (default 16851)
    # Each startBrowser call returns a unique debuggingPort
    # CDPProxy relays from stable proxy_port → dynamic debuggingPort
    # On WSL, proxy target is Windows gateway IP (not 127.0.0.1)

# app/browser/chrome.py
class ChromeBackend(BrowserBackend):
    """Standard Chrome via browser-use CLI."""
    # No dedicated backend — Chrome stores use browser-use CLI directly
    # with --session flag for persistent profile isolation
    # Persistent profiles: managed by browser-use daemon per session
    #   - Cookies, localStorage, login sessions survive restarts
    # Bookmarks: read_bookmarks(slug) parses Chrome Bookmarks JSON
    #   - Auto-injected into agent context for stores with sparse knowledge
```

Each store has a `browser_backend` field + `browser_config` JSON. The BrowserManager creates a **per-store backend instance** (keyed by store_id, not backend_type).

---

## Credential Storage

> Partially implemented — email passwords encrypted via Fernet (`app/utils/crypto.py`). Full credential vault for platform login credentials is planned.

### Design: Encrypted at Rest, Available to Agents

```python
# app/credentials/vault.py
class CredentialVault:
    """Encrypted credential storage using Fernet (symmetric encryption)."""

    def __init__(self, encryption_key: bytes):
        self.fernet = Fernet(encryption_key)

    def encrypt(self, plaintext: str) -> str:
        return self.fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self.fernet.decrypt(ciphertext.encode()).decode()
```

**How credentials are used**:
1. User adds credentials via UI (Settings → Store → Credentials)
2. Stored encrypted in `store_credentials` table
3. When a task encounters a login page, the executor:
   - Queries credentials for that store + platform + country
   - Decrypts in memory
   - For **ziniao**: ziniao has its own auto-fill (credentials stored in ziniao profile), so these are backup/fallback
   - For **chrome**: agent types username/password via browser-use CLI
   - For **2FA/OTP**: if `otp_secret` is set, generates TOTP code and fills it

**Encryption key management**:
- On first run, a random Fernet key is generated and stored in `system_config` table
- Alternatively, user can provide via environment variable `VIBE_SELLER_ENCRYPTION_KEY`
- The key encrypts/decrypts all credential fields

---

## Concurrency Model

### Key Insight: Multiple Stores Can Run in Parallel

Each store = separate browser instance with its own CDP port and proxy. We run **one CDP proxy per store**:

```
Store1 (ziniao, profile A) ──→ ziniao :debugPort1 ──→ CDPProxy :9222
Store2 (ziniao, profile B) ──→ ziniao :debugPort2 ──→ CDPProxy :9223
Store3 (chrome)            ──→ browser-use CLI (--session, no proxy needed)
```

### Per-Store Task Queuing

Multiple tasks for the **same store** run concurrently via CDPMuxProxy, except when tasks share the same platform but target different countries (i.e. the same platform in two different country marketplaces) — those queue because Ziniao needs to switch country. Different platforms (e.g. Amazon + Noon) always run concurrently. Tasks for different stores always run concurrently.

### Ziniao Guard (Account Conflict Detection)

Only one Ziniao account can be active per machine (one Ziniao process). The `BrowserManager` tracks the active `ziniao_account_id`. If store B uses a different Ziniao account than the already-active store A, `start_session()` raises a `RuntimeError` naming the conflicting stores. Same account + different profiles = OK. Chrome stores have no such account restriction (but still use CDPMuxProxy for shared browser access).

### Per-Store Browser-Use Wrapper Isolation

Each store gets a dedicated wrapper script at `~/.vibe-seller/bin/{slug}/browser-use` that enforces session isolation. The wrapper:
- Validates `--session` against allowed sessions for the store
- Blocks `--cdp-url` and `--mcp` flags (prevents escaping isolation)
- Injects store-specific `--session` and (for Ziniao) `--cdp-url` flags
- Auto-starts the Ziniao CDP proxy if needed

Ziniao stores get **dual-session** support — a main session (`{slug}`) for seller center and an auxiliary session (`{slug}-aux`) for non-seller-center URLs:

```bash
# Main session (Ziniao — seller center, routed via CDP proxy)
~/.vibe-seller/bin/test-store/browser-use --session test-store open https://seller.example.com

# Aux session (Chrome — everything else, no CDP proxy)
~/.vibe-seller/bin/test-store/browser-use --session test-store-aux open https://google.com
```

Agent routing (seller center → Ziniao session, everything else → Chrome aux session) is driven by `app/prompts/dual_browser.md`. Chrome-only stores get a single session.

Agent sessions block raw `browser-use` and `mcp__playwright__*` via `--disallowedTools`, and can only use the store-specific wrapper in `~/.vibe-seller/bin/{slug}/`.

### Implementation

Two scheduling paths:
1. **`_auto_run_task()`** (primary) — Per-store concurrent scheduling of plan_then_execute sessions
2. **`TaskQueueScheduler`** (for browser-direct tasks) — Concurrent by default, queued only for platform/country conflicts

---

## Task Execution Flow

### 1. User Creates Task
```
User → selects store + template (or freeform) → provides parameters → Create
```

### 2. Planning Phase
```
System → loads template steps → adapts with input_data → loads knowledge files
       → generates execution plan (list of TaskSteps) → status: "designing"
```

### 3. Queue Phase
```
Scheduler → checks browser session availability → if available, claim → status: "running"
          → if platform/country conflict, status: "queued" → wait
```

### 4. Execution Phase (step by step)
```
For each step in plan:
  1. Update step status → "running", stream via SSE
  2. Execute browser action (via browser-use CLI)
  3. Take screenshot → save to disk, store path in DB
  4. Stream step status + screenshot path via SSE
  5. Verify step result
  6. If login page detected → fetch credentials from vault → auto-login → retry
  7. Update step status → "completed" or "failed"
  8. If failed → pause, allow user intervention or auto-retry
```

### 5. Completion
```
Release browser lock → update task status → notify via SSE
```

### Composite Task Flow (Top-Level Decomposition)
```
User: "Ship items from 5 stores"
  → System loads "Shipment" template (is_composite=true)
  → Reads decompose_rule: group by store
  → Creates 5 child tasks, one per store
  → Each child task enters the normal flow independently
  → Parent task tracks aggregate progress
```

---

## Project Structure

```
vibe-seller/
├── app/                          # Python FastAPI backend
│   ├── main.py                   # FastAPI app, router registration, lifespan
│   ├── config.py                 # Paths, DB URL, defaults
│   ├── database.py               # SQLAlchemy async engine + session
│   ├── auth.py                   # JWT cookie auth (get_current_user)
│   ├── ai/                       # AI agent abstraction
│   │   ├── base.py               # AIAgentBackend ABC
│   │   └── claude_backend.py     # Claude Code CLI subprocess impl
│   ├── browser/                  # Pluggable browser backends
│   │   ├── base.py               # Abstract BrowserBackend + BrowserSessionInfo
│   │   ├── ziniao.py             # Ziniao anti-detect browser
│   │   ├── ziniao_utils.py       # Ziniao HTTP API + auto-launch + WSL support
│   │   ├── cdp_proxy.py          # Async TCP relay for CDP (stable proxy port)
│   │   └── manager.py            # Per-store orchestration + Ziniao guard + wrapper scripts
│   ├── executor/                 # Legacy browser task runner (deprecated)
│   │   └── runner.py             # Stub — browser automation now via browser-use CLI
│   ├── scheduler/                # Task scheduling
│   │   ├── cron.py               # APScheduler cron jobs
│   │   └── task_queue.py         # Per-store concurrent task scheduler
│   ├── skills/                   # Bundled skills (synced to workspace)
│   │   ├── MANIFEST.txt          # File list for remote sync
│   │   └── amazon-invoice/       # Example skill (invoice generation)
│   ├── workspace/                # Workspace management (~/.vibe-seller/)
│   │   ├── manager.py            # Init workspace, venv, store profiles
│   │   ├── knowledge_sync.py     # Sync knowledge/ → workspace
│   │   ├── skills_sync.py        # Sync skills/ → workspace
│   │   └── gws_integration.py    # Optional Google Workspace bundle (opt-in)
│   ├── channels/                 # Message channel integrations
│   ├── events_system/            # Business event extraction + sync
│   ├── events/                   # SSE event bus
│   │   └── bus.py                # asyncio broadcast EventBus
│   ├── models/                   # SQLAlchemy ORM models
│   ├── routers/                  # FastAPI API routes
│   └── schemas/                  # Pydantic request/response schemas
│
├── frontend/                     # React 19 + TypeScript SPA
│   └── src/
│       ├── App.tsx               # Single-file UI (MVP)
│       └── i18n/locales/         # EN/ZH translations
│
├── knowledge -> app/knowledge     # Symlink to project knowledge (in app/ for pip)
├── tests/                        # pytest + vitest test suites
├── data/                         # Runtime data (gitignored)
├── DESIGN.md                     # This file
├── CLAUDE.md                     # Claude Code development guide
└── pyproject.toml                # Python project metadata + ruff config
```

---

## Key Components Detail

### 1. Browser Manager (`app/browser/manager.py`)

Orchestrates per-store browser sessions with pluggable backends, Ziniao guard, and browser-use CLI wrapper generation:

```python
class BrowserManager:
    """Manages browser sessions for all stores."""

    def __init__(self):
        self._backends: dict[str, BrowserBackend] = {}    # store_id -> backend instance
        self._active_sessions: dict[str, BrowserSessionInfo] = {}
        self._proxy_ports: dict[str, int] = {}            # store_id -> proxy_port
        self._active_ziniao_account_id: str | None = None  # Ziniao guard
        self._ziniao_stores: dict[str, str] = {}           # store_id -> store_name
        self._lock = asyncio.Lock()

    async def start_session(self, store, db) -> BrowserSession:
        # Ziniao guard: reject different account
        # Create per-store backend instance
        # Allocate unique proxy port
        # Start backend → CDPProxy → generate browser-use wrapper

    async def stop_session(self, store, db) -> None:
        # Stop backend, clean up proxy port
        # Clear Ziniao account tracking if last store
        # Remove browser-use wrapper

    async def ensure_session(self, store, db) -> BrowserSession:
        # Start or reuse, always regenerate browser-use wrapper

    async def write_mcp_config(self, store, db) -> None:
        # Generate browser-use wrapper WITHOUT starting browser
```

### 2. Task Executor (Legacy — `app/executor/runner.py`)

> **Deprecated**: The legacy executor used Playwright directly. Browser automation is now handled by the AI agent via browser-use CLI wrapper scripts. The executor module is a stub that raises `NotImplementedError`.

### 3. Frontend Task Step Timeline

The main differentiating UI component:

```
┌─────────────────────────────────────────────┐
│ Task: Create Listing for SKU-001            │
│ Store: US-Store1 | Platform: Amazon         │
│ Status: Running (Step 3/7)                  │
├─────────────────────────────────────────────┤
│                                             │
│  ✅ Step 1: Navigate to Inventory           │
│     [screenshot thumbnail]                  │
│                                             │
│  ✅ Step 2: Search for SKU                  │
│     [screenshot thumbnail]                  │
│                                             │
│  🔄 Step 3: Click "Add a Product"           │
│     [live screenshot]                       │
│                                             │
│  ⬚ Step 4: Fill Product Title               │
│  ⬚ Step 5: Fill Description                 │
│  ⬚ Step 6: Set Price                        │
│  ⬚ Step 7: Submit & Verify                  │
│                                             │
├─────────────────────────────────────────────┤
│ Logs:                                       │
│ [12:01:05] Navigating to inventory page...  │
│ [12:01:08] Page loaded, searching SKU...    │
│ [12:01:12] Found SKU, clicking add product  │
└─────────────────────────────────────────────┘
```

---

## API Endpoints

```
# Auth
POST   /api/auth/login              # Email + password login → sets httpOnly JWT cookie
POST   /api/auth/logout             # Clear auth cookie
GET    /api/auth/me                 # Current user info

# Users
GET    /api/users                   # List users (admin)
POST   /api/users                   # Create user (admin)

# Stores
GET    /api/stores                  # List stores
POST   /api/stores                  # Create store
GET    /api/stores/:id              # Store detail
DELETE /api/stores/:id              # Delete store (stops browser, removes wrapper)

# Ziniao Accounts
GET    /api/ziniao-accounts         # List accounts
POST   /api/ziniao-accounts         # Create account
GET    /api/ziniao-accounts/:id/browsers  # List browser profiles
GET    /api/ziniao/launcher         # Download ziniao_webdriver.bat

# Tasks (auto plan+execute on creation)
POST   /api/tasks                   # Create task → auto-starts pipeline
GET    /api/tasks?store_id=         # List tasks (filter by store)
GET    /api/tasks/:id               # Task detail
DELETE /api/tasks/:id               # Cascade-delete task subtree + workspace dir
POST   /api/tasks/:id/retry         # Retry failed task
POST   /api/tasks/:id/agent/stop    # Stop running agent
GET    /api/tasks/:id/steps         # Task steps
GET    /api/tasks/:id/messages      # Agent chat history
POST   /api/tasks/:id/messages      # Send chat message to agent
POST   /api/tasks/:id/questions/answer  # Answer agent's AskUserQuestion
POST   /api/tasks/:id/design        # Manual: start design agent
PATCH  /api/tasks/:id/review-plan   # Toggle plan_mode for a task
POST   /api/tasks/:id/execute-plan  # Manual: execute plan
POST   /api/tasks/:id/start         # Queue task via scheduler
POST   /api/tasks/:id/wake          # Wake a waiting task

# Events (business event tracking)
GET    /api/events                  # List events
POST   /api/events                  # Create event
POST   /api/events/:id/status       # Change status

# Channels
GET    /api/channels/active         # List active channels
POST   /api/channels/configure      # Configure channel

# Schedules (recurring tasks)
GET    /api/schedules               # List schedules (filter by store_id)
POST   /api/schedules               # Create schedule
GET    /api/schedules/:id           # Schedule detail
PUT    /api/schedules/:id           # Update schedule
DELETE /api/schedules/:id           # Delete schedule (child tasks kept)
POST   /api/schedules/:id/pause     # Pause schedule
POST   /api/schedules/:id/resume    # Resume schedule
GET    /api/schedules/:id/tasks     # List child tasks
POST   /api/schedules/:id/trigger   # Manual trigger

# Email Accounts
GET    /api/email-accounts          # List email accounts (no passwords)
POST   /api/email-accounts          # Create email account (Fernet-encrypted)
GET    /api/email-accounts/discover # IMAP auto-discovery by email domain
POST   /api/email-accounts/:id/test # Test IMAP connection
PUT    /api/email-accounts/:id      # Update email account
DELETE /api/email-accounts/:id      # Delete account + all store links

# Store-Email Links
GET    /api/stores/:id/emails       # List linked emails for store
POST   /api/stores/:id/emails       # Link email to store
DELETE /api/stores/:id/emails/:link_id  # Unlink email
POST   /api/stores/:id/emails/poll  # Poll new emails (watermark-based)

# Store Bookmarks
GET    /api/stores/:id/bookmarks    # Read Chrome profile bookmarks

# Real-time
GET    /api/sse                     # SSE stream for all real-time events
```

---

## Subsystem Details

> Moved from CLAUDE.md for progressive disclosure — Claude reads these on demand.

### All-Stores Scheduling

Cross-store scheduling via fan-out. Two recurring task patterns:

| Pattern | Store binding | Tab | Example |
|---------|--------------|-----|---------|
| **Store-specific** | One store (`store_id` set) | Scheduled | "Check Store A listings daily" |
| **All-stores (fan-out)** | All stores (`store_id=NULL`) | Scheduled | "Check inactive listings for every store" |

- **Fan-out logic**: `app/scheduler/fanout.py` — one task per active store per trigger, each goes through normal plan_then_execute pipeline
- **Task grouping**: `Task.batch_id` UUID groups tasks from same fan-out trigger
- **Plan authoring** (plan-mode schedules): Plan is authored once at schedule creation via a dedicated `is_plan_only` Task (user reviews + approves via the review-plan UI). The approved plan is frozen on `Schedule.plan` with `plan_status='ready'`; every subsequent fire copies it into the child task and skips planning. Editing the schedule's prompt invalidates the plan (`stale`); `/replan` authors a new one. Full lifecycle: [docs/subsystems.md § Plan-at-creation lifecycle](docs/subsystems.md#plan-at-creation-lifecycle).
- **No new task status**: fan-out tasks use standard lifecycle (pending → queued → designing → planned → running → completed/failed)
- **Session reuse**: existing per-store task queue serializes fan-out tasks; browser session persists between queued tasks
- **Frontend**: all-stores schedules shown with "All Stores" badge; child tasks grouped by store
- **Task archiving**: terminal tasks (completed/failed) >7 days hidden by default, toggle to show

### Email System

Store-level email connections with IMAP auto-discovery and watermark tracking.

- **EmailAccount model**: Stores IMAP credentials with Fernet-encrypted passwords (key from `JWT_SECRET` via SHA-256)
- **StoreEmailLink**: Many-to-many junction with per-link `watermark_date`, `last_polled_at`, `seen_message_ids`
- **IMAP auto-discovery**: Hardcoded table in `app/channels/imap_discovery.py` for top providers (163, Gmail, QQ, Outlook, Yahoo), heuristic fallback `imap.{domain}:993`
- **Poll endpoint**: `POST /api/stores/{id}/emails/poll` — 60s rate limit per link, per-link asyncio.Lock, watermark advances on success
- **Agent context**: Email tool instructions auto-injected into agent prompts for stores with linked emails

### Chrome Persistent Profiles & Bookmarks

- **Persistent profiles**: Managed by browser-use daemon per session. Chrome stores use `--session {slug}` for profile isolation. Cookies, localStorage, and login sessions survive across tasks.
- **Ziniao aux sessions**: Ziniao stores also get an auxiliary Chrome session (`{slug}-aux`) for non-seller-center URLs (Google, logistics sites, etc.). The aux session starts lazily on first use — zero overhead if unused.
- **Proxy config**: Only relevant for Chrome stores in UI.
- **Bookmarks**: `read_bookmarks(slug)` reads `Default/Bookmarks` JSON from the profile dir. Auto-injected into agent context when knowledge files are sparse.

### Browser & Task Concurrency

**Per-store task scheduling**: Multiple tasks for the same store run concurrently via CDPMuxProxy (up to the proxy's `max_clients` connection limit, default 5 per proxy instance). Tasks with the same platform but different country are queued (Ziniao country switch). Different platforms run concurrently. Tasks for *different* stores always run concurrently.

**Per-store browser isolation**: Each store gets its own backend instance, CDP proxy port, and browser-use wrapper script at `~/.vibe-seller/bin/{slug}/browser-use`. Agent sessions block raw `browser-use` and `mcp__playwright__*` and can only use the store-specific wrapper.

**Ziniao guard (account conflict)**: Only one Ziniao account can be active per machine (one Ziniao process). Multiple *profiles* (different `browserOauth`) on the same account work fine — each gets a unique `debuggingPort` and CDP proxy. But if store A uses Ziniao account #1 and store B uses account #2, store B's task will fail with a clear error: "Store(s) [Store A] are using a different Ziniao account. Stop the browser session first." Chrome stores have no such account restriction (but still use CDPMuxProxy for shared browser and cookie persistence).

**Ziniao dual-browser**: Ziniao stores get dual-session support in their wrapper — a main session (`{slug}`, routed via CDP proxy to Ziniao) and an aux session (`{slug}-aux`, Chrome for non-seller-center URLs). The agent uses AI judgment to route: seller center → Ziniao session, everything else → Chrome aux session. Routing rules defined in `app/prompts/dual_browser.md`. Users can override via `stores/{slug}/browser-routing.md`.

### Ziniao on WSL

WSL **cannot** auto-launch Ziniao because Electron's Node.js V8 rejects unknown `--` flags before the app code runs. The workflow is:

1. User downloads `ziniao_webdriver.bat` from `GET /api/ziniao/launcher` (or uses the copy in the repo root)
2. User double-clicks the `.bat` on Windows — it auto-finds the exe, kills any existing Ziniao, launches in WebDriver mode, and verifies the HTTP API
3. Once Ziniao is running, WSL connects via the gateway IP (auto-detected by `_get_ziniao_host()` from `ip route`)

If Ziniao is already running with the correct port, everything works automatically. The backend only raises errors when Ziniao is unreachable, guiding the user to the launcher script.

### CDP Proxy Architecture

Each Ziniao store gets a stable CDP proxy (`app/browser/cdp_proxy.py`) that listens on `127.0.0.1:{proxy_port}` and relays to the actual Ziniao `debuggingPort` (which changes on each `startBrowser` call). On WSL, the proxy relays to the Windows gateway IP instead of localhost. The browser-use wrapper's `--cdp-url` always points to the stable proxy port.
