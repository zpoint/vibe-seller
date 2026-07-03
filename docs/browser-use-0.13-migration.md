# browser-use 0.13 (CLI 3.0) Migration Plan

Status: **proposed** · Target: bump `browser-use` from `0.12.x` (subcommand
CLI) to `>=0.13.3` (the "CLI 3.0" / browser-harness heredoc interface)
**without breaking any already-released client** (v0.0.1 – v0.0.7).

---

## 1. What changed upstream

browser-use `0.13.3` (commit `f768a06c`, 2026-07-01) deleted the whole
subcommand CLI and its flags. The two interfaces:

| | 0.12.x (current) | 0.13.x (target) |
|---|---|---|
| Invocation | `browser-use open <url>` (subcommands) | `browser-harness <<'PY' … PY` (heredoc code-eval) or `browser-harness -c '…'` |
| Connection | `--cdp-url ws://…` flag | `BU_CDP_URL` / `BU_CDP_WS` env var |
| Session | `--session NAME` flag | `BU_NAME` env var |
| Actions | `open`/`state`/`click`/`input`/`eval`/… subcommands | pre-imported Python helpers: `new_tab()`, `page_info()`, `wait_for_load()`, `click()`, `start_remote_daemon(name)`, … |
| Profile / headed | `--profile` / `--headed` flags | env / daemon options |

The interaction model flips: the agent no longer runs individual shell
subcommands — it writes small Python snippets that the harness evals against
the connected browser. Connection identity moves from **argv flags** to
**environment variables**.

### Spike result — CONFIRMED against the 0.13.3 wheel

Inspected `browser_use-0.13.3-py3-none-any.whl` (entry_points.txt, `cli.py`,
bundled `browser_use/skills/browser-use/SKILL.md`, `beta/service.py`):

- **Binary name is STABLE.** 0.13.3 still ships `console_scripts`:
  `browser-use`, `bu`, `browser`, `browseruse`, `browser-use-tui`, all →
  `browser_use.cli:main`. There is **no separate `browser-harness` binary**
  in the package (browser-harness is a distinct upstream repo the CLI shells
  out to). ⇒ `REAL_BU` resolution (`.../bin/browser-use`) still works after
  upgrade; the wrapper's own filename can stay `browser-use`.
- **CLI is a stdin/`-c` code-eval shim.** No subcommands (`cli.py` has zero
  argparse/click commands). Usage: `browser-use <<'PY' … PY` or
  `browser-use -c '…'`. Helpers are pre-imported; the run harness calls
  `ensure_daemon()` before `exec`.
- **Helpers** (from bundled SKILL.md): `new_tab(url)` (**first navigation**,
  not `goto_url`), `page_info()`, `capture_screenshot()`,
  `click_at_xy(x, y)`, `wait_for_load()`, `ensure_real_tab()`, `js(...)`,
  `cdp("Domain.method", ...)` (raw CDP), `start_remote_daemon(name)` /
  `stop_remote_daemon(name)`.
- **Env vars (ground truth):** `BU_CDP_URL` (http) and `BU_CDP_WS`
  (websocket) select the CDP endpoint to attach to — **this is how we point
  it at `CDPMuxProxy`**; `BU_NAME` names the session/daemon. Browser config
  moved to env too: `BU_BROWSER_DOWNLOADS_PATH`,
  `BU_BROWSER_ALLOWED_DOMAINS`/`BU_BROWSER_PROHIBITED_DOMAINS`,
  `BU_MANAGED_BROWSER_PROFILE`, `BU_BROWSER_VIEWPORT`, etc. `BH_*`
  (`BH_AGENT_WORKSPACE`, `BH_DOMAIN_SKILLS`, `BH_CLIENT`) drive the harness
  workspace/skills. `browser-use --doctor` still exists; `--mcp` still
  supported.
- **Upstream ships a skill** at `browser_use/skills/browser-use/SKILL.md` —
  adapt it into `app/skills_v2/browser-harness/SKILL.md` (add our wrapper
  path, cross-store isolation, aux session; strip cloud/remote-daemon bits
  we don't use).

**Canonical mapping:**
```
# 0.12.x
browser-use --session {slug}-{id} --cdp-url ws://proxy/client-{id} open URL
# 0.13.x
BU_NAME={slug}-{id} BU_CDP_WS=ws://proxy/client-{id} browser-use <<'PY'
new_tab("URL")
PY
```

---

## 2. Why old clients are the hard constraint (and how the sync works)

Every installed client, before each task, runs
`skills_sync.check_and_sync_remote()` (`task_runner_auto.py:121`). That:

1. Polls GitHub for the latest commit touching a **hardcoded path**:
   `…/commits?path=app/skills&sha=main` (`skills_sync.py:395`).
2. Downloads `MANIFEST.txt` + listed files from a **hardcoded base URL**:
   `https://raw.githubusercontent.com/zpoint/vibe-seller/main/app/skills`
   (`config.py:127`).

Both `app/skills` references are **baked into the released binary** — we
cannot change where an old client looks.

Two protections exist, but only partially:

- **Local-package precedence guard** (`skills_sync.py:568-573`): remote sync
  never overwrites a file that ships in the client's own package. So editing
  an *existing* skill file on `main` is invisible to clients **that have the
  guard**.
- **But the guard only exists in v0.0.3+.** `git tag`: v0.0.1 (2026-05-14)
  and v0.0.2 (2026-05-15) predate the guard (added in `2d6d65b`, #3,
  2026-05-17). For those, an in-place rewrite of
  `app/skills/browser-use/SKILL.md` **would** be pulled over their bundled
  copy and break them against their pinned 0.12.x binary.

Also note: **prompts, wrapper code, and Python modules are never synced** —
only `app/skills` and `app/knowledge` cross the network. So changing
`wrapper.py`, `app/prompts/*.md`, `task_runner_context.py`, etc. is
automatically safe for old clients (they only get those via a new pip
release). The *entire* backward-compat risk surface is: **files reachable
under the `app/skills` (and `app/knowledge`) path.**

### Design consequence

- `app/skills/` (and its `browser-use/SKILL.md`) is **frozen forever** as the
  0.12.x legacy tree. Old clients keep pulling compatible content; pre-guard
  clients are safe because nothing 0.13-flavored ever appears at that path.
- CLI-3.0 skills live at a **new path old clients never poll**: **`app/skills_v2/`**.
- All future skill development happens in `app/skills_v2/`. The legacy tree
  is write-once-frozen, so there is **no ongoing double-maintenance** — the
  "duplication" is a one-time copy.

---

## 3. Target design

```
app/skills/           # FROZEN legacy tree (0.12.x subcommand docs)
  DEPRECATED.md        #   <- new top-level note; NOT a dir, NOT in MANIFEST → inert for sync
  MANIFEST.txt         #   unchanged
  browser-use/…        #   unchanged (0.12.x)
  amazon-ads/… etc.    #   unchanged
app/skills_v2/        # NEW tree (0.13 heredoc/env-var docs)  <- new releases bundle + pull this
  MANIFEST.txt         #   own manifest
  browser-harness/…    #   rewritten core skill (renamed from browser-use to match upstream)
  amazon-ads/… etc.    #   full copy, browser-driving docs rewritten to 0.13
```

New releases repoint **all three** `app/skills` references to `app/skills_v2`:

| Reference | File:line | Change |
|---|---|---|
| Local bundled source | `skills_sync.py:116` `_get_local_source` | `files('app') / 'skills'` → `'skills_v2'` |
| Remote download base | `config.py:125-128` `SKILLS_REPO_URL` | default `…/main/app/skills` → `…/main/app/skills_v2` |
| Remote commit-poll path | `skills_sync.py:395` | `path=app/skills` → `path=app/skills_v2` |

That's the whole hosting split. An old client keeps hitting `app/skills`
(frozen, compatible); a new client hits `app/skills_v2` (0.13). They never
cross.

> Consider parameterizing the skills dir name once (a `SKILLS_SUBDIR`
> constant) so all three references read from one place, rather than three
> literals — cheaper to reason about and to test.

---

## 4. Code changes (installed-package side — safe for old clients)

### 4.1 Version pin — `pyproject.toml:22-31`
Replace the `<0.13` cap and its rationale comment with `browser-use>=0.13.3`
(or `>=0.13,<0.14`). Regenerate `uv.lock`.

### 4.2 Wrappers — the shape flips from flags to env + heredoc passthrough
- `app/browser/wrapper.py` (`write_browser_use_wrapper`) and
  `app/browser/web_wrapper.py` (`write_web_browser_use_wrapper`):
  - **Stop** emitting `--session`/`--cdp-url` flags and the
    `open`/`state`/`close` subcommands (aux probe/recycle lines
    `wrapper.py:132-154`, exec lines `:211-216`, `:274`; web `:239`,`:278`).
  - **Start** exporting `BU_NAME="$SESSION"`,
    `BU_CDP_WS="ws://127.0.0.1:{proxy_port}/client-${CLIENT_ID}"` (or
    `BU_CDP_URL`), then `exec browser-harness "$@"` so the agent's heredoc
    passes through.
  - **Validation** (the wrapper's other job — cross-store isolation):
    the arg-parser cases blocking `--cdp-url/--mcp/--connect/--profile`
    (`wrapper.py:347-387`, `web_wrapper.py:108-147`) and the session-format
    regex (`wrapper.py:391`, `web_wrapper.py:150`) must move from
    **flag inspection** to **env enforcement**: reject an agent-supplied
    `BU_NAME`/`BU_CDP_*`, force the server-chosen values. This keeps the
    per-store isolation invariant — now expressed over env, not argv.
  - The aux-session override rule (`{slug}-aux`) carries over as an allowed
    `BU_NAME` value.

### 4.3 Daemon identification — the thorniest change
`0.13` daemons won't carry `--session`/`--cdp-url` in argv, so **every
cmdline-grep breaks**:
- `app/browser/daemon_reaper.py:31-78` (`--cdp-url` UUID + `--session` prefix regexes)
- `app/ai/claude_backend.py:212-237` (`_cleanup_browser_daemons` pgrep patterns)
- `app/browser/manager.py:56-67` (`--session …-aux` aux-kill regex)

Two ways to restore task↔daemon mapping:

- **(Recommended — design fix) Own the mapping in the CDP mux proxy.**
  We already route every task through `CDPMuxProxy` at
  `ws://…/client-{task_id}`. The proxy therefore *already knows* the
  task↔connection map with no cmdline parsing. Move reaping/aux-kill to ask
  the proxy which client owns a task and close that connection, instead of
  grepping `/proc`. This makes the identity contract typed and single-owner
  (aligns with CLAUDE.md "fix from design, not symptom").
- **(Fallback) Identify via env, not argv.** Launch daemons with
  `BU_NAME={slug}-{task_id[:8]}` and read `psutil.Process().environ()`
  instead of `cmdline()`. Cross-platform caveat: `.environ()` needs
  same-user (we have it) and can be slower/restricted on macOS.

Pick one before touching the three files; the proxy route removes an entire
bug class (cmdline parsing races).

### 4.4 Bash safety — `app/ai/bash_safety.py:700-716`
`check_bid_value_shape` parses `browser-use input <idx> "<val>"`. In 0.13 a
bid is set inside a heredoc Python call. Re-target the check to the new form
(inspect the heredoc body / helper call), or the concatenated-bid guard goes
silently dead.

### 4.5 Prompt / context injection (bundled, not synced → safe)
Rewrite the CLI examples to 0.13 heredoc form in:
- `app/task_runner_context.py:120-236` (Ziniao dual-browser, non-Ziniao "run
  browser-use CLI" guidance, Ziniao auto-fill login flow)
- `app/workspace/templates.py:16-27` (`WORKSPACE_CLAUDE_MD`)
- `app/prompts/reflection.md:50`, and any `dual_browser.md` CLI snippets

### 4.6 Knowledge docs — `app/knowledge/common/*` ⚠️ SYNCED — DECIDED: version-neutral
`ziniao-browser.md`, `amazon-sites.md`, `noon-sites.md`,
`ziniao-block-page.md` contained `browser-use open/state/click` examples,
**and knowledge syncs over the network with the same guard/pre-guard caveat**
(`knowledge_sync.py`, `path=app/knowledge`).

**Decision (no `knowledge_v2` split): keep `app/knowledge` version-neutral.**
Knowledge describes *sites* (URLs, page quirks, block-page cues, login
gotchas) — the CLI syntax was incidental. We rewrote the ~28 CLI examples
into version-neutral action prose ("navigate with the browser-use skill",
"read the page", "click Sign in") that points at the skill for exact
syntax. A version-neutral tree is safe for **every** client (0.12 and
0.13, guarded and pre-guard) — nothing to break — so one tree serves all,
avoiding the dual-maintenance of a `knowledge_v2`. The 0.13 helper syntax
lives solely in `app/skills_v2/browser-harness/SKILL.md`.

---

## 5. The new `app/skills_v2/` tree (full parallel — chosen scope)

1. Copy every skill dir from `app/skills/` into `app/skills_v2/`.
2. Rename `browser-use/` → `browser-harness/` (match upstream; update
   frontmatter `name`, `allowed-tools: Bash(browser-harness:*)`), and rewrite
   `SKILL.md` from the subcommand table to the heredoc helper API
   (`new_tab`, `page_info`, `click`, `start_remote_daemon`, …) plus the
   wrapper's env-injection contract.
3. Rewrite all browser-driving docs to 0.13 heredoc syntax. Bulk lives in:
   `amazon-reports/SKILL.md` (L25-892), `amazon-ads/references/mechanics.md`
   (L222-2642), `amazon-shared`, `noon-*`, `review-collect`,
   `amazon-listing/references/*`. Every `~/.vibe-seller/bin/<slug>/browser-use
   … open` example → the new wrapper + heredoc form.
4. Write `app/skills_v2/MANIFEST.txt` (same relative-path-per-line format,
   listing the v2 files; `browser-harness/SKILL.md` replaces
   `browser-use/SKILL.md`).
5. Add `app/skills/DEPRECATED.md` (top-level file → inert for both sync
   tiers) pointing to `app/skills_v2`.

---

## 6. Tests

Update the fixtures that assert the old wrapper shape (they encode the
contract, so they must flip with it):
- `tests/unit/test_browser/test_browser_use_wrapper.py` — assert env
  injection (`BU_NAME`/`BU_CDP_WS`) and env-based blocking instead of
  `--session`/`--cdp-url`.
- `tests/unit/test_browser/test_manager.py:92-141,333` — new exec/env shape.
- `tests/workflow/test_daemon_reaper.py` — fake daemons carry the new
  identity (env or proxy map), not `--cdp-url`/`--session` cmdlines.
- `tests/unit/test_ad_execution_fidelity.py:350-356` — new bid-shape input.
- Add a test pinning the **hosting split**: `_get_local_source` →
  `app/skills_v2`, `SKILLS_REPO_URL` default ends `/app/skills_v2`, commit
  path is `app/skills_v2`; and a regression test that `app/skills/` still
  contains the 0.12.x `browser-use/SKILL.md` (freeze guard).

---

## 7. Docs

- `docs/browser.md`, `docs/subsystems.md`, `DESIGN.md`, `CLAUDE.md:109` —
  update wrapper/daemon descriptions to the env-var + heredoc model.
- **CLAUDE.md — document the upgrade pattern** (new subsection under
  "Claude Workflows"):

  > ### Skill hosting across release lines
  > `app/skills/` is **frozen** as the legacy tree for clients on
  > browser-use 0.12.x. It is the path every released binary hardcodes for
  > sync (`config.py` `SKILLS_REPO_URL`, `skills_sync.py` commit-poll
  > `path=app/skills`) — never repurpose it, and never add files there that
  > assume a newer runtime (pre-v0.0.3 clients lack the local-precedence
  > guard and will pull them). Current skills live in `app/skills_v2/`; new
  > releases point `_get_local_source`, `SKILLS_REPO_URL`, and the commit
  > path at it. For any future breaking runtime change, freeze the current
  > tree and add `app/skills_vN+1/` rather than editing in place. Same rule
  > applies to `app/knowledge`.

---

## 8. In-place server upgrade path (single install: 0.12 → 0.13)

Separate from old-vs-new *clients* (§2): what happens when **one existing
install upgrades in place** (`stop → uv tool upgrade → start`).

### 8.1 Is browser-use actually upgraded? — **yes, forced**
`browser-use` is a hard dependency in `pyproject.toml`. A pin of `>=0.13.3`
cannot be satisfied by an installed 0.12.x, so `uv tool upgrade vibe-seller`
/ `pip install -U` **must** re-resolve and pull 0.13.x. Two caveats:
- **Entry-point rename — NOT a problem (spike-confirmed).** 0.13.3 still
  ships the `browser-use` console script, so the wrapper's embedded
  `.../bin/browser-use` path (`wrapper.py:98-104`) keeps resolving after
  upgrade. A *stale* 0.12 wrapper still breaks — it execs the (now 0.13)
  `browser-use` with `--session … open URL` args the shim rejects — but it
  self-heals per task (§8.2) and we wipe it on boot (§8.4a).
- **Skewed upgrade.** If the package upgrades but browser-use somehow
  doesn't (user-pinned browser-use, offline), new code emits 0.13 wrappers
  against a 0.12 binary → silent break. Guard with a startup version check
  (§8.4c).

### 8.2 Stale wrapper scripts — **self-heal, with a small window**
`write_task_browser_config` (`manager.py:571`) regenerates the correct
wrapper on **every** task launch (`task_runner_exec.py:89,578`;
`task_runner_auto.py:114`). So the first task per store after upgrade
rewrites `~/.vibe-seller/bin/{slug}/browser-use` with the new shape + fresh
binary path. **No migration needed for the common path.** The only gap is
the window before the first task, if the stale wrapper is invoked
out-of-band (debug-store skill, a lingering daemon's self-heal). Close it in
§8.4a.

### 8.3 Leftover daemons / CDP proxy across restart
- **CDP mux proxy:** runs **in-process** (asyncio server inside the FastAPI
  process; `cdp_mux_proxy.py:143`), so it dies with the server on shutdown —
  no orphan proxy process. Proxy ports persist in the DB and are reused on
  restart; `cleanup_stale_sessions` (`manager.py:197`) nulls them on boot.
- **browser-use daemons (separate processes):** graceful shutdown does
  `agent_manager.stop_all()` (`main.py:248`); boot does
  `cleanup_stale_sessions()` → `reap_orphaned_daemons()` (`main.py:156`).
- **⚠️ Reaper cross-version blind spot.** `reap_orphaned_daemons` /
  `_cleanup_browser_daemons` identify daemons by cmdline `--cdp-url` /
  `--session` (0.12 signatures). The daemons left over from the *pre-upgrade*
  server ARE 0.12 daemons with those flags. If we rewrite the reaper to
  recognize only the new (env/proxy) identity, the first boot after upgrade
  **will not reap the pre-upgrade daemons** — they orphan. **Requirement:
  the new reaper must recognize BOTH old (`--cdp-url`/`--session`) and new
  identities for at least one upgrade cycle.** Keep the 0.12 regexes as a
  legacy branch (`daemon_reaper.py:31-78`, `claude_backend.py:212-237`,
  `manager.py:56-67`).

### 8.4 Recommended boot-time safety measures (small, not a separate script)
Fold into the existing `cleanup_stale_sessions()` boot path:
- **(a) Wipe stale wrapper scripts** — on boot, delete
  `~/.vibe-seller/bin/*/browser-use` and `bin/_web/browser-use`. They are
  regenerated per-task anyway; deleting eliminates the §8.2 window and any
  chance of exec-ing a wrapper that points at a removed binary.
- **(b) Reaper recognizes old+new signatures** — see §8.3 (also reaps
  pre-upgrade 0.12 daemons one last time).
- **(c) Startup version assertion** — read the installed browser-use version
  (or probe the binary once) and verify it matches the interface the code
  emits (>=0.13). On mismatch, log loudly and mark browser tasks degraded
  rather than silently generating broken wrappers. Moves the code↔binary
  contract into a runtime check (per CLAUDE.md "fix from design").

These live in the installed package (not synced), so they only affect the
upgrading install — never an old client.

### 8.5 Multiplatform (macOS + Windows) — the upgrade must work on both
The codebase already centralizes platform differences in `app/platform.py`
(`IS_WINDOWS`/`IS_MAC`/`IS_LINUX`, `find_processes_by_pattern`,
`kill_process`, `safe_chmod`, `venv bin = Scripts|bin`); the reaper/manager
use it (psutil, not `ps`/`os.kill`). Reuse it — don't add platform branches.

- **Wrappers are bash on both.** On Windows the server runs under **WSL2**
  (`docs/windows-setup.md`), so `~/.vibe-seller/bin/{slug}/browser-use` is a
  bash script executed inside WSL. Env-var injection (`BU_NAME=… BU_CDP_WS=…
  exec browser-use`) is identical bash on macOS and Windows/WSL — no `.bat`/
  `.ps1` variant needed. The `winchrome` backend only changes *where Chrome
  renders* (native Windows via Task Scheduler); the `browser-use` CLI + its
  CDP attach still run in WSL, so the wrapper/env path is unchanged.
- **⚠️ Daemon identity: env-reading is NOT portable → use the proxy map.**
  0.13 daemons carry identity in the `BU_NAME` **env var**, not argv, so
  `find_processes_by_pattern` (matches *cmdline*) can't see it. Reading env
  via `psutil.Process().environ()` works on Windows/Linux but **raises
  `AccessDenied` on macOS** even for same-user processes. This makes the
  §4.3 **proxy-owned task↔connection map the required cross-platform
  design** (not merely the cleaner one) — the mux proxy already holds the
  `client-{task_id}` mapping in-process, no env/cmdline scraping. Keep the
  legacy cmdline reaper (§8.3) only for one-cycle cleanup of pre-upgrade
  0.12 daemons, which *do* carry `--session`/`--cdp-url` in argv on all OSes.
- **Boot wipe + version check** use `pathlib` / `importlib.metadata` —
  inherently cross-platform.
- **Test the upgrade on both**: a macOS `launchd` install and a
  Windows/WSL2 install — verify wrappers regenerate, no orphaned daemons,
  version assertion passes (§9 phase 5).

## 9. Phasing

0. **Spike** — install 0.13.3, confirm binary name + env var names (§1).
1. **Hosting split** — create `app/skills_v2/` (copy), repoint the three
   references, add freeze test + `DEPRECATED.md`. No behavior change yet.
2. **Wrapper + daemon identity** — flip `wrapper.py`/`web_wrapper.py` to env
   injection; pick the proxy-owned mapping and rewire reaper/backend/manager
   (**keeping old-signature recognition**, §8.3); update their tests. Verify
   end-to-end against a real 0.13 binary.
3. **Boot-time upgrade safety** — add §8.4 (a) stale-wrapper wipe, (b)
   old+new reaper signatures, (c) version assertion.
4. **Skill + prompt rewrite** — rewrite `browser-harness` skill, then the
   per-marketplace docs; update prompts/templates/knowledge.
5. **Bump pin + lock**, run full `pytest -m "unit or workflow"`, e2e a live
   store task **and an in-place 0.12→0.13 upgrade** (verify wrappers
   regenerate, no orphaned daemons).

## 10. Open items to confirm before coding
- **Spike result** — binary/entry-point + exact env var names (§1).
- **Daemon identity** — proxy-owned map (recommended) vs env-read (§4.3);
  either way keep old-signature reaping for one upgrade cycle (§8.3).
- **Knowledge tree** — does `app/knowledge` need the same v2 split, or strip
  CLI snippets from synced knowledge? (§4.6)
- **Aux/`web` session semantics** under `BU_NAME` (naming collisions across
  concurrent tasks on the same store).
