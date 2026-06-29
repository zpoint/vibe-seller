# macOS Deployment — Auto-Launch on Login (`launchd`)

How to run the vibe-seller server as a managed service on a Mac (e.g. a
Mac Mini customer deployment) so it **starts on login and restarts on
crash**. This is the macOS sibling of
[docs/windows-setup.md](windows-setup.md) (which covers the WSL2 / systemd
path).

> The bulk of this guide is the [Gotchas](#gotchas-read-before-you-debug)
> section. Every one of them cost real debugging time on a live box — read
> it before touching `launchctl`.

## Target state

| Component | Mechanism |
|-----------|-----------|
| vibe-seller server | **LaunchAgent** `com.vibe-seller.server` (`RunAtLoad` + `KeepAlive`) |
| Process supervised | `uv run python -m uvicorn app.main:app` (foreground — launchd tracks the real PID) |
| Auto-start at boot | macOS **automatic login** → Aqua session → LaunchAgent loads |
| Logs | `~/Library/Logs/vibe-seller/launchd.{out,err}.log` |
| Browser automation | needs the same logged-in GUI (Aqua) session — see below |

## Why a LaunchAgent (not a LaunchDaemon)

- A **LaunchAgent** runs inside the user's **Aqua (GUI login) session**.
  The browser backends (`chrome`, `ziniao`) drive a *headed* browser, which
  needs a real window-server session — exactly what an Agent gets and a
  Daemon does not.
- It runs as the user, with the user's `~/.vibe-seller/` data dir and tools
  (`uv`, `node`, `claude`), so no path/permission juggling for those.
- Trade-off: an Agent only starts **after someone logs in**. For an
  unattended box, enable **automatic login** (below) so a reboot lands in
  the Aqua session and the Agent loads on its own.

## 1. The plist

`~/Library/LaunchAgents/com.vibe-seller.server.plist` (adjust the user
name, repo path, and port for the target box):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.vibe-seller.server</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USER/.local/bin/uv</string>
        <string>run</string>
        <string>python</string>
        <string>-m</string>
        <string>uvicorn</string>
        <string>app.main:app</string>
        <string>--host</string>
        <string>0.0.0.0</string>
        <string>--port</string>
        <string>7777</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/YOUR_USER/Desktop/vibe-seller</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>/Users/YOUR_USER</string>
        <key>PATH</key>
        <string>/Users/YOUR_USER/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>LOG_DIR</key>
        <string>/Users/YOUR_USER/Desktop/vibe-seller/logs</string>
        <key>BACKEND_PORT</key>
        <string>7777</string>
        <!-- dev mode: full DEBUG + agent prompt logging.
             Remove these two keys (or set LOG_LEVEL=INFO) for production. -->
        <key>LOG_LEVEL</key>
        <string>DEBUG</string>
        <key>AGENT_DEBUG</key>
        <string>1</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>ProcessType</key>
    <string>Interactive</string>

    <key>StandardOutPath</key>
    <string>/Users/YOUR_USER/Library/Logs/vibe-seller/launchd.out.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USER/Library/Logs/vibe-seller/launchd.err.log</string>
</dict>
</plist>
```

> The plist runs the **backend only**, against whatever is already in
> `frontend/dist/`. It deliberately does **not** build the frontend (see
> [Gotcha 6](#6-the-launchagent-does-not-build-the-frontend)).

## 2. Install / load

```bash
mkdir -p ~/Library/Logs/vibe-seller
plutil -lint ~/Library/LaunchAgents/com.vibe-seller.server.plist   # validate XML
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vibe-seller.server.plist
```

`RunAtLoad` starts it immediately. Verify:

```bash
launchctl print gui/$(id -u)/com.vibe-seller.server | grep -iE 'state =|last exit|pid ='
#   state = running
#   pid = NNNN
#   last exit code = (never exited)
curl -s http://127.0.0.1:7777/api/health     # {"status":"ok"}
```

> `bootstrap`/`bootout` are the modern (macOS 11+) verbs. The old
> `launchctl load -w` / `unload` still work but are deprecated and give
> worse error messages — prefer `bootstrap`/`bootout`.

## 3. Day-to-day operations

```bash
LABEL=gui/$(id -u)/com.vibe-seller.server

# Status + last exit code
launchctl print $LABEL | grep -iE 'state =|last exit|pid ='

# Restart the running process (does NOT re-read the plist)
launchctl kickstart -k $LABEL

# Tail logs
tail -f ~/Library/Logs/vibe-seller/launchd.err.log

# Stop / unload
launchctl bootout $LABEL
```

**To change the plist itself** (port, env vars, log level) you must reload —
`kickstart` keeps the old definition:

```bash
launchctl bootout gui/$(id -u)/com.vibe-seller.server   # then wait a beat (see Gotcha 4)
sleep 3
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vibe-seller.server.plist
```

## 4. Deploy a new version

The Agent owns the process; **do not** use `./start.sh` / `./restart.sh`
for a launchd-managed box (see [Gotcha 3](#3-launchd-owns-the-port--dont-also-run-startsh--restartsh)).
Deploy = pull, rebuild the frontend if it changed, then restart via
launchd:

```bash
cd ~/Desktop/vibe-seller
git pull
(cd frontend && pnpm install && pnpm build)     # only if the frontend changed
launchctl kickstart -k gui/$(id -u)/com.vibe-seller.server
```

Python deps are handled automatically: `uv run` syncs the env on start.

## 5. Auto-start at boot (unattended box)

A LaunchAgent only loads once the user is in the Aqua session, so for a
headless reboot enable **automatic login**:

- **System Settings → Users & Groups → Automatically log in as…** → the
  service user.
- This also gives the headed browser backends a real GUI session to draw
  into.

Keep the machine awake:

```bash
sudo pmset -a sleep 0 disablesleep 1     # never sleep (display can still blank)
```

---

## Gotchas (read before you debug)

These are the failures we actually hit bringing this up. Symptoms were
maddening because **several produce a silent `EX_CONFIG` (exit 78) with no
log output at all.**

### 1. launchd's `PATH` is minimal — `uv` won't be found via a login shell

launchd starts jobs with `PATH=/usr/bin:/bin:/usr/sbin:/sbin`. `uv` lives
in `~/.local/bin`, which isn't on it.

The tempting fix — wrapping the command in `/bin/zsh -lc "…"` — **does not
work**: `zsh -c` is *non-interactive*, so it sources `.zshenv` / `.zprofile`
but **skips `.zshrc`**, which is exactly where the `uv` installer (and most
tools) export `PATH`. The job then either can't find `uv` or starts in a
half-configured env.

**Fix:** invoke `uv` by **absolute path** and set `PATH` + `HOME`
explicitly in `EnvironmentVariables`. No shell wrapper needed. (Same class
of bug as the WSL "non-interactive PATH" gotcha in
[windows-setup.md §6](windows-setup.md).)

### 2. Logs must NOT live in `~/Desktop`, `~/Documents`, or `~/Downloads`

Those folders are **TCC-protected**. A launchd agent with no TCC grant
cannot open a `StandardErrorPath` / `StandardOutPath` inside them — so the
job fails to set up its I/O and exits **`EX_CONFIG` (78)** while writing
**absolutely nothing** to the log it couldn't open. The silence is the
tell.

**Fix:** point the log paths at `~/Library/Logs/…` (not TCC-protected,
persistent, and visible in Console.app). The repo lives in `~/Desktop` here
out of convenience; keeping the *logs* out of `~/Desktop` is what matters.
(If you ever see exit 78 with empty logs, this — or Gotcha 1 — is almost
always why.)

### 3. launchd owns the port — don't *also* run `start.sh` / `restart.sh`

`start.sh` `nohup`s its own uvicorn on `:7777`. If you run it while the
Agent is loaded, **two** processes fight for the port: the Agent's
`KeepAlive` keeps trying to bind, hits `EADDRINUSE`, dies, and retries
forever (throttled to every 10 s). You'll see the manual server "working"
while `launchctl print` shows the Agent stuck failing.

**Fix:** pick one owner. On a launchd-managed box that's the Agent — restart
with `launchctl kickstart -k`, never `./restart.sh`. To hand control back to
the scripts, `launchctl bootout` the Agent first.

### 4. `bootstrap` right after `bootout` races — `5: Input/output error`

Re-bootstrapping before launchd has fully released the previous instance
fails with `Bootstrap failed: 5: Input/output error`. The label is still
mid-teardown.

**Fix:** `bootout`, `sleep 2–3`, then `bootstrap` (retry once or twice if
needed). A reload helper:

```bash
launchctl bootout gui/$(id -u)/com.vibe-seller.server 2>/dev/null
sleep 3
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vibe-seller.server.plist
```

### 5. Decoding the exit status

`launchctl list <label>` reports the **raw wait status**, not the exit
code. `19968` looks alarming until you shift it: `19968 >> 8 = 78`
(`EX_CONFIG`). `launchctl print gui/$(id -u)/<label>` shows the friendlier
`last exit code = 78: EX_CONFIG` plus the full resolved environment, working
dir, and log paths — **start there** when a job won't stay up.

### 6. The LaunchAgent does **not** build the frontend

By design the plist runs `uvicorn` directly against the existing
`frontend/dist/`. We keep the build *out* of the supervised command on
purpose:

- a slow build in the `KeepAlive` crash-recovery path would delay every
  restart, and
- a frontend build *break* would take the whole server down on the next
  restart instead of serving the last-good bundle.

So rebuild the frontend as an explicit step of [§4 Deploy](#4-deploy-a-new-version),
not as part of the service.

---

## Troubleshooting cheatsheet

| Symptom | Cause | Fix |
|---------|-------|-----|
| Exit 78 (`EX_CONFIG`), **empty** logs | log path in `~/Desktop` (TCC) **or** `uv` not on PATH | logs → `~/Library/Logs`; absolute `uv` + explicit `PATH` (Gotchas 1–2) |
| `command not found` / exit 127 | `zsh -lc` skipped `.zshrc`; `uv` unresolved | invoke `uv` by absolute path (Gotcha 1) |
| Agent stuck restarting every ~10 s | port held by a `start.sh` `nohup` server | one owner only — `bootout` the manual one (Gotcha 3) |
| `Bootstrap failed: 5: Input/output error` | re-bootstrapped before teardown finished | `bootout` → `sleep 3` → `bootstrap` (Gotcha 4) |
| Server up but serving stale UI | `frontend/dist/` not rebuilt after pull | `pnpm build` then `kickstart -k` (Gotcha 6) |
| Agent never loads after reboot | no Aqua session (nobody logged in) | enable automatic login (§5) |
| Plist edit didn't take effect | `kickstart` reuses the loaded definition | reload with `bootout` + `bootstrap` (§3) |

## See also

- [docs/windows-setup.md](windows-setup.md) — the Windows + WSL2 / systemd
  deployment path (the sibling of this guide).
- [docs/browser.md](browser.md) — browser backends; why headed automation
  needs a GUI session.
- `start.sh` / `restart.sh` / `stop.sh` — the interactive (non-launchd)
  run scripts.
