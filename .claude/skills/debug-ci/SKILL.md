---
name: debug-ci
description: Debug CI failures by downloading artifact logs, reading test code, and tracing agent LLM thinking to find root causes
---

# CI Debug

Debug CI test failures by analyzing artifact logs, test code, and agent behavior.

## Core Principle

**We own the project, not just the PR.**

Every CI failure has a root cause in code, config, or design — not in
"flakiness." Even if a failure predates your current changes, you are
responsible for finding the root cause and fixing it. Never dismiss a
failure as "unrelated to this PR" or "intermittent" — those are
descriptions of symptoms, not conclusions. The conclusion is the root
cause and the fix.

Workarounds like increasing timeouts, adding retries, or loosening assertions
are NOT acceptable. These mask the real problem. Find and fix the design flaw
that the test exposed.

### Fix from design, not from symptom

**Before writing any fix, review the design that produced the bug.**
A failing test is one observation of a design that allowed a failure
to be possible. Patching the observation doesn't change what's
possible — only changing the design does.

Ask in this order, every time:

1. **What contract was violated?** Which invariant did the failure
   prove was unenforced? E.g., "the agent's `email_watermark` value
   must be a recent epoch" was an *assumption*, not an *invariant*,
   so the agent's wrong value was accepted.
2. **Where does the design make the bug possible?** Find the surface
   that lets a wrong value through — usually a missing guard, a
   non-deterministic input, or a contract expressed only in prose.
3. **Move the contract into code.** The fix should make the bug
   class impossible to recreate from this surface, not just unlikely.
   Server-side validation, typed APIs, ownership invariants, single
   coroutine ordering — these are design fixes. Retries, sleeps, and
   "let's just hope the model gets it right next time" are not.
4. **Only after the design fix, ask:** are tests pinning this
   invariant now? If not, add them. The test is the second line of
   defence, not the fix itself.

Symptoms that mean you skipped the design pass and went straight to
a workaround:

- "Add a retry so the model has another chance" — design says: take
  the freedom away. Make it impossible to be wrong.
- "Loosen the assertion to accept the wrong value" — design says:
  the wrong value is wrong; reject it at the boundary.
- "Bump the timeout so the slow path makes it" — design says: why is
  the slow path slow? What invariant is it waiting for?
- "The agent should follow the prompt better" — design says: prompts
  are advisory; if the model can violate the contract, the contract
  has to be enforced server-side.
- "Pin the version / suppress the warning / catch the exception" —
  these hide the symptom, not the cause.

If the natural fix touches a file the PR didn't, fix it anyway. The
goal is "this bug class can't recur," not "this PR is green."

## Mandatory Steps (in order)

### Step 1: Get the failure details

```bash
# Get the failing job ID
gh api repos/{owner}/{repo}/commits/{sha}/check-runs \
  --jq '.check_runs[] | "\(.name): \(.conclusion // .status) \(.id)"'

# Get the failure message
gh api repos/{owner}/{repo}/actions/jobs/{job_id}/logs 2>&1 \
  | grep "FAIL\|E   \|assert" | head -20
```

### Step 2: Download artifact logs (MANDATORY)

**You MUST download and read the server logs before any conclusion.**
CI test output alone is insufficient — the real story is in the server
logs (agent debug events, API calls, task state transitions).

```bash
# Find the run and artifact
run_id=$(gh api "repos/{owner}/{repo}/actions/runs?per_page=5" \
  --jq '.workflow_runs[] | select(.head_sha | startswith("{sha}")) | .id' \
  | head -1)

art_id=$(gh api "repos/{owner}/{repo}/actions/runs/$run_id/artifacts" \
  --jq '.artifacts[] | select(.name=="e2e-server-logs") | .id')

# Download and extract
cd /tmp && gh api "repos/{owner}/{repo}/actions/artifacts/$art_id/zip" \
  > ci-logs.zip && unzip -o ci-logs.zip -d ci-logs
```

Available artifacts:
- `e2e-server-logs` — backend_7777.log + server_stdout.log (for e2e-test)
- `mock-cli-server-logs` — same structure (for e2e-mock-cli)

### Step 3: Read the test code

Read the actual test file to understand:
- What the test creates (stores, tasks, files)
- What it triggers (API calls, schedule triggers)
- What it asserts (file existence, task status, content)
- What it waits for (polling loops, timeouts)

### Step 4: Trace the agent behavior in logs

For e2e tests involving LLM agents, trace the full agent lifecycle:

```bash
# Find the task ID
grep "AGENT_DEBUG.*{test_identifier}" /tmp/ci-logs/backend_7777.log \
  | grep "stdin.*Design\|stdin.*Update" | head -3

# Get agent thinking
grep "AGENT_DEBUG \[{task_id}\]" /tmp/ci-logs/backend_7777.log \
  | grep '"thinking"' | sed 's/.*"thinking": "//;s/", "signature.*//' \
  | sed 's/\\n/\n/g'

# Get tool calls
grep "AGENT_DEBUG \[{task_id}\]" /tmp/ci-logs/backend_7777.log \
  | grep '"name":' | grep -o '"name": "[^"]*"'

# Get result
grep "AGENT_DEBUG \[{task_id}\].*result.*success\|.*result.*error" \
  /tmp/ci-logs/backend_7777.log | tail -1
```

### Step 5: Build timeline

For timeout/race failures, build an exact timeline:

```bash
# Key events with timestamps
grep "Fan-out\|task_update.*completed\|AGENT_DEBUG.*result" \
  /tmp/ci-logs/backend_7777.log | grep "{relevant_pattern}" \
  | cut -d' ' -f1-2
```

Compare against test timeouts and polling intervals.

### Step 6: Identify root cause category

Every failure maps to ONE of these:

| Category | Signal | Fix Location |
|----------|--------|-------------|
| **Race condition** | Test sees stale state, timing-dependent | Test polling logic or event ordering |
| **Platform difference** | Works on macOS, fails on Linux | Symlink resolution, path handling |
| **Model behavior** | Agent uses wrong tool, wrong path, hallucinates | Prompt wording, MCP tool design |
| **Resource exhaustion** | SIGSEGV, timeout after N iterations | Fixture scope, process cleanup |
| **Design flaw** | Symlink + Write restriction, plan mode overhead | Architecture (workspace isolation, task mode) |
| **API/tool bug** | Tool returns success but doesn't persist | MCP server, workspace manager |

### Step 7: Fix the design, not the symptom

**You MUST produce a code fix. Re-triggering CI is a workaround, not a fix.**

- **Race condition** → Fix the event ordering or state machine, not the timeout
- **Platform difference** → Use platform-agnostic APIs (MCP tools), not path hacks
- **Model behavior** → Provide the right tool (MCP) so the model can't go wrong
- **Resource exhaustion** → Fix fixture lifecycle, not retry count
- **Design flaw** → Fix the architecture, not the test

If the root cause is in code you didn't touch in this PR, fix it anyway —
include it in this PR or create a separate commit. The goal is zero
failures on the next run because you fixed the bug, not because the
timing was different.

## Anti-patterns (do NOT do these)

- Increasing `PIPELINE_TIMEOUT` to hide slow tasks
- Adding `time.sleep()` to work around race conditions
- Loosening assertions (`assert x or y` when only `x` should be true)
- Retrying failed assertions in a loop
- Marking tests as `@pytest.mark.skip` or `@pytest.mark.xfail`
- Claiming "flaky" or "intermittent" without a root cause explanation
- Dismissing failures as "unrelated to this PR" — we own the project
- Making conclusions from test output alone (without server logs)
- Re-triggering CI without understanding why it failed

## E2E-Specific Patterns

### Catalog sync test
- Triggers global `_catalog_sync` → creates L2 + L3 tasks for ALL stores
- Test waits for ALL tasks to complete, not just test stores
- L2 phase must complete before L3 tasks are created (race window)
- Agents write via MCP `write_workspace_file` (not built-in Write)

### Browser tests
- Chrome sessions managed by CDPMuxProxy
- `browser-use open` must succeed on first call (retry = infra bug)
- Browser fixtures create/destroy Chrome processes

### Agent task tests
- Tasks go through: pending → queued → designing/running → completed
- Plan mode: agent plans first, then executes (2 phases)
- Auto mode: agent executes directly (1 phase)
- `vibe_seller_set_task_error` MCP tool records an error message +
  category but does NOT transition status — status changes are
  owned by `_auto_run_task` cleanup (which sees `task.error` and
  fails the task)
- `vibe_seller_set_task_result` MCP tool records a custom result
  summary but does NOT change status — status transitions are
  owned by `_auto_run_task` after the agent session exits

## Local Runner Access

If CI runs on local GitHub Actions runners (Docker):

First, resolve the current repo name yourself — GitHub Actions
checks code out to `_work/{repo}/{repo}/`, so the path depends on
the active repo. Either:

```bash
REPO=$(basename "$(gh repo view --json url --jq .url)")
# or, if gh isn't available:
REPO=$(basename -s .git "$(git config --get remote.origin.url)")
```

Then use `$REPO` in the path:

```bash
# Find which runner has the test
for r in mac-mini-1 mac-mini-2 mac-mini-3 mac-mini-4; do
  docker exec $r bash -c "ls /home/runner/actions-runner/_work/$REPO/$REPO/logs/backend_7777.log 2>/dev/null && echo $r" 2>/dev/null
done

# Read live logs
docker exec {runner} bash -c "tail -50 /home/runner/actions-runner/_work/$REPO/$REPO/logs/backend_7777.log"
```
