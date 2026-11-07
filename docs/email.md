---
description: 邮件接入：在 Vibe Seller 里添加邮箱，让 AI 读信、归类、回信——163 / QQ / Gmail 的授权码与应用专用密码怎么拿，一步步配好并验证。
---

# 邮件接入

把客服邮箱接进 Vibe Seller 之后，AI 在跑任务时就能直接查收件箱、按你的
要求归类整理，还能起草并发出回复邮件，不用再来回复制粘贴。

收发都走标准的 IMAP / SMTP 协议。**邮箱密码不用填**——163、QQ 邮箱要填
的是「授权码」，Gmail 要填的是「应用专用密码」，下面按邮箱分别说明怎么拿。

> 下文的截图在 macOS 上完成，Windows、Linux 上的界面和步骤一样。

## 接入前准备

- 一个你常用的邮箱（163 / 126 / yeah.net、QQ 邮箱、Gmail 都支持）；
- 该邮箱的**授权码**（或 Gmail 的应用专用密码）——各邮箱一节有图文步骤；
- Outlook / Hotmail 邮箱**暂不支持**，原因见
  [常见问题](#faq)。

## 网易邮箱（163 / 126 / yeah.net）

163、126、yeah.net 三个后缀的步骤一样，服务器会自动识别，只在
「设置 → POP/SMTP/IMAP」入口操作。

### 第一步：获取客户端授权码

1. 登录[网页版邮箱](https://email.163.com/)（126 登录
   [126.com](https://email.126.com/），yeah.net 同理），点击上方
   **设置 → POP/SMTP/IMAP**；
2. 在客户端协议界面，把 **IMAP/SMTP 服务**开启（推荐开 IMAP，
   它和网页版完全同步）；
3. 弹窗点「继续开启」，按提示用手机扫码或手动发送短信完成验证；
4. 验证通过后页面会显示一串 **16 位客户端授权码**——
   **只显示这一次**，请立刻复制保存。同一个授权码可以给多个客户端用，
   以后要加新客户端，点「新增授权密码」再生成一个即可。

完整图文步骤见网易官方帮助「如何开启客户端协议？」——
[官方说明](https://help.mail.163.com/faqDetail.do?code=d7a5dc8471cd0c0e8b4b8f4f8e49998b374173cfe9171305fa1ce630d7f67ac2a5feb28b66796d3b)。

### 第二步：在 Vibe Seller 里添加

打开 Vibe Seller，进入 **设置 → 邮箱**：

![「设置 → 邮箱」页面](images/email_tab_zh.png)

点 **+ 添加邮箱**：

1. 「邮箱」填完整地址，如 `yourname@163.com`——光标一离开输入框，
   IMAP / SMTP 服务器就会**自动识别**填好（`imap.163.com`、`smtp.163.com`）；
2. 「密码/授权码」粘贴上一步拿到的 16 位授权码；
3. 点 **测试连接**。看到「连接成功」再点 **保存**。

![添加邮箱：服务器自动识别](images/email_addform_zh.png)

授权码填错或服务没开时，红框里会直接显示服务商的真实报错，
对照修正后再测：

![测试连接失败时显示真实报错](images/email_testfail_zh.png)

保存后邮箱出现在列表里，点 **关联到店铺** 把它挂到店铺上——
只有关联之后，该店铺的任务里 AI 才读得到这个邮箱的邮件。

![保存后的邮箱卡片，可关联店铺](images/email_account_zh.png)
![关联店铺后的邮箱卡片](images/email_linked_zh.png)

## QQ 邮箱

### 第一步：开启服务并获取授权码

1. 登录[QQ 邮箱网页版](https://mail.qq.com/)，点顶部 **设置 → 账号与安全**；
2. 找到「POP3/IMAP/SMTP/Exchange/CardDAV 服务」区域，把 **IMAP/SMTP
   服务**开启；
3. 按提示用绑定的手机号发送短信验证，点「我已发送」；
4. 验证通过后弹出一串 **16 位授权码**——这就是以后登录第三方客户端的
   专属密码，**不是 QQ 密码**，请复制保存。

授权码忘了或泄露了，在 **设置 → 账号与安全 → 设备管理 → 授权码管理**
里重置，旧的立即失效。（[官方说明](https://help.mail.qq.com/detail/0/1087)）

!!! note "企业邮箱（exmail）"
    腾讯企业邮箱的地址是 `you@yourcompany.com`，服务器不同
    （`imap.exmail.qq.com` / `smtp.exmail.qq.com`），需要管理员先在
    管理后台开放 IMAP/SMTP 服务，再在网页版 **设置 → 邮箱关联** 里生成
    客户端授权码。添加时把自动识别的服务器改成上面两个地址即可。
    参见[腾讯官方说明](https://open.work.weixin.qq.com/help2/pc/19886)。

### 第二步：在 Vibe Seller 里添加

和网易一样：**设置 → 邮箱 → + 添加邮箱**，地址填
`yourname@qq.com`，密码栏粘贴 16 位授权码，**测试连接**通过后保存、
关联店铺。QQ 邮箱的服务器也会自动识别（`imap.qq.com`、`smtp.qq.com`）。

## Gmail

Gmail 用的不是登录密码，而是**应用专用密码**（App Password），
前提是先开启两步验证。

### 第一步：开启两步验证

1. 打开 [Google 账号安全设置](https://myaccount.google.com/security)；
2. 在「您登录 Google 的方式」下点 **两步验证**，按向导开启
   （[官方说明](https://support.google.com/mail/answer/185839)）。

### 第二步：生成应用专用密码

1. 开启两步验证后，打开
   [应用专用密码](https://myaccount.google.com/apppasswords) 页面；
2. 应用选「其他（自定义名称）」，起个好认的名字（如 `vibe-seller`），
   点 **生成**；
3. Google 会显示一个 **16 位密码**（分四组显示）——复制它。
   填进 Vibe Seller 时**忽略空格**，连成一串输入。

（[官方说明](https://support.google.com/mail/answer/185833)）

!!! note "Google Workspace（公司/学校）账号"
    个人 @gmail.com 账号的 IMAP 从 2025 年 1 月起一直开启，无需设置。
    但公司或学校管理的 Google Workspace 账号，管理员可能**关闭了 IMAP
    或应用专用密码**——如果找不到「应用专用密码」入口或
    「转发和 POP/IMAP」标签，请联系管理员开启。

### 第三步：在 Vibe Seller 里添加

**设置 → 邮箱 → + 添加邮箱**，地址填 `yourname@gmail.com`，
密码栏粘贴 16 位应用专用密码（去掉空格），**测试连接**通过后保存、
关联店铺。服务器自动识别为 `imap.gmail.com`、`smtp.gmail.com`。

## Outlook / Hotmail：暂不支持 {#outlook-hotmail}

**Outlook.com、Hotmail、Live 邮箱目前无法接入。** 微软从 2024 年 9 月
起全面停用了密码登录（Basic Authentication），这类邮箱必须走
OAuth2 授权，而 Vibe Seller 的接入方式是授权码/密码。用 Outlook
邮箱点「测试连接」会看到明确的报错
（`Basic authentication is disabled.`），这不是配置问题。
微软官方说明见
[公告](https://techcommunity.microsoft.com/blog/outlook/keeping-our-outlook-personal-email-users-safe-reinforcing-our-commitment-to-secu/4164184)。
有需要可以先把 Outlook 邮件转发到一个支持的邮箱再接入。

## 验证

按顺序确认三件事：

1. **测试连接**显示「连接成功」——这一步验证的是收信（IMAP）；
2. **保存**后邮箱出现在列表里，且已 **关联到店铺**；
3. 建一个任务问 AI：「看看这个邮箱最近有什么邮件」，它能报出
   最近的邮件主题和发件人，就说明接入成功了。

## 接入后 AI 能做什么

- **读邮件**：查收件箱、按主题/发件人/时间检索、读正文和附件清单。
  邮件每 5 分钟自动同步到本地，任务里也可以让它立刻同步一次；
- **归类整理**：按你的规则把邮件分组、提取要点、汇总成表格或清单；
- **发邮件**：起草回复并按你的要求发送——发送前把内容念给你确认的
  习惯也在（在任务里交代一句「发前给我看」即可）。

邮件只在你自己的部署里收发和存储：邮箱密码加密保存，邮件同步到
本机的数据库里，任务里的 AI 通过受控的工具读写，不会把邮件内容
发到别处。

## 常见问题 {#faq}

<details markdown="1"><summary><b>「测试连接」失败，提示 "LOGIN Login error or password error"？</b></summary>

这是邮箱服务器拒绝登录，通常是这两种情况：

- **授权码填错了**（或填成了登录密码）。163 / QQ 邮箱密码栏必须填
  16 位授权码，不是登录密码；
- **IMAP/SMTP 服务没开**。回邮箱网页版的设置里确认服务是开启状态。

修正好再点「测试连接」，具体原因会直接显示在红框里。
</details>

<details markdown="1"><summary><b>提示「这个邮箱已经添加过了」？</b></summary>

回列表里找到这个邮箱即可；想换授权码，先删除旧的再重新添加。
</details>

<details markdown="1"><summary><b>QQ 邮箱只能收到最近 30 天的邮件？</b></summary>

QQ 邮箱默认只给第三方客户端最近 30 天的邮件。在邮箱网页版
**设置 → 账号与安全** 的服务区域把「收取选项」从「最近 30 天」
改成「全部」即可。
</details>

<details markdown="1"><summary><b>添加时没有自动识别服务器怎么办？</b></summary>

自动识别覆盖了常见邮箱（163 / 126 / yeah.net、QQ、Gmail 等）。
没识别出来的域名会按 `imap.你的域名` 猜一个，多半不对——
请按邮箱服务商提供的服务器地址手动填写。IMAP 一般是 993 端口
（SSL），SMTP 一般是 465（SSL）或 587。
</details>

<details markdown="1"><summary><b>为什么 AI 说没找到新邮件？</b></summary>

邮件每 5 分钟自动同步一次；刚收到的邮件可以稍等，或让任务里
「立刻同步一次」再查。另外确认邮箱已经**关联到对应店铺**——
没关联的邮箱，店铺任务里的 AI 读不到。
</details>

<details markdown="1"><summary><b>怎么更换授权码 / 移除邮箱？</b></summary>

授权码换了以后：先在邮箱列表里 **删除** 旧账户，再用新授权码
重新添加一遍。删除会同时解除和店铺的关联，不会动邮箱里的邮件。
</details>

<details markdown="1"><summary><b>Outlook / Hotmail 能用吗？</b></summary>

目前不支持，详见上面的
[Outlook / Hotmail 一节](#outlook-hotmail)。
微软已停用密码登录，接入需要 OAuth2 支持。
</details>
