# Running E2E Tests Locally with Docker

## Prerequisites

- Docker with Compose v2
- API keys for your LLM providers (set as env vars)

## Quick Start — Full Suite

```bash
# Set provider credentials
export PROVIDER_A_API_KEY="sk-..."
export PROVIDER_A_BASE_URL="https://..."
export PROVIDER_A_MODEL="model-name"
export PROVIDER_B_API_KEY="sk-..."
export PROVIDER_B_BASE_URL="https://..."
export PROVIDER_B_MODEL="model-name"

# Run all E2E tests (3 workers, mirrors CI)
E2E_WORKERS=3 E2E_PROVIDER_MAP=provider_a,provider_a,provider_b \
  docker compose -f docker/docker-compose.yml run --rm e2e
```

The `E2E_PROVIDER_MAP` assigns one provider per xdist worker.
Provider names must match env var prefixes (uppercased):
`provider_a` reads `PROVIDER_A_API_KEY`, `PROVIDER_A_BASE_URL`,
`PROVIDER_A_MODEL`.

## Quick Start — Single Provider

```bash
export PROVIDER_A_API_KEY="sk-..."
export PROVIDER_A_BASE_URL="https://..."
export PROVIDER_A_MODEL="model-name"

E2E_WORKERS=1 E2E_PROVIDER_MAP=provider_a \
  docker compose -f docker/docker-compose.yml run --rm e2e
```

## Iterative Debugging (Recommended Workflow)

Running the full suite takes 10+ minutes. When debugging, run
small subsets for faster feedback (~1-3 min per batch).

### Step 1: Run a small batch

```bash
# Run a single test file (fastest iteration)
E2E_WORKERS=1 E2E_PROVIDER_MAP=provider_a \
  docker compose -f docker/docker-compose.yml run --rm e2e \
  uv run pytest tests/e2e/test_conversation_lifecycle.py -v --e2e --log-cli-level=INFO

# Run a single test method
E2E_WORKERS=1 E2E_PROVIDER_MAP=provider_a \
  docker compose -f docker/docker-compose.yml run --rm e2e \
  uv run pytest tests/e2e/test_task_execution.py::TestAgentPipeline::test_full_pipeline_completes \
  -v --e2e --log-cli-level=INFO
```

### Step 2: Read server logs after a run

Logs persist on the host at `logs/` (mounted volume). Each
run creates a timestamped file; `server_stdout.log` is a
symlink to the latest:

```bash
# Latest run
cat logs/server_stdout.log

# All runs
ls -lt logs/server_*.log
```

### Step 3: Read AGENT_DEBUG logs

The server runs with `AGENT_DEBUG=1` by default. Server logs
show every Claude CLI interaction tagged by task ID:

```
AGENT_DEBUG [c9db5d87] stdin=...    # What we sent to the agent
AGENT_DEBUG [c9db5d87] event=...    # What the agent returned
```

Grep by task ID prefix (first 8 chars, shown in test output):

```bash
# Inside the container or from server logs
grep 'c9db5d87' logs/server_stdout.log
```

### Step 4: Expand to more tests

Once your subset passes, expand gradually:

```bash
# Two related test files
E2E_WORKERS=1 E2E_PROVIDER_MAP=provider_a \
  docker compose -f docker/docker-compose.yml run --rm e2e \
  uv run pytest tests/e2e/test_task_execution.py tests/e2e/test_conversation_lifecycle.py \
  -v --e2e --log-cli-level=INFO

# Full suite with parallelism
E2E_WORKERS=3 E2E_PROVIDER_MAP=provider_a,provider_a,provider_b \
  docker compose -f docker/docker-compose.yml run --rm e2e
```

## Test Categories

| Test file | Type | Notes |
|-----------|------|-------|
| `test_task_execution.py` | API + LLM | Full agent pipeline |
| `test_conversation_lifecycle.py` | API + LLM | Stop/retry flows |
| `test_agent_sandbox.py` | API + LLM | MCP/skills isolation |
| `test_concurrent_tasks.py` | API + LLM | Multi-task concurrency |
| `test_profile_switch.py` | API + LLM | Needs 2 distinct providers |
| `test_compaction.py` | API + LLM | Needs 2 distinct providers |
| `test_llm_browser.py` | Playwright + LLM | Browser automation |
| `test_basic_flow.py` | Playwright UI | Store/task creation UI |
| `test_all_store.py` | Playwright UI | Store listing UI |
| `test_conversation_ui.py` | Playwright UI | Needs `MOCK_CLI` env |
| `test_session_reuse_profile.py` | API + LLM | Browser cookie persistence |
| `test_chrome_concurrent.py` | API + LLM | Chrome CDPMuxProxy concurrency |
| `test_ziniao_browser.py` | API + LLM | Needs Ziniao creds + `e2e-ziniao` service |

Tests requiring 2 providers will be **skipped** if
`E2E_PROVIDER_MAP` has fewer than 2 distinct entries.

## Worker Distribution & Provider Map

The order of providers in `E2E_PROVIDER_MAP` matters.
`pytest-xdist` assigns test files to workers round-robin
in alphabetical order. Some tests are sensitive to which
LLM provider runs them.

See [tests/e2e/PROFILING.md](../tests/e2e/PROFILING.md)
for the full timing profile and rationale for the current
provider map ordering.

## How Profiles Work

E2E tests create LLM profiles the same way the web UI does:

1. Fetch provider presets from `GET /api/profiles/presets`
2. Add `ANTHROPIC_AUTH_TOKEN` (the provider API key)
3. Create profile via `POST /api/profiles`
4. Set as default for the test worker

This means adding a new provider only requires adding a preset
in `app/ai/profiles.py` — no test changes needed.

## Ziniao Tests (Host Networking)

Ziniao tests need `network_mode: host` because the Ziniao
anti-detect browser runs on the host and exposes CDP on random
ports that the container must reach directly. Use the dedicated
`e2e-ziniao` service:

```bash
docker compose -f docker/docker-compose.yml run --rm e2e-ziniao
```

> **Note:** `network_mode: host` requires Docker Desktop with
> "Enable host networking" turned on (Settings → Resources → Network).
> Normal e2e tests do NOT need this — they run entirely inside the
> container.

## Docker Architecture

- **Entrypoint** installs deps as root, then drops to non-root
  user `vibe` for server + tests (Claude CLI requires non-root
  for `bypassPermissions` mode)
- **Playwright** system deps installed via `--with-deps` flag
- **Server** runs in background on port 7777 with `AGENT_DEBUG=1`
- **Network mode** — default (bridge) for `e2e`, host for
  `e2e-ziniao` only (Ziniao CDP ports need direct host access)

## Cleanup

```bash
docker compose -f docker/docker-compose.yml down -v
```
