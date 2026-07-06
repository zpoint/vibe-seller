# Ziniao concurrency & the "browsers kill each other" outage

> Deep-dive on how the Ziniao WebDriver client actually launches browsers,
> why running an all-store scheduled fan-out used to make every store's
> browser die, the full root-cause investigation, and the fix.
> Code touch-points: `app/browser/ziniao.py`, `app/browser/ziniao_utils.py`,
> `app/browser/cdp_mux_proxy.py`.

## TL;DR

- Ziniao **does** support running multiple store browsers concurrently
  (proven against the official demo: 4/4 stores opened at once).
- Ziniao's `startBrowser` is **flaky**: it sometimes returns a
  `debuggingPort` whose DevTools endpoint never initialises — a
  nondeterministic, **per-store** "stale launch".
- Our old recovery for a stale launch was `kill_and_relaunch_ziniao()` →
  **`pkill -9` on the entire Ziniao client**. Under a multi-store fan-out
  that meant recovering one store **destroyed every other store's live
  browser**, cascading into a machine-wide outage.
- **Fix**: recover **per store** (`stopBrowser` this env + retry
  `startBrowser`), never restart the shared client from the per-store
  path; when a client restart is genuinely needed, do it **gracefully**
  (`exit` action, not SIGKILL); call `updateCore` once up front; and make
  the CDP-proxy self-heal hook re-open only its own store.

## How Ziniao WebDriver mode works

Reference: the official demo — **https://github.com/ziniao-open/ziniao_webdriver_demo**
(cloned to `~/Desktop/ziniao_webdriver_demo` during the investigation).
Ziniao WebDriver docs: `open.ziniao.com` docId 98 (API) & 99 (enabling
the WebDriver permission).

The Ziniao client is a single desktop app launched in automation mode:

```
open -a ziniao --args --run_type=web_driver --ipc_type=http --port=16851
```

It then exposes a **single local HTTP control API** on `--port` (default
`16851`). You drive everything by POSTing JSON `{"action": ...}` to it:

| action | purpose |
|---|---|
| `getBrowserList` | list the account's store browsers (`browserOauth`, `browserName`, `siteId`, …) |
| `updateCore` | download **all** browser kernels; loop until `statusCode 0` |
| `startBrowser` | launch one store's browser → returns `debuggingPort`, `browserPath`, `core_version` |
| `stopBrowser` | close one store's browser |
| `exit` | quit the whole client gracefully |

Each `startBrowser` launches an **independent Chromium** for that store
profile with its own **rotating** CDP `debuggingPort`. You then attach a
CDP client to `127.0.0.1:<debuggingPort>` — the demo uses Selenium/
chromedriver via `debuggerAddress`; we attach our `CDPMuxProxy` and speak
CDP directly. The control API is **shared and global** — there is exactly
one client per machine ("one account per machine"), but it can host
**many** concurrent store browsers.

The official demo's `__main__` sequence is the canonical recipe:

```
download_driver()          # fetch all chromedrivers
kill_process()             # SIGTERM (killall), once, with confirmation
start_browser()            # launch client in web_driver mode
update_core()              # download ALL kernels BEFORE opening stores
get_browser_list()
ThreadPoolExecutor(thread_num=3).map(open_one, stores)   # CONCURRENT
```

On a failed open the demo calls `close_store(that_store)` and moves on —
**it never kills the shared client mid-run.**

## The outage

Symptom: trigger the monthly all-store report schedule (立即执行) → it
fans out one task per store → all the stores' browsers fail to connect,
tasks die with `Error: connecting to ws://127.0.0.1:922x/client-…`.
Happened even on a freshly restarted server.

### Reproduction (deterministic)

1. Restart the server.
2. Trigger the all-store schedule, or fire
   `POST /api/stores/{id}/browser/start` for the 4 Ziniao stores.
3. Watch `logs/backend_7777.log`.

Observed cascade (real capture, store names anonymised):

```
proxy 9222 → 30836   (store-A browser up)
store-A task: open sellercentral… → SUCCESS
next store's startBrowser → "stale launch" → Killing Ziniao   ← global pkill -9
    …which kills store-A's browser (30836) too
manager: "CDP proxy :9222 not responding for store-A — forcing restart"
    → stops proxy WITH store-A's live task daemon attached (WS code 1001)
store-A task next command → "connecting to ws://…9222/client-… failed"
```

## Root-cause investigation (what we proved)

| Experiment | Result |
|---|---|
| Official demo, 2 & 4 stores concurrent, healthy client | **4/4 connected** — concurrency works |
| Same demo, repeated back-to-back | **flaky: 4/4 → 2/4 → 0/4** — nondeterministic |
| `updateCore` first, then open | completes, stale still happens → **not the fix** |
| Our `ZiniaoBackend.start`, kill neutralised + updateCore, 4 concurrent | 0/4 (client degraded) |
| Our code sequential (one at a time) | still stale |
| SIGKILL-free cycle, twice | run1 0/4, run2 4/4 — **flaky even without SIGKILL** |

Findings:

1. **Ziniao concurrency is real** — the reference implementation opens 4
   stores at once and all four CDP endpoints answer `/json/version`.
2. **`startBrowser` is inherently flaky** — a launch can come back
   `statusCode 0` with a `debuggingPort` that never binds a working
   DevTools endpoint. Nondeterministic, per-store, independent of
   `updateCore`, SIGKILL, or concurrency. It is a Ziniao-side flake we can
   only **tolerate**, not eliminate.
3. **`pkill -9` degrades the client.** Repeated SIGKILL churn drives the
   client into a state where *most* launches stale — so the very recovery
   we used made the problem worse and more persistent. Graceful `exit`
   avoids this.
4. **The demo tolerates the flake per-store; we amplified it globally.**
   One store's flake triggered a whole-client `pkill -9`, taking down
   every peer → cascade + the manager's health-check force-restart tearing
   down proxies that still had live task daemons.

**Root cause, one line:** Ziniao hands us an occasional, isolated,
retryable *per-store* flake; our code responded by nuking the whole
machine's browsers, converting a one-store retry into an all-store outage.

## The fix

Implemented in `app/browser/ziniao.py` + `app/browser/ziniao_utils.py`:

1. **Per-store recovery, no shared-client kill.** On a stale launch
   `start()` issues `stopBrowser` for *that* env and retries
   `startBrowser` (bounded, `MAX_ZINIAO_ATTEMPTS`). It never calls
   `kill_and_relaunch_ziniao`. If all attempts stale, it raises for **this
   store only** — peers are untouched; the task can be retried/re-queued.
2. **Graceful-first client restart.** `kill_and_relaunch_ziniao` (still
   used by the *user-initiated* force-restart endpoints) now tries the
   `exit` action first and only falls back to `pkill -9` if the client
   ignores it.
3. **`updateCore` up front.** `start()` calls `update_ziniao_core()` once
   after the client is confirmed running, mirroring the demo, so a
   per-store launch never blocks on a kernel download.
4. **CDP-proxy self-heal is per-store.** The `relaunch_upstream` hook
   (added in the self-heal PR) previously called the global kill; it now
   re-opens only its own store (`stopBrowser` + `startBrowser`) to obtain
   a fresh `debuggingPort`, never restarting the shared client.

Net effect: Ziniao's real concurrency is preserved, the unavoidable
per-store flake is isolated to a retry (not an outage), and no store's
recovery can ever tear down a peer.

## Tests

- **CI-safe unit** (`tests/unit/test_browser/test_ziniao_per_store_recovery.py`):
  mocks the Ziniao API, forces stale launches, and asserts recovery is
  per-store `stopBrowser` + retry with **no** `force_kill_ziniao` /
  `kill_and_relaunch_ziniao` call.
- **Local-only e2e** (`tests/e2e/test_ziniao_concurrency_recovery.py`,
  marked `e2e` + `ziniao`, **never in CI**): concurrent multi-store starts
  emit no global-kill markers; a scheduled fan-out survives
  `./restart.sh --dev`; killing the client auto-relaunches it. Run with:

  ```
  ./start.sh 7777
  pytest tests/e2e/test_ziniao_concurrency_recovery.py --e2e -v -m ziniao
  ```

## Operational note

If the Ziniao client gets into a degraded state (most launches stale — can
happen after heavy `pkill -9` churn), the reliable reset is a **clean
restart**: `exit` action (or quit from the GUI), wait, relaunch. A single
graceful cycle restores concurrent launches; SIGKILL loops do not.
