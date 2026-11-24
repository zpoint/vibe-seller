---
description: 安装 Vibe Seller：Windows 下载安装包双击安装，macOS / Linux 复制一条命令；升级与卸载，以及 WSL2、源码安装、服务管理、局域网访问等进阶内容。
---

# 安装

选你的系统，跟着做几分钟就能装好。支持 Windows 10 及以上（64 位）、macOS 13 及以上和 Linux。

=== "Windows"

    1. 下载安装包 [VibeSeller-Setup.exe](https://github.com/zpoint/vibe-seller/releases/latest)；
    2. 双击运行，按提示一路点「下一步」；
    3. 最后一步点「完成」，默认浏览器会自动打开 <http://localhost:7777>，这就是 Vibe Seller。

    以后点开始菜单里的 Vibe Seller，也会自动打开这个网址。当 Vibe Seller 程序在后台运行时（任务栏右下角托盘里有它的图标），也可以自己在浏览器访问 <http://localhost:7777>。

    装好后，Vibe Seller 默认在你登录 Windows 时自动启动。想打开或关闭，右键点击右下角托盘里的 Vibe Seller 图标，勾选或取消 **开机自动启动** 即可。

    ??? note "用命令安装（点击展开）"
        在开始菜单搜索 **PowerShell** 打开，粘贴下面这一行后回车，效果和双击安装包一样：

        ```powershell
        irm https://raw.githubusercontent.com/zpoint/vibe-seller/main/installer/windows/install.ps1 | iex
        ```

        安装包约 650MB：运行需要的东西都已经打包在里面，安装时不用再联网下载，网络慢的电脑也能装上。

=== "macOS"

    1. 打开「终端」：按 ⌘ + 空格，输入「终端」后回车；
    2. 复制下面这一行，粘贴到终端里，回车：

        ```bash
        curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
        ```

        等几分钟，看到 `[ok] Vibe Seller installed!` 就装好了。

    3. 再输入下面这一行并回车，启动 Vibe Seller：

        ```bash
        vibe-seller start
        ```

        提示找不到 `vibe-seller` 命令的话，关掉终端、重新打开一个再试。

    4. 在浏览器里打开 <http://localhost:7777>。

    启动后关掉终端窗口也没关系，Vibe Seller 会继续在后台运行。想让 Mac 开机后自动启动，见 [7×24 小时运行](always-on.md)。

    ??? note "命令卡住不动？（点击展开）"
        安装命令会自动使用系统代理下载（`VIBE_SELLER_PROXY=off` 可以关掉）。如果这行命令本身卡住，先执行 `export https_proxy=http://127.0.0.1:7890`（端口以你的代理软件为准），再重试。

=== "Linux"

    1. 打开终端；
    2. 复制下面这一行，粘贴到终端里，回车：

        ```bash
        curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
        ```

        等几分钟，看到 `[ok] Vibe Seller installed!` 就装好了。

    3. 再输入下面这一行并回车，启动 Vibe Seller：

        ```bash
        vibe-seller start
        ```

        提示找不到 `vibe-seller` 命令的话，关掉终端、重新打开一个再试。

    4. 在浏览器里打开 <http://localhost:7777>。

    启动后关掉终端窗口也没关系，Vibe Seller 会继续在后台运行。

    ??? note "命令卡住不动？（点击展开）"
        安装命令会自动使用系统代理下载（`VIBE_SELLER_PROXY=off` 可以关掉）。如果这行命令本身卡住，先执行 `export https_proxy=http://127.0.0.1:7890`（端口以你的代理软件为准），再重试。

装好之后，接着看 [快速开始](quickstart.md)：配置 AI、新建店铺、创建第一个任务。

## 升级

=== "自动更新"

    默认就是自动的。有新版本时，Vibe Seller 会在后台下载好，并在左侧栏提示你：

    - Windows：点「重启并更新」，等几分钟就装好并重新打开；
    - macOS / Linux：没有任务在运行时会自己更新，不用你操作。

=== "手动更新"

    - Windows：右键点击右下角托盘里的 Vibe Seller 图标，点 **检查更新**；
    - macOS / Linux：在终端执行 `vibe-seller upgrade`。

升级不会动你的数据。

## 卸载

- Windows：在「设置 → 应用」里找到 Vibe Seller，点「卸载」。
- macOS / Linux：在终端执行 `uv tool uninstall vibe-seller`。

卸载不会删除你的数据。店铺、截图、记录都保存在用户目录下的 `.vibe-seller` 文件夹里（Windows 上是 `C:\Users\你的用户名\.vibe-seller`），想彻底清除就手动删掉它。

## 安装失败了？

把仓库链接 <https://github.com/zpoint/vibe-seller> 交给你手边任意一个编程助手，说一句“读一下 README 帮我安装”。它会自己查阅文档、执行命令、帮你排查。也可以到 [社区](community.md) 提问。

## 进阶

下面的内容第一次安装用不到，把 Vibe Seller 部署给团队用、或者想自己改代码时再看。

### Windows 走 WSL2

建议优先使用上面的 Windows 安装包。WSL2 方案让服务运行在 WSL 的 Ubuntu 里、浏览器运行在 Windows 宿主一侧，适合已经把 Vibe Seller 当作团队服务器来部署的场景。这个方案需要 **Windows 11 + mirrored 网络模式**（24H2 的镜像网络有已知问题，外部设备无法连入，需要升级到 25H2 / build 26200 及以上）。完整部署手册（含网络配置、开机自启、浏览器后端、Tailscale 远程访问）见 [Windows + WSL2 Deployment Guide](windows-setup.md)（英文）。

安装方式和 macOS / Linux 相同，在 WSL 的 Ubuntu 中执行：

```bash
curl -sSL https://raw.githubusercontent.com/zpoint/vibe-seller/main/install.sh | bash
vibe-seller start
```

### 从源码安装

二次开发、贡献代码时用：

```bash
git clone https://github.com/zpoint/vibe-seller
cd vibe-seller
./install.sh --dev   # 系统依赖 + venv + 前端构建 + Chromium
./start.sh           # 启动服务，默认 :7777
```

`--dev` 执行完成后会打印 `All required dependencies are installed!`。开发和调试流程见 [开发者指南](dev-guide.md)。

### 服务管理

| 想做什么 | 安装版（macOS / Linux） | 源码安装 | Windows 安装包 |
|---|---|---|---|
| 启动 | `vibe-seller start` | `./start.sh` | 开机自动启动，或点开始菜单的 Vibe Seller |
| 更换端口 | `vibe-seller start --port 8080` | `./start.sh 8080` | 设置 `VIBE_SELLER_PORT` 环境变量 |
| 停止 | `vibe-seller stop` | `./stop.sh` | 托盘 → **退出** |
| 重启 | `vibe-seller stop && vibe-seller start` | `./restart.sh` | 托盘 → **重启服务** |
| 前台运行（查看日志） | `vibe-seller start --foreground` | `./start.sh --dev` | — |
| 补装浏览器引擎 | `vibe-seller install-browser` | `./install.sh --dev` 时装好 | 安装包自带 |
| 登录后自动启动 | `vibe-seller autostart on`（仅 macOS，见 [7×24 小时运行](always-on.md)） | 见 [macOS 常驻服务](macos-setup.md) | 托盘「开机自动启动」，默认开启 |

浏览器引擎在每次启动时也会自动检查并补装，通常不需要手动处理。日志在 `~/.vibe-seller/logs/` 下面。

`vibe-seller start` 默认监听 `0.0.0.0:7777`，同一网络内的其他设备本来就可以连进来。

### 局域网访问

同事不需要安装任何东西，在浏览器打开 `http://<你这台电脑的IP>:7777` 就能一起使用。Windows 上，托盘菜单的 **复制局域网地址** 会列出 IP 地址和机器名地址并自动复制好；macOS / Linux 上直接使用自己的 IP 即可。

!!! warning "默认不需要登录"
    谁能连上这个端口，谁就以默认管理员身份在操作。内网自用没有问题，**不要把它直接暴露到公网**。需要远程访问时请通过 VPN 或 Tailscale，具体见 [Windows + WSL2 Deployment Guide](windows-setup.md) 的 Tailscale 一节。
    想让同事先登录再用，在「设置 → 账号」里打开「需要登录」，见[登录密码怎么设置和找回](faq-login.md#登录密码怎么设置和找回)。

### 让它一直开着

想让定时任务半夜也能跑？先按 [7×24 小时运行](always-on.md) 把电脑设成插电不睡眠、断电后自动开机、登录后自动启动 Vibe Seller。

想把 Vibe Seller 部署成 7×24 常驻服务（登录自动启动、崩溃后自动拉起），看这两份部署手册（英文）：

- [macOS Deployment — Auto-Launch on Login](macos-setup.md)：使用系统自带的 `launchd` 配置一个 LaunchAgent
- [Windows + WSL2 Deployment Guide](windows-setup.md)：在 WSL 里运行 systemd 服务，并设置开机自启
