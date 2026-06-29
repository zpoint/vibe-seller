# Windows + WSL2 Deployment Guide

How to run a Vibe Seller server on a Windows machine via WSL2, reachable
over LAN and Tailscale, with native Windows Chrome for headed-browser
store automation.

This guide is written from a real deployment (`win-host`, a Windows 11
mini-PC) and records every problem hit and how it was solved, so the next
machine is a checklist, not an investigation.

> **TL;DR of the hard-won lesson:** WSL2 **mirrored** networking only
> exposes WSL ports to external machines (LAN, Tailscale) on **Windows 11
> build 26200 (25H2) or newer**. On build 26100 (24H2) the same config
> works *locally* but not for inbound external connections — and no
> portproxy workaround is clean. **Upgrade to 25H2 first.** See
> [Networking](#networking-wsl2-mirrored-mode) below.

---

## 0. Overview of the target state

```
Windows host (25H2, build 26200+)
├── OpenSSH Server            → remote admin from dev machine
├── Tailscale (service)       → remote access from anywhere
├── v2rayN / proxy (autostart)→ outbound proxy for CN networks (port 10808)
├── Google Chrome             → headed browser for human login (CDP :9223)
│     └── launched by Task Scheduler in the interactive session
└── WSL2 (Ubuntu-24.04, mirrored networking)
      ├── systemd enabled
      ├── vibe-seller server  → systemd service on 0.0.0.0:7777
      └── repo at ~/codes/vibe-seller, venv, claude CLI, etc.
```

Mirrored networking means WSL shares the Windows network interfaces:
`0.0.0.0:7777` inside WSL is reachable as `192.168.0.x:7777` (LAN) and
`100.x.x.x:7777` (Tailscale) with **no portproxy** — on 25H2.

---

## 1. SSH access (do this first, from the Windows machine)

OpenSSH Server is built into Windows 11 but Windows Update is often slow
or blocked. The repo ships `scripts/win-machine-setup.bat` — run it **as
Administrator** on the new machine. It:

1. Installs OpenSSH Server (Windows feature, or GitHub download fallback).
2. Starts `sshd`, sets it to auto-start, opens the firewall.
3. Prompts you to paste the dev machine's SSH public key into
   `administrators_authorized_keys` with correct ACLs.
4. Prints hostname + IPs for handoff.

After that, from the dev machine: `ssh Administrator@<ip>`.

**Gotcha — encoding:** a Simplified-Chinese Windows emits GBK-encoded
text; SSH output looks garbled in a UTF-8 terminal. The bat starts with
`chcp 65001` to mitigate, but error messages from Windows tools may still
be mojibake. It's cosmetic — the commands still run.

**Gotcha — session isolation:** an SSH session runs in a **non-interactive
Windows session**. It can run `netsh`, `reg`, services — but it CANNOT:
- launch a visible GUI app (Chrome won't appear on screen),
- reach the Tailscale named pipe (`tailscale serve/up` fail with
  "open \\.\pipe\...\tailscaled: cannot find the file"),
- reliably restart `iphlpsvc` to activate portproxy listeners.

For those, use **Task Scheduler with `LogonType Interactive`** (places the
process in the desktop session) or do it physically/RDP. This is why the
Chrome backend launches Chrome via a scheduled task, not via SSH.

---

## 2. WSL2 install

Windows Update being blocked also blocks `wsl --install`. Workarounds that
worked:

```powershell
# Enable the features via DISM (offline, no Windows Update)
dism /online /enable-feature /featurename:Microsoft-Windows-Subsystem-Linux /all /norestart
dism /online /enable-feature /featurename:VirtualMachinePlatform /all /norestart
# reboot

# vmcompute (Host Compute Service) may be missing — re-enable VMP via DISM
# if `wsl --import` fails with HCS_E_SERVICE_NOT_AVAILABLE, then reboot.
```

Then install the WSL2 app + kernel from the GitHub MSI (Store may be
blocked). **Download via the proxy** if direct GitHub is blocked:

```powershell
curl.exe -x 127.0.0.1:10808 -L -o C:\Windows\Temp\wsl.msi `
  https://github.com/microsoft/WSL/releases/download/2.7.8/wsl.2.7.8.0.x64.msi
msiexec /i C:\Windows\Temp\wsl.msi /qn /norestart
# reboot (vmcompute/HCS needs it)
```

Install Ubuntu:

```powershell
wsl --set-default-version 2
wsl --install -d Ubuntu-24.04 --no-launch
```

Create the default user (avoid the interactive first-launch by importing
or scripting):

```bash
# as root inside the distro
useradd -m -s /bin/bash -G sudo zpoint
echo 'zpoint ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/zpoint
printf '[user]\ndefault=zpoint\n' > /etc/wsl.conf   # then `wsl --shutdown`
```

> **Security note — `NOPASSWD:ALL` is optional.** It's a convenience for an
> unattended single-purpose box (so restart/sync scripts and remote
> automation never stall on a sudo prompt). It is a real privilege
> trade-off: on a shared or higher-sensitivity host, drop `NOPASSWD:` and
> use a passworded sudo instead.

---

## 3. WSL config (`.wslconfig` on the Windows side)

Create `C:\Users\<user>\.wslconfig`:

```ini
[wsl2]
networkingMode=mirrored
firewall=false
dnsTunneling=true
autoProxy=false

[experimental]
hostAddressLoopback=true
autoMemoryReclaim=disabled
```

- `networkingMode=mirrored` — share Windows interfaces (the whole point).
- `firewall=false` — disable the Hyper-V guest firewall (it otherwise
  blocks inbound to WSL). **This removes a layer of guest isolation**, so
  control inbound exposure at the Windows edge instead: leave **Windows
  Defender Firewall on** and open only the ports you actually need, scoped
  as tightly as possible — e.g. the server port to LAN/Tailscale, and
  remote-access (RDP/VNC) to the **Tailscale subnet `100.64.0.0/10` only**
  (see §5). Never expose a WSL service to `0.0.0.0` on an untrusted network.
- `hostAddressLoopback=true` — WSL↔Windows loopback both directions.
- `autoMemoryReclaim=disabled` — avoids WSL idling memory down under a
  long-running server.

`wsl --shutdown` after any `.wslconfig` change.

Enable **systemd** (`/etc/wsl.conf` inside the distro):

```ini
[boot]
systemd=true
[user]
default=zpoint
```

---

## 4. Networking (WSL2 mirrored mode)

**This is the section that cost the most time. Read it.**

### What works on 25H2 (build 26200+)

A WSL process binding `0.0.0.0:7777` is reachable, with **no portproxy**:

| From | URL | Works |
|------|-----|-------|
| WSL itself | `http://localhost:7777` | ✅ |
| Windows host | `http://localhost:7777`, `http://<lan-ip>:7777` | ✅ |
| LAN machines | `http://<lan-ip>:7777` | ✅ |
| Tailscale peers | `http://<ts-ip>:7777`, `http://<hostname>:7777` | ✅ |

Verify: `curl --noproxy '*' http://<lan-ip>:7777/api/health` from another
machine.

### What's broken on 24H2 (build 26100) — and why you must upgrade

On build 26100 the **same** mirrored config exposes the port only to the
**interactive Windows session** (e.g. Chrome on the desktop). External
machines and SSH sessions cannot reach it. Symptoms we hit:

- LAN/Tailscale `curl` to `:7777` times out, but the machine's own Chrome
  reaches `win-host:7777` fine.
- Adding a Windows portproxy `0.0.0.0:7777 → 127.0.0.1:7777` **conflicts**
  with the WSL server (both claim `0.0.0.0:7777` in the shared namespace),
  crashing the server with `[Errno 98] address already in use`.
- A socat relay (`17777 → 7777`) + portproxy `7777 → 17777` works but is
  fragile (IP Helper must be restarted to activate the listener, which SSH
  can't do reliably).
- `tailscale serve` can't be configured from SSH (named-pipe session
  isolation).

**None of these workarounds are worth keeping.** The clean fix is the OS
upgrade. WSL app version alone (2.4.13 → 2.7.8) did **not** fix it — it was
the Windows build.

### Upgrading 24H2 → 25H2

```powershell
# Target the 25H2 feature update explicitly
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" `
  /v TargetReleaseVersion /t REG_DWORD /d 1 /f
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" `
  /v TargetReleaseVersionInfo /t REG_SZ /d 25H2 /f
```

Then **Settings → Windows Update → Check for updates** (the GUI; remote
WU triggers are unreliable). It downloads ~4 GB and reboots.

**Gotcha — WU page shows "出现错误，请稍后再重试" / refreshes empty:** the
BITS service was stopped. Fix:

```powershell
# ensure these are running, then retry the WU page
Start-Service bits ; Start-Service wuauserv ; Start-Service cryptsvc
# if still broken, clear the cache and restart:
net stop wuauserv & net stop bits
rd /s /q C:\Windows\SoftwareDistribution
net start bits & net start wuauserv
```

Confirm the upgrade:

```powershell
reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion" /v DisplayVersion
# DisplayVersion = 25H2, CurrentBuildNumber = 26200
```

After the upgrade, **remove every networking workaround** (portproxy,
socat relay, `tailscale serve`) — mirrored mode does it all natively:

```powershell
netsh interface portproxy reset
```

> Note: `netsh interface portproxy reset` also clears the proxy-forwarding
> rule if you used one for outbound (e.g. `<gateway>:10808 → 127.0.0.1:10808`
> so WSL reaches the Windows proxy). Re-add that one if needed.

---

## 5. Tailscale (remote access)

Install Tailscale on Windows (downloads via proxy if needed) and join the
tailnet with a **pre-authenticated auth key** (no browser needed — this is
a headless/server scenario):

1. Generate a reusable auth key at
   https://login.tailscale.com/admin/settings/keys (Reusable ON, Ephemeral
   **OFF** — it's a server).
2. `tailscale up --authkey=tskey-auth-... --hostname=win-host`

The Tailscale **service** must be running for the CLI/pipe to work. If
`tailscale` commands fail with "is the Tailscale service running?":

```powershell
sc config Tailscale start= auto
sc start Tailscale
```

On 25H2 with mirrored networking you do **not** need `tailscale serve` —
the WSL port is reachable on the Tailscale IP directly. (`tailscale serve`
is only a fallback for older builds and requires the interactive session.)

### Remote control: RDP over Tailscale (recommended)

Win 11 **Pro** has RDP built in. Enable it and scope the firewall to the
**Tailscale subnet only** — reachable over Tailscale from anywhere, but
closed on the LAN and the public internet:

```powershell
# Enable RDP
reg add "HKLM\System\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f
# Allow 3389 ONLY from the Tailscale CGNAT range (NOT 0.0.0.0 / all addresses)
netsh advfirewall firewall add rule name="Tailscale RDP" protocol=TCP dir=in localport=3389 action=allow remoteip=100.64.0.0/10
```

- Connect from any tailnet device by **Tailscale name**: Windows
  `mstsc /v:<host>`; macOS/iOS **Windows App** (ex-"Microsoft Remote
  Desktop") → `<host>`, user `Administrator`. (Win **Home** has no RDP
  server — use a Tailscale-scoped VNC server instead.)
- RDP attaches to the **console session** — the same session the
  `winchrome` Chrome window lives in, so it's how you watch / complete a
  human login (QR scan or slider captcha) on the headed browser.
- RDP needs a non-blank password (`net user Administrator <pw>`). Configure
  auto-login (`Winlogon` `AutoAdminLogon=1` + `DefaultPassword`) so an
  interactive console session always exists for Task Scheduler.

---

## 6. Repo + server setup inside WSL

```bash
# deploy key for the private repo (read-only)
#   add the public half at github.com/<owner>/<repo>/settings/keys
cat > ~/.ssh/config <<'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/vibe_seller_deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config ~/.ssh/vibe_seller_deploy

git clone git@github.com:<owner>/<repo>.git ~/codes/vibe-seller
cd ~/codes/vibe-seller
./install.sh --dev          # installs uv, node, pnpm, sqlite3, claude CLI, playwright
```

> **PATH gotcha** (fixed in `install.sh`): in non-interactive shells (SSH,
> WSL-from-Windows, CI) `~/.local/bin` and the npm prefix aren't on PATH,
> so `check_uv`/`check_pnpm` failed even when installed. `install.sh` now
> bootstraps PATH at the top of the `--dev` block and registers the dirs in
> `$GITHUB_PATH` under CI.

### systemd service for the server

`/etc/systemd/system/vibe-seller.service`:

```ini
[Unit]
Description=Vibe Seller Server
After=network.target

[Service]
Type=simple
User=zpoint
WorkingDirectory=/home/zpoint/codes/vibe-seller
Environment="PATH=/home/zpoint/.local/bin:/home/zpoint/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
ExecStart=/bin/bash /home/zpoint/start-server.sh
Restart=on-failure
RestartSec=10
TimeoutStartSec=180
StandardOutput=append:/home/zpoint/vibe-seller.log
StandardError=append:/home/zpoint/vibe-seller.log

[Install]
WantedBy=multi-user.target
```

`/home/zpoint/start-server.sh` — exec uvicorn directly so systemd tracks
the real process:

```bash
#!/bin/bash
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
cd /home/zpoint/codes/vibe-seller
mkdir -p logs
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 7777 >> logs/backend.log 2>&1
```

> **`Type` gotcha:** do NOT use `start.sh` with `Type=simple` directly —
> `start.sh` forks uvicorn with `nohup ... &` then exits, so systemd thinks
> the service died and kills the whole cgroup. Either `exec` uvicorn (above,
> `Type=simple`) or use `Type=forking` + `PIDFile`. The earlier
> `uv run python ...` wrapper also exited on its own ~30 s in and caused a
> restart loop — call the venv python directly.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vibe-seller
```

### Keep WSL alive

WSL shuts the distro down when no process holds it open, killing the
server. Add a Task Scheduler task **WSL Autostart** that runs at logon:

```
wsl.exe -d Ubuntu-24.04 -u root -- sleep infinity
```

(A foreground `sleep infinity` keeps a Windows→WSL handle open so the
distro — and systemd, and the server — stays up across the session.)

---

## 7. Browser automation — native Windows Chrome (`winchrome` backend)

Some stores need a **headed** Chrome for human login (slider/SMS
challenges). WSLg can show a headed Chrome from an interactive WSL
session, but **not from a systemd service** (no display in the service's
session). So the `winchrome` backend (`app/browser/winchrome.py`) runs
**native Windows Chrome** and connects over CDP:

- Set the store's `browser_backend = 'winchrome'`.
- On first `browser/start`, the backend:
  1. finds `chrome.exe` via `/mnt/c/...`,
  2. creates a Task Scheduler task `VibeSeller-Chrome-<slug>` (interactive
     session, so the window is visible) that launches Chrome with
     `--remote-debugging-port=<debug_port> --user-data-dir=<per-store>`,
  3. starts it if Chrome isn't already on that port,
  4. wraps the CDP port in a `CDPMuxProxy` (per-task tab isolation, same as
     Ziniao).
- On 25H2 mirrored mode the CDP port at `localhost:<debug_port>` is
  reachable from WSL directly — no portproxy.

> **Why not WSLg?** WSLg headed Chrome works interactively but the server
> runs under systemd (no WSLg display), so headed launches from a task
> fail with "launched a headed browser without having a XServer running."
> Native Windows Chrome sidesteps the display problem entirely and is
> visible on the desktop for the human login step. (`DISPLAY=:0` in the
> service env is not enough — the service session has no WSLg.)

Chrome 136+ blocks CDP on the default profile, so a dedicated
`--user-data-dir` per store is mandatory (the backend handles this).

---

## 8. Auto-start summary (survives reboot)

| Component | Mechanism |
|-----------|-----------|
| OpenSSH `sshd` | Windows service, auto-start |
| Tailscale | Windows service, auto-start |
| Proxy (v2rayN) | Task Scheduler `v2rayN Autostart` (at logon) |
| WSL distro | Task Scheduler `WSL Autostart` (`sleep infinity` at logon) |
| vibe-seller server | systemd service `vibe-seller` (enabled) inside WSL |
| Chrome (per store) | Task Scheduler `VibeSeller-Chrome-<slug>`, lazily by backend |
| Windows auto-login | `Winlogon` registry — gives an interactive session at boot |

Power: set the screen to turn off after a few minutes but **never sleep**:

```powershell
powercfg /change monitor-timeout-ac 5
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /h off
```

---

## 9. Troubleshooting cheatsheet

| Symptom | Cause | Fix |
|---------|-------|-----|
| LAN/Tailscale `:7777` times out, local Chrome works | Build 26100 mirrored limitation | Upgrade to 25H2 (§4) |
| Server `[Errno 98] address already in use` | Windows portproxy claims `0.0.0.0:7777` in shared namespace | Remove portproxy (`netsh interface portproxy reset`); on 25H2 none is needed |
| Server restart-loops every ~30 s | `Type=simple` + `start.sh`/`uv run` wrapper exits | `exec` venv python directly (§6) |
| Server dies when SSH session closes | WSL shut down (no holder) | `WSL Autostart` task with `sleep infinity` (§6) |
| `tailscale serve/up` "cannot find pipe" | SSH session can't reach the pipe, or service stopped | `sc start Tailscale`; run from interactive session |
| WU page "出现错误请稍后再重试" | BITS stopped | `Start-Service bits` (§4) |
| Chrome doesn't appear on screen | launched from non-interactive (SSH/systemd) session | launch via Task Scheduler `Interactive` (`winchrome` does this) |
| `install.sh --dev` "missing uv/pnpm" though installed | PATH not bootstrapped in non-interactive shell | already fixed in `install.sh`; re-pull |
| SSH output is mojibake | GBK Windows in UTF-8 terminal | cosmetic; `chcp 65001` in scripts mitigates |

---

## See also

- [docs/macos-setup.md](macos-setup.md) — the macOS (`launchd`) deployment
  path, the sibling of this guide.
- [docs/browser.md](browser.md) — browser backends (Ziniao, chrome,
  winchrome), CDP proxy internals.
- `scripts/win-machine-setup.bat` — the SSH bootstrap script.
- `app/browser/winchrome.py` — the Windows-native Chrome backend.
