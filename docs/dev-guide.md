# Developer Guide

Detailed reference for contributors: feature surface, project
structure, API endpoints, testing flows, and implementation status.
For the user-facing landing page, see [`../README.md`](../README.md).

## What it does

- **Store Management**: Configure e-commerce stores with pluggable browser backends (Chrome, Ziniao, custom CDP)
- **Task Execution**: Create tasks that auto-plan and auto-execute via AI agent — no manual buttons needed
- **AI Agent**: Pluggable AI agent backend (Claude Code CLI for MVP) with workspace memory and knowledge
- **Knowledge System**: Two-layer knowledge — project-level (synced from repo) + local (agent-generated)
- **Task Integration**: TickTick/Dida365 via OAuth — AI agent gets MCP tools for task management
- **Skills System**: Reusable agent procedures with per-skill venvs, synced from repo with user config preservation
- **Channel Integration**: Email (IMAP) and WeCom channels with agent-based event extraction
- **Real-time Feedback**: See live progress, agent messages, and execution logs via SSE streaming
- **Multi-store Parallelism**: Each store gets its own browser instance; tasks across stores run in parallel
- **Workspace AI Assistant**: Conversational chat in the Workspace page — users describe store knowledge in natural language and the AI organizes it into the correct files (store notes, skills, cross-store knowledge)
- **Bilingual UI**: Language switcher (English/Chinese) with i18n support

## Telemetry

Anonymous usage telemetry, default on. Opt out in Settings → AI Agent
or with `VIBE_SELLER_TELEMETRY=0`. See [telemetry.md](telemetry.md).

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Node.js 18+ with pnpm
- [browser-use CLI](https://github.com/browser-use/browser-use) (`pip install browser-use` or via uv)

## Quick Start

### 1. Install Dependencies

```bash
# Install backend dependencies (creates .venv automatically)
uv sync
source .venv/bin/activate

# Verify browser-use CLI is available
browser-use doctor
```

### 2. Start the Server

```bash
./start.sh              # Start on default port 7777
./start.sh 8080         # Start on port 8080
```

The script builds the frontend and starts the backend (which serves the SPA).
Open http://localhost:7777 in your browser.

Other server commands:

```bash
./stop.sh [PORT]        # Stop server (default: 7777)
./stop.sh --all         # Stop all vibe-seller servers
./restart.sh [PORT]     # Restart server
```

### 3. First Task

1. Click **+ New Store** in the left sidebar, name it "My Store"
2. Type `Navigate to google.com` in the chat input at the bottom
3. Press Enter or click Send
4. Watch the browser launch, navigate, and see the screenshot appear in the step timeline

## Development

### Code Style

This project follows **Google Python style**, enforced via pre-commit hooks:

- **Line length**: 80 characters
- **Quotes**: Single quotes for strings, double for docstrings
- **Imports**: Google-style ordering via isort
- **Linter**: ruff (pycodestyle, pyflakes, isort, naming, bugbear, quotes, simplify)
- **Frontend**: ESLint + TypeScript type checking

```bash
# Install pre-commit and hooks (one-time setup)
uv pip install pre-commit
pre-commit install

# Run all checks
pre-commit run --all-files

# Python only
ruff check --fix .     # Lint + auto-fix
ruff format .          # Format
```

Configuration: `pyproject.toml` ([tool.ruff]) and `.pre-commit-config.yaml`.

## Project Structure

```
vibe-seller/
├── app/                   # Python FastAPI backend
│   ├── main.py            # App entry point, router registration, lifespan
│   ├── config.py          # Configuration (paths, DB URL, defaults)
│   ├── database.py        # SQLAlchemy async engine + session
│   ├── auth.py            # JWT cookie auth
│   ├── ai/                # AI agent abstraction layer
│   │   ├── base.py        # AIAgentBackend ABC
│   │   └── claude_backend.py  # Claude Code CLI subprocess impl
│   ├── browser/           # Pluggable browser backends
│   │   ├── base.py        # Abstract BrowserBackend + BrowserSessionInfo
│   │   ├── bookmarks.py   # Chrome bookmark reader
│   │   ├── ziniao.py      # Ziniao anti-detect browser
│   │   ├── ziniao_utils.py  # Ziniao HTTP API + WSL support
│   │   ├── cdp_proxy.py   # Async TCP relay for CDP
│   │   └── manager.py     # Per-store orchestration + Ziniao guard + wrapper scripts
│   ├── scheduler/         # Task scheduling
│   │   ├── cron.py        # Cron + schedule job management
│   │   ├── fanout.py      # All-stores fan-out trigger logic
│   │   └── task_queue.py  # Per-store concurrent task scheduler
│   ├── utils/             # Shared utilities
│   │   └── crypto.py      # Fernet password encryption
│   ├── skills/            # Bundled skills (synced to workspace)
│   ├── workspace/         # Workspace management (~/.vibe-seller/)
│   ├── executor/          # Legacy browser task runner
│   ├── channels/          # Message channel integrations
│   ├── events_system/     # Business event extraction + sync
│   ├── events/            # SSE event bus
│   ├── models/            # SQLAlchemy ORM models
│   ├── routers/           # FastAPI API routes
│   └── schemas/           # Pydantic request/response schemas
├── frontend/              # React 19 + TypeScript SPA
│   └── src/
│       ├── App.tsx        # Single-file UI (MVP)
│       └── i18n/locales/  # EN/ZH translations
├── tests/                 # pytest + vitest test suites
├── knowledge -> app/knowledge  # Symlink to project knowledge (in app/ for pip)
├── data/                  # Runtime data (gitignored)
├── DESIGN.md              # Full architecture design document
├── CLAUDE.md              # Claude Code development guide
└── pyproject.toml         # Python project metadata + ruff config
```

See `CLAUDE.md` for Claude Code conventions and workflows. See `DESIGN.md` for the full architecture plan.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/login` | Login (sets httpOnly JWT cookie) |
| POST | `/api/auth/logout` | Logout |
| GET | `/api/auth/me` | Current user |
| GET | `/api/stores` | List all stores |
| POST | `/api/stores` | Create a store |
| GET | `/api/stores/:id` | Get store detail |
| DELETE | `/api/stores/:id` | Delete a store (stops browser) |
| GET | `/api/ziniao-accounts` | List Ziniao accounts |
| POST | `/api/ziniao-accounts` | Create Ziniao account |
| GET | `/api/ziniao-accounts/:id/browsers` | List browser profiles |
| GET | `/api/ziniao/launcher` | Download ziniao_webdriver.bat |
| GET | `/api/tasks?store_id=&include_archived=` | List tasks (filter by store, archived) |
| POST | `/api/tasks` | Create task (auto plan+execute) |
| GET | `/api/tasks/:id` | Get task detail |
| POST | `/api/tasks/:id/retry` | Retry a failed task |
| POST | `/api/tasks/:id/agent/stop` | Stop running agent |
| POST | `/api/tasks/:id/wake` | Wake a waiting task (optional `{message}`) |
| GET | `/api/tasks/:id/steps` | Get task steps |
| GET | `/api/tasks/:id/messages` | Agent chat history |
| POST | `/api/tasks/:id/messages` | Send message to agent |
| POST | `/api/tasks/:id/questions/answer` | Answer agent question |
| GET | `/api/screenshots/:id` | Serve screenshot image |
| GET | `/api/schedules?store_id=` | List schedules (filter by store) |
| POST | `/api/schedules` | Create a recurring schedule |
| GET | `/api/schedules/:id` | Get schedule detail |
| PUT | `/api/schedules/:id` | Update schedule |
| DELETE | `/api/schedules/:id` | Delete schedule |
| POST | `/api/schedules/:id/pause` | Pause schedule |
| POST | `/api/schedules/:id/resume` | Resume schedule |
| GET | `/api/schedules/:id/tasks` | List child tasks for schedule |
| POST | `/api/schedules/:id/trigger` | Manually trigger schedule (all-stores: fan-out) |
| GET | `/api/email-accounts` | List email accounts |
| POST | `/api/email-accounts` | Create email account (password encrypted) |
| GET | `/api/email-accounts/discover` | Auto-discover IMAP settings |
| POST | `/api/email-accounts/:id/test` | Test IMAP connection |
| DELETE | `/api/email-accounts/:id` | Delete email account |
| GET | `/api/stores/:id/emails` | List linked emails for store |
| POST | `/api/stores/:id/emails` | Link email account to store |
| DELETE | `/api/stores/:id/emails/:link_id` | Unlink email from store |
| POST | `/api/stores/:id/emails/poll` | Poll new emails (watermark-based) |
| GET | `/api/stores/:id/bookmarks` | Read Chrome profile bookmarks |
| GET | `/api/dida365/status` | TickTick/Dida365 connection status |
| POST | `/api/dida365/authorize` | Start OAuth2 flow |
| GET | `/api/dida365/callback` | OAuth2 redirect callback |
| GET | `/api/dida365/projects` | List TickTick projects |
| POST | `/api/dida365/configure` | Save project + MCP path |
| DELETE | `/api/dida365/disconnect` | Disconnect integration |
| POST | `/api/workspace/knowledge/sync` | Sync project knowledge (local + remote) |
| GET | `/api/workspace/knowledge/sync-meta` | Get sync metadata |
| POST | `/api/workspace/skills/sync` | Sync built-in skills (local + remote) |
| GET | `/api/workspace/skills/sync-meta` | Get skills sync metadata |
| GET | `/api/settings/google-workspace/status` | Google Workspace bundle prereqs + enabled flag |
| POST | `/api/settings/google-workspace/enable` | Install the `gws` umbrella skill (admin) |
| POST | `/api/settings/google-workspace/disable` | Remove the `gws` umbrella skill (admin) |
| POST | `/api/workspace/skill` | Create a new user skill |
| POST | `/api/workspace/assistant/message` | Send message to workspace AI assistant |
| POST | `/api/workspace/assistant/stop` | Stop workspace assistant session |
| GET | `/api/workspace/assistant/status` | Check if assistant is running |
| GET | `/api/sse` | SSE stream for real-time updates |

## Testing

### Backend API (curl)

```bash
# Health check
curl http://localhost:7777/api/health

# Create a store
curl -X POST http://localhost:7777/api/stores \
  -H 'Content-Type: application/json' \
  -d '{"name":"Test Store","browser_backend":"chrome","browser_config":{}}'

# List stores
curl http://localhost:7777/api/stores

# Create a task (use store ID from above)
curl -X POST http://localhost:7777/api/tasks \
  -H 'Content-Type: application/json' \
  -d '{"store_id":"<STORE_ID>","title":"Navigate to google.com","description":"Navigate to google.com"}'

# Start the task
curl -X POST http://localhost:7777/api/tasks/<TASK_ID>/start

# Check task status
curl http://localhost:7777/api/tasks/<TASK_ID>

# Get task steps with screenshots
curl http://localhost:7777/api/tasks/<TASK_ID>/steps

# Listen to SSE events
curl -N http://localhost:7777/api/sse
```

### Full E2E Test

1. Start server: `./start.sh`
2. Open http://localhost:7777
3. Create a store, send a task like "Navigate to github.com"
4. Verify: Chromium launches, navigates, screenshots appear in the step timeline with execution logs

## Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Backend | FastAPI | 0.115.6 |
| ORM | SQLAlchemy (async) | 2.0.36 |
| Database | SQLite + aiosqlite | 0.20.0 |
| Validation | Pydantic | 2.10.4 |
| SSE | sse-starlette | 2.2.1 |
| Browser | browser-use CLI (Chromium) | latest |
| Frontend | React | 19.2.0 |
| i18n | react-i18next | 15.4.0 |
| Bundler | Vite | 7.3.1 |
| Styling | Tailwind CSS | 4.2.1 |
| Language | TypeScript | 5.9.3 |

## Implementation Status

### Implemented
- Store CRUD with Chrome and Ziniao browser backends
- Ziniao anti-detect browser integration (auto-launch, profile selection, CDP proxy, WSL support)
- Per-store browser isolation (unique backend instance, proxy port, browser-use wrapper script per store)
- Ziniao guard: account conflict detection (one Ziniao account per machine, multiple profiles OK)
- Per-store task concurrency: tasks run concurrently by default; only queued when same platform + different country (Ziniao country-switch constraint)
- AI agent abstraction layer (`AIAgentBackend` ABC with `ClaudeCodeBackend`)
- Auto plan+execute pipeline: tasks auto-design and auto-execute on creation
- AskUserQuestion UX: multi-question batching, "Other" free-text option, chat reply to agent
- Browser enforcement: agents use per-store browser-use wrapper scripts in `~/.vibe-seller/bin/{slug}/`
- Two-layer knowledge system (project repo sync + local agent-generated)
- Task queue scheduler with concurrent execution and platform/country awareness
- Scheduled tasks (daily/weekly/monthly) with APScheduler MemoryJobStore
- Schedule CRUD API with pause/resume/trigger, sub-tab UI (One-time / Scheduled)
- Email account management with Fernet-encrypted passwords
- IMAP auto-discovery for common providers (163, Gmail, QQ, Outlook, Yahoo, etc.)
- Store-email linking with watermark-based incremental polling
- Email context auto-injected into agent prompts for linked stores
- Persistent Chrome profiles via `--user-data-dir` per store (`~/.vibe-seller/browser_profiles/`)
- Dual browser for Ziniao stores: auxiliary Chrome session with persistent profile for non-seller-center URLs
- Chrome bookmark reading with auto-injection into agent context
- Structured proxy config for Chrome stores
- Channel configuration persistence (email IMAP, WeCom)
- Event sync backends (Dida365, Google Calendar)
- Settings UI with tabs: Users, Channels (email management), Event Sync, AI Agent, Integrations (Google Workspace toggle)
- Real-time SSE streaming (task status, agent messages, progress, logs)
- Three-panel UI: store sidebar, task list, agent chat with progress
- Bilingual i18n support (English/Chinese) with language switcher
- JWT cookie auth (httpOnly, 7-day expiry)
- All-stores schedules: cross-store recurring tasks with automatic fan-out to all stores
- Task archiving: terminal tasks >7 days hidden by default with toggle to show

### Planned (see DESIGN.md)
- Own agent runtime (replace CLI wrapper with direct LLM API calls)
- Custom CDP endpoint backend
- Credential vault (partially implemented — email passwords encrypted via Fernet)
- Task templates (create listing, shipment, etc.)
- Composite tasks (parent decomposes into child tasks)
