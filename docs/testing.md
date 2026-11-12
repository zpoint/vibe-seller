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

## Test-home isolation

The test process never uses the developer's real `~/.vibe-seller` as
its runtime home unless it is an e2e run pointed at a server that
lives there (`tests/conftest.py` enforces this before the first
`app.*` import, and a session-start guard fails loudly if it ever
regresses):

- **Non-e2e runs** get a fresh throwaway `VIBE_HOME` (a session temp
  dir, removed on exit), forced — a stale `VIBE_HOME` in the
  environment cannot quietly keep the suite on a real home. Without
  this, module-level paths derived from the runtime home (chat
  staging, task history, the import-time `backend_<port>.log`) read
  and wrote production state.
- **E2E runs (`--e2e`)** must resolve paths the way the
  **separately-running server** does, so the test home is the
  server's home: set **`E2E_VIBE_HOME`** to the server's home
  (e.g. `E2E_VIBE_HOME=~/.vibe-seller-e2e`), or export `VIBE_HOME`
  the same way. With neither set, the default `~/.vibe-seller` is
  used — which is correct on CI, where the server itself lives there.
  `BACKEND_PORT` is derived from `E2E_BASE_URL`'s port (a URL without
  an explicit port means the scheme's default, 80/443) so the test
  process cannot create a decoy server log under the wrong name.
  `--e2e` is detected from the command line AND `PYTEST_ADDOPTS`
  (options injected there never appear in `sys.argv`). Under
  pytest-xdist (`-n N`, as CI's e2e job runs) the controller publishes
  the decision as `VIBE_TEST_E2E=1` and the workers inherit it — a
  worker's own `sys.argv` never carries `--e2e`.

  **`E2E_VIBE_HOME` requires the server to be on the same
  filesystem**: the e2e helpers seed and read workspace files by local
  path. A remote `E2E_BASE_URL` cannot work with it — run those
  suites from a checkout on the server's host (or in the same
  container; see [docker/E2E_TESTING.md](https://github.com/zpoint/vibe-seller/blob/main/docker/E2E_TESTING.md)).

```bash
# e2e against a server on an isolated home + port
E2E_VIBE_HOME=~/.vibe-seller-e2e E2E_BASE_URL=http://127.0.0.1:7799 \
  pytest --e2e tests/e2e
```

## Server-global state: own a server, don't lock one

The e2e tier runs against ONE shared server, and one xdist worker per
`E2E_PROVIDER_MAP` entry. Most tests only touch their own task or their
own store, so they share it happily. Some state is **server-global**: one
row, one file, or one in-memory singleton that every worker's tests read
and write through the same server:

| Surface | Why it collides |
|---|---|
| the catalog sync fanout (`SYSTEM_CATALOG_SYNC_ID`) | one system schedule, and its fanout rebuilds the catalog of EVERY store on the server |
| the shared `knowledge/` tree | any new file makes every store's catalog stale; a rebuild rotates the catalogs it regenerates |
| `PUT /api/settings` | `auth_required` (a test asserting a 401 only holds it while auth stays on), `max_agent_concurrency` (the server-wide agent semaphore) |
| `PUT /api/vision/config`, `/api/sync/<job>/run` | the one kie.ai key and the one priced catalog; a manual sync run rewrites server-wide state |
| `POST /api/ads/stores/sync` | rewrites the ads binding of every store row |
| a direct write into `app_settings` | bypasses the API but lands in the same one DB |

```
            one pytest process: collect -> sort longest-first (durations.json)
                                  |
                --dist load: the next test goes to whichever worker is free
                                  |
          gw0 (minimax)   gw1 (qwen)   gw2 (glm)   ...   one worker per
                                                         E2E_PROVIDER_MAP entry
     Workers are not typed: each takes the next test, whatever it is.

     a test touching only its OWN task / store      a module touching SERVER-GLOBAL
                     |                              state (table below)
                     v                                         |
     +----------------------------------+                     v  module fixture
     | shared server (CI starts it      |       +-------------------------------+
     | before pytest; every worker      |       | owned server: free port, temp |
     | uses it at once)                 |       | VIBE_HOME, same worker        |
     +----------------------------------+       | profile, own SSE auto-answer; |
                                                | started by the worker running |
                                                | the module, stopped after it  |
                                                +-------------------------------+
     Fake services (ad console, GitHub, ads API) run per test on ephemeral ports.
```

For those a test **owns its server**: `tests/e2e/own_server.py` gives it
a dedicated free port, a dedicated `VIBE_HOME` and its own pid file, and
`owned_agent_stack` adds what a server running real agents needs (the
shared CI server's agent capacity, syncs and telemetry off), points the
helpers at it (`e2e_helpers.point_at_server` — target, worker profile
and SSE auto-answer) and hands the shared server's values back on
teardown.

Ownership replaced a cross-worker `flock` around the catalog sync. A lock
serialises two drivers of ONE tree; it never separates the trees, and a
locked test still sees another worker's knowledge file land between its
two syncs. `trigger_catalog_sync` now takes the base URL and refuses the
shared server outright — same rule as the lock ("never drive a sync
another worker can see"), enforced by naming the server instead of
waiting for it.

`tests/unit/test_e2e_harness_guard.py` holds the inventory: it lists
those surfaces and fails any `tests/e2e` module that touches one without
owning its server. The one exemption is a module that skips itself
without `MOCK_CLI` — those run only in the `e2e-mock-cli` job, which is
one pytest process with no second worker (the guard pins that too, so
parallelising that job re-opens the question).

A module-scoped fixture is **per worker**: if xdist splits a module's
tests across two workers, each worker starts its own server and its own
home. That is the point of owning one — the two runs cannot see each
other's catalogs — at the cost of one more boot.

What an owned server deliberately takes from the shared install, and why:

- **The built frontend.** Every server on the checkout serves the same
  `frontend/dist`, and an owned server never rebuilds it
  (`VIBE_SKIP_FRONTEND_BUILD=1`): `pnpm build` empties `frontend/dist`
  before writing it, so a rebuild makes the shared server answer 404 to
  other workers' UI tests.
- **The agent CLI.** A server spawns `claude` from its own home's
  `node_modules`, else from `PATH`. An owned home is a fresh temp dir with
  no install, so the owned server gets the shared server's CLI directory
  first on its `PATH`, named by the product's own
  `resolve_claude_binary()`; `start.sh` keeps the incoming `PATH`. In CI
  the CLI exists only in the shared home, so without this no agent on an
  owned server can start.

## Stopping a test server

Stop a test/demo/e2e server on a side port with
**`./stop.sh <port> --scoped`** (also `./restart.sh <port> --scoped`).
Scoped stops only the server that listens on that port and its own
child processes. The default `./stop.sh <port>` is deliberately
broader — it also kills anything merely *connected* to the port (a
local proxy relaying to it holds a socket there) and reaps every
instance's leftover task agents machine-wide — which is right for
cleaning up a crashed production server and wrong for a throwaway one
next to other people's work. `tests/unit/test_stop_sh.py` pins the
scoped contract.

## Sync scheduler knobs

Nothing under `tests/` polls an outside host on its own.
`tests/conftest.py` sets `VIBE_GITHUB_MAIN_SYNC=off` and
`VIBE_EXTERNAL_SYNC=off` for every non-e2e run, and CI sets both on its
e2e servers: the jobs that pull from GitHub `main` (skills, knowledge,
provider presets, vision catalog) or call another host (model pricing,
the ads service, kie.ai) stay **registered with no timer** — a manual
`POST /api/sync/{name}/run`, `trigger()` or an event trigger runs the
real code path. Mail polling and the daemon reaper have no switch and
tick everywhere. The e2e that test the sync jobs start their own server
with both switches on (`own_server.SYNC_SWITCHES_ON`), against a fake
GitHub. Two more knobs exist for tests that want a schedule:

| Variable | Meaning |
|----------|---------|
| `VIBE_SYNC_CADENCE_OVERRIDE` | `name=seconds,...` — compress named jobs (`skills=20,presets=30`) |
| `VIBE_SYNC_JITTER` | `0` zeroes the deterministic per-install stagger, so a compressed cadence fires on the nose |

e2e runs (`--e2e`) leave the knob alone, because their server is
pointed at a fake GitHub (`tests/fixtures/fake_github.py`, started
standalone and read back over `/__counts`). See
`tests/e2e/test_sync_scheduler_e2e.py` and
`tests/releases/test_upgrade_sync_state.sh`.

## Upgrade tests (release gates)

Three reusable workflows gate `publish-testpypi` in `release.yml`; each
also runs on its own (`workflow_dispatch`), and the first two on PRs that
touch their surface.

| Workflow | Script / steps | Proves |
|----------|----------------|--------|
| `cli-upgrade.yml` (ubuntu + macOS) | `tests/releases/test_cli_upgrade_restart.sh` | `vibe-seller upgrade` restarts the running server: builds the checkout as 99.0.1/99.0.2, `uv tool install`s the first, starts it, runs the real upgrade, and requires the command to report the server on 99.0.2, a **second boot** in the daemon log, and `vibe-seller stop` still working |
| `cli-upgrade.yml` (ubuntu + macOS) | `tests/releases/test_auto_update.sh` | In-app auto-update (app/auto_update.py): a started OLD server reaches `ready` on its own via the background prefetch (mock PyPI JSON + `UV_FIND_LINKS`), the warm cache is proven with a `UV_OFFLINE=1` re-resolution, a member's restart gets 403, the admin's restart lands the server on NEW with a second daemon-log boot and an idle `update-status` afterwards |
| `sync-upgrade.yml` | `tests/releases/test_upgrade_sync_state.sh` | an older release's sync bookkeeping migrates into `sync_state` |
| `windows-upgrade.yml` | inline pwsh | the native installer upgrades in place, including a silent upgrade over a running server that must come back on its own under a new PID |

Assert on a **restart**, not only on `/api/version`: that endpoint
once re-read the package metadata from disk, so an un-restarted server
reported the new version (see [subsystems.md](subsystems.md#upgrade-restart)).
The CLI test counts uvicorn's per-boot `Application startup complete`
line in the daemon log (the in-place restart keeps the PID, so a PID
check cannot tell); the Windows test, where the installer relaunches
a new process, checks the PID.
To see either restart test fail, build the parent commit's code — both
versions are built from the same checkout, so the checkout's own
`vibe-seller upgrade` is the one under test.

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
- `windows` — Windows-specific tests (process management, venv wrappers). **Deselected by default**; run with `pytest --windows` or automatically on Windows (`os.name == 'nt'`). Cross-platform logic also has plain `unit` tests (`tests/unit/test_platform.py`) that run everywhere. The native Windows installer is built + smoke-tested on a `windows-latest` runner (`.github/workflows/windows-installer.yml`).

## Guidelines

- **Philosophy**: If a test doesn't catch a real feature break, delete it.
- **New feature**: Add a workflow test that exercises the API round-trip.
- **Bug fix**: Add a test that reproduces the bug first (fails), then fix (passes).
- **Contract tests**: When changing API response schemas, update `test_contracts.py` key sets to match TypeScript interfaces.
- **Workflow tests run in parallel in CI** (`pytest-xdist`, `-n auto --dist loadfile`), so each one must be xdist-safe.
  - **What each worker has to itself:** its own SQLite database file per test and its own throwaway `VIBE_HOME`.
  - **What a test must not do:** bind a fixed port, or write a fixed path outside `tmp_path`.
  - **Tests that depend on each other's order:** `loadfile` keeps a file's tests on one worker, so put such tests in the same file.
- **Waiting is an assertion.** `wait_for_task` raises when the status is not reached, so wait only for a state your scenario actually reaches.
  - A no-store task is forced into plan mode and parks at `planned`.
  - To wait for "finished, either way", pass `target=TERMINAL_STATUSES`.

## FakeAgent

Configurable via `FakeAgentScenario` (fields: `plan`, `result`, `todos`, `should_fail`, `fail_at_phase`, `complete_delay`, `tool_calls`, `thinking_text`). Located in `tests/workflow/fake_agent.py`.

- `tool_calls` — list of `{'tool': 'Read', 'input': {...}}` dicts; persisted as `tool_use` role messages before plan
- `thinking_text` — string persisted as `thinking` role message before plan

**Critical rule**: Don't mock away critical pipelines (like `_auto_run_task`) — use FakeAgent instead.

## Fake Anthropic server

`tests/fixtures/fake_anthropic_server.py` is a stdlib, deterministic fake of the Anthropic Messages API (`POST /v1/messages`, streaming and non-streaming) for driving the **real** Claude Code CLI without a model or network. Point a profile's `ANTHROPIC_BASE_URL` at it; the requested model name selects the behaviour:

- `fake-instant` — reply immediately
- `fake-delay-<SECONDS>` — hold the request, then stream a short reply (a slow but healthy model)
- `fake-hang` — accept and never answer (a dead provider)
- `fake-tool-then-delay-<SECONDS>` — one tool call, then the slow reply (slow compose after a `tool_result`)
- `fake-silent-after-delta` — `message_start` + one delta, then silence (a stream that dies mid-body)

Run it with `python tests/fixtures/fake_anthropic_server.py PORT`. It is how the stall reaper's live scenarios were verified (slow first token survives; a dead stream ends within the bound).

## E2E Test Infrastructure

### CI approval gate

LLM-dependent e2e spends provider credits, so on PRs it does not start on
its own: `ci.yml`'s `e2e-test` and `windows-installer.yml`'s `build` wait
on an `e2e-approval` job bound to the `e2e` environment. If a PR's e2e sits
in "Waiting", a reviewer must open the run page and click **Review
deployments → e2e → Approve and deploy** (this approves the test run, not
the PR). Each new push waits again. Nightly and `workflow_dispatch` runs
skip the gate. Mock CLI e2e (`e2e-mock-cli`) never needs approval.

Which models CI runs on is repository configuration, not code. The
repo variable `E2E_PROVIDER_MAP` names one preset per worker, and CI runs
one worker per entry; `E2E_SMOKE_PROVIDER` names the one preset the
single-provider smoke tests use (Windows installer, release). Each
preset's key and model are the secrets `{NAME}_API_KEY` /
`{NAME}_MODEL` (optionally `{NAME}_BASE_URL`), e.g. `QWEN_TOKEN_MODEL`.
Change them under Settings → Secrets and variables → Actions.

### Shared Helpers (`tests/e2e/e2e_helpers.py`)

Consolidated helpers used by all LLM-dependent e2e tests:

- `login(client)` — authenticate httpx client
- `get_task(client, task_id)` — fetch task state
- `get_messages(client, task_id)` — fetch task messages
- `create_task(client, title, *, store_id, description, profile_id, plan_mode)` — create task with logging
- `create_store(client, name)` — create store
- `poll_task_status(client, task_id, targets, *, fail_statuses, timeout)` — poll until target status (returns on fail_status for caller to decide)
- `answer_question(client, task_id, request_id, answers, base_url=None)` — submit answer for pending question
- `get_secret(*keys)` — resolve env var secrets

The helpers all read one module-global target, `BASE_URL`, at CALL time.
`point_at_server(base_url)` moves that target — plus the worker's
provider profile (`setup_worker_profile`) and an SSE auto-answer — onto
a server the module owns, for the duration of the module, and restores
it afterwards; it refuses the shared server's port. `login_client`
(login with retries), `fetch_presets` and `answer_question` take an
explicit `base_url` too, for the helpers shared between modules — the
catalog trigger lives in one module and is driven by another, each
against its own server.

### Running e2e against YOUR server (local development)

By default the suite logs in as the seeded admin
(`admin@vibe-seller.local` / `admin`) — what CI provisions on a clean
`~/.vibe-seller`. On a box where that password has been changed, every
e2e fails at fixture setup with a 401, because `api_client` authenticates
before any test body runs.

Point the suite at your own already-running server and account instead:

```bash
E2E_ADMIN_IDENTIFIER=<your username or email> \
E2E_ADMIN_PASSWORD=<your password> \
  pytest tests/e2e/test_ad_declaration_scoping.py --e2e
```

Both variables are required together — a half-configured override falls
back to the seeded admin rather than logging in as someone you did not
intend. Unset, behaviour is byte-identical to before, so **CI needs no
new secrets**; it keeps using the seeded admin and its existing LLM keys.

Two caveats when running this way:

- You are driving a **real** server with **real** agent credits, against
  whatever stores that server has. Tests create their own ephemeral
  stores (`e2e-*`), but they run beside your real ones.
- `./restart.sh` and `pytest` must not run at the same time — they
  contend, and the suite writes to the same `logs/backend_<port>.log` you
  read for diagnostics.

### Asserting on a real agent without pinning its habits

`test_ad_declaration_scoping.py` drives a real agent against
`fake_ads_console.py`, an in-process stand-in ad console serving TWO
campaigns, and asks about ONE. It is the template for "did the model
respect a boundary?" tests, and the split it makes is the point:

- **Assert on the stub's access log, not the agent's prose.** The
  console records every page it served, so "the excluded campaign was
  never opened" is ground truth. A report can describe restraint it did
  not exercise; the server cannot be talked around.
- **Assert mechanism conditionally.** The test checks the
  `vibe_seller_declare_ad_task` record only *if one exists* — the
  declaration requirement rides on the ad skills, which a localhost stub
  does not oblige a model to load. CI on `glm-4.7` produced a correctly
  scoped review that declared nothing; failing on that would pin one
  model's habits instead of the contract. Absent is fail-safe (no
  declaration → no widening obligation inferred); **wider than asked**
  is the regression, and that is asserted whenever a declaration is
  present.
- **Give each test its own stub.** The fixture is function-scoped: the
  whole-store test visits both campaigns, and a shared server would leave
  that visit in the log for the scoped test to trip over — only when
  xdist happens to land them on the same worker.

The same split drives the other real-agent suites:
`test_ad_tsv_contract.py` checks an audit and its execution against the
fake console's write log, the TSVs and the frozen approval
(`data/approved_changes/`), and `test_knowledge_upkeep.py` checks that a
stale store note is rewritten in place and that a task which learned
nothing leaves knowledge untouched.

### `api_client` Fixture (conftest.py)

Module-scoped fixture providing an authenticated httpx client with a **background SSE listener** that auto-answers any `AskUserQuestion` from any task. Prevents tests from hanging when LLM agents ask unexpected questions during planning or execution.

- **Scope**: `module` — one SSE thread per test file
- **Auto-answer**: replies "Please proceed." to all `task_questions` SSE events
- **Reconnect**: SSE stream auto-reconnects on disconnect
- **Teardown**: force-closes SSE stream to break blocking read, joins thread

Tests that use `api_client` get question handling automatically — no per-test SSE boilerplate needed.

### Parallel Execution (pytest-xdist)

E2E tests run in parallel via `pytest-xdist`, one worker per `E2E_PROVIDER_MAP` entry:

```bash
E2E_PROVIDER_MAP=minimax,glm pytest tests/e2e/ --e2e -n 2 --dist load --maxschedchunk 1 -v
```

- **`--dist load --maxschedchunk 1`, and longest test first.** The controller hands the head of the collection to whichever worker frees up (xdist still keeps two tests pending per worker), so collection order is start order — and `tests/e2e/conftest.py` sorts the tier by the measured seconds in `tests/e2e/durations.json`, longest first. A 6-worker run measured 4880s of work in 1344s of wall time (four workers idle for the last ~600s) because two ~400s tests started at 910s; ordered longest-first the same durations finish in ~814s, the floor. Regenerate the file after a green CI run:
  ```bash
  gh api repos/zpoint/vibe-seller/actions/jobs/<e2e-test-job-id>/logs > /tmp/e2e.log
  .venv/bin/python scripts/e2e_durations.py /tmp/e2e.log
  ```
  `--dist worksteal` cannot use that ordering: it cuts the collection into contiguous chunks up front and steals only from another worker's tail, so a long test late in a chunk still starts late. `--dist loadgroup` is not an option to lean on either — its `@group` nodeid rename does not take effect on pytest 9.2 (pytest-xdist #1386), and no test here carries the mark.
- **Order must be identical in every worker.** Each worker runs the collection itself (the controller never does) and xdist aborts the run when the lists differ, so the sort key is measured seconds and then collection position — no clock, no set iteration, nothing per-worker.
- **Each worker is its own process** with its own Playwright browser and httpx clients. A module-scoped fixture therefore runs once **per worker**, not once per module: when xdist splits a module's tests over two workers, each worker does its own setup — which for a module that owns a server means a server and a home each (`tests/e2e/own_server.py`), so they cannot see each other's state.
- **A worker's servers are stopped before it ends.** `OwnServer` books every server it starts, and a session-scoped fixture checks them after the module fixtures have finalised: a port still answered or a leftover pid file fails the run. It skips the shared server's port — that one is meant to outlive a session.
- Most tests share the one server started outside pytest; a test that touches server-global state starts **its own** server — see [Server-global state: own a server, don't lock one](#server-global-state-own-a-server-dont-lock-one).
- CDPMuxProxy enables concurrent browser tasks without interference

### Concurrency Environment Variables

| Variable | Layer | Default | Purpose |
|---|---|---|---|
| `E2E_PROVIDER_MAP` | Test runner | (empty) | Comma-separated provider per worker (e.g., `kimi,kimi,minimax`); the number of entries is the number of xdist workers (CI and the Docker runner pass `-n` from it). Each worker creates a profile for its assigned provider so tasks are spread across APIs. |
| `MAX_AGENT_CONCURRENCY` | Server (`app/ai/claude_backend.py`) | 2 | Max simultaneous Claude CLI agent subprocesses. Semaphore gate — exceeding this blocks new tasks until a slot opens. |

**Relationship**: `MAX_AGENT_CONCURRENCY` must be ≥ 2× the number of `E2E_PROVIDER_MAP` entries to avoid test timeouts from semaphore contention. CI runs one worker per `E2E_PROVIDER_MAP` entry (a repo variable) with `MAX_AGENT_CONCURRENCY=16`; tests that need two models (profile switch, compaction) take the first two *distinct* providers from the map.

**The CI e2e run stops at the first failure** (`--maxfail=1`): every test spends real provider credits, so a red run should not run the rest of the tier. When several independent failures are expected (a large harness change), drop the flag for that run to see them all at once.

**`--maxfail` under xdist** — pytest-xdist alone does not stop on `--maxfail`: each worker runs every test it was already handed before it shuts down, so an early failure drains the suite on fewer workers and can outlive the step timeout, which then kills the run before pytest prints the failure. `tests/e2e/fail_fast.py` makes it stop: from the `--maxfail`-th failure on, workers skip every test they have not started; tests already running finish.

### Log Tracing

With parallel tests, logs are interleaved. Trace by task ID:

- **Server side**: `AGENT_DEBUG [task_id]` prefix on every agent log line
- **Pytest side**: `e2e` logger; helper functions log `task_id` in messages so you can grep for it
- **Debug a failure**: `grep {task_id[:8]}` in both pytest output and server log
- **Owned servers log separately**: `start.sh` writes `logs/backend_<port>.log`
  and `logs/backend_stdout_<port>.log` in the checkout for each server a
  test owns. The CI artifact `e2e-server-logs` carries them next to the
  shared server's logs, so a failure in a test on its own server is
  diagnosable from CI.

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
