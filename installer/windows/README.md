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
| **Git for Windows (PortableGit)** | [git-for-windows releases](https://github.com/git-for-windows/git/releases) | `git`, `bash.exe`, and the MSYS userland (`curl`, `perl`, `sleep`). Claude Code's Bash tool **requires a file named `bash.exe`** (with only `sh.exe` it silently falls back to the PowerShell tool, which can't run the extensionless bash wrapper). The browser-use wrapper also shells out to `curl`/`perl`/`sleep`. **MinGit is insufficient** — it ships none of these. |
| **claude CLI** | Anthropic native installer | The agent runtime |
| **tray.py** | this dir | Login launcher: Open / Restart / Quit / Check for updates, runs via the bundled `pythonw.exe`. Menu + popups follow the OS language (en/zh). `--open` (used by the finish step) waits for health then opens the browser. |
| **vibe-seller.ico** | `make_icon.py` | Shared brand mark (matches the web favicon); installer/Start-Menu/tray icon |
| **ChineseSimplified.isl** | fetched by `build.ps1` (best-effort) | Chinese wizard strings; auto-selected on Chinese systems, English fallback |

**Browser engine: Playwright Chromium**, downloaded at install via
`playwright install chromium` (so the first install needs network). It
is not the user's system Chrome — a follow-up issue switches the
`chrome` backend to drive the user's installed Chrome/Edge and drops the
download step.

## Architecture (why no Windows Service / no PyInstaller)

- The server's `vibe-seller start` already daemonises via
  `subprocess.Popen` (not `os.fork`), so it runs on Windows as-is. The
  tray calls `start`/`stop` — one daemon code path on every platform.
- The tray runs via the bundled venv's `pythonw.exe` (`pystray` +
  `Pillow` from the wheel bundle) — no separately-frozen exe to sign.
- The tray prepends bundled `claude` + Git for Windows to `PATH` before
  starting the server, and pins `CLAUDE_CODE_GIT_BASH_PATH` at the
  bundled `bash.exe`, so the agent subprocess finds them.
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
Git for Windows) for reproducible, reviewable bumps.

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
