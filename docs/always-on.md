---
description: 让跑 Vibe Seller 的电脑 7×24 小时不睡眠：台式 Mac、MacBook、Windows 各一份完整步骤——插电不睡眠、断电后自动开机、自动登录、Vibe Seller 登录后自动启动。
---

# 让电脑 7×24 小时运行

定时任务（比如每天凌晨导出报表）只在两件事同时成立时才会执行：**这台电脑醒着**，
**Vibe Seller 开着**。电脑睡眠、关机，或者重启后停在登录界面，到点的任务都会被跳过。
**错过的那一次不会补跑**，只能等下一次。

按你的电脑选一份，从头做到尾：

- [Mac mini / iMac（台式 Mac）](#mac-mini--imac台式-mac)
- [MacBook](#macbook)
- [Windows](#windows)

做之前先记住一条：**熄屏、锁屏可以，睡眠、注销不行。**

- 屏幕熄灭、锁屏都不影响任务。实测：一台设成 5 分钟熄屏并锁屏的 Mac，凌晨的紫鸟店铺定时任务每天照常跑完。
- 睡眠时程序全部暂停，任务不会触发。
- 注销（退出登录）会把 Vibe Seller 和浏览器一起关掉。锁屏保留登录，注销不保留。

## Mac mini / iMac（台式 Mac）

1. **插电时不睡眠。** 打开「系统设置 → 能耗」（System Settings → Energy；部分系统版本中文叫「能源」），
   打开「显示器关闭时，防止自动进入睡眠」（Prevent automatic sleeping when the display is off）。
   屏幕多久熄灭随意，在「系统设置 → 锁定屏幕」（Lock Screen）里设置。

    命令行做法（在「终端」里执行，会要求输入开机密码）：

    ```bash
    sudo pmset -c sleep 0 displaysleep 10
    pmset -g    # 检查：sleep 一行应该是 0
    ```

2. **断电后自动开机。** 还是在「系统设置 → 能耗」，打开「断电后自动启动」
   （Start up automatically after a power failure）。有些较新的 Mac 显示的是「接入电源时启动」
   （Start up when power is connected），选「断电后」。命令行做法：

    ```bash
    sudo pmset -a autorestart 1
    ```

3. **开机后自动登录。** 打开「系统设置 → 用户与群组」（Users & Groups），
   在「自动以此身份登录」（Automatically log in as）里选你的账户，输入密码。

    这一项是灰色的，通常是因为打开了「文件保险箱」（FileVault），界面会提示
    「无法打开自动登录，因为文件保险箱已启用。」。要不要为此关掉文件保险箱，你自己权衡：
    开了自动登录，能碰到这台电脑的人开机就能进你的账户，所以把它放在安全的地方。
    查看当前状态：`sysadminctl -autologin status`。

4. **让 Vibe Seller 登录后自己启动。** 在终端执行：

    ```bash
    vibe-seller autostart on
    ```

    之后每次登录都会自动执行一次 `vibe-seller start`；服务没在运行时，这条命令也会马上把它启动。

    - 查看：`vibe-seller autostart status`，显示「Start at login: on」就对了。
    - 关掉：`vibe-seller autostart off`。
    - 用的不是默认端口 7777：三条命令都加上 `--port 端口号`。
    - `vibe-seller upgrade` 升级之后不用重做。

    它只在登录时启动一次，服务中途崩溃不会自动拉起。需要崩溃自动重启、或者你是从源码运行的，
    看 [macOS Deployment — Auto-Launch on Login](macos-setup.md)（英文）。

5. **紫鸟店铺：** 见下面 [紫鸟店铺注意事项](#紫鸟店铺注意事项)。

6. **重启测一次：** 见 [设置完测一次](#设置完测一次)。

## MacBook

1. **一直插着电源，别合盖。** MacBook 合上盖子会睡眠，任务就停了。开着盖子放着就行，屏幕会自己熄灭。
   苹果的「合盖模式」是例外：需要同时接着电源、外接显示器和外接键盘鼠标。

2. **插电时不睡眠。** 打开「系统设置 → 电池」，点「选项」（System Settings → Battery → Options），
   打开「使用电源适配器供电且显示器关闭时，防止自动进入睡眠」
   （Prevent automatic sleeping on power adapter when the display is off）。
   屏幕多久熄灭在「系统设置 → 锁定屏幕」里的「使用电源适配器供电且不活跃时关闭显示器」，设成几分钟都可以。

    命令行做法（在「终端」里执行，会要求输入开机密码）：

    ```bash
    sudo pmset -c sleep 0 displaysleep 10
    pmset -g    # 检查：sleep 一行应该是 0
    ```

    `-c` 表示只在接电源时生效，用电池时照常睡眠。

3. **开机后自动登录。** 打开「系统设置 → 用户与群组」（Users & Groups），
   在「自动以此身份登录」（Automatically log in as）里选你的账户，输入密码。

    这一项是灰色的，通常是因为打开了「文件保险箱」（FileVault），界面会提示
    「无法打开自动登录，因为文件保险箱已启用。」。要不要为此关掉文件保险箱，你自己权衡：
    开了自动登录，能碰到这台电脑的人开机就能进你的账户。
    查看当前状态：`sysadminctl -autologin status`。

4. **让 Vibe Seller 登录后自己启动。** 在终端执行：

    ```bash
    vibe-seller autostart on
    ```

    之后每次登录都会自动执行一次 `vibe-seller start`；服务没在运行时，这条命令也会马上把它启动。

    - 查看：`vibe-seller autostart status`，显示「Start at login: on」就对了。
    - 关掉：`vibe-seller autostart off`。
    - 用的不是默认端口 7777：三条命令都加上 `--port 端口号`。
    - `vibe-seller upgrade` 升级之后不用重做。

    它只在登录时启动一次，服务中途崩溃不会自动拉起，需要的话看
    [macOS Deployment — Auto-Launch on Login](macos-setup.md)（英文）。

5. **紫鸟店铺：** 见下面 [紫鸟店铺注意事项](#紫鸟店铺注意事项)。

6. **重启测一次：** 见 [设置完测一次](#设置完测一次)。

## Windows

下面针对 [Windows 原生安装包](install.md#windows原生安装包推荐)。WSL2 方案看
[Windows + WSL2 Deployment Guide](windows-setup.md)（英文）。

1. **插电时不睡眠。**

    Windows 11：打开「设置 → 系统 → 电源 & 电池」，展开「屏幕、睡眠 & 休眠超时」
    （Settings → System → Power & battery → Screen, sleep, & hibernate timeouts），
    把接通电源时的「在以下时间后使设备进入睡眠状态」（Make my device sleep after）设为「从不」。
    「在以下操作后关闭我的屏幕」（Turn my screen off after）随意。

    Windows 10 和 11 都能用的命令：在开始菜单搜索 PowerShell 打开，粘贴：

    ```powershell
    powercfg /change standby-timeout-ac 0
    powercfg /change hibernate-timeout-ac 0
    powercfg /change monitor-timeout-ac 10
    ```

    前两行是接电源时永不睡眠、永不休眠，第三行是 10 分钟后熄屏。想把休眠彻底关掉，
    右键 PowerShell 选「以管理员身份运行」，再执行 `powercfg /hibernate off`。

2. **断电后自动开机。** 这项在电脑的 BIOS 里，不在 Windows 设置里。很多台式机和迷你主机有一个
   「来电自动开机」类的选项，英文常叫 Restore on AC Power Loss 或 AC Power Recovery，
   名字和位置各厂商不同，查一下主板或整机的说明书。

3. **开机后自动登录。** 用微软官方的 [Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon) 工具：
   下载并运行，填好用户名（Username）、域（Domain）和密码（Password），点「Enable」。
   想关掉就再运行一次点「Disable」；开机时按住 Shift 可以跳过这一次自动登录。

    密码会加密保存在注册表里，但这台电脑的管理员可以读出来；能碰到这台电脑的人开机就能进你的账户，
    把它放在安全的地方。

4. **Vibe Seller 登录后自动启动。** 安装包已经做好了：登录 Windows 后右下角托盘出现 Vibe Seller 图标，
   服务就在后台运行。确认托盘菜单里的「开机自动启动」（Start at login）还勾着，
   它和任务管理器「启动」页里的 Vibe Seller 是同一个开关。

    点托盘的「退出」会连服务一起停掉，下次登录才会再起来，不需要时别点。

5. **别让 Windows 更新挑半夜重启。** 把定时任务常跑的时间放进「使用时段」，系统会尽量避开这段时间重启：

    - Windows 11：「设置 → Windows 更新 → 高级选项 → 使用时段」，在「调整使用时段」里选「手动」，
      再设开始和结束时间（Settings → Windows Update → Advanced options → Active hours → Adjust active hours → Manually）。
    - Windows 10：「设置 → 更新和安全 → Windows 更新 → 更改使用时段」
      （Settings → Update & Security → Windows Update → Change active hours）。

    真的重启了也没关系：自动登录加上托盘自启，会让 Vibe Seller 自己回来。

6. **紫鸟店铺：** 见下面 [紫鸟店铺注意事项](#紫鸟店铺注意事项)。

7. **重启测一次：** 见 [设置完测一次](#设置完测一次)。

## 紫鸟店铺注意事项

- **紫鸟不用一直开着。** 任务开始时，Vibe Seller 会自己用开发者模式启动紫鸟，
  见 [紫鸟：开通与常见报错](ziniao.md)。
- **半夜别在这台电脑上手动用紫鸟。** Vibe Seller 打开店铺前会先关掉你手动打开的紫鸟。
- **提前在这台电脑上手动登录过一次紫鸟。** 第一次登录会遇到「检测到您在新终端登录」，
  要主账号批准，半夜没人能处理。做法见 [报错：「检测到您在新终端登录」](ziniao.md#报错检测到您在新终端登录)。
- **Chrome 店铺**第一次进卖家后台如果要滑块、短信验证，要你在旁边手动登录一次，
  见 [登录状态会保留](stores.md#登录状态会保留)。

## 设置完测一次

1. 重启一次电脑，什么都不碰。
2. 几分钟后，在另一台设备上打开 `http://<这台电脑的IP>:7777`（见 [局域网访问](install.md#局域网访问)），
   能打开就说明自动登录和自动启动都生效了。在外面要看，请走 VPN 或 Tailscale，不要把端口直接暴露到公网。
3. 建一个几分钟后执行的定时任务（见 [定时任务](schedules.md)），等屏幕熄灭之后看它是否照常跑完。
