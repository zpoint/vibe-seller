# Vibe Seller - Claude Code Context

> **For full documentation**: See [docs/dev-guide.md](docs/dev-guide.md)
> (user-facing landing page is [README.md](README.md))

## What & Why

Team collaboration platform for e-commerce store automation. Users create browser-automation tasks organized by store, executed via browser-use CLI (Chrome/Ziniao) with real-time SSE streaming.

## Tech Stack

- **Backend**: Python 3.11+ / FastAPI / SQLAlchemy 2.0 async / SQLite (`~/.vibe-seller/data/vibe_seller.db`) / SSE
- **Frontend**: React 19 / TypeScript / Vite / Tailwind CSS 4 / react-i18next
- **Browser**: browser-use CLI with pluggable backends (Chromium engine)

## Key Conventions

- Component-based frontend: `App.tsx` (state + layout shell) delegates to `views/` (TasksView, EventsView, WorkspaceView, SettingsView), `components/` (Sidebar, modals, UI primitives), `hooks/` (useSSE), with shared `types.ts` and `api.ts`
- Optional integration bundles (e.g. Google Workspace) install per-user via Settings → Integrations; see `app/workspace/gws_integration.py`.
- Pluggable browser backends in `app/browser/` (base.py defines interface); multi-client CDP proxy in `cdp_mux_proxy.py` for concurrent per-store tasks
- AI agent abstraction in `app/ai/` (`AIAgentBackend` ABC, `ClaudeCodeBackend` impl)
- System prompts in `app/prompts/*.md` — loaded once at import via `app/prompts/__init__.py`
- Tasks auto-execute on creation in **auto mode** (default, `plan_mode=false`): `bypassPermissions`, PENDING → RUNNING → COMPLETED. Opt-in **plan mode** (`plan_mode=true`): agent plans in read-only mode, user reviews, then executes (PENDING → DESIGNING → PLANNED → RUNNING → COMPLETED). Toggle via Auto/Plan switch in task detail footer. Non-store tasks always use plan mode. Plan-mode schedules author the plan once at creation via an `is_plan_only=True` Task (user reviews, plan is frozen on `Schedule.plan`, `plan_status=ready`); each subsequent fire copies the frozen plan and skips planning. See `app/plan_states.py` + [docs/subsystems.md](docs/subsystems.md#plan-at-creation-lifecycle).
- JWT cookie auth (httpOnly, 7-day expiry)
- UUIDs for all primary keys
- API routes prefixed with `/api/`
- i18n translations in `frontend/src/i18n/locales/{en,zh}/`
- Agents write to `stores/` and `knowledge/` via MCP `vibe_seller_write_workspace_file` (not the built-in Write tool — it can't write through workspace symlinks; see [docs/workspace.md](docs/workspace.md#symlink-write-caveat))

## Commands

All Python commands must use the project venv at `.venv/`:
```bash
source .venv/bin/activate  # Activate venv first
```

```bash
./start.sh [PORT]      # Start server (default 7777)
./stop.sh [PORT]       # Stop server
./restart.sh [PORT]    # Restart on same port
```

## Code Style

Google Python style enforced via pre-commit + ruff:

- **Line length**: 80 characters
- **Quotes**: Single quotes for strings, double for docstrings/triple-quoted
- **Imports**: Google-style ordering (stdlib → third-party → local `app`)
- **Naming**: PEP 8 / Google (snake_case functions/vars, PascalCase classes)
- **Frontend**: ESLint + TypeScript strict mode

```bash
pre-commit install     # Set up hooks (one-time)
pre-commit run --all-files  # Lint everything
ruff check --fix .     # Python lint + auto-fix
ruff format .          # Python format
```

Config lives in `pyproject.toml` ([tool.ruff]) and `.pre-commit-config.yaml`.

## Fix from design, not from symptom

When given a bug or failing test, **review the design that produced it
before writing any fix.** The failure is one observation of a design
that allowed the bug to be possible. Patching the observation doesn't
change what's possible — only changing the design does.

Ask in this order, every time:

1. **What invariant was violated?** Which contract did the failure
   prove was unenforced — and where in the codebase is that contract
   currently expressed only in prose, prompts, or comments?
2. **Where does the design make the bug possible?** Find the surface
   that lets a wrong value, wrong state, or race window through.
   Common offenders in this codebase: agent-supplied values trusted
   without server-side validation; lifecycle ownership split between
   the manager and the orchestrator; status transitions written from
   multiple coroutines; prose-only contracts for typed cursors
   (`email_watermark`, `last_order_id`, etc.).
3. **Move the contract into code.** The fix should make the bug class
   impossible to recreate from this surface. Server-side validation,
   typed APIs, single-owner coroutines, ownership invariants — these
   are design fixes. Retries, sleeps, "let's just hope the model gets
   it right next time," loosened assertions, and bumped timeouts are
   workarounds that hide the symptom.
4. **Only after the design fix:** are tests pinning this invariant?
   If not, add them. Tests are the second line of defence, not the
   fix itself.

If the natural fix touches a file the current task didn't, fix it
anyway. The goal is "this bug class can't recur," not "this PR is
green." See [.claude/skills/debug-ci/SKILL.md § Fix from design,
not from symptom](.claude/skills/debug-ci/SKILL.md) for the full
checklist and worked examples.

## Claude Workflows

### Adding Translations

1. Add EN text to `frontend/src/i18n/locales/en/translation.json`
2. Add ZH text to `frontend/src/i18n/locales/zh/translation.json`
3. Use: `const { t } = useTranslation(); t('key')` or `t('key', { count: 5 })`

### Adding a Browser Backend

1. Create `app/browser/mybackend.py` implementing `BrowserBackend` from `base.py`
2. Register in `BrowserManager._get_backend()` in `manager.py`
3. `BrowserManager` generates per-store wrapper scripts at `~/.vibe-seller/bin/{store-slug}/browser-use` that inject the correct `--session` and `--cdp-url` flags (with `VIBE_TASK_ID` for multi-client CDP proxy isolation)

Existing backends: `chrome` (Playwright Chromium, macOS/Linux/WSLg), `ziniao` (anti-detect, talks to the Ziniao client over HTTP), `winchrome` (native Windows Chrome via Task Scheduler — for WSL2-on-Windows where a headed window can't render under systemd; see [docs/windows-setup.md](docs/windows-setup.md#7-browser-automation--native-windows-chrome-winchrome-backend)).

> **Store-less `web` browser**: no-store (orchestrator) tasks get a generic Chrome browser (not tied to any store) for neutral public web work — wrapper at `bin/_web/browser-use`, sessions `web`/`web-{task[:8]}`, lazy-started via `POST /api/browser/web/start`. Seller-center work still delegates to a per-store sub-task. Distinct from the per-store `{slug}-aux` session. See [docs/browser.md](docs/browser.md#store-less-web-browser).

### Adding an AI Agent Backend

1. Create `app/ai/mybackend.py` implementing `AIAgentBackend` from `base.py`
2. Implement `run()`, `stop()`, `submit_answer()`, `is_running()`
3. Instantiate and wire up in place of `agent_manager` singleton

### Adding a Message Channel

1. Create `app/channels/mybackend.py`
2. Subclass `BaseChannel` or `ReadWriteChannel`
3. Use `@register_channel` decorator
4. Channel poll → messages → agent extracts events (via agent backend)

> **Note**: The email channel (`email_channel.py`) was removed. Email is now handled via per-account SQLite DBs synced by a background job (`app/scheduler/email_sync.py`), with agent access through `vibe_seller_email_info` and `vibe_seller_send_email` MCP tools instead of the old `vibe_seller_poll_emails` channel-based approach.

### Adding an Event Sync Backend

1. Create `app/events_system/backends/mybackend.py`
2. Subclass `EventBackend` from `syncer.py`
3. Use `@register_backend("mybackend")` decorator

### Adding a Built-in Skill

1. Create `app/skills/my-skill/SKILL.md` with YAML frontmatter (`name`, `description`)
2. Add implementation files (scripts, `requirements.txt`)
3. Update `app/skills/MANIFEST.txt` with relative paths to all files
4. Sync copies files to `~/.vibe-seller/.claude/skills/my-skill/`

> **Built-in browser-use skill**: `app/skills/browser-use/SKILL.md` documents the full browser-use CLI reference (commands, flags, workflows). Agents load it automatically for browser automation tasks.

> **Optional integration bundles** (e.g., Google Workspace) install at runtime via Settings → Integrations rather than `app/skills/`. See [docs/workspace.md § Optional Integration Bundles](docs/workspace.md#optional-integration-bundles).

## System Prompts

All LLM system prompts live in `app/prompts/*.md`, loaded once at import via `app/prompts/__init__.py`.

| File | Purpose |
|------|---------|
| `design_system.md` | Base agent instructions (phases, `{workspace_guidance}` slot for catalog restriction, subagent rules) |
| `reflection.md` | Post-task knowledge/skill creation (delivered via Stop hook, skipped for catalog sync) |
| `scheduled_pretask.md` | Injected by `_build_system_extra()` when `task.schedule_id` is set (non-catalog) — tells the agent to load prior cursor via `vibe_seller_get_schedule_state` |
| `scheduled_watermark.md` | Appended to reflection in Stop hook for scheduled tasks — nudges the agent to persist cursor via `vibe_seller_set_schedule_state` |
| `waiting_instruction.md` | Wait-condition signaling docs |
| `dual_browser.md` | Ziniao vs Chrome routing (injected by `_build_store_context`) |
| `event_extraction.md` | Event extraction (used by `extractor.py`, not task prompts) |

### Design Principle

**All task prompts go through one function: `_build_system_extra()` in `app/task_runner.py`.**

- One template (`design_system.md`) with `{workspace_guidance}` slot (used only for catalog sync restriction; empty for regular tasks)
- One builder assembles all context in fixed order for every task type
- Callers pass `TaskHeader` enum + optional `extra_context`, get back `PromptBundle(prompt, system_extra, mode)`
- No inline prompt assembly outside the builder (except `start_agent` which is intentionally minimal)
- `AGENT_DEBUG=1` logs a truncated preview of the assembled prompt to server output
- Snapshot tests in `tests/unit/test_prompt_assembly.py` verify every `TaskHeader` variant

### Prompt Assembly Order

`_build_system_extra()` always assembles in this order:
1. Base prompt (`design_system.md` with `{workspace_guidance}` filled)
2. Language hint (Chinese/English)
3. Waiting instruction
4. Store context OR all-stores context
5. TickTick integration (if configured)
6. System context (task type, integrations, capabilities)
7. Header-derived extra (plan text for execute/woken)
8. Caller extra context (conversation history, wake trigger)

Post-task reflection is delivered via the Stop hook (not in the system prompt). A one-liner reminder is included in the system prompt so the agent can plan accordingly.

`start_agent()` (ad-hoc agent start) intentionally skips full context — it's for raw interaction.

**Plan-skipping**: Some agents (e.g. MiniMax) may execute simple tasks directly without calling `ExitPlanMode`, leaving the task in DESIGNING with a result but no plan. The state machine allows DESIGNING → COMPLETED for this case. `_auto_run_task` detects this (result set, plan null, no error) and transitions directly to COMPLETED.

**ExitPlanMode control protocol**: hook_callback → `permissionDecision: 'ask'` → can_use_tool → save plan → allow (with `SetMode: bypassPermissions`) or deny (with `message: '...'`). Handler runs sequentially (blocks read loop). Stdin stays open during planning for multi-turn feedback; closed only after execution completes (`_executing` flag).

## Testing

| Tier | Marker | Time | Scope |
|------|--------|------|-------|
| **Unit** | `@pytest.mark.unit` | <10s | Pure logic: models, utils, browser manager, git ops, profile CRUD |
| **Workflow** | `@pytest.mark.workflow` | <60s | Real user journeys: API → DB → state transitions → response shapes |
| **E2E** | `@pytest.mark.e2e` | Slow | Full browser + UI via browser-use / Playwright |

```bash
pytest -m unit                # Unit only
pytest -m workflow            # Workflow only
pytest -m "unit or workflow"  # Fast CI
pytest --e2e tests/e2e        # E2E only (requires running server)
```

- **Philosophy**: If a test doesn't catch a real feature break, delete it.
- **FakeAgent**: Don't mock away `_auto_run_task` — use `FakeAgentScenario` in `tests/workflow/fake_agent.py`.
- **Contract tests**: Update `test_contracts.py` key sets when changing API response schemas.

## Deep-Dive Documentation

- [docs/tasks.md](docs/tasks.md) — task lifecycle, data persistence, agent context, scheduling
- [docs/workspace.md](docs/workspace.md) — workspace assistant, knowledge system, skills
- [docs/subsystems.md](docs/subsystems.md) — all-stores scheduling, email, browser profiles, concurrency, CDP
- [docs/frontend.md](docs/frontend.md) — React components, views, i18n, SSE
- [docs/backend.md](docs/backend.md) — FastAPI modules, models, schemas, config
- [docs/api.md](docs/api.md) — all API routes by router
- [docs/testing.md](docs/testing.md) — test tiers, fixtures, FakeAgent
- [docs/browser.md](docs/browser.md) — browser backends, Ziniao, WSL, CDP proxy
- [docs/events.md](docs/events.md) — SSE event bus, event types, business events
- [docs/windows-setup.md](docs/windows-setup.md) — Windows+WSL2 deployment: SSH bootstrap, WSL2 mirrored networking (MUST be Win 11 25H2/build 26200+ for external port access), `winchrome` native-Chrome backend, systemd service, Tailscale, auto-start, full troubleshooting cheatsheet
- [docs/macos-setup.md](docs/macos-setup.md) — macOS deployment: `launchd` LaunchAgent for auto-start on login + crash-restart, the `EX_CONFIG`/TCC/PATH gotchas, deploy flow, troubleshooting cheatsheet
- [docker/E2E_TESTING.md](docker/E2E_TESTING.md) — local E2E testing with Docker, iterative debugging

## References

| Topic | Location |
|-------|----------|
| Full setup & quick start | [README.md](README.md#quick-start) |
| Project structure | [README.md](README.md#project-structure) |
| Architecture design | [DESIGN.md](DESIGN.md) |
