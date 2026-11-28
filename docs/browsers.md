---
description: Vibe Seller 支持哪些浏览器、怎么选。「店铺浏览器 + 辅助浏览器」的设计，以及紫鸟、AdsPower、悦互联、稳卖、比特浏览器的开通与配置，各有单独一页。
---

# 浏览器

Vibe Seller 通过 CDP（Chrome 底层远程控制协议）驱动**真实浏览器**，AI 在页面上的操作和你本人一样。每个店铺绑定一个浏览器后端；防关联店铺（紫鸟 / AdsPower / 悦互联 / 稳卖 / 比特浏览器）还会自动再配一个 Chrome 辅助浏览器。本页说明支持哪些浏览器、怎么选；两个浏览器怎么分工见 [店铺浏览器与辅助浏览器](store-and-aux-browser.md)，每种浏览器的下载与配置见各自的指南。

## 支持哪些浏览器

目前支持六种浏览器后端。下载、注册团队账号都走各家官网：

| 后端 | 是什么 | 适合 | 平台 | 官网 / 下载 | 创建团队账号 | 配置指南 |
|---|---|---|---|---|---|---|
| Chrome | 系统自带 Chrome / Chromium（Playwright 拉起） | 单店自用、公开网页任务 | macOS / Linux / WSLg | 系统自带，无需安装 | — | [店铺与浏览器 → 新建店铺](stores.md#新建店铺) |
| 紫鸟 Ziniao | 第三方防关联浏览器（开发者模式 / WebDriver，CDP 驱动） | 一个团队管多账号、平台风控严格 | Windows / macOS / Linux，也支持 WSL | [ziniao.com](https://www.ziniao.com) · [下载](https://www.ziniao.com/download) | 官网注册企业账号 → 主账号在[开放平台](https://open.ziniao.com/docSupport?docId=99)开通开发者模式、给子账号勾选 WebDriver 登录 | [紫鸟配置指南](ziniao.md) |
| AdsPower | 防关联浏览器（API Key 接入） | 一个团队管多账号 | Windows、macOS，也支持 WSL | [adspower.net](https://www.adspower.net/download) | 付费套餐 + 生成 API Key（API & MCP 页），环境要用 SunBrowser 内核 | [AdsPower配置指南](adspower.md) |
| 悦互联 YueHuLian | 国产电商防关联浏览器 | 一个团队管多账号 | Windows / macOS / WSL | [kj.chinafic.com](https://kj.chinafic.com/) · 官网「客户端下载」 | 官网注册企业账号 → 控制台开子账号、分配店铺权限（团队登录 = 团队名称 + 子账号 + 密码） | [悦互联配置指南](yuehulian.md) |
| 稳卖 WenMai | 电商防关联浏览器 | 一个团队管多账号 | Windows / macOS，也支持 WSL | [wenmai-ai.com](https://www.wenmai-ai.com) | 客户端里开企业子账号、授权店铺（企业登录 = 企业名称 + 用户名 + 密码），店铺要绑定设备 | [稳卖配置指南](wenmai.md) |
| 比特浏览器 BitBrowser | 国产防关联浏览器 | 一个团队管多账号，免费版可用 | Windows、macOS，也支持 WSL | [bitbrowser.net](https://www.bitbrowser.net/) · [下载](https://www.bitbrowser.net/download/) | 官网注册账号（免费版 10 个窗口）；子账号需主账号授权窗口 | [比特浏览器配置指南](bitbrowser.md) |

## 店铺要用 Chrome 内核

紫鸟、悦互联、稳卖这类防关联浏览器，大多可以给每个店铺单独选浏览器内核。**Vibe Seller 只能控制 Chrome（Chromium）内核的店铺**，Firefox 内核和其他非 Chrome 内核的店铺都不支持：任务打不开这类店铺，会提示「这个店铺用的不是 Chrome 内核」。

绑定店铺前，请在防关联浏览器里打开这个店铺的设置，把浏览器内核选成 Chrome（有的软件写作 Chromium 或「Chrome 内核」）。已经绑定的店铺改完内核后不用重新绑定，再运行一次任务即可。

## 选哪个浏览器后端

| | 紫鸟（浏览器后端） | AdsPower（浏览器后端） | 悦互联（浏览器后端） | 稳卖（浏览器后端） | 比特浏览器（浏览器后端） | Chrome |
|---|---|---|---|---|---|---|
| 是什么 | 第三方防关联浏览器 | 防关联浏览器（API Key 接入） | 国产电商防关联浏览器 | 电商防关联浏览器（自带本地 API） | 国产防关联浏览器，免费版可用 | 系统自带的 Chrome / Chromium |
| 适合 | 一个团队管多个账号 | 一个团队管多个账号 | 一个团队管多个账号 | 一个团队管多个账号 | 一个团队管多个账号 | 单店自用、公开网页任务 |
| 需要准备 | 紫鸟企业账号并开通开发者模式 | 付费套餐和 API Key，环境使用 SunBrowser 内核 | 悦互联团队账号（团队 + 子账号 + 密码） | 稳卖企业账号（企业 + 用户名 + 密码），店铺要绑定设备 | 比特浏览器账号；子账号需主账号授权窗口 | 无额外要求 |
| 登录状态 | 存在紫鸟自己的档案里 | 存在 AdsPower 客户端里（API Key 要对应登录账号） | 存在悦互联客户端里（Vibe Seller 自动登录） | 存在稳卖客户端里（Vibe Seller 自动登录） | 存在比特浏览器客户端里（Vibe Seller 自动登录） | 存在店铺专属的 Chrome 档案目录 |
| 多店铺并发 | 一台机器同时只能登一个紫鸟账号 | 一台机器只支持一个 AdsPower 账号 | 一台机器只支持一个悦互联账号（更严：第二个账号都加不进来） | 一台机器只支持一个稳卖账号 | 一台机器只支持一个比特浏览器账号 | 没有这个限制 |
| 支持平台 | Windows / macOS / Linux，也支持 WSL | Windows / macOS，也支持 WSL | Windows / macOS / WSL | Windows / macOS / WSL | Windows / macOS，也支持 WSL | macOS / Linux / WSLg |

- **Chrome**：单店自用或公开网页任务，不想安装额外软件时选它。
- **紫鸟**：团队管理多个账号、平台风控严格，且已开通开发者模式时选它。
- **AdsPower**：团队管理多个账号，已有付费套餐并能使用 API Key 时选它。
- **悦互联**：团队管理多个账号，希望 Vibe Seller 自动打开并登录客户端时选它。
- **稳卖**：团队管理多个账号，且每个店铺都能绑定设备时选它。
- **比特浏览器**：团队管理多个账号，想先用免费版时选它。

## 任务打不开店铺：报错速查

任务开始时会先打开店铺所在的浏览器，打不开时任务会直接失败，页面上会显示一条红色横幅说明原因（AI 也会收到同样的提示并被停下，不会自己乱试）。按横幅上写的话对号入座：

<details markdown="1" id="error-login-required"><summary><b>「浏览器客户端处于未登录状态」</b></summary>

店铺所在的浏览器客户端（紫鸟、悦互联这类软件）被退出登录了，而且没有保存过的登录信息可以自动登回。在本机打开这个客户端，手动登录一次，然后重试任务即可。

</details>

<details markdown="1" id="error-client-not-running"><summary><b>「浏览器客户端没有在运行」</b></summary>

店铺所在的浏览器客户端没开，或者刚好还在启动中。打开它再重试任务；如果反复出现，重启一次客户端。

</details>

<details markdown="1" id="error-account-mismatch"><summary><b>「客户端登录的账号和保存的不一致」</b></summary>

客户端现在登录的账号，和当初绑定店铺时保存的账号不是同一个（比如同事换过账号）。在客户端里退出当前登录，登回当初绑定的那个账号；或者删掉这个店铺重新绑定现在的账号。

</details>

<details markdown="1" id="error-api-not-enabled"><summary><b>「客户端的本地接口没有开启」</b></summary>

这类客户端自带一个给自动化用的「本地接口」，默认多半是关着的。在客户端的设置里找到 API / 自动化 / local API 相关的开关打开，然后重试任务。

</details>

<details markdown="1" id="error-plan-required"><summary><b>「客户端的本地接口需要付费套餐」</b></summary>

这家浏览器的本地接口是付费功能，当前账号的套餐不含它。到官网给账号升级（或换一个含接口权限的套餐），然后重试任务。

</details>

<details markdown="1" id="error-store-unavailable"><summary><b>「客户端打开了这个店铺，但提示无法使用」</b></summary>

店铺在客户端里存在，但打不开——比如没有绑定设备、店铺过期或被停用。横幅和报错文字里会写客户端的原话；到客户端里找到这个店铺，按它的提示处理后重试。

</details>

<details markdown="1" id="error-store-not-found"><summary><b>「客户端里找不到这个店铺」</b></summary>

绑定时的店铺在客户端里已经不存在了（被删掉，或者当前登录的账号看不到它）。确认店铺还在客户端里、当前账号能看到它；不在的话，删掉 Vibe Seller 里的这个店铺重新绑定。

</details>

## 各浏览器配置指南

- [紫鸟：开通与常见报错](ziniao.md) —— 开通开发者模式、绑定、报错、WSL 启动器、凭据存储。
- [AdsPower：配置指南](adspower.md) —— 下载、生成 API Key、绑定、环境要用 SunBrowser 内核、自动启动。
- [悦互联：配置指南](yuehulian.md) —— 下载、创建团队账号、绑定、一台机器一个账号、自动登录。
- [稳卖：配置指南](wenmai.md) —— 下载、创建企业账号并授权店铺、绑定设备、绑定、一台机器一个账号、自动登录。
- [比特浏览器：配置指南](bitbrowser.md) —— 下载、登录一次即可、绑定、Windows 与 macOS。

- Chrome —— 无需额外配置，新建店铺时浏览器后端选「Chrome」即可，见 [店铺与浏览器 → 新建店铺](stores.md#新建店铺)。
