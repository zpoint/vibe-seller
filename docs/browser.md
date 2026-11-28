# Browser Management

> User guide: [Stores & Browsers](stores.md)

Pluggable browser backend system for launching and managing browser instances per store.

## Architecture

```
BrowserBackend (abstract)         BrowserManager (singleton)
    ├── ChromeBackend                 Playwright Chromium (macOS/Linux/WSLg)
    ├── WinChromeBackend              native Windows Chrome (WSL2-on-Windows)
    ├── ZiniaoBackend                 anti-detect, HTTP API → CDP proxy
    └── InjectCdpBackend              anti-detect, no API → inject CDP port
          ├── YuehulianBackend        悦互联 (Windows/WSL + macOS)
          ├── WenmaiBackend           稳卖 (Windows + WSL + macOS)
          └── BitBrowserBackend       比特浏览器 (Windows/macOS, WSL-on-Windows)

All backends front their browser with CDPMuxProxy for shared access and
cookie persistence; BrowserManager generates a per-store browser-use
wrapper that connects via the proxy. The two anti-detect families differ
only in how they obtain the CDP endpoint — ziniao asks its local HTTP API,
the inject family (yuehulian) drives the GUI client via a Node inspector +
renderer DOM and plants `--remote-debugging-port` into the launched kernel
— plus `--disable-backgrounding-occluded-windows`, so a minimized or covered
store window keeps rendering and agent screenshots don't hang
(see the yuehulian section below). Downstream of the CDP endpoint they are
identical.

**WenmaiBackend** (稳卖) is the inject family's hybrid — an HTTP-driven
client with inspector only for what the API cannot do; see its section
below and [docs/wenmai.md](wenmai.md).

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

`is_antidetect()` / `antidetect_display_name()` also live here — they read
the vendor registry (`vendors.py`), so a newly registered vendor is
covered without editing this file.

### `vendors.py` — the vendor registry (spec + registration)

One `BrowserVendorSpec` per browser vendor, declared in the vendor's own
module and registered through the plugin context
(`ctx.register_browser_backend(name, cls, spec=...)` — core vendors from
`builtin_plugin.py`, private vendors from plugin wheels). Everything
shared reads the spec, which is what keeps "a new vendor = one vendor
module (+ translations + one docs page), zero shared-code edits" true:

- identity: key, zh/en display names, kind (`antidetect` / `plain`),
  platforms, `selectable` (offered in the store form);
- accounts — two SEPARATE concepts (R8: one account per machine unless
  the vendor's official API serves several at once): the account rule
  `account_cardinality` (`single` — the anti-detect default; `multi` —
  the API takes the account on every call, allowed only with a
  `MULTI_ACCOUNT_VENDORS` entry citing the vendor's API docs, today
  only ziniao; `none` — plain browsers only) and whether anything is
  stored, `credential_fields` (form + storage schema, secrets
  encrypted; empty = the user signs in in the client and the local API
  acts as that login). Validated at registration
  (`app/browser/vendor_rules.py`). Also `profiles_scope`;
- picker: `picker_kind` (`generic` — the shared `VendorSection` — vs
  `custom` — the vendor keeps its own UI, ziniao until migrated);
- wiring: `client_factory` (the `InjectClient` the engine runs),
  `win_app` (the install descriptor the locator serves), an optional
  `client_path_setting`;
- text: `i18n_prefix` (the vendor's string OVERRIDES — the generic UI
  resolves `<prefix><Suffix>` first, else the shared `browserVendor.<Suffix>`
  key interpolating `{{vendor}}`, so a new vendor ships only its
  credential field labels plus the overrides it wants;
  `test_vendor_ui_strings.py` pins that every key resolves) and `docs`
  (per-topic `setup` / `errors` / `one_account` help links;
  `test_docs_links.py` checks the URLs).

For a `single` vendor WITH `credential_fields`,
`vendor_account_lock(vendor.key)` is a per-vendor `LoopLock` shared by
store creation, store deletion, and both the generic and legacy credential
save routes. Creation snapshots the opaque stored (still encrypted) values
before waiting for that lock and revalidates them after acquiring it; if
the account changed in flight it creates nothing and returns
`409 {status: "account_changed", message}` so the user selects the profile
again. Deleting the vendor's LAST bound store deletes only its declared
credential fields in the same database transaction as the store delete;
unrelated settings such as the client path survive. `multi` / `none`
vendors and `single` vendors without stored credential fields do not enter
this account lifecycle.

The store picker binds a profile by `ref || name` (the stable id the
vendor's `StoreRef.ref` reports; vendors without one use the name) and
greys out an env that exists but cannot be opened
(`StoreRef.available === false`, its `unavailable_reason` shown). The
generic stores route surfaces every `InjectError` the client raises as
a structured `{status: <code.lower()>, message}` — the shared taxonomy
is the whole status space, so a paid gate or an account mismatch
reaches the UI as itself, never a 500.

`tests/unit/test_no_vendor_literals.py` enforces the invariant: no
anti-detect vendor key literal in shared code (ziniao grandfathered until
its migration; the yuehulian compat aliases exempted while they exist).

`bitbrowser` (比特浏览器) is the first spec-only local-API vendor: `single`
with a stored account + password (encrypted through the generic
credentials route) and GUI auto-login through the client's own login
page, `lists_profiles` straight from the client's local API, the
Windows install descriptor plus an optional saved client path, and the
docs links for the picker's error hints.

### `local_api_client.py` — the local-API client family

The ~25 anti-detect vendors whose desktop client exposes a local HTTP
API (list / start(id) → CDP / stop on 127.0.0.1) implement
:class:`LocalApiClient` instead of writing a client: the uniform parts
are class attributes (port + discovery, auth mode + field, list/open/
close paths, rate-limit interval, timeouts), the vendor parts are small
overridables (`parse_stores`, `parse_opened` — `opened_from_ws` parses a
ws URL, a port-only answer passes through because the engine verifies
the endpoint — `is_logged_out` on every call, `map_error`,
`discover_port`, async `auth_headers`, `is_rate_limited`, the
`list_request`/`open_request`/`close_request` request hooks and
`next_page` pagination). Subclasses needing extra endpoints (a kernel
check, a health probe) call `api_call` — the one shared response path
with the rate limit, auth headers, logged-out check (before the
single rate-limit retry and before `map_error`) and the
no-payload-logging rule. The parametrized contract suite
(`tests/unit/test_browser/local_api_contract.py`) runs every subclass
against a fake local API serving its documented responses; a real
vendor adds its adapter plus ONE fixture file
`tests/unit/test_browser/local_api_vendors/<vendor_key>.py` exporting
`CONTRACT = ContractFixture(...)` and gets every contract test for
free — never an edit to the suite (a convention check fails the build
until the fixture exists).

`bitbrowser.py` (split into `bitbrowser_cdp.py` / `bitbrowser_launch.py`
/ `bitbrowser_login.py`) is the first real subclass: the port and the
optional `x-api-key` token
are read from the client's own `config.json` (cached per client
instance), the 工作台 workbench tab is closed over CDP in `after_open`
(keyed on the profile's `workbench` setting so a late appearance is
still caught), non-Chromium kernels are greyed out in the picker and
refused at open via a `/browser/detail` pre-check, and the client is
located and launched when it is not running. The login module is the
auto-login policy + driver: the two logged-out shapes are classified
(a device kick refuses — one account, one computer — and a
recent-auto-login logout trips the ping-pong guard, both persisted per
machine), plain expiry drives the login form through the shared
inspector, and success is decided by polling the local API, never the
DOM.

### `manager.py` — Browser Manager

Singleton that orchestrates browser sessions across all stores:

- `start_session(store, db)` — Launches browser (Ziniao only), creates/updates `BrowserSession` DB record, generates wrapper script. The launch is bounded by `VIBE_BROWSER_START_TIMEOUT_S` (default 180s) and the dead-mux relaunch path is budgeted per store — see § Browser Lifecycle
- `stop_session(store, db)` — Closes browser, updates DB, removes wrapper script
- `ensure_session(store, db)` — Start or reuse a session, always regenerates wrapper
- `write_mcp_config(store, db)` — Generates browser-use wrapper WITHOUT starting browser (used at task launch)
- `get_cdp_port(store_id)` — Returns CDP port if available
- `remove_browser_use_wrapper(slug)` — Removes a store's browser-use wrapper

**Proxy-port ownership (one port set per server instance)**: every CDPMuxProxy this manager starts serves `GET /vibe/instance` with the server instance's id (`BrowserManager.instance_id`, minted per process), and the allocator (`app/browser/proxy_ports.py`) **binds** each port it hands out — the store's mux later serves on that very socket — so two vibe-seller servers on one machine can never hand out, or attach an agent onto, the same proxy port. The reservation is **bind-only** (no `listen()`, no `SO_REUSEADDR`): during the window between allocation and the mux coming up, a probe gets a prompt `ECONNREFUSED` (no accept backlog — a listening reservation used to swallow the wrappers' probes and log one `opening handshake failed` per store start); the mux calls `listen()` itself right before `websockets.serve` takes the socket over. Dropping `SO_REUSEADDR` is what keeps the allocation atomic — on Linux a silent double-bind needs it on *both* sockets — at a documented cost: a port left in `TIME_WAIT` by a just-stopped mux cannot be re-reserved for ~60 s, so the allocator walks to the next port (the wrapper already follows the typed `proxy_port` the start API return). A port held by anything else (another server's proxy, an agent-spawned CDP bridge, any unrelated listener) is skipped; a DB-saved port (`BrowserSession.proxy_port`, `AppSettings['web_browser_proxy_port']`) is re-adopted only when the bind proves it free, so a port that went foreign while the server was down is reallocated. The wrappers' liveness probe is the same contract from the agent side: a listener that does not answer `/vibe/instance` with the baked id is "down" — it calls the start API and follows the typed `proxy_port` the API returns. Single-instance machines keep exactly the same ports (base 9222; 9322 preferred in demo mode). On reconnect, the mux also refuses an upstream that answers with a *different browser GUID* than the one recorded at first connect (vendors rotate debugging ports; store B's fresh browser must never be driven as store A's) — see `cdp_mux_upstream._connect_upstream`.

**Per-instance port range (`VIBE_CDP_PROXY_BASE`)**: a second knob for running several servers on one machine without them queueing for the same preferred ports. `VIBE_CDP_PROXY_BASE=<int>` (1024–65000) moves the whole per-instance range — store, web and aux proxy ports all start there and walk up; a garbage or out-of-range value logs a warning and falls back to the default (9222, or 9322 in demo mode), and unset behaves exactly as before, so it is invisible to users. Allocation is still bind-before-use with the ownership handshake, so ranges may even overlap safely — the base is about keeping test servers out of each other's way, not the safety property. Lane conventions on the dev machine (test servers only, never production): bitbrowser 9400–9419, wenmai 9420–9439, yhl 9440–9459, adspower 9460–9479, c2 9480–9499, c1 9500+.

**Multiple server instances on one machine (known limitation)**: the port ownership above makes two servers safe at the CDP level, but two servers can still both drive the *single GUI client* of an inject-driven vendor (悦互联, 稳卖's login form). The machine-wide inspector lock serialises inspector sessions, not whole GUI flows across servers — one server's store-form listing while another server runs a task can interleave in the client. Production runs one server per machine, so this is a test-environment hazard, not a user-facing bug.

**Per-store isolation**: Each store gets its own browser-use wrapper script at `~/.vibe-seller/bin/{slug}/browser-use`. browser-use 0.13 removed the subcommand CLI (`open`/`state`/`click`) — the agent now drives the browser by piping Python helpers via a heredoc (`browser-use <<'PY' … PY`), and connection identity moved from flags to **environment variables**. The wrapper enforces session isolation: it auto-assigns the per-task session, accepts only `--session {slug}-aux` as an override (mapped to `BU_NAME`), blocks `--cdp-url`/`--mcp`/`--connect`/`--profile` flags plus any agent-supplied `BU_*` env var, and injects the session name (`BU_NAME`, was `--session`) and CDP endpoint (`BU_CDP_WS`, was `--cdp-url`) as env vars — along with `BH_RUNTIME_DIR`/`BH_RUNTIME_DIR_SHARED` for daemon state and `VIBE_STORE_DOWNLOADS` (the store's download dir, resolved for this server's `VIBE_HOME`; skills reference it instead of a hardcoded default-home path, and the agent's launch env sets the same value). `VIBE_STORE_DOWNLOADS` arrived with **wrapper format v10** — v9 is C1's ownership-checked liveness; v10 exists so a wrapper written by v9 (no export) is reaped on boot and regenerated with the export, rather than surviving without it forever. An asyncio lock serializes `start/stop`.

**Store slug (folder name)**: a store's slug is a stored, unique column, `Store.slug` — every caller reads it; nothing derives it from the name at runtime. It is picked by `new_store_slug()` in `app/store_slugs.py` when the store is created or renamed: the ASCII leftovers of the name (`[A-Za-z0-9_-]`, separators collapsed; entirely non-ASCII → `store-<store_id[:8]>`) whenever that is free — the same folder names as always — and, only where it is taken, part of the store id appended (`示例乙1店` → `1-7d41e0` next to an existing `示例甲1店` → `1`). The ASCII constraint comes from browser-use 0.13's `BU_NAME` validation (`browser_harness` `_check`, regex `[A-Za-z0-9_-]{1,64}`), so a slug is also capped at 52 characters, and is never another store's session name (`<slug>-aux`, `<slug>-<8 hex>`). Before the column existed the slug was recomputed from the name, and two different Chinese names with the same digit (both `1`) were refused as a collision — or, before that guard, shared one folder and browser. On upgrade, `migrate_store_slugs()` (run by `init_db`) gives each existing store exactly the slug it had, so no folder moves; stores an older version let share one slug are split, the oldest keeping it and each other getting its own slug plus a copy of the shared folders.

**Per-task session names**: Each task gets its own browser-use daemon session: `{slug}-{VIBE_TASK_ID[:8]}` (e.g., `my-store-a1b2c3d4`). Combined with CDPMuxProxy isolation (request ID rewriting, session-based event routing, target filtering), this enables concurrent tasks within a single shared browser. Without `VIBE_TASK_ID` (e.g. manual use), falls back to the bare slug.

**Ziniao guard**: Only one Ziniao account can be active per machine. `_start_session_locked` checks `_active_ziniao_account_id` — if a different account is already active, it raises `RuntimeError` with the names of conflicting stores. Chrome stores have no such account restriction (but still use CDPMuxProxy for shared browser and cookie persistence).

`_start_session_locked` raises for two more reasons, both there to keep one broken store from becoming a machine-wide outage: the **relaunch budget** is spent (`VIBE_BROWSER_RELAUNCH_MAX` per `VIBE_BROWSER_RELAUNCH_WINDOW_S`), or `backend.start()` blew through `VIBE_BROWSER_START_TIMEOUT_S`. All three failures are per store — peers keep running and the task can be retried.

> **Multiple stores of the SAME account run concurrently** (verified against the official Ziniao demo). Ziniao's `startBrowser` is flaky (nondeterministic per-store "stale launch" — either the debug port never binds, or it binds but Ziniao's SBP layer never injects, leaving the env with no account auto-fill and `navigator.webdriver` unmasked). Readiness therefore checks **both** (`_cdp_port_reachable` + `_instrumentation_live`); recovery is **per store** (`stopBrowser` + retry) and budgeted, never a global client kill — a global kill destroys every other store's live browser and cascades. See [docs/ziniao-concurrency.md](ziniao-concurrency.md) for the full mechanism, root-cause investigation, and fix.

**Wrapper script generation**: `write_browser_use_wrapper()` in `app/browser/wrapper.py` generates a bash script per store. Session validation uses a regex check (`^{slug}(-aux|-{8hex})?$`). For both Chrome and Ziniao stores, the wrapper auto-starts the CDP proxy and injects `BU_CDP_WS=ws://…/client-{task_id}` (the 0.13 replacement for `--cdp-url`) pointing at CDPMuxProxy. EVERY session — aux included, both backends — gets an explicit `BU_CDP_WS` on its own store's proxy: per-task sessions use `client-{task_id}`, aux uses the stable `client-aux`. (The former Ziniao-aux "Chrome direct" exemption exported no endpoint; `browser_harness`'s ambient discovery then attached to a *different store's* browser — wrong Amazon account, wrong downloads dir — observed live with two Ziniao stores. Wrapper format v3 removed it.) The auto-start probe is an OWNERSHIP check (wrapper format v9): the listener on the baked port must answer `/vibe/instance` with this server instance's id — a foreign server's proxy or an agent-spawned CDP bridge reads as down, the wrapper calls the start API and follows the typed `proxy_port` it returns. The block checks the browser start API's HTTP status and exits with a clear error on non-2xx responses. A final readiness check after the poll loop catches cases where the browser started but the proxy isn't answering as ours.

**Wrapper safety — the agent must never touch a local Chrome**: the agent's `PATH` prepends the store `bin/<slug>` dir, so bare `browser-use` resolves to the wrapper. Three layers keep it from ever falling through to the real `browser-use` binary (which would attach to the user's own Chrome):
1. **Written before start** — `write_browser_use_wrapper()` runs *before* `backend.start()` in `_start_session_locked`, so a failed/stale launch still leaves a working wrapper (its auto-start block retries `browser/start` on the next call).
2. **Version-aware boot wipe** — each wrapper embeds a monotonic `WRAPPER_FORMAT_VERSION`; `_wipe_generated_wrappers()` deletes only *older* wrappers and keeps current+newer, so a restart never leaves a wrapper-less window (and never nukes a newer version — rollback-safe). It **also reaps orphans**: a wrapper whose embedded `store_id` is no longer in the DB. Version alone never caught those, so a deleted store's wrapper lived forever holding a *frozen* `proxy_port` — and since ports are re-allocated from the base every boot, that number eventually lands on a live store. The orphan then finds the port already up, skips the start API (so its dead `store_id` never 404s) and points `BU_CDP_WS` at **another store's** browser: wrong account, wrong downloads dir — the same cross-store hole v3 closed for aux. A wrapper with no parseable store id (the store-less `_web`) is kept, so the check can never mass-reap.
3. **Guard** — `apply_agent_venv_path` puts a guard `browser-use` (`bin/_guard`) on `PATH` below the wrapper but above the venvs; if the wrapper is somehow missing, bare `browser-use` hits the guard and **exits non-zero** instead of reaching the real binary. The guard also shims the raw console-script aliases (`bu`, `browser`, `browseruse`, `browser-use-tui`, `browser-harness`, `browser-harness-mcp`) — each reaches the real binary and would skip wrapper *and* guard, so they error and point at `browser-use` (the shims are extensionless bash scripts: same Windows caveat as the `browser-use` guard itself, no `.exe` interception).
4. **Absolute `REAL_BU`, or no wrapper at all** — `app/browser/bu_binary.py` is the only producer of the path a wrapper forwards to, and it either returns an absolute path or raises `BrowserUseNotFound`. There is deliberately no bare-name fallback: since the agent's PATH puts the wrapper dir *first*, a wrapper forwarding to bare `browser-use` re-resolves to **itself**. That is what happened on native Windows, where the console shim is `browser-use.exe` with no extensionless sibling — the wrapper recursed until the guard rejected the inherited `BU_NAME`. Refusing to write the wrapper is safe precisely because of layer 3.

See [docs/browser-use-0.13-migration.md § wrapper-format versioning](browser-use-0.13-migration.md) and [docs/ziniao-concurrency.md](ziniao-concurrency.md).

**Dual browser (Ziniao)**: Ziniao stores get dual-session support in their wrapper — main session (`{slug}-{task[:8]}`) and aux session (`--session {slug}-aux`, mapped to `BU_NAME`) BOTH route through the store's own CDPMuxProxy; aux rides the stable `client-aux` proxy client so it never shares a task client's tab pool. No separate backend instance for aux. (This per-store `{slug}-aux` is distinct from the store-less `web` browser below.)

### Store-less web browser

No-store (orchestrator) tasks — those created in the general/all-stores task list with `store_id=None` — get a single generic Chrome browser that is **not tied to any store**, for neutral public web work (search, tracking/logistics/carrier pages, research). Store-specific seller-center work still requires delegating to a per-store sub-task.

- **Wrapper**: `write_web_browser_use_wrapper()` in `app/browser/web_wrapper.py` generates `~/.vibe-seller/bin/_web/browser-use` (reserved slug `_web` = `config.WEB_BROWSER_SLUG`, which no store slug can be — they never start with `_`). Session names are `web` / `web-{VIBE_TASK_ID[:8]}`. Like the per-store wrapper it targets browser-use 0.13 — it injects `BU_NAME`/`BU_CDP_WS` env vars (not flags) and the agent drives it by piping Python via a heredoc. It is Chrome-only — no Ziniao routing, no `{slug}-aux` split — so it is a deliberately simpler sibling of the per-store wrapper (they share the env-injection + wedge-recovery logic conceptually; keep them in sync).
- **PATH**: `apply_agent_venv_path()` prepends `bin/_web` for no-store tasks (store tasks use `bin/{slug}`).
- **Lifecycle**: `BrowserManager.start_web_session()` launches one shared Chrome (`ChromeBackend`, keyed on `_web`) + CDPMuxProxy; per-task tab isolation is by `VIBE_TASK_ID` exactly like stores. The proxy port persists across restarts in `AppSettings['web_browser_proxy_port']` (stores use `BrowserSession.proxy_port`; the web browser has no such row). Started lazily by the wrapper via `POST /api/browser/web/start` on first use.
- **Isolation caveat**: the `_web` profile is shared and has no per-store IP masking or store login — the agent prompt hard-forbids opening any seller center or logging into store/platform accounts on it.

**Wedge recovery (timeout-bounded)**: A renderer can wedge (hang on the CDP handshake) while the browser process stays alive — every subsequent call against that daemon then hangs identically. browser-use 0.13 removed the subcommands the old self-heal relied on (`state`/`close`), so the wrapper no longer classifies calls or probes with a read-only subcommand. Instead, every proxy (non-aux) call is bounded by a hard timeout (perl `alarm` — macOS has no GNU `timeout`; the interval timer survives `execve` and `SIGALRM` kills the exec'd browser-use). On a wedge (rc 142) the wrapper reloads *this* session's daemon via `browser-use --reload` (scoped by `BU_NAME` → `browser_harness` `restart_daemon()`) and surfaces the failure — it does **not** auto-retry, because a 0.13 heredoc can mutate the page (click/type), so blindly re-running could double-apply. The agent re-issues on the reported error against the fresh daemon. Aux sessions run the same bounded self-heal path as every other session (introduced in wrapper format v3).

**Recovery is bounded, and stops at the daemon (wrapper format v6)**: a reload only cures a wedge that lives in the daemon. When the *browser* is the wedged party — renderer resource-exhausted, or the site serving a maintenance page — the reload reconnects to the same sick browser and the next call times out identically. v5 answered every timeout with that one lever, so the caller saw an unbounded run of errors that each looked transient and kept paying 120s a round to learn nothing (observed live: a store spent 20 minutes cycling reload → navigate → timeout and lost 11 downloads, every round reported as an ordinary error). The wrapper now counts *consecutive* timeouts per session in `$BH_RUNTIME_DIR/wedge-<session>.n`: the **1st** reloads this session's daemon, the **2nd** exits `WEDGE_UNRECOVERABLE_RC` (75, distinct from 142) with an error telling the caller to stop retrying and record the unfinished work as a gap. Any non-timeout status — including an ordinary error — proves the browser is reachable and clears the counter, so a healthy browser never inherits a stale count. The counter is keyed by session, not by store, so one wedged `--worker N` slot cannot spend another slot's budget. Pinned by `tests/unit/test_wrapper_wedge_escalation.py`.

> **Why the wrapper does not recycle the browser.** A store's browser is *shared*: concurrent tasks and `--worker N` slots each get their own daemon and mux client on ONE browser. Recycling it is a store-wide, multi-tenant action that destroys every peer's tabs and login state mid-task, and every peer's next call would escalate the same way — a stop/start storm bounded only by the per-store relaunch budget (`VIBE_BROWSER_RELAUNCH_MAX` per `VIBE_BROWSER_RELAUNCH_WINDOW_S`). One tenant does not get to make that call. It would also not have worked: `browser/start` only relaunches Ziniao when its control API is not answering, and `_start_session_locked` tears the env down only when the CDP proxy is *dead* — in this wedge both still answer, so the request is a no-op. Recycling a shared browser belongs to `BrowserManager`, which holds the lock, knows the other tenants, and owns the budget.

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
- Configurable `max_clients` (default 16) — a CDPMuxProxy connection limit per proxy instance, not a system-wide concurrency cap. Sized for `MAX_AGENT_CONCURRENCY × (1 main + VIBE_BROWSER_WORKER_SLOTS) + 1 aux` plus reconnect headroom, so a worker slot the wrapper promised an agent is never denied by capacity
- **One live connection per client id.** Registration used to be an unguarded `self._clients[client_id] = client`: a second connection displaced the first in the dict and every response for that id then routed to the newcomer's socket, while the displaced driver waited forever — no error, no log. The newest connection now wins *deliberately*: the older one is popped first (so its `finally` sees it no longer owns the id and skips the deferred cleanup that would close the survivor's tabs), then closed with code **4409**, and the collision is logged at ERROR. Taking over rather than refusing is the fail-safe direction — the common real cause is a daemon whose socket died without a clean close, and refusing would wedge every later call for that task

**Upstream reconnect**: When the browser disconnects, the proxy sends error responses to all pending client requests, then attempts reconnection with exponential backoff (max 10 attempts, 1s→30s delay). Single-flight guard (`_reconnecting` flag, reset in `finally`) prevents concurrent reconnect attempts. After 10 failures, the proxy stops itself gracefully. `_reconnecting` is always cleared even if `_running` becomes false during reconnect.

**Client reconnect grace period**: When a client disconnects, its tabs are not cleaned up immediately. Instead, the client enters a configurable grace period (default 480s, controlled by the `cleanup_grace` constructor parameter) during which tabs are preserved. If the client reconnects within this window, its tabs are recovered and the client resumes where it left off. Only after the grace period expires without reconnection are the client's tabs closed and routing entries removed.

**Client identification**: The `VIBE_TASK_ID` environment variable is set by `ClaudeCodeBackend` when spawning the agent subprocess. The browser-use wrapper script reads it to construct the WebSocket URL `ws://127.0.0.1:{port}/client-{VIBE_TASK_ID}`, connecting directly (bypassing HTTP discovery). Falls back to a random UUID if not set.

### Parallel workers — `browser-use --worker N`

The two identifiers that isolate a browser driver — the browser-use daemon (`BU_NAME`) and the mux client id — are both derived from `VIBE_TASK_ID`. That is what makes a **sequential** subagent correct: it inherits the variable, lands on the same daemon, its calls serialise through one connection, and the page its parent opened is still there.

A **concurrent** subagent is a different story, and the reason is upstream of us: Claude Code's Agent-tool subagents run *in the parent's process* (`src/utils/agentContext.ts` isolates them with `AsyncLocalStorage` precisely because several can run at once), and nothing writes a per-agent id into a child command's environment — `CLAUDE_CODE_AGENT_ID` is only ever read, and only for separate-process swarm teammates. So every Bash call in an agent tree sees one identical environment. Two concurrent drivers therefore share one `BU_NAME`, and `browser_harness.daemon.serve` handles each socket connection in its own asyncio task with **no lock anywhere in the package** — the two `handle()` coroutines interleave on one tab. The failure mode is silent wrong data.

`--worker N` closes it without handing the environment to the agent:

```bash
browser-use --worker 2 <<'PY'
new_tab("https://example.com/report")
print(page_info())
PY
```

| | main session | `--worker 2` |
|---|---|---|
| `BU_NAME` | `{slug}-{task[:8]}` | `{slug}-{task[:8]}-w2` |
| `BU_CDP_WS` | `…/client-{task}` | `…/client-{task}-w2` |

Same store, same proxy port, same logged-in browser — a separate **tab**, via the target-ownership isolation above. Design points:

- **The caller supplies a number, never an id.** The bound (`VIBE_BROWSER_WORKER_SLOTS`, default 3; `0` disables the flag) is resolved at wrapper-generation time and baked into the script, which range-checks it and rejects non-digits and leading zeros. The session allowlist regex carries the same bound as a second gate. "Agent invents an arbitrary session id" stays unrepresentable — that pattern is what made rotating `VIBE_TASK_ID` per call harmful.
- **The slot lands on both identifiers.** Bijective by construction, so a slot can never produce a daemon and a client id that disagree.
- **`--worker` and `--session` are mutually exclusive.** A slot is a slot on the main store browser; `-aux` is a different browser.
- **Slot assignment belongs to the parent agent** — only it knows how many subagents it is launching. A parent that hands the same slot to two subagents no longer corrupts data silently: they collide on one client id, and the mux logs it at ERROR and closes the loser (see the one-connection-per-id rule above).
- **The daemon stays reapable.** `{slug}-{task[:8]}-w2` still carries its owning task id, and `daemon_reaper.task_prefix_for_bu_name` matches the `-w<N>` tail — otherwise every parallel subagent would leak a daemon, and a mux client against `max_clients`, for the life of the server.
- **Tabs are per-slot; downloads are not.** Every slot writes to the same store downloads dir (`$VIBE_STORE_DOWNLOADS`).

The same flag exists on the store-less `_web` wrapper, with the same contract. Implementation: `app/browser/worker_slots.py` (shared by both wrappers), agent-facing docs in `app/skills_v2/browser-harness/SKILL.md`.

**Reading slots in the log.** Ownership lines truncate the client id, and slots append their suffix — so a plain `client_id[:8]` renders a task's main client and every worker as the *same string*, exactly when you need them apart. `short_cid()` (`cdp_mux_types.py`) keeps the head **and** re-attaches the slot: `1a2b3c4d`, `1a2b3c4d-w2`. Note the two renderings coexist — connect/disconnect lines carry the full id, ownership lines the short one — so anything parsing the log must fold both onto one key (`tests/e2e/mux_probe.py` does; it also refuses to certify isolation from a log written before this fix, where worker tabs were attributed to the main client and every disjointness check passes vacuously).

**Test coverage.** Four tiers, each closing a different gap:

| Tier | File | Proves |
|---|---|---|
| unit | `tests/unit/test_browser/test_worker_slots.py` | the wrapper's slot→(daemon, client) derivation, validation, reapability, log visibility |
| unit | `tests/unit/test_browser/test_cdp_client_collision.py` | one live connection per client id; takeover doesn't destroy the survivor |
| integration (CI, real Chromium) | `tests/integration/test_worker_slot_isolation.py` | the **real generated wrapper** — not fabricated ids — gives concurrent callers isolated tabs |
| e2e (real LLM) | `tests/e2e/test_parallel_subagent_slots.py` | a real agent *reaches for the flag* from human intent alone; zero slots used is a **failure**, since that means the prompt guidance regressed |

`tests/e2e/run_part_c_click_lab.py` is a manual sustained-load check (three subagents clicking through a stateful page for minutes); it needs a server started with a widened `VIBE_BROWSER_WORKER_SLOTS`, so it is a script rather than a collected test.

**HTTP endpoints**: Serves `/json/version` and `/json/list` for browser-use CLI discovery, with `webSocketDebuggerUrl` rewritten to point at the proxy.

**Download path override** (`download_dir`): macOS sends SIGTERM to the browser-use daemon process after 3-5 min of background execution (PPID=1, stdout/stderr=/dev/null, no terminal). This is standard macOS behavior for idle background processes with no UI — we cannot prevent it. Each daemon restart creates a fresh `BrowserProfile` whose model validator generates a random `/tmp/browser-use-downloads-{uuid}/` directory and calls `Browser.setDownloadBehavior` with it. The proxy pins the path itself on every upstream connect and reconnect (`_startup_download_behavior`, sessionless from the mux's persistent root session), making downloads deterministic regardless of daemon restarts. Both `ZiniaoBackend` and `ChromeBackend` pass `download_dir` when constructing the proxy. The agent learns the resolved dir from its store context and from `VIBE_STORE_DOWNLOADS` in its environment (set by the agent launch and exported by the wrapper) — agent-facing docs never spell the path.

**The pin is IMMORTAL** (pinned by `tests/unit/test_browser/test_cdp_download_rewrite.py`): Chrome ties the browser-wide download behaviour to the DevTools **session** that last set it — the message's `sessionId`, not the connection. The browser_harness daemon relay scopes every non-Target call to its page session, so a *forwarded* client `Browser/Page.setDownloadBehavior` (even with its path rewritten) transfers pin ownership to a mortal tab; when that session ends, Chrome resets to the profile default and every later download leaves the store dir (proven live by the harness lane's F2 investigation). So when `download_dir` is set, the mux answers a client's `set*DownloadBehavior` **locally** with Chrome's own success shape and never forwards it — the setting stays owned by the root session for the mux's lifetime. Without `download_dir` (aux-style proxies) the call is forwarded unchanged. Each answered call is logged at INFO (client + requested path). Download events are unaffected: the startup pin enables them on the root session and the mux's existing broadcast fans `Browser.download*` events out to every client, so browser-use's click-with-download-detection keeps resolving.

**Enforcement semantics** (all pinned by `tests/unit/test_browser/test_cdp_download_rewrite.py`): a `downloadPath` **inside** the pin — the pin itself or a subdirectory of it, after normalization — is LEFT AS-IS, so a flow that waits for its file in a subfolder keeps finding it; only a path **outside** the pin is rewritten (rewrite, not refuse: a refused CDP error fails the agent mid-flow and a failed agent improvises), and every rewrite is logged at INFO. A Windows-shaped pin (drive letter or backslash — winchrome's `dl_win`, native-Windows Ziniao) is compared with `ntpath` semantics on ANY server OS: case-insensitive, `/` and `\` equivalent, drive letter included. The upstream-side pin is unchanged: `_startup_download_behavior` sends `Browser.setDownloadBehavior` with the exact pin on the first connect AND on every reconnect, and browser-use's watchdog `/tmp/browser-use-downloads-*` dir is still rewritten to the pin (the original deterministic-downloads fix).

### `cdp_proxy.py` — Legacy TCP Proxy (fallback)

Simple async TCP relay: listens on `127.0.0.1:{listen_port}`, forwards raw bytes to `{target_host}:{target_port}`. No CDP protocol awareness — only supports a single client at a time. Kept as `CDPTcpProxy` fallback. On WSL, `target_host` is the Windows gateway IP (detected by `_ziniao_host()`).

### `chrome.py` — Chrome Backend

Launches the store's Chromium with Playwright's `launch_persistent_context` (profile at `~/.vibe-seller/browser_profiles/{slug}/`) and fronts it with CDPMuxProxy. A store browser must look to a page like a person's browser window, because platform risk control (Taobao's 生意参谋 "abnormal access" prompt, Amazon's re-sign-in) reads what a page can see. `_launch_options()` owns that:

- `--disable-blink-features=AutomationControlled` — Playwright launches with `--enable-automation` over a debugging pipe, and either makes `navigator.webdriver` true. The switch only changes what pages read; CDP control (input, screenshots, downloads, tabs) is unaffected.
- `no_viewport` + `--window-size=1280,800` — the page fills the real window instead of Playwright's emulated 1280×720 viewport, like the Ziniao and winchrome backends.
- `channel='chromium'` — the full Chromium build in both modes. Headless otherwise runs `chromium-headless-shell`, which drops every session cookie at startup (it ignores the restore-on-startup pref `_enable_session_restore` sets), so turning on 「后台运行浏览器」 logged Taobao-style stores out.
- The store's own proxy (`browser_config.proxy_server` / `proxy_bypass` from the store form) becomes Playwright's `proxy`; credentials in the URL (`http://user:pass@host:port`) move to its username/password, since Chromium rejects them inside `--proxy-server`. Without a store proxy the browser follows the system proxy, like any browser.

`tests/integration/test_chrome_store_launch.py` pins all four on a real Chromium. The wrappers also export `BH_TAB_MARKER=0`: browser_harness would otherwise prefix every driven tab's `document.title` with a horse emoji, which the page can observe.

### `ziniao.py` — Ziniao Backend

Anti-detect browser with HTTP API (getBrowserList, startBrowser, stopBrowser). Each profile has unique fingerprint/cookies/proxy.

- Default socket port: **16851**
- One Ziniao process per machine, multiple profiles (different `browserOauth`) on same account
- Brings the Ziniao client up itself whenever it is not answering — not running, opened by hand in normal mode, on another port, or leftovers — by stopping every Ziniao process and relaunching it in WebDriver mode (`ziniao_startup.py`); WSL can only do the stopping. No flag, no prompt. See [docs/ziniao-concurrency.md § Client startup](ziniao-concurrency.md#client-startup-always-relaunch-kill-all-120-s-deadline-2026-11-20)
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

1. If Ziniao is already running with the HTTP API on port 16851, it works seamlessly (WSL reaches it via 127.0.0.1 in mirrored networking, or the gateway IP in NAT mode)
2. If it is not answering and Windows shows **no listener** on the port (normal mode, or leftovers), the server stops every Ziniao process through Windows interop, then raises the error guiding the user to run `ziniao_webdriver.bat` (which stops every Ziniao process too, then launches in WebDriver mode and waits up to 120 s)
3. If Windows **does** show a listener WSL cannot reach, nothing is killed — that is a networking problem (`ziniao_wsl_unreachable`), and killing would lose a healthy client only the `.bat` can bring back. A timeout alone never kills on WSL
4. The launcher script is served at `GET /api/ziniao/launcher` — **generated at download time** by `ziniao_launcher.py` (below), with the located exe path baked in, so a double-click finds a custom install dir exactly like a default one

### `win_app_registry.py` — the Windows-registry lookup (App Paths / Uninstall)

The registry half of the install discovery, split out of the locator:
App Paths and the Uninstall table, through native `winreg` where
available and a UTF-8-pinned PowerShell fallback under WSL (which must
query the Uninstall CHILD keys — a container key queried without the
child wildcard returns its own empty values). Everything importers need
is re-exported from `win_app_locator`.

### `win_app_locator.py` — Windows install discovery (Ziniao & 悦互联)

One bounded locator for where a Windows client's main exe lives, shared by Ziniao and 悦互联 — a custom install dir (another drive, a Chinese-named folder) is found like the default one. Ordered hints; the first hit that **validates** wins:

1. `configured` — the account's `client_path` (a full `.exe` is operator intent)
2. `process` — the exe path **remembered when the client was stopped** (`kill_all_ziniao` feeds it before the launch that needs it; a live query is always too late)
3. `app_paths` / `uninstall` — registry via native `winreg` (under WSL, PowerShell with UTF-8 pinned on both sides, so non-ASCII paths survive any console code page): App Paths, then Uninstall `InstallLocation` → `DisplayIcon` → `UninstallString` dir (parsed, never executed)
4. `shortcut` — Desktop (user + Public) and Start Menu `.lnk` targets, read from the file by `lnk_file.py` in pure Python (`WScript.Shell` is ANSI-bound and breaks on non-ASCII paths under a non-matching system locale)
5. `conventional` — `X:\Program Files[ (x86)]\<name>`, `X:\<name>`, `%LOCALAPPDATA%\Programs\<name>` on every fixed drive, one level down only; no recursive disk scan

Validation means an existing `.exe` that is the app's **main** binary — exact name for Ziniao; for 悦互联 (whose exe name is a locale string) the largest non-rejected `.exe` directly in the dir — never an uninstaller, a Chromium kernel under `resources\`, or an Electron helper. A hit is cached until the file disappears. Every hint logs at INFO what it tried, and a miss returns `not_found_message` listing every searched surface and advising to open the client once by hand (never "set the path" — there is no UI for it). `run_powershell` (UTF-8 both sides) and `win_path_to_local` / `win_path_to_wsl` live here — moved out of `yuehulian_win.py` so both clients share them.

### `mac_app_locator.py` — macOS install discovery (shared by vendors)

The macOS sibling of `win_app_locator.py`: one vendor-neutral way to find, launch and inspect a vendor's Mac client wherever it was installed. A vendor module declares a `MacAppSpec` (`key`, `display_name`, `bundle_ids`, `app_names`); the shared file names no vendor. Ordered hints; the first `.app` bundle that **validates** wins:

1. `saved` — the vendor's saved client path: the `.app` bundle, the executable inside it, or a folder directly containing one of the spec's bundle names
2. `process` — a running process whose executable sits inside a bundle named in the spec (the client is already up)
3. `spotlight` — `mdfind` by `CFBundleIdentifier`, which finds the app anywhere Spotlight indexes
4. `applications` — `/Applications` and `~/Applications` by bundle name (what LaunchServices itself scans; the fallback when Spotlight excludes a folder or has not indexed yet)

Validation reads the bundle's `Contents/Info.plist` (`plistlib`) and requires `Contents/MacOS/<CFBundleExecutable>` to exist; nothing is executed during discovery. The result `LocatedMacApp` carries the bundle path, the executable and the bundle id. `not_found_message(spec)` lists every searched surface. Every hint logs at INFO what it tried.

- **`launch(located, args=())`** — `open <app> [--args …]`, detached. The args may carry secrets (an API key), so they are **never logged and never appear in errors**: the `RuntimeError` carries only the app path, the return code and `open`'s stderr (truncated), and a timeout or `OSError` is reduced to a fixed message / `strerror` (a `TimeoutExpired`'s own text embeds the full command line). `open --args` passes args only when the app is not already running.
- **`app_processes(located)`** — every process (psutil) whose real executable path is inside the bundle, main and helpers alike; the vendor layer filters (e.g. an Electron main has no `--type=` arg).

All of it is blocking (subprocess, process scan, filesystem): call it from async code through `asyncio.to_thread`. The system probes are injectable (`MacLocatorRunners`), so the unit tests run on Linux.

### `ziniao_utils.py` — Ziniao Utilities

Shared helpers used by both `ziniao.py` (task execution) and `ziniao_accounts` router (profile listing):

- `send_http(port, data)` / `try_connect_ziniao(...)` — Send a command to the Ziniao HTTP API
- `ensure_ziniao_running(port, client_path, user_info, ...)` — the one way to get the client answering in WebDriver mode; implemented in `ziniao_startup.py` (below)
- `launch_ziniao(client_path, port)` — start the client in WebDriver mode; fails at once when the launch itself is refused (macOS `open -a` exit code, missing `.exe`)
- `find_ziniao_exe_windows()` — resolve the installed `ziniao.exe` via the shared locator (`win_app_locator.py`, above); this is what turns the default bare `ziniao` client path into a launchable full path on Windows
- `is_ziniao_installed_mac()` — Spotlight `mdfind` check for ziniao.app (handles /Applications and ~/Applications)

### `ziniao_launcher.py` — the WSL launcher download

Builds the `ziniao_webdriver.bat` served at `GET /api/ziniao/launcher` (the file is **generated, not static**): the server runs the shared locator and bakes the exe path it found into the script, so the double-click launch works for a custom or Chinese-named install dir, not just the conventional ones. The script is UTF-8 with `chcp 65001` before any non-ASCII line (batch files are parsed in the console code page), tries the baked path first, and keeps the old fixed-path list as the fallback for a stale baked path (a reinstall to yet another folder). The kill/launch/port-probe steps are the same ones the static script always had.

### `ziniao_startup.py` / `ziniao_processes.py` — Client startup

- `ensure_ziniao_running` — probe; if nothing answers, one machine-wide **round**: stop every Ziniao process, launch, wait up to **120 s** (a request the client accepted may use all of what is left — Ziniao asks for ≥120 s per request), and on a miss stop everything and relaunch once more. Concurrent callers join the round. `ZiniaoStartError` carries `reason` + `task_category`; `ZiniaoStartingError` (a `BrowserBusyError` → 503) when a caller's `wait_s` runs out first. A client that accepted but is slow is never killed while this server has Ziniao browsers open
- `BrowserManager.ensure_ziniao_ready(store, db)` — the store-level entry (account conflict check, `live` flag); `start_session` calls it **before** taking the global launch lock — so Ziniao is touched only when a task actually uses the store's browser (never at task start), plus store setup and MCP. One round at a time: callers in a server join it, and a machine-wide OS lock (`app/browser/machine_lock.py`, freed by the OS when its process exits) serialises processes; a round is hard-capped so nothing stays locked
- `is_ziniao_process` / `kill_all_ziniao` — the one rule for "this is a Ziniao process": executable under a Ziniao-owned directory (install dir, `ziniaobrowser`, `SuperBrowserCores`, `ziniao.app`) or a `ziniao*` name; never the updater/installer, never by command line (a `pkill -f ziniao` would hit this server in a `*ziniao*` checkout)
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

### `adapters/yuehulian*.py` — Yuehulian Backend (Route B, Windows + macOS)

**悦互联 (Yuehulian)** is an ixBrowser OEM with **no local automation API**. Instead of HTTP (ziniao), it is driven by injecting a CDP debug port into the client-launched kernel:

- **`inject/`** — shared Route-B engine (`InjectCdpBackend`), the `InjectClient` boundary, and typed operator errors (their terminal/transient contract for failed starts: [Failed store-browser starts](#failed-store-browser-starts--the-typed-contract)) (`CLIENT_NOT_RUNNING` / `LOGIN_REQUIRED` / `ACCOUNT_MISMATCH` / `API_NOT_ENABLED` / `PLAN_REQUIRED` / `STORE_NOT_FOUND` / `STORE_UNAVAILABLE` / `UNSUPPORTED_KERNEL` / `CDP_UNREACHABLE` / `ACCOUNT_CONFLICT` / `IDLE_EXPIRED` / `UNSUPPORTED_PLATFORM`; the new vendor-neutral codes: `ACCOUNT_MISMATCH` — the client is logged into a different account than the saved one, never auto-logged-out; `API_NOT_ENABLED` / `PLAN_REQUIRED` — the client's local API is switched off / behind a paid plan (most vendors gate it, the #1 support case); `STORE_UNAVAILABLE` — the env exists but cannot be opened, carrying the vendor's own message; `UNSUPPORTED_KERNEL` — the env runs a non-Chromium kernel, and its surfaced text carries the stable `[UNSUPPORTED_KERNEL]` token so the UI's docs link keys on it, not on translatable prose). The engine's flow is `ensure_running` → `ensure_logged_in` → login gate → `open_store` → CDP verify (BOTH reachability AND the kernel family: `/json/version`'s `Browser` must be Chromium-family — `Chrome/` / `HeadlessChrome/` / `Chromium/` / `Edg/`, one constant; a non-Chromium kernel can expose a PARTIAL CDP — Firefox's old Remote Agent — which is reachable but not drivable, so the engine closes the store and raises `UNSUPPORTED_KERNEL` before `after_open` or the proxy) → **`after_open`** → `CDPMuxProxy`. `after_open` is an adapter hook (default no-op) for clients whose env opens on an intermediate page that must be clicked through to the real store page — its **ordering constraint** is load-bearing: it runs AFTER CDP is verified but BEFORE the CDPMuxProxy fronts the env, because the proxy's startup cleanup reaps orphan web tabs, so the page the hook produces must exist first (with `keep_last_page` it is kept). → **`after_open`** → `CDPMuxProxy`. `after_open` is an adapter hook (default no-op) for clients whose env opens on an intermediate page that must be clicked through to the real store page — its **ordering constraint** is load-bearing: it runs AFTER CDP is verified but BEFORE the CDPMuxProxy fronts the env, because the proxy's startup cleanup reaps orphan web tabs, so the page the hook produces must exist first (with `keep_last_page` it is kept). **`stop()` closes only what THIS server opened** (C4): the engine records the opened kernel's *browser GUID* (the `/json/version` `webSocketDebuggerUrl` tail — the same identity the mux records for its upstream) at open, and a later stop probes the recorded endpoint ONCE (no retries — stop runs under the manager's global lock): alive + GUID match ⇒ CDP `Browser.close` + the vendor's `close_store` (healthy path); alive + GUID differs ⇒ the port was reused by another kernel — close neither; dead ⇒ the kernel is gone and `close_store(ref)` could only close a kernel the user opened by hand on that profile — skipped. A vendor whose stop path genuinely must close-and-reopen (its client lists a dead env as Opened and only its own close clears that) declares `reopens_hand_opened=True` on its `BrowserVendorSpec` — the manager injects the flag into the browser config — and only then may the stale path close (yuehulian; its user docs describe close-and-reopen). The default is ATTACH: with the profile left open, the next `browser/start` finds it and the vendor's open answers with the existing endpoint.
- **`inject/client.py`'s `StoreRef`** — what a vendor's list returns per env: `name` (display), `platform`, and `ref` — the STABLE binding identifier a store is reopened by (an env id; empty means "the name is the ref", so name-keyed clients are unchanged). `available=False` marks an env that exists but cannot be opened (e.g. no device bound), with `unavailable_reason` carrying a TOKEN from a closed vendor-neutral vocabulary (`no_device` / `unsupported_kernel` / `expired` / `disabled` / `other`, constants in the module) that the frontend translates (`browserVendor.unavailable.<token>`) — never vendor-language prose, which leaked one language into the other's UI — and `unavailable_detail` optional free text (the vendor's own words) for a tooltip or fallback.
- **`adapters/yuehulian_win.py`** — Windows I/O: `ProcessLayer` (locate install — via the shared `win_app_locator.py`, so a custom dir like `D:\悦互联` is found through registry, shortcuts or conventional dirs, and a miss carries `locate_error` into the `ClientNotRunning` message; process matching is a case-insensitive prefix of the located dir plus the legacy `YueHuLian` name; Electron **main** pid, excluding the kernel `chrome.exe`; launch; kernel `--user-data-dir`s; `DevToolsActivePort`) and `InspectorSession` — a thin subclass of the **shared** `app/browser/win_inspector.py` (open the Node inspector via `ELECTRON_RUN_AS_NODE` + `process._debugProcess`; `Runtime.evaluate` incl. `webContents.executeJavaScript`; `require('inspector').close()`), to which any inject-driven adapter can hand just a PowerShell fragment resolving `$exe`. The shared session is machine-safe: one inspector per machine (`MachineLock`), a busy port that serves a session it did not open is refused, and the connected `process.pid` is verified — see its docstring; the yuehulian subclass adds only the spawn-hook install/uninstall helpers.
- **`adapters/yuehulian_dom.py`** — pure parsing + JS for the renderer's Element-UI UI (UIA is a dead end — accessibility is off): the store table (drops fixed-column duplicate rows), the login-state route hash, the click-Open-by-row JS (scoped to the store's own fixed-right action row — robust to the stateful Open/Opened label and to mixed states), **store-list navigation** (the list is a tags-view tab whose hash stays `#/index`, so it **clicks the menu item**, not the hash), **next-page** JS (the table paginates), and the **团队登录** form-fill JS (`fill_team_login_js` — native-setter + input/change so Element-UI's v-model updates).
- **`adapters/yuehulian_mac.py`** — the macOS I/O twin of `yuehulian_win.py`, picked by `get_platform()` in `yuehulian_client.py` (`_process_layer` / `_inspector_session`; the DOM layer is shared — the Mac client is the same Vue/Element-UI app). `MacProcessLayer` locates the client through the shared **`mac_app_locator.py`** (saved `client_path` → a running process inside a `悦互联.app` bundle → Spotlight `mdfind` by bundle id → `/Applications`/`~/Applications`; validated via `Info.plist`), launches it with `open`, and picks the Electron MAIN pid as the process whose exe is `Contents/MacOS/<CFBundleExecutable>` with no `--type=` arg (kernels live outside the bundle under `YueHuLian-Resources`, so they never match). Its inspector (`mac_inspector.py`) subclasses the shared `win_inspector.InspectorSession` and overrides ONLY `_trigger`: `SIGUSR1` to the main pid opens the Node inspector on 9229 (validated live — no `ELECTRON_RUN_AS_NODE` needed on macOS), inheriting the machine lock, busy refusal and pid verification. The Mac spawn hook (`MAC_SPAWN_HOOK_JS`) is the Windows twin with the kernel regex widened for the Mac kernel path (`…/YueHuLian-Resources/chrome/<ver>/Chromium.app/Contents/MacOS/Chromium` — the udd sits under `YueHuLianData/<env-id>` with spaces in the path; the client already passes `--use-mock-keychain`, never a debug port). `terminate_kernels(udd)` kills leftover kernels by pid (a SIGKILLed client orphans them, still holding the profile's singleton lock) — matched on exe under `YueHuLian-Resources` AND that env's exact `--user-data-dir`. The Mac client ignores TERM/AppleScript quit (only its own 安全退出 or SIGKILL stops it) and comes back logged-in after a relaunch.
- **`adapters/yuehulian_creds.py`** — the machine's one team-login credential in `app_settings` (password encrypted): `save_credentials`, `credentials_status`, `inject_yuehulian_config` (shared by the manager + the enumerate router).
- **`adapters/yuehulian_client.py`** — `RealYuehulianClient` composing the above. `ensure_logged_in` drives the 团队登录 form when logged out (no-op if already in; `LoginRequired` on missing creds / un-driveable form / a submit that never logs in). The form fill is **polled past a transient NOFIELDS** — the login tab's inputs render asynchronously after the tab click (seen live on the Mac client; a latent race on Windows too), so the same JS is retried a few times before giving up. `list_stores` navigates to the list then **pages through** collecting every store; `open_store` pages to the store's row, installs the spawn-hook (adds `--remote-debugging-port=0`, and on every kernel spawn `--disable-backgrounding-occluded-windows` — without it Chromium stops producing frames for a minimized/covered window and `Page.captureScreenshot` hangs), clicks Open, waits for the intercepted kernel's `--user-data-dir`, then reads a **fresh** (mtime-guarded) `DevToolsActivePort` (OS-aware: a native-Windows server reads `C:\…` directly, WSL reads `/mnt/…`). An already-open store reports a clear `CDP_UNREACHABLE`, not a misleading not-found. When the client still lists the store as **Opened** (an env left over from a client that was closed or crashed with it open, or one a person opened by hand — neither has a debug port), `open_store` closes it with the client's own batch **close browser** (only that row ticked, the selection read back until it settles, the confirm dialog accepted) and opens it again, so the next browser use recovers by itself. On top of that recovery, if the reopened kernel still cannot write its `DevToolsActivePort` because an **orphaned kernel** (a client killed with SIGKILL leaves its kernel re-parented, holding the env's singleton lock — macOS) still holds the profile, the process layer terminates leftover kernels for that env's dir (a no-op returning `[]` on Windows) and opens it once more. The platform's I/O layers are picked in two small seams (`_process_layer` / `_inspector_session`). Rows are matched by a cell reading **exactly** the store name, and every drive of the client GUI (list, login, open, close) takes one lock (`_ui_lock`), so store setup's list and a task's open never interleave on the same window.

From the CDP endpoint on it reuses `CDPMuxProxy` exactly like the other backends, and it is routed through the anti-detect prompt path (`is_antidetect`) like ziniao — minus ziniao's credential auto-fill (yuehulian envs are session-persisted). **One login per machine, auto-signed-in**: the team-login credential is configured in the create-store form (the 悦互联团队登录 section, like ziniao's add-account) and `InjectCdpBackend.start()` / the enumerate endpoint call `ensure_logged_in()` so a logged-out client is signed in automatically (`InjectClient`'s default is a no-op, so ziniao is unaffected). Store selection comes from `GET /api/yuehulian/stores`; the **aux** browser is a login-less Chromium like ziniao's; agent prompts name the actual product (悦互联), never "Ziniao". **Windows (incl. WSL-on-Windows) and macOS**; refused elsewhere (plain Linux) at runtime and hidden in the UI there. A live smoke (`tests/e2e/yuehulian_live_smoke.py`, not in CI) drives the real client through every state.

### `adapters/wenmai*.py` — Wenmai Backend (Route B hybrid; Windows native + WSL + macOS)

**稳卖 (Wenmai)** is the inject family's hybrid: its client ships a **local HTTP API** (Express, **port 3980**, running on every launch), so list / open / close go over HTTP like ziniao's — no GUI scraping, no port injection — with the vendor's documented **0.5 s rate limit** honoured by a per-loop serialised gate. The kernel's OWN debug endpoint comes back from `openEnv` (`selenium`): even a store a person opened by hand has one, and opening an already-open store returns the SAME pid and port, so nothing is ever closed and reopened. The **Node inspector** (the shared `app/browser/win_inspector.py`) is used only for what the API cannot do: the **enterprise login form** (the official API has no password login — the cloud login response is encrypted; the phone tab has an image captcha) and the store-entry middle page's 打开店铺 click (over CDP, after open).

- **`adapters/wenmai_client.py`** — `RealWenmaiClient`: `is_logged_out` (ONE function — the docs claim 401; the real shapes are a top-level `status:"error"` envelope and `getUserInfo` code 200 with `token: null`) and `map_error` (ONE function to the shared taxonomy). `open_store` verifies the returned endpoint answers and recovers a wedged env (the client can pick a debug port in a Windows reserved range; the kernel's bind silently fails and closeEnv does not clear it): closeEnv → re-open → kill exactly the kernel matching all three of the port flag, the 稳卖 kernel exe path and this store's env id in `--user-data-dir` — nothing else on the machine is ever touched. A **Firefox-kernel env raises `UnsupportedKernel`** (no `webdriverPath` in the openEnv response; and the engine's Chromium-family check catches one that does expose a partial CDP).
- **`adapters/wenmai_dom.py`** — pure JS: the enterprise login fill (placeholder-matched, native-setter + input events), the middle-page click + readiness probe (matched by the middlePage PATH, never a hardcoded extension id).
- **`adapters/wenmai_win.py`** — the process layer; the WENMAI locator spec lives HERE (vendor knowledge stays in the vendor module), main pid = the 3980 listener owner.
- **Credentials**: `company` / `username` / `password` (enterprise login), injected into the browser config as **`wenmai_<field>`** by the machine-credential storage — the spec's field keys stay neutral. **One account per machine.**
- **Platforms**: Windows (native — server, agent, wrapper and browser all on Windows), WSL (a WSL server driving the Windows client) and macOS are supported. A live smoke (`tests/e2e/wenmai_live_smoke.py`, not in CI) drives the real client through every state; see [docs/wenmai.md](wenmai.md) for the user-facing guide.

## Failed store-browser starts — the typed contract

When a store-browser start fails with an inject-taxonomy error
(`app/browser/inject/errors.py`), the start route
(`POST /api/stores/{id}/browser/start`) answers a TYPED body —
`detail.code` (the stable code), `message`, `hint` — instead of a bare
`HTTP 500`, and the wrapper prints a typed line the agent can act on.
A bare 500 taught a task agent to treat the failure as a puzzle and
SSH into a production machine (incident 97db66d2); this contract makes
that class of improvising impossible.

**Terminal vs transient** (`is_terminal` in `inject/errors.py`):

- `TERMINAL_CODES` — `UNSUPPORTED_KERNEL`, `STORE_UNAVAILABLE`,
  `STORE_NOT_FOUND`, `PLAN_REQUIRED`, `API_NOT_ENABLED`,
  `ACCOUNT_MISMATCH`: only the operator can fix these. The task is
  ended FAILED with the typed error and the snake-cased code as
  `error_category` (so the failure banner and its docs link render),
  and the agent is stopped — through the ONE external-stop writer,
  `task_finalize.stop_as_failed` (see
  [docs/tasks.md § Terminal-state writers](tasks.md#terminal-state-writers)).
- `LOGIN_REQUIRED` is terminal **only without stored credentials**
  (`LoginRequired.credentials_stored=False`, the default): no stored
  login means only the operator can fix it. With credentials that
  were tried and did not take (`credentials_stored=True`, set by the
  yuehulian client's login attempts), it is NOT terminal: the
  operator's fix (re-saving them) is not required for a retry to
  succeed — a slow client or a form needing a second attempt recovers
  on retry — so ending the task would be wrong.
- `TRANSIENT_CODES` — `CLIENT_NOT_RUNNING`, `CDP_UNREACHABLE`: the
  wrapper retries exactly ONCE
  (`START_TRANSIENT_RETRY_SLEEP_S`), then reports the typed line;
  the server never fails the task for these.

**The wrapper's line** (`app/browser/wrapper_start.py`): a typed
failure prints `ERROR: [CODE] <hint>` plus an explicit "report this
to the user and stop; do NOT investigate the browser client, its
files or its local API" — even on the second transient failure. A
body with NO taxonomy code (a backend outside the inject family,
e.g. Ziniao) prints exactly the pre-contract output: Ziniao's errors
are never reclassified.

**The `X-Vibe-Task` header**: the wrapper's start POST names the task
it is starting the browser for, so the server can end that task. The
header is agent-reachable (the wrapper's credentials live in its
env), so it only ever SELECTS a task — the task must be THIS store's
own RUNNING/DESIGNING run or it is ignored (a QUEUED, PLANNED or
WAITING task is waiting for its turn or a person, not a browser; the
state machine would allow FAILED from those, hence an explicit check
in `app/browser/start_failure.py`). The operator MCP
(`app/agent_mcp/tools_stores.py` at `/api/mcp`) calls the route with
no Request and no task id: it gets the typed body only.

The deterministic stand-in for tests and repros is the
`fake_start_vendor` fixture (see
[docs/testing.md](testing.md#the-fake-start-vendor-fixture)).

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
- **Relaunch circuit breaker** (`VIBE_BROWSER_RELAUNCH_MAX`, default 3,
  per `VIBE_BROWSER_RELAUNCH_WINDOW_S`, default 600s; 0 disables): the
  counterpart to `stop_session` above. `start_session` relaunches a
  store's whole env whenever its mux looks dead, and the wrapper reaches
  that path on *every* `browser-use` call — so a wedged Ziniao client
  turned recovery into a restart storm that wiped agent tabs mid-task and
  deepened the wedge. Past the budget the start fails with an actionable
  error; a healthy launch clears it.
- **Bounded launch** (`VIBE_BROWSER_START_TIMEOUT_S`, default 180s; 0
  disables): `start_session` holds a **global** lock — deliberate, it
  serializes Ziniao `startBrowser` so the shared client isn't hit
  concurrently — so an unbounded per-store retry loop starves every other
  store's launch. `backend.start()` is wrapped in `asyncio.wait_for`; on
  timeout the half-started env is torn down (per-store `stopBrowser`) and
  only that store fails.
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
`psutil.environ()` `AccessDenied`). On POSIX, `BH_RUNTIME_DIR` stays at
`$VIBE_HOME/r` when even the maximum 64-character session's socket fits
macOS's 104-byte `sun_path` (including the NUL). A longer home falls back
to a private deterministic `/tmp/vs-<hash>` directory; `iter_pidfiles()`
then scans both the new and old directories, so upgrading cannot orphan
existing daemons. The pid *record* has two shapes on
live browser-use versions — a plain integer (what our wrapper-spawned
daemons write: the wrapper always exports `BU_CDP_WS`, so upstream takes
its non-local spawn branch) and the spawn-lock JSON
`{"pid": N, "started": <fingerprint>}` (browser-use ≥ 0.13.9,
local-Chrome spawn path, retained by the daemon) — and
`parse_pid_record` (`bh_daemons.py`) accepts both. A reader that assumed
the plain int treats the JSON as corrupt and unlinks a live daemon's
files without killing it. It runs every 5 minutes:

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
2. **Periodic reaper** (`daemon_reaper` job of the central sync scheduler, `daemon_reaper.py`): every 5 minutes and once at boot, same orphan detection logic
3. **Server startup** (`cleanup_stale_sessions()` in `manager.py`): in-place-upgrade safety — warns if the installed browser-use is `< 0.13` (`warn_on_browser_use_version_mismatch()`), wipes stale auto-generated wrapper scripts (`_wipe_generated_wrappers()`, so a pre-upgrade 0.12-shaped wrapper is never invoked before the next task regenerates it), then calls `reap_orphaned_daemons()` to clean up daemons from the previous run (both 0.13 pid-file and legacy 0.12 argv) while preserving daemons for active tasks (e.g. WAITING tasks that survive restart)

**Design Rationale**:
Cleanup at `start()` was removed because it caused a race condition: if a task was retried quickly, the new session could start its daemon, then the cleanup from the new `start()` would kill the freshly-started daemon. Cleanup now only happens at `stop()` (guaranteed to kill the old daemon) and via the periodic reaper (for orphaned daemons when server restarts).

**Note**: Live-process discovery goes through `find_processes_by_pattern` and killing through `kill_with_escalation` (`process_utils.py`), both `psutil`-backed for cross-platform support.

## browser-use 0.13 version notes (audited 2026-11-05)

The server pins `browser-use>=0.13.14,<0.14` (`pyproject.toml`); the
cap keeps a newer uv from resolving a 1.0 pre-release. The
integration surface we depend on is unchanged from 0.13.3 through
0.13.14: the `browser-use`/`bu`/`browser` console scripts, the heredoc
code-eval CLI, `BU_NAME`/`BU_CDP_WS`/`BU_CDP_URL`/
`BH_RUNTIME_DIR(_SHARED)`/`BH_TMP_DIR(_SHARED)`, daemon files
`bu-<BU_NAME>.pid`/`.sock` under `BH_RUNTIME_DIR`, `--doctor`,
`--reload`. What changed between those versions:

| Version | Change | Affects us |
|---|---|---|
| 0.13.9 (browser-harness 0.1.12) | daemon pid records *can* be JSON (`{"pid", "started"}`) — the local-Chrome spawn path publishes it and the daemon retains it; our wrapper path (always `BU_CDP_WS`) still writes plain ints | yes — `parse_pid_record` accepts both shapes |
| 0.13.9 | `-c`/`--code` become an explicit removal hint (exit 2) — they never executed code on any 0.13.x (0.13.3 already fell through to the harness USAGE); heredoc stdin works on every 0.13.x | skill mandates the heredoc form |
| 0.13.9 | new helpers `activate_tab`, `start_recording`/`stop_recording`; `switch_tab` gains an `activate=` kwarg | optional; marked "(browser-use ≥ 0.13.9)" in the skill |
| 0.13.14 | nothing in the harness layer — `browser-harness` 0.1.13 is byte-identical to 0.1.12 (changes are in browser-use's own agent/LLM/integration code) | no |

The agent-facing skill (`app/skills_v2/browser-harness/SKILL.md`) stays
valid across 0.13.3–0.13.14 — it syncs to clients on any of them, so
version-specific helpers there carry an explicit minimum-version note.
Full diff record: [browser-use-0.13-migration.md](browser-use-0.13-migration.md).

## Adding a New Backend / Vendor

An anti-detect browser vendor is one vendor module plus translations and
one docs page — no shared-code edits (`test_no_vendor_literals.py` fails
the PR otherwise):

1. Create the vendor module (`app/browser/adapters/myvendor.py`):
   the `InjectClient` implementation, the `BrowserBackend` subclass, and
   the `BrowserVendorSpec` (display names, credential fields, docs
   links, `client_factory`; see `vendors.py` above).
2. Register it in `builtin_plugin.py` (or a private plugin's
   `install()`): `ctx.register_browser_backend('myvendor', MyBackend,
   MYVENDOR_SPEC)`.
3. Add the i18n strings under the spec's `i18n_prefix` and the docs page
   `docs/myvendor.md` (+ `.en.md`).

The generic routes (`routers/browser_vendors.py`), the generic picker
(`components/VendorSection.tsx`), the manager's credential injection and
the install locator all pick the vendor up from the registry. A plain
(non-vendor) engine backend is still `BrowserBackend` from `base.py`,
registered the same way with `spec=None`.

## § AdsPower

`app/browser/adapters/adspower.py` — the textbook
`LocalApiClient` vendor (list / start / stop on `127.0.0.1:<port>`
with a Bearer API key). `AdsPowerClient(LocalApiClient)` sets the
uniform parts as class attributes (`vendor_key`, `auth='bearer'`,
`auth_field='api_key'`, `min_interval_s=1.2`, GET paths with the query
strings embedded in `list_request` / `open_request` /
`close_request`) and `next_page` (page until a short page). The whole
vendor declaration is the `ADSPOWER_VENDOR` `BrowserVendorSpec` in
`adspower_backend.py` (single account, one `api_key` credential field,
platforms {windows, mac, wsl}, the docs links, the `win_app`
descriptor); the thin `AdsPowerBackend` engine wrapper lives beside it;
the platform layers (win / mac / common) sit behind the ONE picker
`_platform_layer_module()`.

What the base cannot express stays as vendor overrides (each
unit-tested in `tests/unit/test_browser/test_adspower_*.py`):

- **Port discovery**: the client writes its CURRENT base URL to its
  own `local_api` file (`%APPDATA%\adspower_global\cwd_global\
  source\local_api` on Windows, `~/Library/Application Support/...`
  on macOS); the default 50325 is only used when free (Hyper-V/WSL
  port exclusions can reserve it forever), so `discover_port` reads
  the file (via `to_thread`) and `api_call` re-reads it ONCE when a
  call cannot connect (the port changes on every client launch). An
  explicitly configured `api_port` pins the whole machinery to
  probe-only.
- **D4 `ensure_running`**: probe `/status` → launch the GUI
  (remember-me signs in) → a 30 s grace for a shell someone else just
  started → a headless instance started with the API key (the
  documented `--headless=true --api-key=<key>` flags; the key rides
  the environment on Windows — never the PowerShell script text — and
  argv only on macOS). The GUI is never killed; the two coexist.
- **D7 failed-start decision**: a code-0 answer with an empty
  endpoint is ambiguous (a Firefox kernel AND an orphaned SunBrowser
  of a killed client both answer that way) — kernel process evidence
  (`--user-data-dir` names the profile) decides, and only THAT
  profile's process tree is ever killed. The list carries no kernel
  field, so the picker cannot grey Firefox profiles out; the ENGINE's
  P9 check refuses a non-Chromium endpoint at open.
- **R11 kernel states**: `is updating` (a download IS running —
  Windows GUI, ready in ~40 s) polls the full budget with the
  「正在下载」 hint; `is not ready` (the kernel is MISSING — nothing
  downloads it from a background session, verified on macOS) polls
  briefly and fails with the actionable 「打开 AdsPower 下载内核」
  hint naming the kernel. A poll that saw `is updating` at any point
  keeps the downloading hint (the GUI shape is updating → not ready
  → ready).
- **The bounded rate-limit resend**: AdsPower rate-limits with HTTP
  200 + a `code:-1` body (`is_rate_limited` override); `api_call`
  re-sends up to three rounds beyond the base's single retry.
- **The not-authenticated shape** (`Require api-key`, no key stored)
  raises LOGIN_REQUIRED per the shared contract — AdsPower has no
  session-expiry shape (a still-valid key keeps working signed out);
  "the client is signed out entirely" is decided at the process level
  in `ensure_running`. The generate-a-key advice lives in the
  `vendors.adspower.LoginRequired` i18n override; a keyed client's
  `API Key mismatch` maps to `AccountMismatch`.

Contract fixture: `tests/unit/test_browser/local_api_vendors/
adspower.py` (the B9 convention — never edit the shared
`local_api_contract.py`).

## Planned Backends

- **Custom CDP**: Connect to any browser already exposing a CDP endpoint. Start/stop are no-ops.
