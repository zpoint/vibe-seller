---
description: 安装 Vibe Seller：Windows 原生安装包、macOS / Linux 一行命令、升级与卸载、WSL2 进阶方案、源码安装、服务管理与局域网访问。
---

# 安装

安装只需三分钟：选择系统，复制一条命令，打开网页。装好后第一次怎么用，见 [快速开始](quickstart.md)。

> 文中的 `~` 指你的用户目录，Windows 上就是 `C:\Users\<你的用户名>`。
> 你的店铺数据、截图、日志和模型记忆都保存在 `~/.vibe-seller/` 这一个目录里，卸载程序不会删除它。

## 环境要求

开始安装前准备三样东西：

- 一个 LLM API Key：Claude、DeepSeek、Kimi、MiniMax、GLM、通义千问、MiMo 任选一家，获取方式见 [配置 AI 模型](llm-keys.md)
- 一个浏览器：紫鸟（防关联浏览器），或者使用程序自带的 Chromium
- 一台长期运行的电脑：macOS、Linux，或 Windows 10 版本 1809 / Windows Server 2019 及以上（64 位）

不需要注册账号，不需要购买服务，代码和数据都保存在你自己的机器上。

## Windows：原生安装包（推荐）

到 [最新 release](https://github.com/zpoint/vibe-seller/releases/latest) 下载 **`VibeSeller-Setup.exe`**，双击运行。

安装向导全程使用中文提示（向导语言跟随系统，中文系统显示中文）。最后一步默认勾选 **现在打开 Vibe Seller**，点击「完成」后自动启动服务并打开浏览器，直接进入网页后台 <http://localhost:7777>。

也可以在命令行完成安装。在开始菜单搜索 **PowerShell** 打开，粘贴下面这一行后回车：

```powershell
irm https://raw.githubusercontent.com/zpoint/vibe-seller/main/installer/windows/install.ps1 | iex
```

!!! note "为什么安装包有 650MB"
    Python 运行环境、Claude Code、Git 和 Chromium 浏览器引擎都已打包在安装包里，安装时不需要联网下载任何依赖，网络较慢或公司代理严格的机器同样能装上。代价就是安装包体积较大，约 650MB。

装完之后你需要知道的几件事：

- **安装位置**：程序装在你自己的账户下，不会弹出 UAC 提示，也不需要管理员权限，程序文件位于 `%LOCALAPPDATA%\Programs\VibeSeller`。
- **开机自动启动**：登录 Windows 后服务就在后台运行。右下角托盘有一个 Vibe Seller 图标，菜单包含 **打开 Vibe Seller**、**复制局域网地址**、**重启服务**、**检查更新**、**退出**（跟随系统语言）。托盘只是入口，服务的启动和停止使用同一套命令，退出托盘会一并停止服务。
- **升级**：在托盘点击 **检查更新**，它会自动从 GitHub 找到新版本的 `VibeSeller-Setup.exe`，下载并打开安装程序覆盖安装。这期间服务和托盘会自动退出，安装完成后下次登录自动恢复。也可以到 [release 页](https://github.com/zpoint/vibe-seller/releases/latest) 下载新的安装包手动安装一次。两种方式都只替换程序文件，`~/.vibe-seller/` 里的数据不会变动。
- **更换端口**：给系统添加 `VIBE_SELLER_PORT` 环境变量（默认 7777），托盘会按这个端口启动服务。
- **卸载**：在 Windows「设置 → 应用」中卸载 Vibe Seller。店铺数据保留在 `~/.vibe-seller/`，需要彻底清除时自行删除该目录。

## macOS / Linux：一行命令

打开终端（Mac 上按 ⌘ + 空格，输入「终端」后回车），粘贴下面这一行：

```bash
curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
```

脚本会依次装好工具链（uv、Node、Claude Code）、安装 Vibe Seller，再下载 AI 需要使用的 Chromium（约 250MB，国内网络自动走 npmmirror 镜像，选择较快的一侧）。看到 `[ok] Vibe Seller installed!` 表示安装完成，末尾还会提示你接下来要执行的命令。

然后启动服务：

```bash
vibe-seller start
```

打开 <http://localhost:7777> 即可使用。

命令行会输出三行信息：

```
vibe-seller running on http://0.0.0.0:7777  (PID 12345)
  logs: ~/.vibe-seller/logs/backend_7777.log
  stop: vibe-seller stop --port 7777
```

服务在后台运行，**关闭终端窗口也不会停止**；日志文件和进程号都保存在 `~/.vibe-seller/` 下面。

升级：

```bash
vibe-seller upgrade
```

这条命令会连同它使用的 Claude Code 一起升级。正在运行的服务会自动重启到新版本；如果此时还有任务在执行，服务会先让这些任务完成（期间不再接收新任务，排队中的任务不会丢失，重启后继续排队），再切换到新版本。如果等待一段时间后仍未切换，命令会提示你手动执行 `vibe-seller stop && vibe-seller start`。

卸载：

```bash
uv tool uninstall vibe-seller
```

只移除程序本身，`~/.vibe-seller/` 里的数据、店铺记忆和截图都会保留。

## 进阶：Windows 走 WSL2

建议优先使用上面的原生安装包。WSL2 方案让服务运行在 WSL 的 Ubuntu 里、浏览器运行在 Windows 宿主一侧，适合已经把 Vibe Seller 当作团队服务器来部署的场景。这个方案需要 **Windows 11 + mirrored 网络模式**（24H2 的镜像网络有已知问题，外部设备无法连入，需要升级到 25H2 / build 26200 及以上）。完整部署手册（含网络配置、开机自启、浏览器后端、Tailscale 远程访问）见 [Windows + WSL2 Deployment Guide](windows-setup.md)（英文）。

安装方式和 macOS / Linux 相同，在 WSL 的 Ubuntu 中执行：

```bash
curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
vibe-seller start
```

## 从源码安装（二次开发 / 贡献代码）

```bash
git clone https://github.com/zpoint/vibe-seller
cd vibe-seller
./install.sh --dev   # 系统依赖 + venv + 前端构建 + Chromium
./start.sh           # 启动服务，默认 :7777
```

`--dev` 执行完成后会打印 `All required dependencies are installed!`。开发和调试流程见 [开发者指南](dev-guide.md)。

## 服务管理

需要启动服务、更换端口或查看日志时，对照下表选择命令：

| 想做什么 | 安装版（macOS / Linux） | 源码安装 | Windows 安装包 |
|---|---|---|---|
| 启动 | `vibe-seller start` | `./start.sh` | 开机自动启动，或点开始菜单的 Vibe Seller |
| 更换端口 | `vibe-seller start --port 8080` | `./start.sh 8080` | 设置 `VIBE_SELLER_PORT` 环境变量 |
| 停止 | `vibe-seller stop` | `./stop.sh` | 托盘 → **退出** |
| 重启 | `vibe-seller stop && vibe-seller start` | `./restart.sh` | 托盘 → **重启服务** |
| 前台运行（查看日志） | `vibe-seller start --foreground` | `./start.sh --dev` | — |
| 补装浏览器引擎 | `vibe-seller install-browser` | `./install.sh --dev` 时装好 | 安装包自带 |

浏览器引擎在每次启动时也会自动检查并补装，通常不需要手动处理。

`vibe-seller start` 默认监听 `0.0.0.0:7777`，同一网络内的其他设备本来就可以连进来。

## 局域网访问

同事不需要安装任何东西，在浏览器打开 `http://<你这台电脑的IP>:7777` 就能一起使用。Windows 上，托盘菜单的 **复制局域网地址**（Copy LAN address）会列出 IP 地址和机器名地址并自动复制好；macOS / Linux 上直接使用自己的 IP 即可。

!!! warning "默认不需要登录"
   谁能连上这个端口，谁就以默认管理员身份在操作。内网自用没有问题，**不要把它直接暴露到公网**。需要远程访问时请通过 VPN 或 Tailscale，具体见 [Windows + WSL2 Deployment Guide](windows-setup.md) 的 Tailscale 一节。

## 安装失败了？

把仓库链接 <https://github.com/zpoint/vibe-seller> 交给你手边任意一个编程 Agent（Claude Code、Codex、opencode、Cursor 都可以），说一句“读一下 README 帮我安装”。它会自己查阅文档、执行命令，绝大多数环境问题一两条命令就能修复。

## 让它一直开着

想把 Vibe Seller 部署成 7×24 常驻服务（登录自动启动、崩溃后自动拉起），看这两份部署手册（英文）：

- [macOS Deployment — Auto-Launch on Login](macos-setup.md)：使用系统自带的 `launchd` 配置一个 LaunchAgent
- [Windows + WSL2 Deployment Guide](windows-setup.md)：在 WSL 里运行 systemd 服务，并设置开机自启
