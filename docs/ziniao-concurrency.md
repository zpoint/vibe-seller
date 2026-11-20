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
  nondeterministic, **per-store** "stale launch". A second shape of the
  same flake: the port *does* bind, but Ziniao's own SBP layer never
  injects into the env's pages — no account auto-fill, no
  `紫鸟验证码服务` OTP panel, no `已托管账号Passkey` overlay, and
  `navigator.webdriver` left unmasked at `true`. Both shapes are treated
  as a stale launch (see § The fix, item 1).
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

   Readiness is **two gates**, both on the raw Ziniao port before the mux
   is built. `_cdp_port_reachable` proves *Chrome* is up;
   `_instrumentation_live` proves *Ziniao* is. The second evaluates
   `navigator.webdriver === true` on a real http(s) page — a positive
   read means SBP never injected, so the env has no auto-fill/OTP/passkey
   and leaks automation to the platform (which then bounces every page to
   the login wall). A port-only gate waved that env through and handed an
   agent a browser it could not log in with. The instrumentation gate is
   deliberately **fail-open**: only a positive `webdriver === true` is a
   verdict; unreachable, eval error, or no-page-yet all pass, so a probe
   bug can never become a store-wide outage.
2. **The shared client is restarted only when its API is not
   answering.** A per-store problem never restarts it. The one place
   that does — the client startup round, § Client startup below — runs
   only when nothing answers on the control port (normal mode, not
   running, leftovers, hung with no store browser of ours open), which
   is also why it cannot ask the client to `exit` politely first.
3. **`updateCore` up front.** `start()` calls `update_ziniao_core()` once
   after the client is confirmed running, mirroring the demo, so a
   per-store launch never blocks on a kernel download.
4. **CDP-proxy self-heal is per-store.** The `relaunch_upstream` hook
   (added in the self-heal PR) previously called the global kill; it now
   re-opens only its own store (`stopBrowser` + `startBrowser`) to obtain
   a fresh `debuggingPort`, never restarting the shared client.
5. **The per-store relaunch is budgeted.** Per-store is necessary but not
   sufficient: `BrowserManager.start_session` also relaunches a store's
   whole env whenever its mux looks dead, and the wrapper can reach that
   path on *every* `browser-use` call. With the client wedged, each
   relaunch fails the same way, so the recovery became its own storm —
   three stores cycling stop/start every ~20 s for five minutes, which
   hammered the shared client until it hung and left every agent on a
   freshly-wiped, logged-out browser. `_note_relaunch` bounds it to
   `VIBE_BROWSER_RELAUNCH_MAX` per `VIBE_BROWSER_RELAUNCH_WINDOW_S` per
   store (cleared on a healthy launch), and `backend.start()` is capped by
   `VIBE_BROWSER_START_TIMEOUT_S` — `start_session` holds a **global**
   lock (deliberate: it serializes `startBrowser` so the shared client
   isn't hit concurrently), so an unbounded per-store retry loop starves
   every other store's launch. One broken store now fails fast instead of
   taking the machine with it.

Net effect: Ziniao's real concurrency is preserved, the unavoidable
per-store flake is isolated to a retry (not an outage), and no store's
recovery can ever tear down a peer.

## Waiting on the global lock is bounded too (2026-08-24)

The lock above is what makes one store's launch everyone's business, and
`start_backend_bounded` caps how long a launch may *run*. It did not cap how
long a peer may **wait**, and an `asyncio.Lock` waits for ever.

So a wedged holder stopped being a slow launch and became a **hang**:

```
POST /api/stores/{id}/browser/start      (proxies unset)
  → HTTP 000 (240.003041s)
```

No status, no body, nothing naming what to restart. The agent's own reading was
*"the infrastructure is broken"* — which is all `HTTP 000` can mean — and no
retry could help, because the next call queued behind the same holder. A hang is
worse than a failure here: the task looks alive, its budget drains, and the
wrapper's own 90 s curl gives up before the server has said anything at all.

`GlobalLaunchLock` (`app/browser/launch_guards.py`) bounds the **wait**:

- `VIBE_BROWSER_LOCK_WAIT_S`, default **60 s** — deliberately shorter than the
  wrapper's 90 s curl, so the caller gets a real response, and shorter than
  `VIBE_BROWSER_START_TIMEOUT_S` (180 s), so a peer's legitimate launch is
  reported rather than waited out.
- The refusal **names the holder and how long it has been in there**, which is
  the one fact that separates *"somebody is launching, retry shortly"* from
  *"held past the launch cap — restart Ziniao"*.
- Routes map it to **503, not 500**: nothing is known to be wrong with this
  store.
- **Nothing is force-released.** The holder may be inside Ziniao's client, and
  interrupting it is how a half-built env is left behind. This bounds waiting,
  not the work.
- `0` disables the ceiling, for an operator who wants the old behaviour back.

## Client startup: always relaunch, kill-all, 120 s deadline (2026-11-20)

**Before:** whether a Ziniao that was not answering got fixed depended on
who asked. The execute/wake pre-check relaunched it (under the global
launch lock) before the agent ran — whether or not the task would ever
open the browser — `POST /browser/start` only with `?force=1`, and store
setup showed a "close Ziniao, Refresh, then Force Restart" panel. A launch was one try of 30 × (2 s + ≤10 s request);
on Windows 72 % of our launches timed out.

**Now there is one behaviour** (`app/browser/ziniao_startup.py`), and it
runs **only when Ziniao is actually used**: a task's first use of the
store's browser (the store wrapper's `POST /browser/start`), store setup's
store list, MCP `browser(start_store)` / `manage_ziniao_account(browsers)`.
A task that never opens the browser never touches Ziniao — a Ziniao the
user opened by hand stays open. No flag, no prompt.

1. **Probe** `getBrowserList` (connect ≤5 s, read ≤10 s). Any JSON
   answer — even `-10003` — is WebDriver mode: done. Unchanged fast path.
2. **Accepted but silent** (read timeout): one more probe with 20 s.
   Still silent and **this server has Ziniao store browsers open or a
   launch in progress** → leave it alone: its kernels still serve CDP,
   and killing it would recreate the outage above. On WSL a timeout
   never kills either.
3. Otherwise a **round**. Only one runs at a time, and nothing can stay
   locked for ever:
   - within a server, concurrent callers **join** the round in flight (a
     second kill-all would kill the first round's launch);
   - across processes — two servers of the same user, or the old and the
     new process of a restart — a machine-wide **OS lock**
     (`app/browser/machine_lock.py`: `flock` / `msvcrt.locking` on
     `~/.vibe-seller-ziniao-startup.lock`) lets one round run at a time.
     The OS drops it when its process exits (crash, `kill -9`, restart),
     so a lock file left on disk never blocks anyone. Having taken it, a
     round **looks again** before killing: another process may have just
     brought Ziniao up;
   - a round has a hard cap (`ROUND_CAP_S`, 360 s) — whatever hangs
     inside, it ends (`round_stuck`) and gives the lock back, and the next
     caller starts afresh;
   - stop **every** Ziniao process (`app/browser/ziniao_processes.py`) —
     Ziniao's own WebDriver doc (`ziniao-open/skill-hub`,
     `ziniao-webdriver-doc`, trap 3): before each launch and before a
     retry, kill the main client *and* its kernels, or leftovers keep the
     port. A name match is not enough: on Windows the client also runs
     `env-kit.exe` from its install dir and a `node.exe` under
     `%APPDATA%\ziniaobrowser\env-kit`, next to `ziniaobrowser.exe` /
     `superbrowser.exe` kernels. The rule is "executable under a
     Ziniao-owned directory, or a `ziniao*` name", never the updater,
     never a command-line match. POSIX: SIGKILL the client (it respawns
     kernels and survives SIGTERM), SIGTERM kernels with 3 s grace, then
     clear `Singleton*` in their `--user-data-dir`s (see the runbook
     below). Survivors (started with admin rights) fail fast
     (`kill_failed`); a foreign program on the port fails fast
     (`port_in_use`);
   - launch, then poll until a **120 s deadline**. A refused connection
     is re-polled every 2 s (a client that binds at 8 s is seen at
     ~8-10 s, as before); a request the client *accepted* gets every
     second left as its read timeout — the first `getBrowserList` on a
     cold client signs in, Ziniao asks for ≥120 s per request, and the
     old 10 s cap cut it short so the next request started over;
   - deadline missed → stop everything again, relaunch once, another
     120 s. Then `startup_timeout`, cached for
     `VIBE_ZINIAO_START_COOLDOWN_S` (180 s) so a fan-out or a retrying
     agent cannot kill-and-relaunch in a loop. Store setup (a click) is
     not held back.
4. **Who waits how long.** `POST /browser/start` runs it **before** the
   global launch lock (a
   round can take ~4 min and must not starve other stores), and its
   Ziniao wait plus its lock wait share one `VIBE_BROWSER_LOCK_WAIT_S`
   budget, so it answers before the wrapper's 90 s curl: 503 "Ziniao is
   starting" while the round goes on; the wrapper re-POSTs on 503 up to 3
   times. Store setup answers 503 `{"status":"starting"}` after 45 s and
   the page asks again with the spinner and the elapsed time.
5. **Accounts.** Only one Ziniao account runs per machine. A caller for
   another account never kills the client while the first account's
   stores are open, or while the client its last round brought up still
   answers (`ziniao_account_conflict`).
6. **WSL** cannot launch (Electron rejects the flags via interop). It
   stops every Ziniao process only when Windows shows no listener on the
   port, then points at `ziniao_webdriver.bat` (which now stops every
   Ziniao process too and waits 120 s); a listener WSL cannot reach is a
   networking error, nothing is killed.

Errors carry their cause as data (`ZiniaoStartError.reason`), never
parsed from text.

## Tests

- **CI-safe unit** (`tests/unit/test_browser/test_ziniao_startup.py`, on
  `ziniao_fake_host.py` — a fake machine at the psutil / subprocess /
  httpx / clock boundary): normal mode killed and relaunched without
  error; leftover kernels (by path) killed, updater kept; a 30 s first
  answer succeeds; a dead launch is relaunched once; two fail once with
  the reason; concurrent callers share one round; a hung client with
  live stores is left alone; survivors / port in use fail fast;
  cooldown; WSL kill + `.bat`, never kill an unreachable listener; a
  caller past its wait gets "starting" while the round goes on.
- **CI-safe unit** (`tests/unit/test_browser/test_launch_lock.py`): a wedged
  holder is refused rather than waited out, the lock survives the refusal, and
  the message distinguishes contention from a stuck holder.
- **CI-safe unit** (`tests/unit/test_browser/test_ziniao_per_store_recovery.py`):
  mocks the Ziniao API, forces stale launches, and asserts recovery is
  per-store `stopBrowser` + retry with **no** client kill-all
  (`kill_all_ziniao`) or relaunch (`launch_ziniao`) call.
- **Local-only e2e** (`tests/e2e/test_ziniao_concurrency_recovery.py`,
  marked `e2e` + `ziniao`, **never in CI**): concurrent multi-store starts
  emit no global-kill markers; a scheduled fan-out survives
  `./restart.sh --dev`; killing the client auto-relaunches it. Run with:

  ```
  ./start.sh 7777
  pytest tests/e2e/test_ziniao_concurrency_recovery.py --e2e -v -m ziniao
  ```

## Runbook: recovering a wedged Ziniao client ("stale launch" everywhere)

**Symptom:** `startBrowser` returns `statusCode 0` + a `debuggingPort`, but
that port never binds (`/json/version` unreachable), so every task fails at
browser launch. Chrome may even spawn (process visible) yet its
remote-debugging port never comes up. The official `ziniao_webdriver_demo`
fails identically — a fast way to confirm it's the client, not our code.

**Second symptom, same root cause:** the port binds and `/json/version`
answers, but the env comes up uninstrumented — log line `Ziniao env on
…:… came up WITHOUT its instrumentation (SBP not injected…)`. To the user
this looks identical (every task fails at browser launch), but the
diagnosis differs. Confirm by hand on the store's raw debug port:

```bash
# 'true' means SBP did not inject — an anti-detect browser must mask this.
curl -s "http://127.0.0.1:<raw_cdp_port>/json/list"   # pick a page target
# then Runtime.evaluate `navigator.webdriver` over its webSocketDebuggerUrl
```

A wedged client can also present as `/json/version` accepting the TCP
connection and then never responding (curl hangs to timeout) — that is
the same wedge, not a slow machine.

**Root cause (the real one):** `pkill -9` on a Ziniao Chrome leaves stale
**`SingletonLock` / `SingletonSocket` / `SingletonCookie`** files in that
store's user-data dir
(`~/Library/Application Support/ziniaobrowser/userdata/chrome_*`). On the
next launch Chrome sees the stale singleton and comes up **without binding
`--remote-debugging-port`**. A plain restart does **not** clear this — the
lock files persist on disk. (This is *why* the fix above replaces the
global `pkill -9` with per-store `stopBrowser` + graceful `exit`: a clean
shutdown lets Chrome remove its own singleton files.)

**Recovery (macOS; adapt paths for Windows/Linux):**

```bash
# 1. Stop Ziniao + all its Chrome (every Ziniao process, by the same rule
#    the server uses; also clears Singleton* in the kernels' profiles).
python3 -c "from app.browser.ziniao_processes import kill_all_ziniao as k, describe as d; \
  r = k(wsl=False); print('stopped:', d(r.killed), '| left:', d(r.survivors))"
sleep 6

# 2. Clear the stale singleton locks from ALL profiles (safe only when no
#    Chrome is running — step 1 guarantees that).
find ~/Library/Application\ Support/ziniaobrowser/userdata \
  -maxdepth 2 -name 'Singleton*' -print -delete

# 3. Relaunch in WebDriver mode (or just let the next browser/start do it).
open -a ziniao --args --run_type=web_driver --ipc_type=http --port=16851

# 4. Verify: one startBrowser should now bind its debuggingPort.
#    (statusCode 0 AND the reported port actually LISTENS.)
```

**Prevention:** never `pkill -9` Ziniao's Chrome in normal operation — use
the `exit` action / per-store `stopBrowser`. If a hard kill or crash *does*
happen, clearing the singleton locks (step 2) is the fix — not a reboot,
reinstall, or account reset. A machine reboot works only because it wipes
the locks too.
