---
description: Install Vibe Seller — on Windows download the installer and double-click it; on macOS / Linux paste one command. Upgrading, uninstalling, and advanced topics: WSL2, installing from source, service management, LAN access.
---

# Installation

Pick your system and follow along — it takes a few minutes. Runs on Windows 10 or later (64-bit), macOS 13 or later, and Linux.

=== "Windows"

    1. Download the installer, [VibeSeller-Setup.exe](https://github.com/zpoint/vibe-seller/releases/latest);
    2. Double-click it and click **Next** through the wizard;
    3. On the last page click **Finish** — your browser opens Vibe Seller.

    To open it again later, click Vibe Seller in the Start menu.

    Once installed, Vibe Seller starts automatically when you sign in to Windows. To turn that on or off, right-click the Vibe Seller tray icon (bottom-right) and check or uncheck **Start at login**.

    ??? note "Install from the command line (click to expand)"
        Open **PowerShell** (search for it in the Start menu), paste this line and press Enter. It does the same as double-clicking the installer:

        ```powershell
        irm https://raw.githubusercontent.com/zpoint/vibe-seller/main/installer/windows/install.ps1 | iex
        ```

        The installer is about 650 MB: everything it needs is inside, so setup downloads nothing else and works on a slow network.

=== "macOS"

    1. Open **Terminal**: press ⌘ + Space, type "Terminal" and press Enter;
    2. Copy this line, paste it into the terminal and press Enter:

        ```bash
        curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
        ```

        It takes a few minutes. When you see `[ok] Vibe Seller installed!`, it's done.

    3. Type this line and press Enter to start Vibe Seller:

        ```bash
        vibe-seller start
        ```

        If it says `vibe-seller` is not found, close the terminal, open a new one and try again.

    4. Open <http://localhost:7777> in your browser.

    You can close the terminal once it's started; Vibe Seller keeps running in the background. To have your Mac start it automatically, see [Keep It Running 24/7](always-on.md).

    ??? note "Command stuck? (click to expand)"
        The install command uses your system proxy for downloads when it answers (`VIBE_SELLER_PROXY=off` turns this off). If the command itself hangs, run `export https_proxy=http://127.0.0.1:7890` (your proxy client's port) and try again.

=== "Linux"

    1. Open a terminal;
    2. Copy this line, paste it into the terminal and press Enter:

        ```bash
        curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
        ```

        It takes a few minutes. When you see `[ok] Vibe Seller installed!`, it's done.

    3. Type this line and press Enter to start Vibe Seller:

        ```bash
        vibe-seller start
        ```

        If it says `vibe-seller` is not found, close the terminal, open a new one and try again.

    4. Open <http://localhost:7777> in your browser.

    You can close the terminal once it's started; Vibe Seller keeps running in the background.

    ??? note "Command stuck? (click to expand)"
        The install command uses your system proxy for downloads when it answers (`VIBE_SELLER_PROXY=off` turns this off). If the command itself hangs, run `export https_proxy=http://127.0.0.1:7890` (your proxy client's port) and try again.

Once it's installed, go on to [Quickstart](quickstart.md): set up AI, add a store, run your first task.

## Upgrade

=== "Automatic"

    This is the default. When a new version comes out, Vibe Seller downloads it in the background and tells you in the sidebar:

    - Windows: click **Restart and update**; a few minutes later it's installed and opens again;
    - macOS / Linux: it updates itself when no task is running — nothing to do.

=== "By hand"

    - Windows: right-click the Vibe Seller tray icon (bottom-right) and click **Check for updates**;
    - macOS / Linux: run `vibe-seller upgrade` in a terminal.

Upgrading never touches your data.

## Uninstall

- Windows: in **Settings → Apps**, find Vibe Seller and click **Uninstall**.
- macOS / Linux: run `uv tool uninstall vibe-seller` in a terminal.

Uninstalling keeps your data. Stores, screenshots and history live in the `.vibe-seller` folder in your home directory (on Windows, `C:\Users\<your-name>\.vibe-seller`); delete that folder to remove them too.

## Install failed?

Paste the repo URL <https://github.com/zpoint/vibe-seller> into whatever coding agent you have at hand and say “read the README and install this.” It will work through the docs and the commands and help you troubleshoot. You can also ask in the [community](community.en.md).

## Advanced

You won't need anything below for a first install. Read on when you deploy Vibe Seller for a team or want to change the code.

### Windows through WSL2

Prefer the Windows installer above. The WSL2 path runs the server inside Ubuntu/WSL with the browser on the Windows host — it's the setup for teams that treat one box as their Vibe Seller server. It needs **Windows 11 with mirrored networking** (mirrored mode on 24H2 has a known problem that blocks inbound LAN connections — you need 25H2 / build 26200+); the full runbook — networking, auto-start, browser backend, Tailscale for remote access — is the [Windows + WSL2 Deployment Guide](windows-setup.md).

Installation inside WSL is the same as macOS / Linux:

```bash
curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
vibe-seller start
```

### From source

For contributing or building on it:

```bash
git clone https://github.com/zpoint/vibe-seller
cd vibe-seller
./install.sh --dev   # system deps + venv + frontend build + Chromium
./start.sh           # serves :7777
```

`--dev` finishes with `All required dependencies are installed!`. Development and debugging workflows: [Developer Guide](dev-guide.md).

### Service management

| What you want | Installed (macOS / Linux) | From a source checkout | Windows installer |
|---|---|---|---|
| Start | `vibe-seller start` | `./start.sh` | starts on login; or the Start Menu entry |
| Different port | `vibe-seller start --port 8080` | `./start.sh 8080` | set `VIBE_SELLER_PORT` |
| Stop | `vibe-seller stop` | `./stop.sh` | tray → **Quit** |
| Restart | `vibe-seller stop && vibe-seller start` | `./restart.sh` | tray → **Restart server** |
| Run in the foreground (watch logs) | `vibe-seller start --foreground` | `./start.sh --dev` | — |
| (Re)install the browser engine | `vibe-seller install-browser` | done by `./install.sh --dev` | bundled |
| Start after login | `vibe-seller autostart on` (macOS only; see [Keep It Running 24/7](always-on.md)) | see [macOS Service](macos-setup.md) | tray **Start at login**, on by default |

The engine check also runs by itself at every server start, so you rarely need that row. Logs are under `~/.vibe-seller/logs/`.

`vibe-seller start` binds `0.0.0.0:7777` by default, so other devices on your network can already reach it.

### Access from the local network

Teammates install nothing — they open `http://<your-machine's-ip>:7777`. On Windows, tray → **Copy LAN address** lists both the IP and the machine-name URL and puts one on your clipboard. On macOS / Linux, use that machine's own address.

!!! warning "There is no login by default"
    Anyone who can reach the port is acting as the default administrator. That's fine on a trusted LAN — **don't expose it to the public internet.** For remote access use a VPN / Tailscale; see the Tailscale section of the [Windows + WSL2 Deployment Guide](windows-setup.md).
    To make colleagues log in first, turn on **Require Login** in **Settings → Account** — see [How do I set up or recover the login password?](faq-login.en.md#how-do-i-set-up-or-recover-the-login-password).

### Keep it running around the clock

Want scheduled tasks to run in the middle of the night? First follow [Keep It Running 24/7](always-on.md): no sleep on power, start up after a power cut, and Vibe Seller starting after login.

To turn it into a proper always-on service (starts on boot, restarts if it dies), use one of these runbooks:

- [macOS Deployment — Auto-Launch on Login](macos-setup.md): a `launchd` LaunchAgent, no extra software
- [Windows + WSL2 Deployment Guide](windows-setup.md): a systemd service inside WSL plus keep-alive
