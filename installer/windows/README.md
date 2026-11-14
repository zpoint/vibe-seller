# Native Windows installer

A double-click `VibeSeller-Setup.exe` that runs Vibe Seller natively on
Windows — **no WSL, no manual Ziniao/networking setup**. Modelled on
Ollama: a per-user install plus a system-tray launcher that starts the
server on login. Starting on login is on by default and can be
switched off from the tray menu (**开机自动启动 / Start at login**) —
that item toggles Windows' own StartupApproved flag for the shortcut,
the same switch Task Manager and Settings show, so app upgrades keep
the user's choice.

## Requirements

**Windows 10 version 1809 / Windows Server 2019 (build 17763) or
later, x64.** That is Claude Code's floor: the bundled `claude.exe`
imports the ConPTY API (`CreatePseudoConsole` / `ClosePseudoConsole`)
from `KERNEL32`, which older builds lack, so on e.g. Server 2016 (build
14393) it cannot even load and every task fails at spawn (#149). The
installer checks the build in `InitializeSetup` and refuses with that
reason; there is no supported way to run it on older Windows.

## What it bundles

| Component | Source | Why |
|---|---|---|
| **CPython (relocatable)** | [python-build-standalone](https://github.com/astral-sh/python-build-standalone) | Run the server with no system Python |
| **App + deps (offline wheels)** | `pip wheel` in CI | Offline install; the `vibe-seller` wheel carries the built web UI in `app/static` |
| **uv** | [astral-sh/uv](https://github.com/astral-sh/uv) | Fast venv + offline install on the target |
| **Git for Windows (PortableGit)** | [git-for-windows releases](https://github.com/git-for-windows/git/releases) | `git`, `bash.exe`, and the MSYS userland (`curl`, `perl`, `sleep`). Claude Code's Bash tool **requires a file named `bash.exe`** (with only `sh.exe` it silently falls back to the PowerShell tool, which can't run the extensionless bash wrapper). The browser-use wrapper also shells out to `curl`/`perl`/`sleep`. **MinGit is insufficient** — it ships none of these. |
| **claude CLI** | Anthropic native installer | The agent runtime |
| **Playwright Chromium** (+ headless shell, ffmpeg, winldd) | `playwright install chromium` in CI, run by the Playwright from this bundle's own wheel set | The `chrome` backend's browser. Installed to `%LOCALAPPDATA%\ms-playwright\` (Playwright's default per-user dir, so the app needs no env var), each dir carrying its `INSTALLATION_COMPLETE` marker. Bundled — not downloaded at install — because that download went straight to `cdn.playwright.dev`, ignored the Windows system proxy, and ran hidden behind a wizard whose Cancel is disabled: on networks that can't reach the CDN users sat on "Downloading browser engine" for an hour. Adds ~260 MB to the installer (652 MB with Playwright 1.64's chromium-1248, up from 394 MB). |
| **tray.py** | this dir | Login launcher: Open / Copy LAN address / Restart / Check for updates / Start at login / Quit, runs via the bundled `pythonw.exe`. Menu + popups follow the OS language (en/zh). `--open` (used by the finish step) waits for health then opens the browser. |
| **autostart.py** | this dir | The "Start at login" switch behind the tray menu. It reads and writes Windows' own StartupApproved flag for the Startup shortcut (`HKCU\...\Explorer\StartupApproved\StartupFolder`), the same switch Task Manager and Settings toggle — never the shortcut's existence, which the installer owns. The installer never writes the flag, so upgrades keep the user's choice. |
| **vibe-seller.ico** | `make_icon.py` | Shared brand mark (matches the web favicon); installer/Start-Menu/tray icon |
| **ChineseSimplified.isl** | fetched by `build.ps1` (required; a failed fetch fails the build) | Chinese wizard strings; auto-selected on Chinese systems |

**The install needs no network.** The post-install
`playwright install chromium` still runs, but against the bundled
browsers it is an offline, sub-second no-op: it registers this install
under `ms-playwright\.links` (so another Playwright's garbage collection
keeps our browsers) and would download only a piece the bundle lacked.
`build.ps1` fails the build if any location the shipped Playwright looks
in is incomplete, and `windows-installer.yml` installs with the
Playwright CDN blocked in the hosts file, then requires `playwright
install` to succeed against a dead download host and Chromium to launch.

A Chromium the `chrome` backend launched runs *from* the bundled dirs and
outlives the server the installer kills (Windows doesn't take children
down with the parent), so `PrepareToInstall` and the uninstaller stop
processes running from exactly those dirs (`BrowserDirs`, passed by
`build.ps1`) before overwriting or removing them; the uninstaller then
deletes the dirs. CI covers both with a held-open Chromium.

## In-place updates (tray "检查更新" and the in-app "重启并更新")

Both entry points share `app/windows_update.py`. The server's
background loop (`app/auto_update.py`) stages the offered
`VibeSeller-Setup.exe` under `<VIBE_HOME>\updates\<version>\` —
resumable (`.part` + HTTP `Range`), verified against the GitHub
asset's `size` and `digest` (`sha256:<hex>`; a mock may omit the
digest → size-only), with `install-meta.json` recording what the file
was verified under. Only the newest version dir is kept.

- **Tray 检查更新** reuses that verified stage for the offered version
  instead of downloading again; with no stage it behaves exactly as
  before (temp download, interactive wizard).
- **In-app 重启并更新** (`POST /api/system/update/restart`, admin,
  409 unless the stage is `ready`) launches the staged exe with
  `/SILENT /SUPPRESSMSGBOXES /NORESTART` — the wizard shows **only
  the progress window** (verified: no language dialog in silent mode;
  `WizardSilent` is true for `/SILENT`, so `RelaunchTrayAfterSilentUpgrade`
  still fires). The server does **not** stop itself first:
  `PrepareToInstall` stops server/tray/browsers, and the relaunch
  brings the tray — and with it the server — back. The launch is
  detached (`DETACHED_PROCESS` + `CREATE_NEW_PROCESS_GROUP`, trying
  `CREATE_BREAKAWAY_FROM_JOB` first so a job-object supervisor or CI
  runner cannot take the installer down when its parent server dies).

## Building on a dev host (not CI)

`build.ps1` stages wheels with the **running** Python (`pip wheel`), so
the host interpreter must match the bundled `PyVersion` (3.11.x) — CI
ensures this with `setup-python`. On a different host Python the bundle
installs an empty venv (offline `uv pip install --no-index` refuses the
mismatched tags); the build now fails loudly instead (wheel-tag guard).
`uv python install 3.11` + putting its dir first on PATH is enough:

    powershell -File installer\windows\build.ps1 -AppVersion 0.1.0

Inno Setup 6 must be reachable at `C:\Program Files (x86)\Inno Setup 6`
(the script's fixed lookup path); a per-user install can be exposed
with a directory junction.

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

Bundled versions come from `app/bundle_pins.json`. CPython, uv and Git
for Windows are pinned there. The **Claude Code CLI and Playwright are
not pinned in the repo** — they say `latest` (and `playwright` is bare in
`pyproject.toml`). `build.ps1` resolves them first
(`scripts/resolve_bundle_pins.py`): the newest releases, unless
`BUNDLE_CLAUDE_VERSION` / `BUNDLE_PLAYWRIGHT_VERSION` name exact versions.
It stamps the answer into both files before building, so the bundled
`claude.exe`, the bundled Chromium, the wheel's `playwright==` and
`bundle.json` all describe one pair.

In CI the same two values are workflow inputs (`claude_version`,
`playwright_version`) on `release.yml`, `nightly.yml` and
`windows-installer.yml`, with the repository variables of the same
`BUNDLE_*` names as the fallback a tag push reads. A release resolves
once and hands the answer to every job that builds, so its PyPI wheel
and its installer always agree. When the resolved pair differs from the
previous release's, the bundle fingerprint moves and Windows installs
take the full installer for that release; otherwise it is a pip update.

## Verification status

- [x] CI builds the `.exe` on `windows-latest`
- [ ] **Manual smoke test on a real Windows box** (cannot be automated in
      CI — needs a desktop session for the tray + a logged-in Chrome):
  1. Run `VibeSeller-Setup.exe` → installs without admin
  2. Tray icon appears; **Open Vibe Seller** loads the UI at `:7777`
  3. Create a store + a browser task; confirm the agent drives Chrome
  4. **Restart** / **Quit** from the tray stop/start the daemon
  5. Reboot → tray auto-starts from the Startup shortcut
  6. Tray → untick **Start at login** → Task Manager's Startup list
     shows Vibe Seller disabled (same flag, different UI); reboot →
     the tray does not start. Tick it again → reboot → it starts.
     (The shortcut stays in the Startup folder either way — that is
     by design; the installer owns the file, the flag owns the choice.)
  7. Uninstall removes the app; `%LOCALAPPDATA%\vibe-seller` data remains

## Process-spawn rules on native Windows

Two spawn details are Windows-specific and both bit us on a dev
(npm-installed) box. The **packaged** install is unaffected — it bundles
`claude.exe` and puts that dir on `PATH` — but the rules are here
because this is the only native-Windows doc.

- **Resolve `claude.exe`, never the npm `.cmd`/bare shim.** npm lays down
  `claude`, `claude.cmd` and `claude.ps1` side by side, and
  `os.access(path, os.X_OK)` answers **True for all of them** on Windows
  (it cannot tell an executable from any readable file), so the resolver
  used to hand `CreateProcess` a `#!/bin/sh` script → `WinError 2` on
  every task. The package's real entry point is a native binary on every
  platform (`"bin": {"claude": "bin/claude.exe"}`), so that is what
  `resolve_claude_binary()` targets. It also keeps `cmd.exe` out of the
  spawn, which matters because…
- **`cmd.exe` caps a command line at 8191 chars; `CreateProcess` allows
  32767.** The assembled system prompt (10–20KB with store context) is
  the only large argument — the task prompt and every later message go
  over stdin as stream-json. Via the `.cmd` shim that prompt blew the
  8191 cap and the agent died instantly with `命令行太长`; spawning the
  `.exe` directly gives 4× the headroom and skips cmd's re-quoting of
  every argument (a `--add-dir` path containing `&` or `^` would
  otherwise break).
- **The system prompt never rides the command line.** Even 32767 is
  not a bound we control: the prompt carries the store context and, for
  a planned run, the whole plan, so a schedule whose plan passed ~7K
  chars died at spawn with `[WinError 206] 文件名或扩展名太长` and zero
  messages. `append_system_prompt` always writes it to
  `.system-prompt.md` in the **task dir** — per-task, gitignored, wiped
  on retry — and passes `--append-system-prompt-file`, on every OS and
  whichever binary (`.exe` or a `npm i -g` `.cmd` shim) resolved, so
  the command line stays the same size whatever the prompt holds. The
  workspace assistant has no task dir, so its prompt goes to
  `%USERPROFILE%\.vibe-seller\data\system-prompts\<session>.md`
  instead (a runtime dir, kept out of the workspace git history).
- **A proxy that refuses connections is dropped before spawn (#168).**
  Claude Code takes its proxy only from `HTTPS_PROXY`-style env vars
  (ours, plus the `env` block of `%USERPROFILE%\.claude\settings.json`),
  never from the Windows system proxy. A leftover
  `HTTPS_PROXY=http://127.0.0.1:<port>` whose Clash/v2rayN is no longer
  running failed every task with `API Error: Connection refused — a
  firewall or proxy may be blocking it (ECONNREFUSED)`.
  `app/ai/agent_proxy.py` probes the proxy the agent would use (the
  settings file is `%CLAUDE_CONFIG_DIR%\settings.json` when the agent
  env sets it); if its port refuses, every proxy key is removed from
  the spawn env and blanked through a `--settings` file next to the
  system prompt. The task shows a system notice naming the proxy
  (scheme, host and port only — never credentials) and where it was
  set. A proxy that accepts or only times out is kept.
- **`browser-use` wrappers embed an absolute `REAL_BU`.** Windows
  installs the console script as `browser-use.exe` with no
  extensionless sibling, so the old bare-name fallback made the wrapper
  exec itself. See [docs/browser.md](../../docs/browser.md) §&nbsp;Wrapper
  safety.

## Known first-run-CI risks (expected to need iteration)

- **claude CLI fetch** (`Get-ClaudeCli` in `build.ps1`) — the native
  installer's output path is captured by globbing for `claude.exe`;
  verify and pin once the first CI run shows the real layout.
- python-build-standalone tag/asset name drift (pinned `$PbsTag`).
