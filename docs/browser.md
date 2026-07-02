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

**Per-store isolation**: Each store gets its own browser-use wrapper script at `~/.vibe-seller/bin/{slug}/browser-use`. The wrapper enforces session isolation: validates `--session` starts with the store slug, blocks `--cdp-url` and `--mcp` flags, and injects store-specific arguments. An asyncio lock serializes `start/stop`.

**ASCII-only slug**: `store_slug(name, store_id)` in `app/browser/manager.py` strips any character outside `[A-Za-z0-9_-]` and collapses separators. Pure-ASCII names keep their existing slug; names that reduce to empty (e.g. entirely CJK) fall back to `store-<store_id[:8]>`. This constraint comes from browser-use's upstream `validate_session_name` (regex `^[A-Za-z0-9_-]+$`) — a non-ASCII `--session` is rejected before the daemon ever starts. Callers that have a `Store` object must pass `store.id` as the second argument so the fallback is available. The one-shot `scripts/migrate_store_slugs.py` renames any on-disk directory whose legacy slug differs from the new one.

**Per-task session names**: Each task gets its own browser-use daemon session: `{slug}-{VIBE_TASK_ID[:8]}` (e.g., `my-store-a1b2c3d4`). Combined with CDPMuxProxy isolation (request ID rewriting, session-based event routing, target filtering), this enables concurrent tasks within a single shared browser. Without `VIBE_TASK_ID` (e.g. manual use), falls back to the bare slug.

**Ziniao guard**: Only one Ziniao account can be active per machine. `_start_session_locked` checks `_active_ziniao_account_id` — if a different account is already active, it raises `RuntimeError` with the names of conflicting stores. Chrome stores have no such account restriction (but still use CDPMuxProxy for shared browser and cookie persistence).

**Wrapper script generation**: `_write_browser_use_wrapper()` generates a bash script per store. Session validation uses a prefix check (`case $SESSION in {slug}|{slug}-*)`). For both Chrome and Ziniao stores, the wrapper auto-starts the CDP proxy and injects `--cdp-url` pointing at CDPMuxProxy. For Ziniao stores, aux sessions (`{slug}-aux`) are exempt from auto-start and CDP injection (they use Chrome directly). For Chrome stores, all sessions go through the proxy — no aux exemption. The auto-start block checks the browser start API's HTTP status and exits with a clear error on non-2xx responses. A final CDP readiness check after the poll loop catches cases where the browser started but the proxy isn't ready.

**Dual browser (Ziniao)**: Ziniao stores get dual-session support in their wrapper — main session (`{slug}-{task[:8]}`) routes to Ziniao via CDP proxy, aux session (`{slug}-aux`) uses Chrome directly. No separate backend instance for aux — browser-use manages Chrome sessions natively. (This per-store `{slug}-aux` is distinct from the store-less `web` browser below.)

### Store-less web browser

No-store (orchestrator) tasks — those created in the general/all-stores task list with `store_id=None` — get a single generic Chrome browser that is **not tied to any store**, for neutral public web work (search, tracking/logistics/carrier pages, research). Store-specific seller-center work still requires delegating to a per-store sub-task.

- **Wrapper**: `write_web_browser_use_wrapper()` in `app/browser/wrapper.py` generates `~/.vibe-seller/bin/_web/browser-use` (reserved slug `_web` = `config.WEB_BROWSER_SLUG`, which `store_slug()` can never produce for a real store). Session names are `web` / `web-{VIBE_TASK_ID[:8]}`. It is Chrome-only — no Ziniao routing, no `{slug}-aux` split — so it is a deliberately simpler sibling of the per-store wrapper (they share the self-heal + URL-shape guard logic conceptually; keep them in sync).
- **PATH**: `apply_agent_venv_path()` prepends `bin/_web` for no-store tasks (store tasks use `bin/{slug}`).
- **Lifecycle**: `BrowserManager.start_web_session()` launches one shared Chrome (`ChromeBackend`, keyed on `_web`) + CDPMuxProxy; per-task tab isolation is by `VIBE_TASK_ID` exactly like stores. The proxy port persists across restarts in `AppSettings['web_browser_proxy_port']` (stores use `BrowserSession.proxy_port`; the web browser has no such row). Started lazily by the wrapper via `POST /api/browser/web/start` on first use.
- **Isolation caveat**: the `_web` profile is shared and has no per-store IP masking or store login — the agent prompt hard-forbids opening any seller center or logging into store/platform accounts on it.

**Aux daemon probe + recycle**: The aux daemon is launched lazily by browser-use itself on the first command, and occasionally wedges after a heavy page load (Chromium watchdog racing in-flight dispatches) — the socket keeps accepting connects but subsequent `sock.recv` calls block for the full CLI timeout. To recover without operator intervention, the Ziniao wrapper runs a short probe (`timeout 3 browser-use --session {slug}-aux state >/dev/null`) before exec; if the probe fails, it calls `browser-use --session {slug}-aux close` so the real command below relaunches a fresh daemon. The probe is skipped when the real command is itself `close`, `sessions`, or `shutdown` so recovery paths still work on a stuck daemon. `state` is read-only and the 3-second cap means a wedged daemon can't eat more than a few seconds on the healthy-path. Chrome wrappers don't emit the block (Chrome has no aux daemon — everything routes through CDPMuxProxy).

**URL-shape guard for `open` / `navigate`**: An agent's `browser-use open https://x.com/page?a=1&b=2` is silently destructive under any common shell: zsh treats `?` as a glob and `&` as a background operator BEFORE the wrapper sees the command, so the URL arrives truncated (or absent entirely) and `browser-use open` quietly "navigates" to nothing — the next `state` call returns the previous page and the agent assumes success. The wrapper can't fix the calling shell, but it CAN detect the shape that proves shell-mangling already happened. After argument parsing, the wrapper walks `PASSTHROUGH[@]` for an `open` or `navigate` subcommand; if the next positional arg doesn't start with `http://`, `https://`, `about:` (legitimate for session-recovery navigations to `about:blank`), or `file://` (legitimate for opening local artifacts the agent generated), the wrapper exits 2 with a stderr message pointing at the quoting fix:

```
ERROR: 'browser-use open' expects an http(s)://, about:, or file:// URL.
       Got: <missing>

Likely cause: the calling shell (zsh/bash) parsed special
characters in your URL before the wrapper saw it. URLs
containing '?', '&', or '#' MUST be quoted, e.g.:
  browser-use open 'https://example.com/page?a=1&b=2'
```

This converts a silent failure into a loud one on the first call instead of compounding through retries. Tests in `tests/unit/test_browser/test_browser_use_wrapper.py::TestWrapperUrlValidation` exercise the generated bash directly with good/bad URL shapes.

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
- The browser-use wrapper connects to the proxy via `--cdp-url`, not directly to Ziniao

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

## Daemon Reaper

`app/browser/daemon_reaper.py` — periodic background service that
cleans up orphaned `browser-use` daemon processes.

Browser-use daemons fully detach from their parent process
(`ppid=1`, own `pgid`), so they are not killed when a task
stops or the server restarts.  The reaper runs every 5 minutes:

1. Lists all `browser_use.skill_cli.daemon` processes via `ps`
2. Extracts task IDs via two methods:
   - Full UUID from `--cdp-url client-{UUID}` (Ziniao daemons)
   - 8-char prefix from `--session {slug}-{id[:8]}` (Chrome daemons)
3. Queries DB for active tasks (pending/designing/running/etc.)
4. Kills any daemon whose task ID is not in the active set
5. Skips daemons without identifiable task ID (e.g. manual sessions)

Uses `DaemonInfo` dataclass with `full_task_id` and `task_id_prefix`
fields (no magic sentinel strings).  8-char prefix matching has
~1/4B collision chance per pair — accepted as known limitation.

**Three-layer cleanup**:
1. **Per-task** (`_cleanup_browser_daemons()` in `claude_backend.py`): kills task-specific daemons on agent **stop** (not start, to avoid race conditions with fast retries). Uses two `pgrep` patterns — full UUID in `--cdp-url` (Ziniao) and 8-char prefix scoped to `--session` arg (Chrome)
2. **Periodic reaper** (`start_reaper_loop()` in `daemon_reaper.py`): background loop every 5 minutes, uses same orphan detection logic
3. **Server startup** (`cleanup_stale_sessions()` in `manager.py`): calls `reap_orphaned_daemons()` to clean up daemons from the previous run while preserving daemons for active tasks (e.g. WAITING tasks that survive restart)

**Design Rationale**:
Cleanup at `start()` was removed because it caused a race condition: if a task was retried quickly, the new session could start its daemon, then the cleanup from the new `start()` would kill the freshly-started daemon. Cleanup now only happens at `stop()` (guaranteed to kill the old daemon) and via the periodic reaper (for orphaned daemons when server restarts).

**Note**: Uses `pgrep`/`os.kill` (Unix-only).

## Adding a New Backend

1. Create `app/browser/mybackend.py` implementing `BrowserBackend` from `base.py`
2. Register in `BrowserManager._get_backend()` in `manager.py`

## Planned Backends

- **Custom CDP**: Connect to any browser already exposing a CDP endpoint. Start/stop are no-ops.
