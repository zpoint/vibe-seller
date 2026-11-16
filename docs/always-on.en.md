---
description: Keep the computer that runs Vibe Seller awake around the clock on Mac and Windows — no sleep on power, start up after a power cut, automatic login, Vibe Seller starting by itself, plus notes on Ziniao, the lock screen and Windows Update restarts.
---

# Keep It Running 24/7

A scheduled task (say, a report export at 3 a.m.) only runs when two things are true:
**the computer is awake** and **Vibe Seller is running**. If the computer is asleep, shut
down, or sitting at the login screen after a reboot, the task is skipped when its time
comes. **A missed run is not made up later** — the schedule just waits for the next one.

This page shows how to keep a Mac or a Windows PC online. Start with the summary, then
follow the steps for your system.

| Setting | Mac | Windows (installer) |
|---|---|---|
| No sleep while plugged in (screen may turn off) | Turn on the prevent-sleep option in "**Energy**" or "**Battery → Options**"; or `sudo pmset -c sleep 0` | Set sleep to "**Never**"; or `powercfg /change standby-timeout-ac 0` |
| Start up after a power cut | Desktop Macs: "**Start up automatically after a power failure**"; or `sudo pmset -a autorestart 1` | Turn on power-on-after-AC-loss in the BIOS (name varies by maker) |
| Log in automatically after start-up | "**Users & Groups → Automatically log in as**" | Microsoft's Autologon tool |
| Vibe Seller starts after login | Add it once yourself (one command block below) | Built into the installer: tray "**Start at login**" is on by default |
| Don't | Log out, shut down, close a MacBook lid without an external display | Log out, shut down; move update restarts away with active hours |

## Screen off and locked: fine. Asleep: not fine

- **A dark screen or a locked screen is fine.** Vibe Seller and the store browsers keep
  running. Tested: on a Mac set to turn the display off and lock after 5 minutes, the
  overnight scheduled tasks for Ziniao stores finish every night.
- **Sleep is not.** While the computer sleeps, everything is paused and no task fires.
- **Logging out is not either.** Vibe Seller and the browsers run inside your login
  session, and logging out closes them. Locking keeps the session; logging out ends it.
- Set the screen-off timer to whatever you like — it saves power and doesn't affect tasks.

## Mac

### 1. No sleep while plugged in

**Desktop Macs (Mac mini, iMac):**

1. Open **System Settings → Energy** (Chinese UI: 系统设置 → 能耗, or 能源 on some versions).
2. Turn on "**Prevent automatic sleeping when the display is off**" (显示器关闭时，防止自动进入睡眠).

**MacBook:**

1. Keep it plugged in.
2. Open **System Settings → Battery** and click **Options** (系统设置 → 电池 → 选项).
3. Turn on "**Prevent automatic sleeping on power adapter when the display is off**"
   (使用电源适配器供电且显示器关闭时，防止自动进入睡眠).

When the screen turns off is set in **System Settings → Lock Screen** (锁定屏幕) under
"**Turn display off on power adapter when inactive**" — any value works.

From the command line: open Terminal and run (it asks for your login password):

```bash
sudo pmset -c sleep 0 displaysleep 10
```

`-c` means "on charger", `sleep 0` means never sleep, `displaysleep 10` turns the screen
off after 10 minutes. Check it — the `sleep` line should read `0`:

```bash
pmset -g
```

### 2. Start up after a power cut, and log in automatically

**Start up after a power cut (desktop Macs):** in **System Settings → Energy**, turn on
"**Start up automatically after a power failure**" (断电后自动启动). Some newer Macs show
"**Start up when power is connected**" (接入电源时启动) instead — choose **After power
failure**. From the command line:

```bash
sudo pmset -a autorestart 1
```

A MacBook has a battery and doesn't need this.

**Automatic login:** Vibe Seller only starts after you log in, so after a power cut or an
update restart the Mac has to log itself in.

1. Open **System Settings → Users & Groups** (用户与群组).
2. Under "**Automatically log in as**" (自动以此身份登录), pick your account and enter its password.

If the option is greyed out, FileVault is usually on — the pane says "Automatic login can't
be turned on because FileVault is enabled." Whether to turn FileVault off for this is your
call: with automatic login, anyone who can reach the machine can power it on into your
account, so keep it somewhere safe.

Check the current state:

```bash
sysadminctl -autologin status
```

### 3. Make Vibe Seller start after login

`vibe-seller start` starts the server once; after a reboot it does not come back on its
own. Paste this whole block into Terminal. It uses macOS's built-in `launchd` to run
`vibe-seller start` once after every login:

```bash
mkdir -p ~/Library/LaunchAgents ~/Library/Logs
cat > ~/Library/LaunchAgents/com.vibe-seller.start.plist <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.vibe-seller.start</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(command -v vibe-seller)</string>
    <string>start</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$HOME</string>
    <key>PATH</key>
    <string>$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>AbandonProcessGroup</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/vibe-seller-start.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/vibe-seller-start.log</string>
</dict>
</plist>
EOF
plutil -lint ~/Library/LaunchAgents/com.vibe-seller.start.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.vibe-seller.start.plist
```

- Before pasting, make sure the `vibe-seller` command works (`vibe-seller --version`
  prints something).
- The last line runs it once right away. If the server is already running, the log says
  "already running" — that's harmless.
- To check it worked: open <http://localhost:7777>, or read
  `~/Library/Logs/vibe-seller-start.log`.
- Nothing to redo after `vibe-seller upgrade`.
- To remove it:

```bash
launchctl bootout gui/$(id -u)/com.vibe-seller.start
rm ~/Library/LaunchAgents/com.vibe-seller.start.plist
```

This only starts the server once per login; it does not restart it if it crashes. For
crash restarts, or if you run from a source checkout, see
[macOS Deployment — Auto-Launch on Login](macos-setup.md).

### 4. Don't close the MacBook lid

Closing a MacBook's lid puts it to sleep, and tasks stop. The easy way is to leave it open
and plugged in; the screen turns itself off. Apple's closed-display mode is the exception:
it needs the power adapter, an external display, and an external keyboard and mouse
connected.

## Windows (installer)

This covers the [native Windows installer](install.md#windows-native-installer-recommended).
For the WSL2 setup see the [Windows + WSL2 Deployment Guide](windows-setup.md).

### 1. No sleep while plugged in

**Windows 11:**

1. Open **Settings → System → Power & battery** and expand **Screen, sleep, & hibernate
   timeouts** (设置 → 系统 → 电源 & 电池 → 屏幕、睡眠 & 休眠超时).
2. Set the plugged-in "**Make my device sleep after**" (在以下时间后使设备进入睡眠状态) to **Never**.
3. "**Turn my screen off after**" (在以下操作后关闭我的屏幕) can be anything — a dark
   screen doesn't affect tasks.

**A command for both Windows 10 and 11:** search the Start menu for PowerShell, open it,
and paste:

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 10
```

The first two lines mean never sleep and never hibernate on AC power; the third turns the
screen off after 10 minutes. To switch hibernation off completely, right-click PowerShell,
choose **Run as administrator**, and run:

```powershell
powercfg /hibernate off
```

### 2. Start up after a power cut, and log in automatically

**Start up after a power cut:** this lives in the PC's BIOS, not in Windows Settings. Many
desktops and mini PCs have an option usually called Restore on AC Power Loss or AC Power
Recovery; the name and location vary by maker, so check the motherboard or PC manual.

**Automatic login:** the Vibe Seller tray starts after you sign in to Windows, so after a
restart the PC has to sign itself in. Use Microsoft's official
[Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon) tool:

1. Download and run Autologon.
2. Fill in Username, Domain and Password, then click **Enable**.
3. To turn it off, run it again and click **Disable**. Holding Shift during start-up skips
   automatic login once.

The password is stored encrypted in the registry, but an administrator on this PC can read
it back, and anyone who can reach the machine can power it on into your account — keep it
somewhere safe.

### 3. Vibe Seller starts by itself

The installer already does this: after you sign in to Windows, the Vibe Seller icon
appears in the tray and the server runs in the background.

- The tray's "**Start at login**" (开机自动启动) item is ticked by default — make sure it
  still is. It is the same switch as Vibe Seller's entry on Task Manager's Startup page.
- The tray's "**Quit**" (退出) also stops the server, until the next sign-in. Don't click it
  if you don't mean to.

### 4. Keep Windows Update from restarting at night

Windows Update sometimes needs a restart. Put the hours your schedules usually run inside
**active hours**, and Windows tries to avoid restarting then:

- **Windows 11:** **Settings → Windows Update → Advanced options → Active hours**, set
  **Adjust active hours** to **Manually**, then pick the start and end times
  (设置 → Windows 更新 → 高级选项 → 使用时段 → 调整使用时段 → 手动).
- **Windows 10:** **Settings → Update & Security → Windows Update → Change active hours**
  (设置 → 更新和安全 → Windows 更新 → 更改使用时段).

If it restarts anyway, automatic login plus the tray's start-at-login bring Vibe Seller back.

## Ziniao stores

- **Ziniao doesn't need to stay open.** When a task starts, Vibe Seller launches Ziniao in
  developer mode by itself — see [Ziniao: Setup & Common Errors](ziniao.md).
- **Don't use Ziniao by hand on this computer at night.** Vibe Seller closes a Ziniao you
  opened yourself before it opens a store.
- **Log in to Ziniao by hand on this computer once, in advance.** The first login hits
  "检测到您在新终端登录" (new device) and needs the main account's approval, which nobody
  can give at 3 a.m. See
  ["检测到您在新终端登录" when binding a store](ziniao.md#检测到您在新终端登录-when-binding-a-store).
- **Chrome stores:** if the seller center asks for a slider or SMS check the first time,
  you need to log in by hand once — see [Logins stick around](stores.md#logins-stick-around).

## Checking in when you're away

On the same network, open `http://<this computer's IP>:7777` — see
[Access from the local network](install.md#access-from-the-local-network). From outside,
use a VPN or Tailscale; never expose the port to the public internet.

## Test it once

1. Restart the computer and don't touch it.
2. A few minutes later, open `http://<this computer's IP>:7777` from another device. If it
   loads, automatic login and auto-start both work.
3. Create a schedule that fires a few minutes from now (see [Scheduled Tasks](schedules.md))
   and check it still runs to completion after the screen has turned off.
