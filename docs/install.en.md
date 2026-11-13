---
description: Install Vibe Seller — the native Windows installer, the macOS / Linux one-liner, upgrade and uninstall, the WSL2 path, installing from source, service management and LAN access.
---

# Installation

Three minutes: pick your OS, paste one command, open the web page. For what to do once it's running, see [Quickstart](quickstart.md).

> `~` means your home directory — on Windows that's `C:\Users\<your-name>`.
> Your store data, screenshots, logs and the agent's notes all live in one folder, `~/.vibe-seller/`. Uninstalling does not touch it.

## Requirements

- One LLM API key — any of Claude / DeepSeek / Kimi / MiniMax / GLM / Qwen / MiMo. Where to get one: [Configure AI Models](llm-keys.md)
- A browser — Ziniao (the anti-detect browser) or the bundled Chromium engine
- A machine you can leave on: macOS, Linux, or Windows 10 version 1809 / Windows Server 2019 or later (64-bit)

No account to register, no subscription to buy. The code and the data stay on your machine.

## Windows: native installer (recommended)

Download **`VibeSeller-Setup.exe`** from the [latest release](https://github.com/zpoint/vibe-seller/releases/latest) and run it.

The wizard follows your OS language (Chinese on a Chinese system). The last page has **Open Vibe Seller now** checked by default — hit Finish and the server starts and the web UI opens at <http://localhost:7777>.

Or install in one line — open **PowerShell** (search for it in the Start menu), paste the whole line, press Enter:

```powershell
irm https://raw.githubusercontent.com/zpoint/vibe-seller/main/installer/windows/install.ps1 | iex
```

!!! note "Why the installer is 650 MB"
    The Python runtime, Claude Code, Git and the Chromium browser engine are all inside it, so setup downloads nothing else — it installs fine on a slow or heavily proxied network. The trade-off is size: about 650 MB.

After installation:

- **It installs per-user** — no UAC prompt, no admin rights; the program lives in `%LOCALAPPDATA%\Programs\VibeSeller`.
- **It starts on login.** Once you're signed in, the server is already running and a Vibe Seller tray icon appears: **Open Vibe Seller** / **Copy LAN address** / **Restart server** / **Check for updates** / **Quit**. The tray is only an entry point — it drives the same CLI underneath, and **Quit** stops the server with it.
- **Upgrade**: tray → **Check for updates** looks up the newest `VibeSeller-Setup.exe`, downloads it and opens the installer over your existing install; the server and tray exit during that and come back on the next login. Or grab the new installer from the [releases page](https://github.com/zpoint/vibe-seller/releases/latest) yourself. Either way only the program is replaced — `~/.vibe-seller/` keeps everything.
- **Change the port**: set a `VIBE_SELLER_PORT` environment variable (default 7777); the tray and the server both pick it up.
- **Uninstall**: remove "Vibe Seller" in Windows **Settings → Apps**. Your data stays in `~/.vibe-seller/`; delete that folder if you want it gone.

## macOS / Linux: one command

Open a terminal (on a Mac: ⌘ + Space, type "Terminal", Enter) and paste this whole line:

```bash
curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
```

The script installs the toolchain it needs (uv, Node, Claude Code), installs Vibe Seller, and downloads the Chromium the agent drives (about 250 MB — it picks whichever of the official CDN and the npmmirror China mirror answers faster). You're done when the output ends with `[ok] Vibe Seller installed!`; the last lines tell you what to run next.

Then start the server:

```bash
vibe-seller start
```

Open <http://localhost:7777> and you're in.

The command prints three lines:

```
vibe-seller running on http://0.0.0.0:7777  (PID 12345)
  logs: ~/.vibe-seller/logs/backend_7777.log
  stop: vibe-seller stop --port 7777
```

The server runs in the background — **closing the terminal doesn't stop it**. The PID file and the log live under `~/.vibe-seller/`.

Upgrade:

```bash
vibe-seller upgrade
```

This upgrades the Claude Code CLI it runs as well. The running server moves itself onto the new version; if tasks are mid-flight it lets them finish first (no new task starts meanwhile, and tasks sitting in the queue aren't lost — they're re-queued on the next boot), then restarts. If it can't complete on its own, the command prints the `vibe-seller stop && vibe-seller start` to run.

Uninstall:

```bash
uv tool uninstall vibe-seller
```

That removes the program only — your data, per-store memory and screenshots under `~/.vibe-seller/` are left alone.

## Advanced: Windows through WSL2

Prefer the native installer above. The WSL2 path runs the server inside Ubuntu/WSL with the browser on the Windows host — it's the setup for teams that treat one box as their Vibe Seller server. It needs **Windows 11 with mirrored networking** (mirrored mode on 24H2 has a known problem that blocks inbound LAN connections — you need 25H2 / build 26200+); the full runbook — networking, auto-start, browser backend, Tailscale for remote access — is the [Windows + WSL2 Deployment Guide](windows-setup.md).

Installation inside WSL is the same as macOS / Linux:

```bash
curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
vibe-seller start
```

## From source (contributing / second development)

```bash
git clone https://github.com/zpoint/vibe-seller
cd vibe-seller
./install.sh --dev   # system deps + venv + frontend build + Chromium
./start.sh           # serves :7777
```

`--dev` finishes with `All required dependencies are installed!`. Development and debugging workflows: [Developer Guide](dev-guide.md).

## Service management

| What you want | Installed (macOS / Linux) | From a source checkout | Windows installer |
|---|---|---|---|
| Start | `vibe-seller start` | `./start.sh` | starts on login; or the Start Menu entry |
| Different port | `vibe-seller start --port 8080` | `./start.sh 8080` | set `VIBE_SELLER_PORT` |
| Stop | `vibe-seller stop` | `./stop.sh` | tray → **Quit** |
| Restart | `vibe-seller stop && vibe-seller start` | `./restart.sh` | tray → **Restart server** |
| Run in the foreground (watch logs) | `vibe-seller start --foreground` | `./start.sh --dev` | — |
| (Re)install the browser engine | `vibe-seller install-browser` | done by `./install.sh --dev` | bundled |

The engine check also runs by itself at every server start, so you rarely need the last one.

`vibe-seller start` binds `0.0.0.0:7777` by default, so other devices on your network can already reach it.

## Access from the local network

Teammates install nothing — they open `http://<your-machine's-ip>:7777`. On Windows, tray → **Copy LAN address** lists both the IP and the machine-name URL and puts one on your clipboard. On macOS / Linux, use that machine's own address.

!!! warning "There is no login by default"
   Anyone who can reach the port is acting as the default administrator. That's fine on a trusted LAN — **don't expose it to the public internet.** For remote access use a VPN / Tailscale; see the Tailscale section of the [Windows + WSL2 Deployment Guide](windows-setup.md).

## Install failed?

Paste the repo URL <https://github.com/zpoint/vibe-seller> into whatever coding agent you have at hand and say “read the README and install this.” It will work through the docs and the commands and help you troubleshoot.

## Keep it running around the clock

To turn it into a proper always-on service (starts on boot, restarts if it dies), use one of these runbooks:

- [macOS Deployment — Auto-Launch on Login](macos-setup.md): a `launchd` LaunchAgent, no extra software
- [Windows + WSL2 Deployment Guide](windows-setup.md): a systemd service inside WSL plus keep-alive
