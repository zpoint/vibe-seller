# Native Windows installer

A double-click `VibeSeller-Setup.exe` that runs Vibe Seller natively on
Windows — **no WSL, no manual Ziniao/networking setup**. Modelled on
Ollama: a per-user install plus a system-tray launcher that starts the
server on login.

## What it bundles

| Component | Source | Why |
|---|---|---|
| **CPython (relocatable)** | [python-build-standalone](https://github.com/astral-sh/python-build-standalone) | Run the server with no system Python |
| **App + deps (offline wheels)** | `pip wheel` in CI | Offline install; the `vibe-seller` wheel carries the built web UI in `app/static` |
| **uv** | [astral-sh/uv](https://github.com/astral-sh/uv) | Fast venv + offline install on the target |
| **MinGit** | [git-for-windows MinGit](https://gitforwindows.org/mingit.html) | `git` **and** `bash` — Claude Code runs its Bash tool through Git Bash on Windows, so the existing **bash** browser-use wrapper works unchanged |
| **claude CLI** | Anthropic native installer | The agent runtime |
| **tray.py** | this dir | Login launcher: Open / Restart / Quit, runs via the bundled `pythonw.exe` |

**No browser is bundled.** This PR uses the `chrome` backend's Playwright
Chromium (installed at setup). A follow-up issue switches the backend to
drive the user's installed Chrome/Edge and drops that step.

## Architecture (why no Windows Service / no PyInstaller)

- The server's `vibe-seller start` already daemonises via
  `subprocess.Popen` (not `os.fork`), so it runs on Windows as-is. The
  tray calls `start`/`stop` — one daemon code path on every platform.
- The tray runs via the bundled venv's `pythonw.exe` (`pystray` +
  `Pillow` from the wheel bundle) — no separately-frozen exe to sign.
- The tray prepends bundled `claude` + MinGit to `PATH` before starting
  the server, so the agent subprocess finds them.
- Install lands under `%LOCALAPPDATA%\Programs\VibeSeller`; user data
  under `%LOCALAPPDATA%\vibe-seller` (left intact on uninstall).

## Build

Not buildable on macOS/Linux — wheels are `win_amd64`,
python-build-standalone is per-OS, and Inno Setup compiles on Windows.
CI builds it on `windows-latest` (`.github/workflows/windows-installer.yml`).

Locally on a Windows box:

```powershell
# from the repo root, with Python 3.11 + Node on PATH and Inno Setup 6 installed
.\installer\windows\build.ps1 -AppVersion 0.1.0
# → installer\windows\out\VibeSeller-Setup.exe
```

`build.ps1` parameters pin every third-party version (CPython, uv,
MinGit) for reproducible, reviewable bumps.

## Verification status

- [x] CI builds the `.exe` on `windows-latest`
- [ ] **Manual smoke test on a real Windows box** (cannot be automated in
      CI — needs a desktop session for the tray + a logged-in Chrome):
  1. Run `VibeSeller-Setup.exe` → installs without admin
  2. Tray icon appears; **Open Vibe Seller** loads the UI at `:7777`
  3. Create a store + a browser task; confirm the agent drives Chrome
  4. **Restart** / **Quit** from the tray stop/start the daemon
  5. Reboot → tray auto-starts from the Startup shortcut
  6. Uninstall removes the app; `%LOCALAPPDATA%\vibe-seller` data remains

## Known first-run-CI risks (expected to need iteration)

- **claude CLI fetch** (`Get-ClaudeCli` in `build.ps1`) — the native
  installer's output path is captured by globbing for `claude.exe`;
  verify and pin once the first CI run shows the real layout.
- python-build-standalone tag/asset name drift (pinned `$PbsTag`).
- `playwright install chromium` needs network at install time (removed
  when the system-Chrome backend lands).
