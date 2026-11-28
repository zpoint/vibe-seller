---
description: AdsPower 接入 Vibe Seller 的一页说明：下载安装、API 权限、生成 API Key、绑定、一台机器一个账号、内核要求（SunBrowser）、以及常见问题。
---

# AdsPower：配置指南

[AdsPower](https://www.adspower.net) 是一款防关联浏览器。绑定后，Vibe Seller 通过它自带的本地接口（Local API）打开你的店铺环境执行任务，不需要你手动操作客户端。

**AdsPower 后端支持 Windows、macOS，也支持 WSL**。

不用 AdsPower 的话，这一页可以跳过。Vibe Seller 还支持其他浏览器，见 [浏览器](browsers.md)。

## 下载与安装

1. 打开 <https://www.adspower.net/download>，下载你电脑系统对应的客户端（Windows 或 macOS，AdsPower Global）并安装。装在哪个目录都可以，Vibe Seller 会自己找到；实在找不到时你可以在设置里手动指定安装位置。
2. 安装后至少手动启动并登录一次，确认账号正常、能看到你的环境列表。

AdsPower 客户端没开时，Vibe Seller 会自动打开它；客户端没登录时，任务会提示你先在客户端里登录（见[AdsPower 客户端需要登录](#adspower-客户端需要登录)）。

!!! tip "请保持这台电脑的防火墙开启。"

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

## 绑定时要填什么

新建店铺时浏览器后端选「AdsPower」：

| 字段 | 是什么 |
|---|---|
| API Key | 上面生成的那个 Key |

保存后 Vibe Seller 会加载当前登录账号的环境列表（显示「编号 + 名称 + 分组」），在「选择环境...」里挑出这家店对应的那一个，点「创建」。

## 一台机器只支持一个账号

API Key 必须和 AdsPower 客户端里登录的账号对应：一台机器同一时间只有一个 AdsPower 登录，所有 AdsPower 店铺共用这一个 Key。要换账号：先在「设置 → 店铺」里删掉所有 AdsPower 店铺，再重新生成 Key、重新绑定。

!!! warning "环境要用 SunBrowser（Chrome 内核）"
    Vibe Seller 只能控制 SunBrowser 内核的环境。新建环境时，「浏览器」一栏选 SunBrowser。
    已经是 FlowerBrowser（火狐）内核的环境改不了内核：请新建一个 SunBrowser 环境，把店铺账号迁过去，再到 Vibe Seller 的店铺设置里改绑新环境。
    [浏览器 → 店铺要用 Chrome 内核](browsers.md#店铺要用-chrome-内核)

## 常见提示 / 报错

<details id="adspower-客户端需要登录">
<summary>提示「AdsPower 客户端需要登录」怎么办？</summary>
<a id="sign-in-needed"></a>

说明这台电脑上的 AdsPower 客户端**没有登录**。处理办法：

1. 打开 AdsPower 客户端窗口，用**和 API Key 同一个账号**登录；
2. 如果登录时要求验证码（新设备确认），按提示完成——子账号收不到验证码时见[「子账号收不到验证码（新设备验证）」](#子账号收不到验证码新设备验证)；
3. 登录完成后，回到 Vibe Seller 点「刷新」或重试任务。

</details>

<details id="子账号收不到验证码新设备验证">
<summary>子账号收不到验证码（新设备验证）怎么办？</summary>
<a id="verification-code"></a>

用**账号密码创建的内部成员**（子账号）登录 AdsPower 时，如果换了一台电脑（新设备），AdsPower 会要求输入验证码。这个验证码发给的是**成员自己的**邮箱、手机或身份验证器——但内部成员的邮箱是一个虚拟的 `@***.adspower.com` 地址，**收不到真邮件**，所以成员自己往往拿不到验证码。三种解决办法：

1. **只这一次要登录**：管理员打开 AdsPower 的「成员管理」，找到这个成员那一行，「安全管理」一栏点「身份验证器」，复制当前的「验证码」（或生成一个「临时验证码」），把它输入到登录页的「请输入验证码」框里。
2. **永久解决**：在同一个「身份验证器」对话框里，把「密钥」（或二维码）配到成员自己的验证器 App（如 Google Authenticator）里，以后验证码由成员自己生成。
3. **整个团队关闭双重验证**（影响所有成员）：管理员在「全局设置 → 账户安全 → 双重验证」里选关闭，或选「低」（只在新设备或 90 天后才要求验证）。

只有**人在 AdsPower 窗口里登录**才会遇到验证码——Vibe Seller 不代登录，所以遇到它说明需要有人去客户端里完成这次登录。

官方文档：[成员管理使用常见问题](https://help.adspower.net/docs/cheng-yuan-guan-li-shi-yong-chang-jian-wen-ti) · [双重验证设置](https://help.adspower.net/docs/2faSetting)

</details>

<details>
<summary>提示「AdsPower 还没有这个环境要用的浏览器内核」怎么办？</summary>

这台电脑的 AdsPower 还没装该内核，而且它不会自己下载。打开 AdsPower 客户端，在设置里（「内核管理」）下载该内核，或把环境改成已安装的内核版本。

</details>

<details>
<summary>任务进行中关掉了 AdsPower 怎么办？</summary>

不用处理，下次用到时 Vibe Seller 会自动重新打开。你在 AdsPower 里手动打开的环境，任务会直接用，不会关掉。

</details>

<details>
<summary>凭据存在哪里？</summary>

API Key 保存在你本地的 `~/.vibe-seller/data/vibe_seller.db`（SQLite），加密存储，密钥从本机安装派生；接口不会把 Key 明文返回给前端。因为是机器级的（一个 AdsPower 登录），所有 AdsPower 店铺共用这一个 Key。想看具体加密实现见 [开发者指南](dev-guide.md)。

!!! warning "AdsPower 会把 API Key 写进它的日志"
    AdsPower 自己会把 API Key 明文写进它的日志文件（在本机的 `%APPDATA%\adspower_global` 目录下）。如果这台电脑多人使用，请把该目录当作敏感文件对待；必要时在「API & MCP」里重置 Key。

</details>

| 界面提示 | 含义 / 怎么办 |
|---|---|
| AdsPower 还没有配置 API Key：在 AdsPower 左侧「API & MCP」点生成 Key，复制填到 Vibe Seller。 | 没填 Key。按上面「生成 API Key」操作。 |
| AdsPower 不认这个 API Key：请在 AdsPower 左侧「API & MCP」复制当前登录账号的 Key，重新填到 Vibe Seller；换过账号或重置过 Key 都要重填。 | Key 和当前登录的账号不匹配。重新生成 / 复制 Key。 |
| AdsPower 客户端需要登录：请打开 AdsPower，用和 API Key 同一个账号登录，按提示完成验证（如邮箱/短信验证码），然后点刷新或重试任务。 | 客户端没登录。见上面[「AdsPower 客户端需要登录」](#adspower-客户端需要登录)。 |
| AdsPower 里找不到这个环境：可能被删了，或者没有授权给当前登录的账号。请检查后再试。 | 环境被删了，或当前账号没有它的授权。 |
| 这个环境用的是 FlowerBrowser（火狐内核），Vibe Seller 只能操作 SunBrowser（Chrome 内核）的环境。请在 AdsPower 新建一个「浏览器」选 SunBrowser 的环境，再把这家店铺改绑到新环境。 | 环境用的是火狐内核。新建一个 SunBrowser 环境并把店铺改绑过去（见上面的警告框）。 |
| AdsPower 正在下载浏览器内核，下载完再试一次。 | 第一次用某个内核版本，AdsPower 在下载。等一会重试。 |
| AdsPower 的接口用不了：团队成员需要管理员在团队设置里开通 API 权限。请检查后再点刷新。 | 接口被拒（原文会带上 AdsPower 的消息）。一般是权限或套餐问题。 |
| 没找到 AdsPower：请先安装并打开 AdsPower；如果装在别的位置，请在设置里选一下它的安装位置，然后点刷新。 | 没安装，或装在找不到的位置。 |
| 这个浏览器不支持当前系统。 | AdsPower 后端支持 Windows、macOS，也支持 WSL。 |
