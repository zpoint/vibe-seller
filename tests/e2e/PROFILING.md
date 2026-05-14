# E2E Test Profiling & Worker Distribution

## Why This Matters

E2E tests use real LLM providers. Different providers have
different reliability characteristics.

## Provider Reliability (observed 2026-03)

| Provider | Plan quality | Post-plan execution | Browser-use CLI |
|----------|-------------|---------------------|-----------------|
| MiniMax  | Good        | Reliable            | Follows syntax  |
| Kimi     | Good        | Flaky (see below)   | Unreliable      |

### Kimi `: ` prefix bug (workaround in place)

Kimi-k2.5 prepends `: ` to tool call parameters when processing
multi-step numbered instructions ("Step 1: ... Step 2: ...").
This affects **all tool types** — not just Bash:

| Tool | Symptom |
|------|---------|
| Bash | `command: ": \"echo hello"` → no-op |
| Write | `file_path: ": "`, `content: ":"` → ZodError |
| Read | `file_path: ": UUID\nWrite{...}"` → garbage path |

The circuit breaker catches the resulting loop and kills the agent.
Single-action tasks work fine; multi-step numbered instructions
trigger the bug.

**Workaround**: E2E task descriptions use single flowing sentences
instead of "Step N:" numbered patterns. Use Write/Read tool
references explicitly ("Use the Write tool to create...") rather
than imperative file operations ("Create /tmp/file.txt").

**Regression test**: `test_task_execution.py::TestKimiBashBug`
is marked `xfail` and documents the exact failure pattern.

## Worker Distribution

Tests use `-n 4` with default `--dist load` (round-robin).
All tests are provider-agnostic — no `xdist_group` pinning needed.

## Per-Test Timing (CI run #23712021696)

| Test | Time | Notes |
|------|------|-------|
| test_llm_browser::test_llm_responds | 9s | Direct API |
| test_llm_browser::test_llm_follows_instructions | 12s | Direct API |
| test_llm_browser::test_llm_reads_homepage | 12s | Playwright+LLM |
| test_llm_browser::test_llm_extracts_contact_details | 7s | Playwright+LLM |
| test_llm_browser::test_ai_navigates_site_via_gui | 81s | Full GUI agent |
| test_task_execution::test_progresses_beyond_pending | 34s | Store task |
| test_task_execution::test_full_pipeline_completes | 72s | Read+write files |
| test_task_execution::test_status_transitions | 15s | Store task |
| test_task_execution::test_without_store | 15s | No store |
| test_task_execution::test_question_answer_flow | 52s | Q&A flow |
| test_conversation_lifecycle::test_plan_then_execute | 271s | Full pipeline |
| test_conversation_lifecycle::test_stop_then_retry | 54s | Stop+retry |
| test_conversation_lifecycle::test_stop_then_continue | 292s | Stop+continue |
| test_conversation_lifecycle::test_question_answer | 67s | Q&A flow |
| test_basic_flow (3 tests) | 2s | Playwright UI only |
| test_all_store (2 tests) | 19s | Playwright UI only |
| test_profile_switch::test_same_profile_stop_retry | 139s | Stop+retry |
| test_profile_switch::test_profile_switch_retry | 97s | 2 providers |
| test_profile_switch::test_profile_switch_continue | 111s | 2 providers |
| test_concurrent_tasks::test_two_browser_tasks | ~105s | CDPMuxProxy |
| test_concurrent_tasks::test_different_platforms | ~60s | Concurrent bash |
| test_concurrent_tasks::test_different_stores | ~60s | Concurrent bash |
| test_concurrent_tasks::test_three_concurrent | ~60s | 3 concurrent |
| test_agent_sandbox | 9s | MCP/skills check |
| test_compaction | 764s | Profile switch+history |

## Adding New Tests

- Avoid Bash echo commands in task descriptions — Kimi
  mangles them. Use Write tool or Read tool instead.
- All tests auto-balance across workers, no grouping needed.
- Provider map is set in `.github/workflows/ci.yml` and
  `docker/docker-compose.yml`.
