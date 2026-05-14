---
name: debug-task
description: Debug agent task execution infrastructure issues — wrong cwd, wrong files, wrong venv, browser startup failures, agent retry loops, skill loading failures
---

# Debug Task Infra

Debug agent task execution infrastructure issues by analyzing agent logs.

## Core Philosophy

**Every agent failure, detour, or workaround is an infra/platform bug — not an agent problem.**

When an agent:
- Tries a wrong file path then searches for the right one → **catalog or path resolution bug**
- Uses wrong CLI syntax then self-corrects → **skill docs not loaded or incomplete**
- Takes a screenshot but can't find/view it → **tool integration bug**
- Retries a command with different args → **missing docs or unclear API**
- Reflects "no failures" when there were failures → **reflection prompt bug**

Even if the agent eventually succeeds, each detour:
1. Wastes tokens and time
2. Signals a gap in the platform that WILL hit other tasks
3. Must be traced to a specific infra root cause and filed as a bug

**Do NOT dismiss detours as "the agent will self-correct."** Your job is to
find every single failure step, trace it to a code/config/prompt root cause,
and report it.

## When to use

When a task execution shows symptoms like:
- Agent sees unexpected files or can't find expected ones
- Agent runs in wrong working directory
- Browser (Ziniao/Chrome) fails to start or takes multiple retries
- Task uses wrong Python environment
- Store context (bookmarks, browser config) missing from agent prompt
- Agent repeats same bash command 3+ times (infra issue, not agent fault)
- Agent repeats browser-open attempts (browser startup failure)
- Agent can't load skills (wrong skill path or skill not synced)
- Agent passes wrong arguments to tools (schema mismatch)
- Agent uses workarounds for things that should "just work"
- Agent's post-task reflection misses failures that clearly happened

## Debug methodology

### RULE: Read agent logs BEFORE any conclusion

**Every time this skill is invoked, you MUST read the actual agent debug
logs before making any claim about what happened.** Do not assume from
file existence, code reading, or task_messages alone. The agent debug
logs are the source of truth.

```bash
# 1. Find which run(s) happened — tasks can restart with different profiles!
grep "Starting agent.*{task_id}" logs/backend_7777.log

# 2. Get agent debug logs for the CORRECT run (match the date/time)
grep "AGENT_DEBUG.*{task_id}" logs/backend_7777.log | grep "{date}"

# 3. Find the transcript file (full session log, most detailed)
ls ~/.claude/projects/*{task_id}*/*.jsonl

# 4. Check if a skill body was actually loaded into context
#    (use strings unique to that skill's SKILL.md body)
grep -c "unique string from SKILL.md" <transcript.jsonl>
```

**Common mistakes to avoid:**
- Reading logs from the wrong run (task restarted with different profile)
- Checking transcript before the task finishes — skill loading may happen
  mid-task, not at start. Wait for task to complete before concluding.
- Assuming a skill is "loaded" because the file exists in the workspace
- Assuming `slash_commands` listing = skill content in LLM context (it doesn't —
  only metadata loads on discovery, body loads on trigger)
- Making claims about what the agent "saw" without transcript evidence
- Skipping the transcript file — it's the only way to confirm skill body loading

### Step 0: Pick the task to debug

If the user didn't specify a task ID, find the most recent task from the last hour:

```bash
sqlite3 ~/.vibe-seller/data/vibe_seller.db \
  "SELECT id, status, substr(result,1,100), substr(error,1,100) \
   FROM tasks WHERE created_at > datetime('now', '-1 hour') \
   ORDER BY created_at DESC LIMIT 5;"
```

If exactly one task was active in the last hour, debug that one.
If multiple, ask the user which one. If none, ask the user for a task ID.

### Step 1: Pull ALL task messages

Read every message — not just errors. Detours hide in `thinking` messages
where the agent says things like "let me try another approach" or "file
doesn't exist, let me search."

```bash
# Full task log — read ALL of it, not just tail
sqlite3 ~/.vibe-seller/data/vibe_seller.db \
  "SELECT seq, role, content FROM task_messages \
   WHERE task_id='{task_id}' ORDER BY seq;"
```

### Step 2: Walk through sequentially and flag EVERY detour

For each step, ask: "Did this step succeed on the first try?"

If not, classify the failure:

| Failure Type | Signal in Logs | Root Cause Category |
|---|---|---|
| Wrong file path → search/glob → find correct path | `Read` error → `Glob` → `Read` success | **Catalog/path resolution bug** |
| Hallucinated CLI command → error → correct syntax | `Bash` error with usage/invalid choice message | **Skill docs not effective enough** |
| Tool output not usable → workaround | Screenshot returns bytes, agent searches for .png | **Tool integration gap** |
| File doesn't exist but agent expected it | `Read` error "File does not exist" | **Catalog lists nonexistent file, or agent guessed** |
| Duplicate table headers in catalog | Agent reads garbled catalog | **Catalog generation bug** |
| Browser open retried (even once) | >1 `browser-use open` to same URL | **CDP proxy / Ziniao / wrapper script bug** |
| Agent says "no failures" in reflection | Contradicts actual log | **Reflection prompt doesn't force log review** |

### Step 3: For each detour, trace to root cause

Every detour must map to ONE of these:
1. **Catalog bug** — wrong path, missing `project/` prefix, duplicate headers, unlisted file
2. **Skill docs bug** — missing command, wrong syntax example, undocumented limitation
3. **Tool integration bug** — output not consumable, file not saved where expected
4. **System prompt bug** — unclear instructions, missing "ONLY read catalog files"
5. **Workspace bug** — missing symlinks, wrong isolation, files not synced
6. **Reflection prompt bug** — agent misses failures, doesn't save learnings

### Step 4: Report findings

For each bug, provide:
- **Seq range**: which message steps were wasted
- **What happened**: agent action → error → workaround
- **Steps wasted**: count of unnecessary steps
- **Root cause**: specific code/file/prompt that caused it
- **Fix location**: exact file and what to change

## Known bug patterns (from past investigations)

### Catalog path resolution
The L1 catalog (`app/knowledge/CATALOG.md`) lists files like `common/amazon-sites.md`.
These are synced to `~/.vibe-seller/knowledge/project/common/amazon-sites.md`.
But the store CATALOG.md copies L1 entries verbatim without adding the `project/` prefix.
From the agent's CWD, `knowledge/` symlinks to `~/.vibe-seller/knowledge/`, so the
correct relative path is `knowledge/project/common/amazon-sites.md` — but the catalog
says `common/amazon-sites.md`.

**Fix**: `_filter_l1_for_store()` in `app/workspace/knowledge_sync.py` should prepend
`project/` to L1 file paths in the store catalog, or the system prompt should tell the
agent the base path.

### Duplicate table header in store CATALOG
`_filter_l1_for_store()` returns a string starting with `| File | Relevance | Summary |`
header. If `_build_store_catalog()` also adds a header, or if the function is called
twice, the catalog gets a duplicate header row.

**Check**: `app/workspace/knowledge_sync.py` lines around `_build_store_catalog` and
`_filter_l1_for_store`.

### Agent hallucinates CLI commands (browser-use and others)
Agent invents CLI syntax that doesn't exist instead of using the commands
documented in the loaded skill. Examples seen in the wild:
- `browser-use scroll 10` (correct: `browser-use scroll down --amount 500`)
- `browser-use get text` without index (correct: `browser-use get text <index>`)
- `browser-use screenshot` without path then searching for .png files

**How skill loading works** (important for diagnosis):
Skills go through 3 stages — **discovery ≠ loading ≠ in context**:

1. **File exists** — `.claude/skills/` is **copied** (not symlinked) into the
   task workspace from `~/.vibe-seller/.claude/skills/`
   (`app/workspace/manager.py` ~line 710, `shutil.copytree`)
2. **Discovered** — Claude Code finds the SKILL.md via `--add-dir` and lists
   it in the init event's `slash_commands` array
3. **Content in LLM context** — the SKILL.md content is actually sent to
   the LLM as part of the prompt. **This is the step that matters and the
   step that's hardest to verify.**

**CRITICAL**: A skill appearing in `slash_commands` in the init event does
NOT prove its content is in the LLM's context window. Claude Code may
defer loading skill content until the skill is invoked or until a matching
`allowed-tools` pattern fires.

**IMPORTANT**: browser-use SKILL.md is an **upstream/official** doc from the
browser-use project. Do NOT modify it to fix agent behavior issues.

**Diagnosis steps** (in order of evidence strength):

1. Check the init event for the correct run (watch for task restarts!):
   ```bash
   # Find ALL starts — tasks can restart with different profiles
   grep "Starting agent.*{task_id}" logs/backend_7777.log
   # Then filter debug logs by the correct date/time
   grep "AGENT_DEBUG.*{task_id}.*system.*init" logs/backend_7777.log
   ```
   Look for `slash_commands` — does the skill name appear? If NO → file
   missing or `--add-dir` wrong → **infra bug**.
   If YES → skill was **discovered** but this does NOT mean body was loaded.

2. Check the **transcript file** (definitive evidence):
   Claude Code saves full transcripts at (replace `<user>` with your
   macOS / Linux username and `<repo>` with the repo dir):
   `~/.claude/projects/-Users-<user>-Desktop-<repo>-tasks-{task_id}/{session_id}.jsonl`

   Search for unique strings from the SKILL.md body:
   ```bash
   # Find the transcript file
   ls ~/.claude/projects/*{task_id}*/*.jsonl

   # Search for SKILL.md body content (use strings unique to the skill)
   # For browser-use: "browser-use doctor", "browser-use tunnel",
   # "Browser Automation with browser-use CLI"
   grep -c "browser-use doctor" <transcript.jsonl>
   grep -c "Browser Automation with browser-use CLI" <transcript.jsonl>
   ```
   If count is 0 → **skill body was NEVER loaded into context**.
   If count > 0 → skill body was loaded.

3. Check if agent ever explicitly Read the skill file:
   ```bash
   sqlite3 ~/.vibe-seller/data/vibe_seller.db \
     "SELECT content FROM task_messages \
      WHERE task_id='{task_id}' AND content LIKE '%browser-use/SKILL.md%';"
   ```

Per ctx7 docs, Claude Code skill loading is 3-stage:
- Stage 1: metadata (name + description) loads immediately on discovery
- Stage 2: SKILL.md body loads **when triggered by user queries**
- Stage 3: references/examples load on demand

If skill metadata is discovered but body never loads, the triggering
mechanism failed. Known causes:
- Duplicate skill discovery (task CWD is inside the git repo at
  `~/.vibe-seller/`, so Claude Code finds `.claude/skills/` at the
  git root AND in the task workspace copy) — check init event for
  duplicate `(project)` entries
- Skill directory structure issue (wrong path, symlink, missing files)
- Claude Code version-specific bugs in skill loading

**Classification**:
- Skill file missing from workspace → **infra bug** (workspace prep / skill sync)
- `--add-dir` not pointing to workspace → **infra bug** (claude_backend.py)
- Skill discovered but body not in context (transcript confirms) →
  **skill loading/triggering bug** (investigate Claude Code's trigger
  mechanism, possibly model-specific or language-specific)
- Skill body confirmed in context but agent ignored → **LLM behavior**

### Ziniao/browser-use open must succeed on first call
The infra handles all browser lifecycle: CDP mux proxy, wrapper scripts,
session management, auto-retry at the infra level. From the agent's
perspective, `browser-use open <url>` is a **one-shot operation** that
must always succeed on the first call.

**Any retry by the agent = infra bug.** This includes:
- Agent calls `browser-use open` more than once for the same URL
- Agent calls `browser-use state` and gets empty/error, then re-opens
- Agent sleeps and retries after open "just in case"

**Root causes** (all in infra, not agent):
- CDP mux proxy not routing correctly → `cdp_mux_proxy.py`
- Ziniao process not started before agent launch → `browser/manager.py`
- Wrapper script not injecting correct `--session`/`--cdp-url` → `~/.vibe-seller/bin/{store}/browser-use`
- Port conflict between concurrent tasks → CDP proxy isolation bug
- Stale browser profile → wrapper script or profile management

**Check**:
```bash
# Count browser-use open calls (should be exactly 1 per URL)
sqlite3 ~/.vibe-seller/data/vibe_seller.db \
  "SELECT content FROM task_messages \
   WHERE task_id='{task_id}' AND role='tool_use' \
   AND content LIKE '%browser-use open%';"

# Check wrapper script
cat ~/.vibe-seller/bin/{store}/browser-use

# Check CDP proxy state
lsof -i | grep -i cdp
```

### Agent reads files not in catalog
Agent guesses file names like `seller-info.md` that don't exist, despite having
read the CATALOG.md which lists only existing files. The system prompt says to
"Read the store catalog... Load only the files listed" but isn't forceful enough.

**Fix**: System prompt should say: "The catalog is the COMPLETE list of available
files. Do NOT attempt to read files not listed in it."

## Debug checklist (infrastructure)

### 1. Database state
```bash
sqlite3 ~/.vibe-seller/data/vibe_seller.db \
  "SELECT id, status, result, error FROM tasks ORDER BY created_at DESC LIMIT 5;"
```

### 2. Agent logs (backend logs)
Check `logs/backend_<port>.log` (e.g., `logs/backend_7777.log`):

```bash
# Real-time log following
tail -f logs/backend_7777.log | grep -Ei "error|fail|warn"

# Search for specific task
grep "task_id=xxx" logs/backend_7777.log
```

### 3. Working directory
```bash
ps aux | grep "claude.*{task_id}" | grep -oE "\-\-add-dir [^ ]+"
```
Expected: `~/.vibe-seller/tasks/{task_id}/`

### 4. Workspace isolation
```bash
ls -la ~/.vibe-seller/tasks/{task_id}/
ls -la ~/.vibe-seller/tasks/{task_id}/.claude/skills/
readlink ~/.vibe-seller/tasks/{task_id}/knowledge
```

### 5. Browser startup
```bash
sqlite3 ~/.vibe-seller/data/vibe_seller.db "SELECT * FROM browser_sessions;"
cat ~/.vibe-seller/bin/{store-slug}/browser-use
```

### 6. Store context injection
```bash
sqlite3 ~/.vibe-seller/data/vibe_seller.db \
  "SELECT substr(content,1,500) FROM task_messages \
   WHERE task_id='{task_id}' AND seq=1;"
```

## Key code paths

| File | Responsibility |
|------|----------------|
| `app/routers/tasks.py` | `_auto_run_task()`, context injection, `_build_store_context()` |
| `app/ai/claude_backend.py` | Process spawn, cwd, env vars, venv activation |
| `app/workspace/` | `workspace_manager.prepare_task_workspace()`, isolation |
| `app/workspace/knowledge_sync.py` | Catalog generation (L1/L2/L3), path resolution |
| `app/browser/manager.py` | Browser config, wrapper scripts, startup logic |
| `app/skills/` | Built-in skills, skill manifest, skill sync |
| `app/prompts/reflection.md` | Post-task reflection prompt for knowledge capture |
| `app/prompts/design_system.md` | Planning agent instructions, catalog usage |
| `~/.vibe-seller/` | Runtime: `data/`, `tasks/`, `.claude/skills/`, `bin/` |

## Common root causes

| Symptom | Likely cause | Fix location |
|---------|--------------|--------------|
| Wrong CWD | `prepare_task_workspace()` not used or cwd not passed | `claude_backend.py` |
| Wrong venv | `VIRTUAL_ENV` not set or wrong PATH | `claude_backend.py` spawn |
| Wrong files | Workspace not isolated, agent sees parent/other tasks | `workspace/` isolation |
| Browser fails | Startup race, port conflict, stale profile | `browser/manager.py` |
| Missing context | `_build_store_context()` returns empty/wrong | `tasks.py` context build |
| Skill not found | Skill not synced to `~/.vibe-seller/.claude/skills/` | `app/skills/sync.py` |
| Hallucinated CLI syntax | Skill body not loaded — agent didn't invoke `/skill` | System prompt must tell agent to load skill before use |
| Tool output not usable | Agent can't consume base64/binary output | Skill docs + tool integration |
| Browser open retry (even once) | CDP proxy, Ziniao startup, or wrapper bug | `cdp_mux_proxy.py`, `browser/manager.py`, wrapper scripts |
| Catalog path wrong | L1 paths missing `project/` prefix in L3 catalog | `knowledge_sync.py` |
| Duplicate catalog header | `_filter_l1_for_store` + `_build_store_catalog` double header | `knowledge_sync.py` |
| Agent guesses files | System prompt doesn't enforce catalog-only reads | `design_system.md` |
| Reflection misses failures | Prompt doesn't force sequential log review | `reflection.md` |

## Quick commands

```bash
# Latest backend errors
tail -100 logs/backend_7777.log | grep -Ei "error|fail|exception"

# Task workspace
find ~/.vibe-seller/tasks -type d -mtime -1 | head -5

# Running agents
ps aux | grep -E "claude|browser-use" | grep -v grep

# Browser sessions
sqlite3 ~/.vibe-seller/data/vibe_seller.db "SELECT * FROM browser_sessions;"

# Recent task messages with errors
sqlite3 ~/.vibe-seller/data/vibe_seller.db \
  "SELECT task_id, substr(content,1,150) FROM task_messages \
   WHERE content LIKE '%error%' OR content LIKE '%fail%' \
   ORDER BY created_at DESC LIMIT 10;"

# Check skill sync
diff -r app/skills/amazon-invoice ~/.vibe-seller/.claude/skills/amazon-invoice \
  2>/dev/null || echo "Skills differ or not synced"

# Check catalog paths resolve correctly
cat ~/.vibe-seller/stores/{slug}/CATALOG.md
ls ~/.vibe-seller/knowledge/project/common/  # L1 files land here
```
