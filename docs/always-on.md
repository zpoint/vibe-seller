---
description: 让跑 Vibe Seller 的电脑 7×24 小时不睡眠：Mac 和 Windows 的插电不睡眠、断电后自动开机、自动登录、Vibe Seller 开机自启，以及紫鸟、锁屏、Windows 更新重启这些注意事项。
---

# 让电脑 7×24 小时运行

定时任务（比如每天凌晨导出报表）只在两件事同时成立时才会执行：**这台电脑醒着**，
**Vibe Seller 开着**。电脑睡眠、关机，或者断电重启后停在登录界面，到点的任务都会被跳过。
**错过的那一次不会补跑**，只能等下一次。

这一页讲怎么把 Mac 或 Windows 电脑设置成一直在线。先看总表，再按自己的系统往下做。

| 设置 | Mac | Windows（安装包） |
|---|---|---|
| 插电时不睡眠（屏幕可以关） | 「能耗」或「电池 → 选项」里打开防止自动睡眠；或 `sudo pmset -c sleep 0` | 睡眠设成「从不」；或 `powercfg /change standby-timeout-ac 0` |
| 断电后自动开机 | 台式 Mac 打开「断电后自动启动」；或 `sudo pmset -a autorestart 1` | 在 BIOS 里打开来电自动开机（各厂商名字不同） |
| 开机后自动登录 | 「用户与群组 → 自动以此身份登录」 | 微软官方工具 Autologon |
| Vibe Seller 登录后自动启动 | 需要自己加一次（下面一段命令） | 安装包已经做好，托盘里「开机自动启动」默认勾选 |
| 不要做的事 | 注销、关机、不接显示器时合上 MacBook | 注销、关机；用「使用时段」把更新重启挪开 |

## 先分清：熄屏、锁屏可以，睡眠不行

- **屏幕熄灭、锁屏都没关系。** Vibe Seller 和店铺浏览器在后台照常运行。实测：一台设成
  5 分钟熄屏并锁屏的 Mac，凌晨的紫鸟店铺定时任务每天照常跑完。
- **睡眠不行。** 电脑睡着时程序全部暂停，到点的任务不会触发。
- **注销（退出登录）也不行。** Vibe Seller 和浏览器跑在你的登录会话里，注销会把它们一起关掉。
  锁屏会保留会话，注销不会。
- 屏幕熄灭的时间随便设，省电又不影响任务。

## Mac

### 1. 插电时不睡眠

**Mac mini、iMac 这类台式 Mac：**

1. 打开「系统设置 → 能耗」（英文界面是 System Settings → Energy；部分系统版本中文叫「能源」）。
2. 打开「显示器关闭时，防止自动进入睡眠」（Prevent automatic sleeping when the display is off）。

**MacBook：**

1. 一直插着电源。
2. 打开「系统设置 → 电池」，点「选项」（System Settings → Battery → Options）。
3. 打开「使用电源适配器供电且显示器关闭时，防止自动进入睡眠」
   （Prevent automatic sleeping on power adapter when the display is off）。

屏幕多久熄灭在「系统设置 → 锁定屏幕」（Lock Screen）里的
「使用电源适配器供电且不活跃时关闭显示器」，设成几分钟都可以。

命令行做法：打开「终端」，输入下面这行（会要求输入开机密码）：

```bash
sudo pmset -c sleep 0 displaysleep 10
```

`-c` 表示「接电源时」，`sleep 0` 是不睡眠，`displaysleep 10` 是 10 分钟后熄屏。
检查一下，`sleep` 一行应该是 `0`：

```bash
pmset -g
```

### 2. 断电后自动开机、自动登录

**断电后自动开机（台式 Mac）：** 在「系统设置 → 能耗」里打开「断电后自动启动」
（Start up automatically after a power failure）。有些较新的 Mac 显示的是「接入电源时启动」
（Start up when power is connected），选「断电后」。命令行做法：

```bash
sudo pmset -a autorestart 1
```

MacBook 有电池，不需要这一项。

**自动登录：** Vibe Seller 要在你登录之后才会启动，所以停电恢复、系统更新重启之后，
电脑需要自己登录进去。

1. 打开「系统设置 → 用户与群组」（Users & Groups）。
2. 在「自动以此身份登录」（Automatically log in as）里选你的账户，输入密码。

这一项是灰色的，通常是因为打开了「文件保险箱」（FileVault），界面会提示
「无法打开自动登录，因为文件保险箱已启用。」。要不要为了自动登录关掉文件保险箱，
你自己权衡：开了自动登录，能碰到这台电脑的人开机就能进你的账户，所以把它放在安全的地方。

查看当前状态：

```bash
sysadminctl -autologin status
```

### 3. 让 Vibe Seller 登录后自己启动

`vibe-seller start` 只启动这一次，电脑重启后不会自己起来。在终端里整段粘贴下面的命令，
它会用 macOS 自带的 `launchd` 在每次登录后执行一次 `vibe-seller start`：

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

- 粘贴前先确认 `vibe-seller` 命令能用（在终端输入 `vibe-seller --version` 有输出）。
- 最后一行会马上执行一次。服务已经在运行时，日志里会写「already running」，不影响什么。
- 看是否成功：打开 <http://localhost:7777>，或者看日志 `~/Library/Logs/vibe-seller-start.log`。
- `vibe-seller upgrade` 升级后不用重做。
- 不想要了：

```bash
launchctl bootout gui/$(id -u)/com.vibe-seller.start
rm ~/Library/LaunchAgents/com.vibe-seller.start.plist
```

这一段只负责登录时启动一次，服务中途崩溃不会自动拉起。需要崩溃自动重启、或者你是从源码运行的，
看 [macOS Deployment — Auto-Launch on Login](macos-setup.md)（英文）。

### 4. MacBook 别合盖

MacBook 合上盖子会睡眠，任务就停了。最省事的做法是开着盖子插电放着，屏幕会自己熄灭。
苹果的「合盖模式」是例外：需要同时接着电源、外接显示器和外接键盘鼠标。

## Windows（安装包）

下面针对 [Windows 原生安装包](install.md#windows原生安装包推荐)。WSL2 方案看
[Windows + WSL2 Deployment Guide](windows-setup.md)（英文）。

### 1. 插电时不睡眠

**Windows 11：**

1. 打开「设置 → 系统 → 电源 & 电池」，展开「屏幕、睡眠 & 休眠超时」
   （Settings → System → Power & battery → Screen, sleep, & hibernate timeouts）。
2. 把接通电源时的「在以下时间后使设备进入睡眠状态」（Make my device sleep after）设为「从不」。
3. 「在以下操作后关闭我的屏幕」（Turn my screen off after）随便设，熄屏不影响任务。

**Windows 10 和 11 都能用的命令：** 在开始菜单搜索 PowerShell 打开，粘贴：

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 10
```

前两行是接电源时永不睡眠、永不休眠，第三行是 10 分钟后熄屏。想把休眠彻底关掉，
右键 PowerShell 选「以管理员身份运行」，再执行：

```powershell
powercfg /hibernate off
```

### 2. 断电后自动开机、自动登录

**断电后自动开机：** 这项在电脑的 BIOS 里，不在 Windows 设置里。很多台式机和迷你主机有一个
「来电自动开机」类的选项，英文常叫 Restore on AC Power Loss 或 AC Power Recovery，
名字和位置各厂商不同，查一下主板或整机的说明书。

**自动登录：** Vibe Seller 的托盘在你登录 Windows 之后才启动，所以重启之后电脑要能自己登录。
用微软官方的 [Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon) 工具：

1. 下载并运行 Autologon。
2. 填好用户名（Username）、域（Domain）和密码（Password），点「Enable」。
3. 想关掉就再运行一次点「Disable」。开机时按住 Shift 可以跳过这一次自动登录。

密码会加密保存在注册表里，但这台电脑的管理员可以读出来；能碰到这台电脑的人开机就能进你的账户，
把它放在安全的地方。

### 3. Vibe Seller 自动启动

安装包已经做好了：登录 Windows 后，右下角托盘出现 Vibe Seller 图标，服务就在后台运行。

- 托盘菜单里的「开机自动启动」（Start at login）默认勾选，确认它还勾着。它和任务管理器
  「启动」页里的 Vibe Seller 是同一个开关。
- 点托盘的「退出」会连服务一起停掉，下次登录才会再起来。不需要时别点它。

### 4. 别让 Windows 更新挑半夜重启

Windows 更新有时要重启。把定时任务常跑的时间放进「使用时段」，系统会尽量避开这段时间重启：

- **Windows 11：**「设置 → Windows 更新 → 高级选项 → 使用时段」，在「调整使用时段」里选「手动」，
  再设开始和结束时间（Settings → Windows Update → Advanced options → Active hours → Adjust active hours → Manually）。
- **Windows 10：**「设置 → 更新和安全 → Windows 更新 → 更改使用时段」
  （Settings → Update & Security → Windows Update → Change active hours）。

真的重启了也没关系：前面的自动登录加上托盘自启，会让 Vibe Seller 自己回来。

## 紫鸟店铺

- **紫鸟不用一直开着。** 任务开始时，Vibe Seller 会自己用开发者模式启动紫鸟，
  见 [紫鸟：开通与常见报错](ziniao.md)。
- **半夜别在这台电脑上手动用紫鸟。** Vibe Seller 打开店铺前会先关掉你手动打开的紫鸟。
- **提前在这台电脑上手动登录过一次紫鸟。** 第一次登录会遇到「检测到您在新终端登录」，
  要主账号批准，半夜没人能处理。做法见 [报错：「检测到您在新终端登录」](ziniao.md#报错检测到您在新终端登录)。
- **Chrome 店铺**第一次进卖家后台如果要滑块、短信验证，要你在旁边手动登录一次，
  见 [登录状态会保留](stores.md#登录状态会保留)。

## 不在电脑旁边时怎么看

同一个网络里，用 `http://<这台电脑的IP>:7777` 打开就行，见 [局域网访问](install.md#局域网访问)。
在外面要看，请走 VPN 或 Tailscale，不要把端口直接暴露到公网。

## 设置完检查一遍

1. 重启一次电脑，什么都不碰。
2. 几分钟后，在另一台设备上打开 `http://<这台电脑的IP>:7777`，能打开就说明自动登录和自动启动都生效了。
3. 建一个几分钟后执行的定时任务（见 [定时任务](schedules.md)），等屏幕熄灭之后看它是否照常跑完。
