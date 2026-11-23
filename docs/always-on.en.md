---
description: Keep the computer that runs Vibe Seller awake around the clock — one complete how-to each for desktop Macs, MacBooks and Windows — no sleep on power, start up after a power cut, automatic login, and Vibe Seller starting after login.
---

# Keep It Running 24/7

A scheduled task (say, a report export at 3 a.m.) only runs when two things are true:
**the computer is awake** and **Vibe Seller is running**. If the computer is asleep, shut
down, or sitting at the login screen after a reboot, the task is skipped when its time
comes. **A missed run is not made up later** — the schedule just waits for the next one.

Pick your computer and follow its steps from top to bottom:

- [Mac mini / iMac (desktop Mac)](#mac-mini--imac-desktop-mac)
- [MacBook](#macbook)
- [Windows](#windows)

One rule first: **screen off and locked is fine; asleep or logged out is not.**

Where a step has **GUI / Command line** tabs, **pick one** and follow it — you don't need both.

- A dark or locked screen doesn't affect tasks. Tested: on a Mac set to turn the display
  off and lock after 5 minutes, the overnight scheduled tasks for Ziniao stores finish every night.
- While the computer sleeps, everything is paused and no task fires.
- Logging out closes Vibe Seller and the browsers. Locking keeps you logged in; logging out doesn't.

## Mac mini / iMac (desktop Mac)

1. **No sleep while plugged in.**

    === "GUI"

        Open **System Settings → Energy** (Chinese UI: 系统设置 → 能耗, or 能源 on some
        versions) and turn on "**Prevent automatic sleeping when the display is off**"
        (显示器关闭时，防止自动进入睡眠). When the screen turns off is up to you, under
        **System Settings → Lock Screen**.

    === "Command line"

        In Terminal (it asks for your login password):

        ```bash
        sudo pmset -c sleep 0 displaysleep 10
        pmset -g    # check: the sleep line should read 0
        ```

2. **Start up after a power cut.**

    === "GUI"

        Also in **System Settings → Energy**, turn on "**Start up automatically after a power
        failure**" (断电后自动启动). Some newer Macs show "**Start up when power is connected**"
        (接入电源时启动) instead — choose **After power failure**.

    === "Command line"

        ```bash
        sudo pmset -a autorestart 1
        ```

3. **Log in automatically.** Open **System Settings → Users & Groups** (用户与群组) and pick
   your account under "**Automatically log in as**" (自动以此身份登录), then enter its password.

    If the option is greyed out, FileVault is usually on — the pane says "Automatic login
    can't be turned on because FileVault is enabled." Whether to turn FileVault off for this
    is your call: with automatic login, anyone who can reach the machine can power it on into
    your account, so keep it somewhere safe. Check the current state with
    `sysadminctl -autologin status`.

4. **Make Vibe Seller start after login.** In Terminal:

    ```bash
    vibe-seller autostart on
    ```

    From then on every login runs `vibe-seller start` once; if the server isn't running
    right now, this command starts it too.

    - Check: `vibe-seller autostart status` should say "Start at login: on".
    - Turn it off: `vibe-seller autostart off`.
    - Not on the default port 7777? Add `--port <port>` to all three commands.
    - Nothing to redo after `vibe-seller upgrade`.

    It starts the server once per login; it does not restart it if it crashes. For crash
    restarts, or if you run from a source checkout, see
    [macOS Deployment — Auto-Launch on Login](macos-setup.md).

5. **Ziniao stores:** see [Ziniao stores](#ziniao-stores) below.

6. **Test it with a restart:** see [Test it once](#test-it-once).

## MacBook

1. **Keep it plugged in, lid open.** Closing a MacBook's lid puts it to sleep, and tasks
   stop. Leave it open; the screen turns itself off. Apple's closed-display mode is the
   exception: it needs the power adapter, an external display, and an external keyboard and
   mouse connected.

2. **No sleep while plugged in.**

    === "GUI"

        Open **System Settings → Battery** and click **Options** (系统设置 → 电池 → 选项),
        then turn on "**Prevent automatic sleeping on power adapter when the display is off**"
        (使用电源适配器供电且显示器关闭时，防止自动进入睡眠). When the screen turns off is set in
        **System Settings → Lock Screen** under "**Turn display off on power adapter when
        inactive**" — any value works.

    === "Command line"

        In Terminal (it asks for your login password):

        ```bash
        sudo pmset -c sleep 0 displaysleep 10
        pmset -g    # check: the sleep line should read 0
        ```

        `-c` means it only applies on the charger; on battery the Mac still sleeps as usual.

3. **Log in automatically.** Open **System Settings → Users & Groups** (用户与群组) and pick
   your account under "**Automatically log in as**" (自动以此身份登录), then enter its password.

    If the option is greyed out, FileVault is usually on — the pane says "Automatic login
    can't be turned on because FileVault is enabled." Whether to turn FileVault off for this
    is your call: with automatic login, anyone who can reach the machine can power it on into
    your account. Check the current state with `sysadminctl -autologin status`.

4. **Make Vibe Seller start after login.** In Terminal:

    ```bash
    vibe-seller autostart on
    ```

    From then on every login runs `vibe-seller start` once; if the server isn't running
    right now, this command starts it too.

    - Check: `vibe-seller autostart status` should say "Start at login: on".
    - Turn it off: `vibe-seller autostart off`.
    - Not on the default port 7777? Add `--port <port>` to all three commands.
    - Nothing to redo after `vibe-seller upgrade`.

    It starts the server once per login and does not restart it if it crashes; for that see
    [macOS Deployment — Auto-Launch on Login](macos-setup.md).

5. **Ziniao stores:** see [Ziniao stores](#ziniao-stores) below.

6. **Test it with a restart:** see [Test it once](#test-it-once).

## Windows

This covers the [native Windows installer](install.md).
For the WSL2 setup see the [Windows + WSL2 Deployment Guide](windows-setup.md).

1. **No sleep while plugged in.**

    === "GUI"

        Windows 11: open **Settings → System → Power & battery**, expand **Screen, sleep, &
        hibernate timeouts** (设置 → 系统 → 电源 & 电池 → 屏幕、睡眠 & 休眠超时), and set the
        plugged-in "**Make my device sleep after**" (在以下时间后使设备进入睡眠状态) to **Never**.
        "**Turn my screen off after**" (在以下操作后关闭我的屏幕) can be anything.

        On Windows 10, use the **Command line** tab.

    === "Command line"

        Works on Windows 10 and 11: search the Start menu for PowerShell, open it, and paste:

        ```powershell
        powercfg /change standby-timeout-ac 0
        powercfg /change hibernate-timeout-ac 0
        powercfg /change monitor-timeout-ac 10
        ```

        The first two lines mean never sleep and never hibernate on AC power; the third turns
        the screen off after 10 minutes. To switch hibernation off completely, right-click
        PowerShell, choose **Run as administrator**, and run `powercfg /hibernate off`.

2. **Start up after a power cut.** This lives in the PC's BIOS, not in Windows Settings.
   Many desktops and mini PCs have an option usually called Restore on AC Power Loss or AC
   Power Recovery; the name and location vary by maker, so check the motherboard or PC manual.

3. **Sign in automatically.** Use Microsoft's official
   [Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon) tool: download
   and run it, fill in Username, Domain and Password, and click **Enable**. To turn it off,
   run it again and click **Disable**; holding Shift during start-up skips automatic sign-in once.

    The password is stored encrypted in the registry, but an administrator on this PC can
    read it back, and anyone who can reach the machine can power it on into your account —
    keep it somewhere safe.

4. **Vibe Seller starts after sign-in.** The installer already does this: after you sign
   in, the Vibe Seller icon appears in the tray and the server runs in the background. Make
   sure the tray's "**Start at login**" (开机自动启动) item is still ticked — it is the same
   switch as Vibe Seller's entry on Task Manager's Startup page.

    The tray's "**Quit**" (退出) also stops the server, until the next sign-in. Don't click
    it if you don't mean to.

5. **Keep Windows Update from restarting at night.** Put the hours your schedules usually
   run inside **active hours**, and Windows tries to avoid restarting then:

    - Windows 11: **Settings → Windows Update → Advanced options → Active hours**, set
      **Adjust active hours** to **Manually**, then pick the start and end times
      (设置 → Windows 更新 → 高级选项 → 使用时段 → 调整使用时段 → 手动).
    - Windows 10: **Settings → Update & Security → Windows Update → Change active hours**
      (设置 → 更新和安全 → Windows 更新 → 更改使用时段).

    If it restarts anyway, automatic sign-in plus the tray's start-at-login bring Vibe Seller back.

6. **Ziniao stores:** see [Ziniao stores](#ziniao-stores) below.

7. **Test it with a restart:** see [Test it once](#test-it-once).

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

## Test it once

1. Restart the computer and don't touch it.
2. A few minutes later, open `http://<this computer's IP>:7777` from another device (see
   [Access from the local network](install.md#access-from-the-local-network)). If it loads,
   automatic login and auto-start both work. From outside, use a VPN or Tailscale; never
   expose the port to the public internet.
3. Create a schedule that fires a few minutes from now (see [Scheduled Tasks](schedules.md))
   and check it still runs to completion after the screen has turned off.
