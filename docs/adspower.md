---
description: AdsPower 接入 Vibe Seller 的一页说明：下载安装、API 权限、生成 API Key、绑定时要填什么、一台机器一个账号、自动启动、端口自动发现、内核要求（SunBrowser）、以及凭据存在哪里。
---

# AdsPower：配置指南

[AdsPower](https://www.adspower.net) 是一款防关联浏览器（国内份额前列，本页对应 **AdsPower Global** 国际版客户端）。绑定后，Vibe Seller 通过它自带的本地接口（Local API）打开你的店铺环境执行任务，不需要你手动操作客户端。

**AdsPower 后端支持 Windows、macOS，也支持 WSL**。其他平台上这个选项不会出现在「浏览器后端」下拉里。

不用 AdsPower 的话，这一页可以跳过。Vibe Seller 还支持其他浏览器，见 [浏览器](browsers.md)。

## 下载与安装

1. 打开 <https://www.adspower.net/download>，下载 Windows 客户端（AdsPower Global）并安装。装在哪个目录都可以，Vibe Seller 会自己找到；实在找不到时你可以在设置里手动指定安装位置。
2. 安装后至少手动启动并登录一次，确认账号正常、能看到你的环境列表。

## 付费套餐与 API 权限

AdsPower 的本地接口需要**付费套餐**；团队成员默认没有 API 权限，需要管理员在团队设置里开通（超级管理员默认有）。如果接口报错提示权限问题，找你的团队管理员开通「API」权限即可。

子账号只能看到**授权给它**的环境：环境列表是空的，通常表示「没有授权」，不是坏了。在 AdsPower 的团队管理里给这个子账号分配环境权限。

## 生成 API Key

AdsPower 开启「安全校验」后，所有接口调用都要带 API Key（每个成员一个，互不相同）：

1. 在 AdsPower 客户端左侧菜单打开 **「API & MCP」**。
2. 确认「接口状态」显示**成功**。
3. 点 **「生成 Key」**，复制生成的 Key。

两点要记牢：

- **Key 属于当前登录 AdsPower 的那个账号**。换账号登录后旧 Key 会失效（提示「API Key mismatch」）；在客户端里重置 Key 也会让旧 Key 立刻失效。
- Key 只显示在客户端里，忘记就重置一个。

## 一台机器只支持一个账号

API Key 必须和 AdsPower 客户端里登录的账号对应：一台机器同一时间只有一个 AdsPower 登录，所有 AdsPower 店铺共用这一个 Key。要换账号：先在「设置 → 店铺」里删掉所有 AdsPower 店铺，再重新生成 Key、重新绑定。

## 绑定时要填什么

新建店铺时浏览器后端选「AdsPower」：

| 字段 | 是什么 |
|---|---|
| API Key | 上面生成的那个 Key |

保存后 Vibe Seller 会加载当前登录账号的环境列表（显示「编号 + 名称 + 分组」），在「选择环境...」里挑出这家店对应的那一个，点「创建」。

**一台机器只支持一个 AdsPower 登录**：API Key 必须和客户端里登录的账号对应，所以所有 AdsPower 店铺共用这一个账号的 Key。

## 环境要用 Chrome 内核（重要）

和别的防关联浏览器一样，**Vibe Seller 只能控制 SunBrowser（Chrome 内核）的环境**。AdsPower 还提供 FlowerBrowser（Firefox 内核），这类环境没有 CDP 接口，任务打不开它，会提示切换内核。

- 在 AdsPower 里**新建环境**时，「浏览器」一栏选 **SunBrowser**（另有 FlowerBrowser / Beta / SunBrowser Flash 等选项，都不要选）。
- 已经是 FlowerBrowser（火狐）内核的环境：AdsPower 的接口会拒绝修改已创建环境的内核（返回「The browser kernel cannot be updated」），所以**可靠的解决办法是新建一个「浏览器」选 SunBrowser 的环境，再把店铺账号迁过去**。
- 换了新环境后，**店铺要重新绑定**：新环境有一个新的环境ID，请在 Vibe Seller 的店铺设置里把绑定改成新环境（这一点和[浏览器说明](browsers.md#店铺要用-chrome-内核)里「改内核不用重新绑定」的一般说法不同——那是给内核可以直接改的软件准备的，AdsPower 这里改不了）。

详见 [浏览器 → 店铺要用 Chrome 内核](browsers.md#店铺要用-chrome-内核)。

## 自动启动与登录

- 客户端没开：Vibe Seller 会自动拉起 AdsPower 客户端。客户端记住了登录（Remember me），拉起后接口就可用。
- 客户端开着但**没登录**（停在登录页）：Vibe Seller 会用你保存的 API Key 在后台启动一个无窗口的 AdsPower 服务（官方的 `--headless --api-key` 模式）登录，不会动你开着的客户端窗口。都不行时才提示你手动登录。
- 客户端登录过期：同上，先自动拉起，不行就后台用 Key 启动；还不行会提示「AdsPower 没有登录」。

**说明**：后台启动时，你的 API Key 会出现在 AdsPower 的启动命令行参数里（这是 AdsPower 官方设计的用法，Key 本来就是给它验的）。Key 在 Vibe Seller 里加密保存，除此之外不会出现在日志或接口返回里。

## 接口端口自动发现

AdsPower 本地接口的端口**每次启动都可能变**（默认 50325，被占用时自动换一个），你不需要配置任何端口：Vibe Seller 每次连接前都会读取 AdsPower 自己写的端口文件，自动找到当前接口。

## 提示「还没有这个环境要用的浏览器内核」

任务报「AdsPower 还没有这个环境要用的浏览器内核（如 SunBrowser 156）：后台不会自动下载」时，说明这台电脑的 AdsPower 还没装该内核，而且它在后台**不会自己下载**（在 macOS 上实测过）。处理办法：

1. 打开 AdsPower 客户端窗口，在设置里下载这个内核（客户端里也叫「内核管理」）；
2. 或者把环境改成已经安装的内核版本（新建环境时「浏览器」旁边的版本选已安装的）。

注意：Vibe Seller 不会替你改环境的内核设置——那是你的指纹配置。

## 任务进行中关掉了 AdsPower

不用手动处理。下一次用到浏览器时，Vibe Seller 会自动重新拉起客户端（登录是记住的），再打开店铺环境。

一个细节：客户端被强行关掉（比如任务管理器结束进程）时，已经打开的店铺浏览器**不会跟着关**，而新起的客户端又看不到它——这时再打开同一个环境会失败。Vibe Seller 会识别这种情况，自动清掉那个残留的浏览器进程再重新打开，你不用管。

如果你在 AdsPower 里**手动打开了**某个环境，任务用这个环境时会直接连上它，不会关掉重开。

## 两个浏览器配合使用

和其他防关联浏览器一样，AdsPower 店铺的任务也会配一个 Chrome 辅助浏览器，去打开 AdsPower 打不开的物流、海外网站，原理见 [两个浏览器配合使用](store-and-aux-browser.md)。

## 常见提示 / 报错

| 界面提示 | 含义 / 怎么办 |
|---|---|
| AdsPower 要求 API Key：在 AdsPower 左侧「API & MCP」点生成 Key，复制填到 Vibe Seller。 | 没填 Key。按上面「生成 API Key」操作。 |
| AdsPower 不认这个 API Key：Key 要从 AdsPower 当前登录的账号里复制；换过账号或重置过 Key，都要重新填。 | Key 和当前登录的账号不匹配。重新生成 / 复制 Key。 |
| AdsPower 没有登录，后台启动也没成功：请在 AdsPower 窗口登录后点刷新。 | 客户端停在登录页且后台启动失败。手动登录一次。 |
| AdsPower 里找不到这个环境：可能被删了，或者没有授权给当前登录的账号。 | 环境被删了，或当前账号没有它的授权。 |
| 这个环境用的是 FlowerBrowser（火狐内核）…… | 见上面「环境要用 Chrome 内核」：新建 SunBrowser 环境。 |
| AdsPower 正在下载浏览器内核，下载完再试一次。 | 第一次用某个内核版本，AdsPower 在下载。等一会重试。 |
| AdsPower 的接口用不了：……（原文）。团队成员需要管理员在团队设置里开通 API 权限。 | 接口被拒（原文会带上 AdsPower 的消息）。一般是权限或套餐问题。 |
| 没找到 AdsPower：请先安装并打开 AdsPower；如果装在别的位置，请在设置里选一下它的安装位置。 | 没安装，或装在找不到的位置。 |
| 不支持的平台 | AdsPower 后端只在 Windows（含 WSL-on-Windows）可用。 |

## 凭据存在哪里

API Key 保存在你本地的 `~/.vibe-seller/data/vibe_seller.db`（SQLite），加密存储（Fernet），密钥从本机安装派生；接口不会把 Key 明文返回给前端。因为是机器级的（一个 AdsPower 登录），所有 AdsPower 店铺共用这一个 Key。想看具体加密实现见 [开发者指南](dev-guide.md)。

另外注意：**AdsPower 自己会把 API Key 明文写进它的日志文件**（在本机的 `%APPDATA%\adspower_global` 目录下）。如果这台电脑多人使用，请把该目录当作敏感文件对待；必要时在「API & MCP」里重置 Key。
