# Testing

## 3-Tier Architecture

| Tier | Marker | Time | Scope |
|------|--------|------|-------|
| **Unit** | `@pytest.mark.unit` | <10s | Pure logic: models, utils, browser manager, git ops, profile CRUD |
| **Workflow** | `@pytest.mark.workflow` | <60s | Real user journeys: API → DB → state transitions → response shapes. Mocks: agent, browser, filesystem |
| **E2E** | `@pytest.mark.e2e` | Slow | Full browser + UI via Playwright |

## Running Tests

Activate the project venv first: `source .venv/bin/activate`

```bash
pytest -m unit                # Unit only
pytest -m workflow            # Workflow only
pytest -m "unit or workflow"  # Fast CI
pytest --e2e tests/e2e        # E2E only (requires running server + secrets)
pytest --e2e                  # Everything including e2e
```

E2E tests are **deselected by default** — `pytest tests/` only runs unit+workflow. Pass `--e2e` to include them. When `--e2e` is passed, missing LLM secrets cause hard failures (no silent skip).

## Test Structure

```
tests/
├── conftest.py              # Shared pytest fixtures
├── unit/                    # Unit tests
│   ├── test_ai/             # AI backend tests
│   ├── test_browser/        # Browser backend tests
│   ├── test_models/         # Database model tests
│   └── test_routers/        # API route tests
├── workflow/                # Workflow tests
│   └── fake_agent.py        # FakeAgent for test scenarios
├── integration/             # Integration tests
├── e2e/                     # End-to-end tests (Playwright + API)
│   ├── e2e_helpers.py       # Shared helpers (login, polling, task CRUD)
│   ├── conftest.py          # Fixtures (authenticated_page, api_client with SSE)
└── ai/                      # AI integration tests (manual)
```

## Test Markers

- `unit` — Fast unit tests (isolated, no external deps)
- `workflow` — Real user journey tests with mocked externals
- `integration` — Integration tests (database, API)
- `e2e` — End-to-end tests (browser + LLM, gated by `--e2e` flag)
- `ai` — AI integration tests (requires API keys)

## Guidelines

- **Philosophy**: If a test doesn't catch a real feature break, delete it.
- **New feature**: Add a workflow test that exercises the API round-trip.
- **Bug fix**: Add a test that reproduces the bug first (fails), then fix (passes).
- **Contract tests**: When changing API response schemas, update `test_contracts.py` key sets to match TypeScript interfaces.

## FakeAgent

Configurable via `FakeAgentScenario` (fields: `plan`, `result`, `todos`, `should_fail`, `fail_at_phase`, `complete_delay`, `tool_calls`, `thinking_text`). Located in `tests/workflow/fake_agent.py`.

- `tool_calls` — list of `{'tool': 'Read', 'input': {...}}` dicts; persisted as `tool_use` role messages before plan
- `thinking_text` — string persisted as `thinking` role message before plan

**Critical rule**: Don't mock away critical pipelines (like `_auto_run_task`) — use FakeAgent instead.

## E2E Test Infrastructure

### Shared Helpers (`tests/e2e/e2e_helpers.py`)

Consolidated helpers used by all LLM-dependent e2e tests:

- `login(client)` — authenticate httpx client
- `get_task(client, task_id)` — fetch task state
- `get_messages(client, task_id)` — fetch task messages
- `create_task(client, title, *, store_id, description, profile_id, plan_mode)` — create task with logging
- `create_store(client, name)` — create store
- `poll_task_status(client, task_id, targets, *, fail_statuses, timeout)` — poll until target status (returns on fail_status for caller to decide)
- `answer_question(client, task_id, request_id, answers)` — submit answer for pending question
- `get_secret(*keys)` — resolve env var secrets

### `api_client` Fixture (conftest.py)

Module-scoped fixture providing an authenticated httpx client with a **background SSE listener** that auto-answers any `AskUserQuestion` from any task. Prevents tests from hanging when LLM agents ask unexpected questions during planning or execution.

- **Scope**: `module` — one SSE thread per test file
- **Auto-answer**: replies "Please proceed." to all `task_questions` SSE events
- **Reconnect**: SSE stream auto-reconnects on disconnect
- **Teardown**: force-closes SSE stream to break blocking read, joins thread

Tests that use `api_client` get question handling automatically — no per-test SSE boilerplate needed.

### Parallel Execution (pytest-xdist)

E2E tests support parallel execution via `pytest-xdist`:

```bash
E2E_WORKERS=2 pytest tests/e2e/ --e2e -n 2 --dist loadfile -v
```

- `--dist loadfile` keeps all tests from one file on one worker (required for module-scoped fixtures)
- Each worker is a separate process with its own Playwright browser and httpx clients
- All workers connect to the same shared server (started externally)
- CDPMuxProxy enables concurrent browser tasks without interference

### Concurrency Environment Variables

| Variable | Layer | Default | Purpose |
|---|---|---|---|
| `E2E_WORKERS` | Test runner (CI) | 0 (sequential) | Number of pytest-xdist worker processes. Set >1 to run tests in parallel. |
| `E2E_PROVIDER_MAP` | Test runner (CI) | (empty) | Comma-separated provider per worker (e.g., `kimi,kimi,minimax`). Each xdist worker creates a profile for its assigned provider so tasks are spread across APIs. |
| `MAX_AGENT_CONCURRENCY` | Server (`app/ai/claude_backend.py`) | 2 | Max simultaneous Claude CLI agent subprocesses. Semaphore gate — exceeding this blocks new tasks until a slot opens. |

**Relationship**: `MAX_AGENT_CONCURRENCY` must be ≥ 2× `E2E_WORKERS` to avoid test timeouts from semaphore contention. CI uses `E2E_WORKERS=3` + `E2E_PROVIDER_MAP=kimi,kimi,minimax` + `MAX_AGENT_CONCURRENCY=9`.

### Log Tracing

With parallel tests, logs are interleaved. Trace by task ID:

- **Server side**: `AGENT_DEBUG [task_id]` prefix on every agent log line
- **Pytest side**: `e2e` logger; helper functions log `task_id` in messages so you can grep for it
- **Debug a failure**: `grep {task_id[:8]}` in both pytest output and server log

## Mock CLI E2E Tests

For browser integration tests without real LLM credentials, use `MOCK_CLI` mode:

```bash
# Start server with mock CLI (outputs deterministic stream-json events)
MOCK_CLI=tests/e2e/mock_cli.py ./start.sh 7777

# Run Playwright tests against mock server
E2E_BASE_URL=http://localhost:7777 pytest tests/e2e/test_conversation_ui.py -m e2e
```

The mock CLI script (`tests/e2e/mock_cli.py`) simulates the Claude CLI stream-json protocol — it outputs thinking deltas, tool_use blocks, ExitPlanMode control requests, and results. The real `ClaudeCodeBackend` processes these through the full pipeline (`_handle_event` → `_emit_message`/`_emit_ephemeral` → SSE → frontend), so the test covers the entire stack.

Configure mock behavior via `MOCK_CLI_SCENARIO` env var (JSON):
```bash
MOCK_CLI_SCENARIO='{"plan":"## Custom Plan","skip_plan":true}' ...
```

CI runs this automatically in the `e2e-mock-cli` job (no LLM secrets required).

## Fixtures

### Database Fixtures

- `async_engine` — SQLAlchemy async engine for tests
- `async_db_session` — Fresh database session for each test
- `test_user` — Authenticated test user
- `test_store` — Chrome backend test store
- `test_task` — Pre-created test task

### API Fixtures

- `async_client` — HTTP client with database override
- `authenticated_client` — Authenticated HTTP client
- `auth_token` — JWT token for test user
- `auth_headers` — Authorization headers

### Mock Fixtures

- `mock_agent_manager` — Mocked AI agent manager
- `mock_browser_manager` — Mocked browser manager
- `override_get_db` — Database dependency override

## Frontend Tests

Vitest + React Testing Library. Run from `frontend/` directory:

```bash
cd frontend && npx vitest run        # All frontend tests
cd frontend && npx vitest run --watch # Watch mode
```

Test helpers in `src/test/helpers.tsx`:
- `makeConversationItem(type, overrides)` — factory for all conversation item types including `tool_call`, `thinking`
- `renderConversationStream(overrides)` — renders ConversationStream with defaults and optional prop overrides

## Environment Variables

Tests use:
```bash
SECRET_KEY=test-secret-key-for-testing-only
DATABASE_URL=sqlite+aiosqlite:///:memory:
MOCK_CLI=tests/e2e/mock_cli.py       # E2E mock CLI mode (no LLM needed)
MOCK_CLI_SCENARIO='{"plan":"...","skip_plan":false}'  # Optional mock CLI config
```
