# Browser Management

Pluggable browser backend system for launching and managing browser instances per store.

## Architecture

```
BrowserBackend (abstract)     BrowserManager (singleton)
    ├── ZiniaoBackend             auto-launches & connects via CDP proxy
    └── CustomCDPBackend (planned)

Both Chrome and Ziniao stores use CDPMuxProxy for shared browser access
and cookie persistence. BrowserManager generates a browser-use wrapper
script per store that connects via the proxy.

Agent interaction: browser-use CLI wrapper scripts at
~/.vibe-seller/bin/{slug}/browser-use (not MCP)
```

## Files

### `base.py` — Abstract Interface

Defines the contract all browser backends must implement:

```python
@dataclass
class BrowserSessionInfo:
    cdp_port: int | None = None     # CDP port (if applicable)
    pid: int | None = None          # Process ID (if applicable)
    browser: Any = None             # Playwright Browser object
    ws_endpoint: str | None = None  # WebSocket endpoint (if applicable)

class BrowserBackend(ABC):
    async def start(self, browser_config: dict) -> BrowserSessionInfo
    async def stop(self, info: BrowserSessionInfo) -> None
```

### `manager.py` — Browser Manager

Singleton that orchestrates browser sessions across all stores:

- `start_session(store, db)` — Launches browser (Ziniao only), creates/updates `BrowserSession` DB record, generates wrapper script
- `stop_session(store, db)` — Closes browser, updates DB, removes wrapper script
- `ensure_session(store, db)` — Start or reuse a session, always regenerates wrapper
- `write_mcp_config(store, db)` — Generates browser-use wrapper WITHOUT starting browser (used at task launch)
- `get_cdp_port(store_id)` — Returns CDP port if available
- `remove_wrapper(store_name)` — Removes a store's browser-use wrapper

**Per-store isolation**: Each store gets its own browser-use wrapper script at `~/.vibe-seller/bin/{slug}/browser-use`. browser-use 0.13 removed the subcommand CLI (`open`/`state`/`click`) — the agent now drives the browser by piping Python helpers via a heredoc (`browser-use <<'PY' … PY`), and connection identity moved from flags to **environment variables**. The wrapper enforces session isolation: it auto-assigns the per-task session, accepts only `--session {slug}-aux` as an override (mapped to `BU_NAME`), blocks `--cdp-url`/`--mcp`/`--connect`/`--profile` flags plus any agent-supplied `BU_*` env var, and injects the session name (`BU_NAME`, was `--session`) and CDP endpoint (`BU_CDP_WS`, was `--cdp-url`) as env vars — along with `BH_RUNTIME_DIR`/`BH_RUNTIME_DIR_SHARED` for daemon state. An asyncio lock serializes `start/stop`.

**ASCII-only slug**: `store_slug(name, store_id)` in `app/browser/manager.py` strips any character outside `[A-Za-z0-9_-]` and collapses separators. Pure-ASCII names keep their existing slug; names that reduce to empty (e.g. entirely CJK) fall back to `store-<store_id[:8]>`. This constraint comes from browser-use 0.13's `BU_NAME` validation (`browser_harness` `_check`, regex `[A-Za-z0-9_-]{1,64}`) — a non-ASCII session name is rejected before the daemon ever starts. Callers that have a `Store` object must pass `store.id` as the second argument so the fallback is available. The one-shot `scripts/migrate_store_slugs.py` renames any on-disk directory whose legacy slug differs from the new one.

**Per-task session names**: Each task gets its own browser-use daemon session: `{slug}-{VIBE_TASK_ID[:8]}` (e.g., `my-store-a1b2c3d4`). Combined with CDPMuxProxy isolation (request ID rewriting, session-based event routing, target filtering), this enables concurrent tasks within a single shared browser. Without `VIBE_TASK_ID` (e.g. manual use), falls back to the bare slug.

**Ziniao guard**: Only one Ziniao account can be active per machine. `_start_session_locked` checks `_active_ziniao_account_id` — if a different account is already active, it raises `RuntimeError` with the names of conflicting stores. Chrome stores have no such account restriction (but still use CDPMuxProxy for shared browser and cookie persistence).

> **Multiple stores of the SAME account run concurrently** (verified against the official Ziniao demo). Ziniao's `startBrowser` is flaky (nondeterministic per-store "stale launch"); recovery is **per store** (`stopBrowser` + retry), never a global client kill — a global kill destroys every other store's live browser and cascades. See [docs/ziniao-concurrency.md](ziniao-concurrency.md) for the full mechanism, root-cause investigation, and fix.

**Wrapper script generation**: `write_browser_use_wrapper()` in `app/browser/wrapper.py` generates a bash script per store. Session validation uses a regex check (`^{slug}(-aux|-{8hex})?$`). For both Chrome and Ziniao stores, the wrapper auto-starts the CDP proxy and injects `BU_CDP_WS=ws://…/client-{task_id}` (the 0.13 replacement for `--cdp-url`) pointing at CDPMuxProxy. EVERY session — aux included, both backends — gets an explicit `BU_CDP_WS` on its own store's proxy: per-task sessions use `client-{task_id}`, aux uses the stable `client-aux`. (The former Ziniao-aux "Chrome direct" exemption exported no endpoint; `browser_harness`'s ambient discovery then attached to a *different store's* browser — wrong Amazon account, wrong downloads dir — observed live with two Ziniao stores. Wrapper format v3 removed it.) The auto-start block checks the browser start API's HTTP status and exits with a clear error on non-2xx responses. A final CDP readiness check after the poll loop catches cases where the browser started but the proxy isn't ready.

**Wrapper safety — the agent must never touch a local Chrome**: the agent's `PATH` prepends the store `bin/<slug>` dir, so bare `browser-use` resolves to the wrapper. Three layers keep it from ever falling through to the real `browser-use` binary (which would attach to the user's own Chrome):
1. **Written before start** — `write_browser_use_wrapper()` runs *before* `backend.start()` in `_start_session_locked`, so a failed/stale launch still leaves a working wrapper (its auto-start block retries `browser/start` on the next call).
2. **Version-aware boot wipe** — each wrapper embeds a monotonic `WRAPPER_FORMAT_VERSION`; `_wipe_generated_wrappers()` deletes only *older* wrappers and keeps current+newer, so a restart never leaves a wrapper-less window (and never nukes a newer version — rollback-safe).
3. **Guard** — `apply_agent_venv_path` puts a guard `browser-use` (`bin/_guard`) on `PATH` below the wrapper but above the venvs; if the wrapper is somehow missing, bare `browser-use` hits the guard and **exits non-zero** instead of reaching the real binary.

See [docs/browser-use-0.13-migration.md § wrapper-format versioning](browser-use-0.13-migration.md) and [docs/ziniao-concurrency.md](ziniao-concurrency.md).

**Dual browser (Ziniao)**: Ziniao stores get dual-session support in their wrapper — main session (`{slug}-{task[:8]}`) and aux session (`--session {slug}-aux`, mapped to `BU_NAME`) BOTH route through the store's own CDPMuxProxy; aux rides the stable `client-aux` proxy client so it never shares a task client's tab pool. No separate backend instance for aux. (This per-store `{slug}-aux` is distinct from the store-less `web` browser below.)

### Store-less web browser

No-store (orchestrator) tasks — those created in the general/all-stores task list with `store_id=None` — get a single generic Chrome browser that is **not tied to any store**, for neutral public web work (search, tracking/logistics/carrier pages, research). Store-specific seller-center work still requires delegating to a per-store sub-task.

- **Wrapper**: `write_web_browser_use_wrapper()` in `app/browser/web_wrapper.py` generates `~/.vibe-seller/bin/_web/browser-use` (reserved slug `_web` = `config.WEB_BROWSER_SLUG`, which `store_slug()` can never produce for a real store). Session names are `web` / `web-{VIBE_TASK_ID[:8]}`. Like the per-store wrapper it targets browser-use 0.13 — it injects `BU_NAME`/`BU_CDP_WS` env vars (not flags) and the agent drives it by piping Python via a heredoc. It is Chrome-only — no Ziniao routing, no `{slug}-aux` split — so it is a deliberately simpler sibling of the per-store wrapper (they share the env-injection + wedge-recovery logic conceptually; keep them in sync).
- **PATH**: `apply_agent_venv_path()` prepends `bin/_web` for no-store tasks (store tasks use `bin/{slug}`).
- **Lifecycle**: `BrowserManager.start_web_session()` launches one shared Chrome (`ChromeBackend`, keyed on `_web`) + CDPMuxProxy; per-task tab isolation is by `VIBE_TASK_ID` exactly like stores. The proxy port persists across restarts in `AppSettings['web_browser_proxy_port']` (stores use `BrowserSession.proxy_port`; the web browser has no such row). Started lazily by the wrapper via `POST /api/browser/web/start` on first use.
- **Isolation caveat**: the `_web` profile is shared and has no per-store IP masking or store login — the agent prompt hard-forbids opening any seller center or logging into store/platform accounts on it.

**Wedge recovery (timeout-bounded)**: A renderer can wedge (hang on the CDP handshake) while the browser process stays alive — every subsequent call against that daemon then hangs identically. browser-use 0.13 removed the subcommands the old self-heal relied on (`state`/`close`), so the wrapper no longer classifies calls or probes with a read-only subcommand. Instead, every proxy (non-aux) call is bounded by a hard timeout (perl `alarm` — macOS has no GNU `timeout`; the interval timer survives `execve` and `SIGALRM` kills the exec'd browser-use). On a wedge (rc 142) the wrapper reloads *this* session's daemon via `browser-use --reload` (scoped by `BU_NAME` → `browser_harness` `restart_daemon()`) and surfaces the failure — it does **not** auto-retry, because a 0.13 heredoc can mutate the page (click/type), so blindly re-running could double-apply. The agent re-issues on the reported error against the fresh daemon. Aux sessions run the same bounded self-heal path as every other session (wrapper format v3).

> **No URL-shape guard in 0.13**: under the old subcommand CLI, `browser-use open https://x.com/page?a=1&b=2` was silently destructive because the calling shell (zsh) parsed `?`/`&` before the wrapper saw it, so the URL arrived truncated; the wrapper detected that shape and errored. In 0.13 there is no `open`/`navigate` subcommand — the agent passes the URL *inside* the piped Python (a quoted string in the heredoc body), so the shell never sees the URL as a bare argument and this mangling class can't occur. The guard has been removed.

### `cdp_mux_proxy.py` — Multi-Client CDP Proxy (primary)

WebSocket-level CDP multiplexing proxy that allows multiple browser-use CLI processes (one per task) to share a single browser simultaneously. Replaces the old TCP relay for Ziniao stores.

**Architecture**: Single upstream WebSocket to the browser, multiple downstream client WebSockets. Each client connects via `ws://127.0.0.1:{port}/client-{task_id}`.

**Isolation mechanisms** (borrowed from [cdp-tunnel](https://github.com/dyyz1993/cdp-tunnel)):
1. **Request ID rewriting** — each client's `msg.id` is remapped to a global counter; responses route back to the correct client
2. **Session-based event routing** — CDP `sessionId` maps to the owning client; page events only reach the tab owner
3. **Target filtering** — `getTargets()` responses are stripped of other clients' targets; cross-client `attachToTarget`/`closeTarget` is blocked

**No browser-context isolation**: CDP's `Target.createBrowserContext` creates incognito-like contexts with a fresh cookie jar — cookies, localStorage, and login sessions from the default profile are **not** inherited (verified by test against Chromium). Since our tasks need the store's pre-logged-in sessions, we intentionally skip this mechanism and have all tasks share the default profile context. Isolation is enforced at the proxy level via the three mechanisms above instead.

**Key design details**:
- `_pending_attached_events` cache handles the race where `Target.attachedToTarget` arrives before `Target.createTarget` response (common with `setAutoAttach`)
- `Browser.close` is intercepted — cleans up client state without closing the browser
- Client disconnect (clean or crash) triggers `_cleanup_client()` which closes the client's tabs and removes all routing entries
- Startup cleanup closes orphan tabs left by prior server crashes
- Configurable `max_clients` (default 5) — a CDPMuxProxy connection limit per proxy instance, not a system-wide concurrency cap

**Upstream reconnect**: When the browser disconnects, the proxy sends error responses to all pending client requests, then attempts reconnection with exponential backoff (max 10 attempts, 1s→30s delay). Single-flight guard (`_reconnecting` flag, reset in `finally`) prevents concurrent reconnect attempts. After 10 failures, the proxy stops itself gracefully. `_reconnecting` is always cleared even if `_running` becomes false during reconnect.

**Client reconnect grace period**: When a client disconnects, its tabs are not cleaned up immediately. Instead, the client enters a configurable grace period (default 480s, controlled by the `cleanup_grace` constructor parameter) during which tabs are preserved. If the client reconnects within this window, its tabs are recovered and the client resumes where it left off. Only after the grace period expires without reconnection are the client's tabs closed and routing entries removed.

**Client identification**: The `VIBE_TASK_ID` environment variable is set by `ClaudeCodeBackend` when spawning the agent subprocess. The browser-use wrapper script reads it to construct the WebSocket URL `ws://127.0.0.1:{port}/client-{VIBE_TASK_ID}`, connecting directly (bypassing HTTP discovery). Falls back to a random UUID if not set.

**HTTP endpoints**: Serves `/json/version` and `/json/list` for browser-use CLI discovery, with `webSocketDebuggerUrl` rewritten to point at the proxy.

**Download path override** (`download_dir`): macOS sends SIGTERM to the browser-use daemon process after 3-5 min of background execution (PPID=1, stdout/stderr=/dev/null, no terminal). This is standard macOS behavior for idle background processes with no UI — we cannot prevent it. Each daemon restart creates a fresh `BrowserProfile` whose model validator generates a random `/tmp/browser-use-downloads-{uuid}/` directory and calls `Browser.setDownloadBehavior` with it. Because `setDownloadBehavior` is browser-wide (last writer wins), the download path changes on every restart, and files end up in an unpredictable temp dir. The proxy intercepts every `Browser.setDownloadBehavior` call and rewrites `downloadPath` to `~/.vibe-seller/downloads/{store-slug}/`, making downloads deterministic regardless of daemon restarts. Both `ZiniaoBackend` and `ChromeBackend` pass `download_dir` when constructing the proxy.

### `cdp_proxy.py` — Legacy TCP Proxy (fallback)

Simple async TCP relay: listens on `127.0.0.1:{listen_port}`, forwards raw bytes to `{target_host}:{target_port}`. No CDP protocol awareness — only supports a single client at a time. Kept as `CDPTcpProxy` fallback. On WSL, `target_host` is the Windows gateway IP (detected by `_ziniao_host()`).

### `ziniao.py` — Ziniao Backend

Anti-detect browser with HTTP API (getBrowserList, startBrowser, stopBrowser). Each profile has unique fingerprint/cookies/proxy.

- Default socket port: **16851**
- One Ziniao process per machine, multiple profiles (different `browserOauth`) on same account
- Auto-launches Ziniao client if not running (via `ziniao_utils.py`), except on WSL
- Each `startBrowser` call returns a unique `debuggingPort` → CDPProxy relays to it
- The browser-use wrapper connects to the proxy via the `BU_CDP_WS` env var (0.13; was `--cdp-url`), not directly to Ziniao

**API Documentation**: https://open.ziniao.com/docSupport?docId=147

#### Ziniao API Behaviors (tested 2026-03-17)

- **Single session per profile**: Calling `startBrowser` with the same `browserOauth` while a session is already open **replaces** the existing session. The old CDP port becomes dead, and a new debugging port is returned. Only one browser window is visible at a time per profile.
- **`stopBrowser` action**: Closes a browser session. Use action `stopBrowser` (not `closeBrowser`, which returns 404). Requires the same auth fields (`company`, `username`, `password`) plus `browserOauth`.
- **`startBrowser` response**: Returns `debuggingPort` (random each launch) and `statusCode: 0` on success.
- **`getBrowserList` action**: Lightweight probe to check if Ziniao is running and authenticated. Fast response, good for connectivity checks.

#### WSL Support

WSL cannot launch Ziniao automatically because Electron's Node.js V8 rejects unknown `--` flags (like `--run_type=web_driver`) before the app code runs. Instead:

1. If Ziniao is already running with the HTTP API on port 16851, it works seamlessly (WSL reaches it via the gateway IP)
2. If Ziniao is not running, the backend raises an error guiding the user to download and run `ziniao_webdriver.bat` on Windows
3. The launcher script is served at `GET /api/ziniao/launcher`

### `ziniao_utils.py` — Ziniao Utilities

Shared helpers used by both `ziniao.py` (task execution) and `ziniao_accounts` router (profile listing):

- `send_http(port, data)` — Send command to Ziniao HTTP API
- `ensure_ziniao_running(port, client_path, user_info)` — Check if running, auto-launch if not (or guide WSL users to bat launcher). Raises `ZiniaoNormalModeError` on Mac when Ziniao is in normal mode (not WebDriver)
- `get_ziniao_status(port, user_info)` — Returns structured `{status, platform}` dict for frontend display. Status values: `running_webdriver`, `no_permission` (-10003), `running_normal`, `not_running`, `not_installed`
- `kill_and_relaunch_ziniao(port, client_path, user_info)` — Mac only: SIGKILL Ziniao, poll for termination, relaunch in WebDriver mode, poll for API readiness
- `_is_ziniao_process_running()` — Cross-platform: `tasklist` on Windows/WSL, `pgrep -f 'ziniao.app/Contents/MacOS'` on Mac
- `_is_ziniao_installed_mac()` — Spotlight `mdfind` check for ziniao.app (handles /Applications and ~/Applications)
- `is_wsl()` — Detect if running under Windows Subsystem for Linux
- Platform-specific launch commands (Mac tested, Windows tested, WSL requires manual bat launch)

#### Mac-Specific Ziniao Behavior

On Mac, Ziniao runs as an Electron app at `/Applications/ziniao.app`. Two modes:

1. **Normal mode**: User double-clicks Ziniao. No HTTP API, no `--run_type=web_driver` flags. Process detectable via `pgrep -f 'ziniao.app/Contents/MacOS'`
2. **WebDriver mode**: Launched with `open -a ziniao --args --run_type=web_driver --ipc_type=http --port=16851`. Exposes HTTP API on the configured port

Key behaviors:
- `open -a ziniao` does NOT pass `--args` to an already-running instance — must kill first to switch modes
- `pkill` (SIGTERM) may not fully terminate Ziniao (daemon auto-restarts); SIGKILL (`pkill -9`) is required
- WebDriver permission error (`statusCode: -10003`) means the BOSS account hasn't enabled WebDriver on the Ziniao Open Platform. Guide: https://open.ziniao.com/docSupport?docId=99

## Browser Lifecycle — idle termination + tab cap

Browsers start lazily but historically were **never stopped**
(`stop_session`'s only caller was store deletion), so every launched
browser lived until reboot — accumulating one tab per agent navigation
(`new_tab` is the only primitive the skills use), ending in
thousand-tab windows. Two mechanisms bound this
(`app/browser/idle_sweep.py`, `VIBE_*` knobs in `env_options.py`):

- **Idle-browser sweeper** (1-min cron job): stops a store's main
  browser, its aux browser, or the store-less `web` browser when BOTH
  hold — no live task is bound to it (PENDING/QUEUED/DESIGNING/
  PLANNED/RUNNING; **WAITING does not hold** — a parked task can wait
  hours and the wrapper lazily restarts the browser on wake), AND its
  CDP mux reports no connected clients and ≥ `VIBE_BROWSER_IDLE_S`
  (default 300s; 0 disables) since the last activity. Activity = any
  client CDP message or upstream `Target.*` event (the latter also
  captures a HUMAN using the window). Termination uses the existing
  per-store-safe paths — `stop_session` / `stop_aux` — and never
  touches the shared Ziniao client (see docs/ziniao-concurrency.md).
- **Ziniao envs really stop now**: `ZiniaoBackend.stop()` sends the
  per-store `stopBrowser` captured at start (previously it only tore
  down the mux proxy and the Chromium env lived forever).
- **Per-client tab cap** (`VIBE_TAB_CAP`, default 12; 0 disables): the
  mux LRU-closes a client's oldest tab beyond the cap on every
  `Target.createTarget`, strictly within that client's ownership — a
  long task can no longer accumulate unbounded tabs mid-run. A closed
  old tab an agent revisits yields a normal target-not-found error it
  recovers from.

## Daemon Reaper

`app/browser/daemon_reaper.py` — periodic background service that
cleans up orphaned `browser-use` daemon processes.

Browser-use daemons fully detach from their parent process
(`ppid=1`, own `pgid`), so they are not killed when a task
stops or the server restarts.

browser-use 0.13 (`browser_harness`) spawns each daemon as
`python -m browser_harness.daemon` and records its identity in a
**PID FILE** — `<BH_RUNTIME_DIR>/bu-<BU_NAME>.pid` (+ `.sock`) — with the
session name in the `BU_NAME` env var, **not** in argv. So the reaper
reads identity from the pid files (portable; avoids macOS
`psutil.environ()` `AccessDenied`). It runs every 5 minutes:

1. Enumerates `bu-*.pid` files under `BH_RUNTIME_DIR` (via
   `iter_pidfiles()` in `bh_daemons.py`), each keyed on
   `BU_NAME = {slug}-{task_id[:8]}`
2. Cross-checks each pid file against the set of live
   `browser_harness.daemon` processes (guards PID reuse); deletes stale
   files whose daemon is gone
3. Extracts the 8-char task-id suffix from `BU_NAME`
   (`bu-{slug}-{id8}.pid`)
4. Queries DB for active tasks (pending/designing/running/etc.)
5. Kills any daemon whose task ID is not in the active set and removes
   its pid/sock files
6. Skips daemons without an identifiable task ID — bare `{slug}` /
   `{slug}-aux` / manual sessions

**Legacy 0.12 compat**: daemons from the old `browser_use.skill_cli.daemon`
(carrying identity in argv — full UUID from `--cdp-url client-{UUID}`,
8-char prefix from `--session {slug}-{id8}`) are still recognised for
**one in-place-upgrade cycle**, so the first boot after an upgrade reaps
the pre-upgrade daemons instead of orphaning them. `DaemonInfo` carries
`full_task_id`, `task_id_prefix`, and the 0.13 `cleanup_paths` (pid +
sock). 8-char prefix matching has ~1/4B collision chance per pair —
accepted as known limitation.

**Three-layer cleanup**:
1. **Per-task** (`_cleanup_browser_daemons()` in `claude_backend.py`): kills task-specific daemons on agent **stop** (not start, to avoid race conditions with fast retries). Calls `kill_bh_daemons(lambda name: name.endswith(f'-{tid8}'))` — matching the pid-file `BU_NAME` for this task's id8 (legacy `pgrep` on `browser_use.skill_cli.daemon` argv kept for upgrade compat)
2. **Periodic reaper** (`start_reaper_loop()` in `daemon_reaper.py`): background loop every 5 minutes, uses same orphan detection logic
3. **Server startup** (`cleanup_stale_sessions()` in `manager.py`): in-place-upgrade safety — warns if the installed browser-use is `< 0.13` (`warn_on_browser_use_version_mismatch()`), wipes stale auto-generated wrapper scripts (`_wipe_generated_wrappers()`, so a pre-upgrade 0.12-shaped wrapper is never invoked before the next task regenerates it), then calls `reap_orphaned_daemons()` to clean up daemons from the previous run (both 0.13 pid-file and legacy 0.12 argv) while preserving daemons for active tasks (e.g. WAITING tasks that survive restart)

**Design Rationale**:
Cleanup at `start()` was removed because it caused a race condition: if a task was retried quickly, the new session could start its daemon, then the cleanup from the new `start()` would kill the freshly-started daemon. Cleanup now only happens at `stop()` (guaranteed to kill the old daemon) and via the periodic reaper (for orphaned daemons when server restarts).

**Note**: Live-process discovery goes through `find_processes_by_pattern` and killing through `kill_with_escalation` (`process_utils.py`), both `psutil`-backed for cross-platform support.

## Adding a New Backend

1. Create `app/browser/mybackend.py` implementing `BrowserBackend` from `base.py`
2. Register in `BrowserManager._get_backend()` in `manager.py`

## Planned Backends

- **Custom CDP**: Connect to any browser already exposing a CDP endpoint. Start/stop are no-ops.
