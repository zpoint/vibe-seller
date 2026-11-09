---
description: 任务的结果、报表和告警直接发进群里——群机器人接入：企业微信 / 钉钉 / 飞书 / Slack 的建机器人步骤、安全设置、测试和常见问题。
---

# 群机器人（企业微信 / 钉钉 / 飞书 / Slack）

在 Vibe Seller 里接好群机器人，任务跑完的结论、报表和提醒就能直接发到群里，
不用再手工复制粘贴。企业微信、钉钉、飞书（Lark）和 Slack 都支持。

接好之后，你（或者任务里的 AI 助手）说一句「把这份周报发到运营群」，消息就
进群了。想先确认通没通，随时点一下「测试」。

> 下文的截图在 macOS 上完成，Windows、Linux 的步骤一样。文中的 `~` 指你的
> 用户目录，Windows 上就是 `C:\Users\<你的用户名>`。

## 能做什么

- 把任务结果、告警、报表发进群；企业微信机器人还能发**文件**（PDF、图片、
  表格等，最大 20 MB）。
- 发什么、什么时候发由你决定：手动点「测试」，或者在任务里让 AI 助手发。
  没有任何自动推送——任务完成通知仍然只在 Vibe Seller 网页里。
- 一个机器人对应一个群。要发多个群，就加多个机器人。

四个平台的能力略有差别：

| | 企业微信 | 钉钉 | 飞书 / Lark | Slack |
|---|---|---|---|---|
| 群类型 | 仅内部群 | 普通群、外部群 | 群聊 | 频道 |
| 安全设置 | 无（URL 即凭证） | 关键词 / 加签 / IP 段 | 关键词 / 签名 / IP 白名单 | 无（URL 即凭证） |
| 文本 | 2 KB | 约 20 KB | 20 KB | 40 000 字符 |
| Markdown | 4 KB（精简子集） | 约 20 KB（自动带标题） | 20 KB（卡片形式） | 12 000 字符 |
| 发文件 | ✔ ≤ 20 MB | ✘ | ✘ | ✘ |
| 频率限制 | 20 条/分钟 | 20 条/分钟 | 100 条/分钟 | 以官方为准 |

## 准备什么

1. 一个你有权限往里加机器人的群（各平台对群类型的要求见下）。
2. 群机器人的 **Webhook 地址**——按下面对应平台的步骤拿到。这个地址
   本身就是凭证，不要发到公开的地方。
3. 钉钉、飞书如果开了**加签**或**自定义关键词**，把密钥 / 关键词一起
   记下来，添加机器人时要填。

## 企业微信（WeCom）

### 在企业微信里创建机器人

1. 打开企业微信**电脑端**（手机端也可以），进入要接收消息的群。
   只有**内部群**支持群机器人：含微信联系人的外部群没有添加入口；
   群如果开了「仅群主管理」，普通成员也看不到入口。
2. 点右上角 **···（更多）→ 消息推送 → 添加**，选「新创建一个机器人」
   （老版本的入口叫「添加群机器人」）。
3. 起个名字，点「添加机器人」，然后**复制地址**。地址形如：

   ```
   https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx
   ```

4. （可选）把机器人「发布到企业」后，同事可以在别的群里直接添加它。

「官方说明：[如何设置「消息推送」](https://open.work.weixin.qq.com/help2/pc/14931) ·
[消息推送配置说明](https://developer.work.weixin.qq.com/document/path/91770)」

使用时注意：

- 每个机器人**每分钟最多 20 条**消息；
- 文本不超过 2 KB，Markdown 不超过 4 KB（支持标题、加粗、链接、行内代码、
  引用、三种字体颜色的精简语法，不支持表格）；
- 文件 5 字节 ～ 20 MB，先上传再发送，素材 3 天内有效；
- 群开了「全员禁言」时，机器人消息会发送成功但**不显示**。

拿到地址后，回到 Vibe Seller 按[在 Vibe Seller 里添加](#在-vibe-seller-里添加)继续。

## 钉钉（DingTalk）

### 在钉钉里创建机器人

1. 打开钉钉，进入群聊，点右上角**群设置**（齿轮图标）→ **机器人 →
   添加机器人**，选**自定义机器人**（有的版本入口叫「智能群助手」）。
   普通群和外部群都可以，机器人只发群消息，不发单聊。
2. 起个名字（可选头像），然后完成**安全设置**——钉钉要求至少选一种：
   - **自定义关键词**：最多 10 个，发出的消息必须包含其中至少一个。
     记下其中一个，稍后填进 Vibe Seller 的「关键词」栏——没有包含
     关键词的消息，Vibe Seller 会自动在末尾补上。
   - **加签**：复制 `SEC` 开头的密钥，稍后填进 Vibe Seller 的
     「签名密钥」栏，每条消息的签名由 Vibe Seller 自动带上。
   - **IP 地址（段）**：只允许指定出口 IP 调用。填 Vibe Seller 所在
     机器的公网出口 IP；不想用这种就留空。
3. 勾选《自定义机器人服务及免责条款》，点**完成**，复制 **Webhook 地址**：

   ```
   https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx
   ```

「官方说明：[自定义机器人接入](https://open.dingtalk.com/document/group/custom-robot-access) ·
[自定义机器人安全设置](https://open.dingtalk.com/document/robots/customize-robot-security-settings)」

使用时注意：

- 每个机器人**每分钟最多 20 条**，超过会被限流 10 分钟；
- Markdown 消息会自动用正文首行做标题（会话列表和通知里显示的就是它）；
- 不支持发文件；消息过长可能被拒，建议控制在 20 KB 以内。

拿到地址后，回到 Vibe Seller 按[在 Vibe Seller 里添加](#在-vibe-seller-里添加)继续。

## 飞书（Feishu / Lark）

### 在飞书里创建机器人

1. 进入群聊，点右上角**更多 → 设置 → 群机器人 → 添加机器人**，选
   **自定义机器人**。
2. 设置头像、名称和描述，点**添加**，然后复制 **Webhook 地址**并点
   **完成**：

   ```
   https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
   ```

   自定义机器人只能用在这一个群里，不需要管理员审批。
3. 建议接着在机器人详情页配置**安全设置**（可以多选）：
   - **自定义关键词**：最多 10 个，消息要包含至少一个。把其中一个填进
     Vibe Seller 的「关键词」栏。
   - **签名校验**：复制密钥填进 Vibe Seller 的「签名密钥」栏，签名由
     Vibe Seller 自动计算。
   - **IP 白名单**：只允许指定 IP / 网段调用，最多 10 条。留空表示不用。

「官方说明：[自定义机器人使用指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)」

使用时注意：

- 频率限制**每分钟 100 条、每秒 5 条**；整点、半点系统繁忙，容易触发
  限流（11232），重要消息尽量避开这些时刻；
- 请求体不超过 20 KB；
- Markdown 以**卡片**形式发送；
- 不支持发文件。

拿到地址后，回到 Vibe Seller 按[在 Vibe Seller 里添加](#在-vibe-seller-里添加)继续。

## Slack

### 在 Slack 里创建机器人

1. 打开 [api.slack.com/apps](https://api.slack.com/apps)，**Create New
   App**，选一个 workspace。
2. 在应用设置里打开 **Incoming Webhooks**，把开关打开。
3. 点 **Add New Webhook to Workspace**，选择要发消息的频道，**Authorize**。
4. 回到设置页复制 **Webhook URL**：

   ```
   https://hooks.slack.com/services/T00000000/B00000000/xxxxxxxxxxxx
   ```

「官方说明：[Sending messages using incoming webhooks](https://api.slack.com/messaging/webhooks)」

使用时注意：

- 消息固定发到创建 Webhook 时选的那个频道，事后不能改；
- 发出去的消息不能撤回；URL 本身就是凭证，泄露后 Slack 会撤销它；
- 纯文本不超过 40 000 字符，Markdown（Block Kit）不超过 12 000 字符；
- 不支持发文件；没有签名密钥，「签名密钥」栏不要填。

拿到地址后，回到 Vibe Seller 按下面一节继续。

## 在 Vibe Seller 里添加

1. 打开 Vibe Seller 网页，进入 **设置 → 集成 → 群机器人**，点**添加机器人**：

   ![群机器人卡片：还没有配置机器人](images/chatbots_empty_zh.png)

2. 填**名称**（比如「运营告警」，方便以后一眼认出）；把拿到的 **Webhook
   URL** 整段粘进去。平台会根据 URL 自动识别并显示标签，旁边还有该平台的
   链接：

   ![添加机器人：粘贴企业微信 Webhook URL](images/chatbots_form_wecom_zh.png)

3. **钉钉**和**飞书**会多出两栏，按你在平台上的安全设置填写：
   - **签名密钥（加签 / 签名校验）**：把 `SEC` 开头的密钥（钉钉）或
     签名密钥（飞书）填进来。没开签名就留空。
   - **关键词（自定义关键词）**：填你在平台上设置的关键词之一。没开
     关键词就留空。

   ![添加钉钉机器人：签名密钥和关键词](images/chatbots_form_dingtalk_zh.png)

4. 点**保存**。列表里会出现这个机器人：平台标签、是否已加签、关键词
   一目了然，Webhook 地址默认打码显示：

   ![已添加的机器人列表](images/chatbots_list_zh.png)

5. 点这条机器人上的**测试**。看到绿色提示，群机器人就接好了（示例里
   的机器人用的是本地测试地址，实际使用时这里是各平台的 Webhook
   地址）：

   ![测试成功：机器人行下方的绿色提示](images/chatbots_test_ok_zh.png)

   失败时，机器人行下方会显示平台返回的真实原因。比如图里的
   `token is not exist`（钉钉错误码 300005）表示 access_token 不对
   或机器人已被删除——回群里重新复制地址即可。其他错误的含义见
   [常见问题](#常见问题)：

   ![测试失败：机器人行下方的红色提示](images/chatbots_test_fail_zh.png)

编辑：点**编辑**可以改名称、Webhook、密钥和关键词。完整的 Webhook 地址
只有管理员能显示查看，其他人看到的始终是打码地址。

删除：点**删除**并确认。Vibe Seller 里不再有这个机器人；群里那个机器人
本身还在，不想要了记得去群里把它移除。

## 验证

1. 点**测试**，群里出现 `Vibe Seller: chat bot connection test ✅`；
2. 再跑一次真实任务：建任务时说「跑完把结论发到 XX 群」，或者任务结束后
   发一条跟进消息「把结果发到 XX 群」。任务里的 AI 助手会用群机器人把
   结果发过去（企业微信还可以让它把报表文件一起发）。

## 机器人会发什么

- **只在被要求时发**：你点「测试」、任务里的 AI 助手按你的要求发、
  接入的外部 AI 工具发。Vibe Seller 不会自动往群里推任务通知。
- 任务里的 AI 助手可以发文本和 Markdown（企业微信还可以发文件，任务
  产出和下载的文件都可以，单个不超过 20 MB）。它看不到你的 Webhook
  密钥，密钥只保存在 Vibe Seller 里。
- Markdown 的效果各平台不同：

  | 平台 | Markdown 表现 |
  |---|---|
  | 企业微信 | 精简子集：标题、加粗、链接、行内代码、引用；不支持表格 |
  | 钉钉 | 标题、加粗、链接、图片、列表、引用；通知标题取首行 |
  | 飞书 | 以卡片发送，支持常用 Markdown |
  | Slack | 标准 Markdown（Block Kit） |

## 常见问题

<details markdown="1"><summary><b>测试失败，提示是什么意思？</b></summary>

测试按钮会把平台返回的原文显示出来，括号里是平台的错误码。常见的有：

| 提示（错误码） | 原因 | 怎么办 |
|---|---|---|
| `invalid webhook url`（企业微信 93000） | 地址不对或机器人已删除 | 回群里重新复制地址 |
| `token is not exist`（钉钉 300005） | access_token 不对或机器人已删除 | 回群里重新复制地址 |
| `param invalid: incoming webhook access token invalid`（飞书 19001） | 地址不对或机器人已删除 | 回群里重新复制地址 |
| `no_team`（Slack） | Webhook URL 不对或已被撤销 | 回应用设置重新复制 |
| `Key Words Not Found`（飞书 19024） | 开了关键词校验，消息里没有关键词 | 在「关键词」栏填对关键词，或改用签名校验 |
| `keywords not in content`（钉钉 310000） | 开了关键词校验，消息里没有关键词 | 在「关键词」栏填你在平台上设置的关键词之一 |
| `sign not match` / `invalid timestamp`（钉钉 310000） | 开了加签但密钥不对 | 核对「签名密钥」是否是这个机器人的 SEC… 密钥 |
| `sign match fail or timestamp is not within one hour`（飞书 19021） | 签名校验没过 | 核对密钥；再不行检查 Vibe Seller 所在机器的系统时间 |
| `ip … not in whitelist`（钉钉 310000）/ `Ip Not Allowed`（飞书 19022） | 开了 IP 白名单，出口 IP 不在里面 | 换成关键词 / 加签，或把出口 IP 加进白名单 |

企业微信的报错里还会带出口 IP（`from ip: …`），方便你核对白名单。

</details>

<details markdown="1"><summary><b>提示发送成功，群里却没有消息？</b></summary>

三种常见情况：

1. 企业微信群开了**全员禁言**——机器人消息会发送成功但不显示，请群主
   关掉禁言再试（官方说明里明确写了这一条）；
2. 发错了群：一个 Webhook 只对应一个群（Slack 对应创建时选的频道），
   想发别的群就为那个群再加一个机器人；
3. 确认看的是不是机器人所在的那个群，而不是同名的其他群。

</details>

<details markdown="1"><summary><b>关键词是怎么自动补上的？</b></summary>

钉钉、飞书的安全关键词要求每条消息都包含关键词。你只需要在「关键词」
栏填上平台里设置的关键词**之一**：任务发出的消息如果没有包含它，Vibe
Seller 会自动在末尾补一行，不需要任务里的 AI 助手知道关键词是什么；
消息本来就含关键词时不会重复添加。

</details>

<details markdown="1"><summary><b>飞书开了关键词，Markdown 消息报 Key Words Not Found？</b></summary>

飞书官方说明里写明，关键词校验「只对 text、title 这类文本参数值生效」，
而 Markdown 是以卡片形式发送的，卡片正文不在这些字段里。遇到这个报错，
建议把安全设置换成**签名校验**，或者在卡片正文的标题里也带上关键词。
（这一条我们没有在真实群里复测过，按报错处理即可。）

</details>

<details markdown="1"><summary><b>IP 白名单怎么填？</b></summary>

填 Vibe Seller 所在机器访问公网时的**出口 IP**（不是局域网 IP）。在那台
机器上打开任意一个查询「我的 IP」的网页就能看到。家用宽带的出口 IP 会
定期变化，白名单容易失效——这种情况建议改用**关键词**或**加签**。
钉钉支持网段（`1.1.1.0/24`），飞书支持通配（`123.12.1.*`）。

</details>

<details markdown="1"><summary><b>消息太长发不出去？</b></summary>

各平台的上限见开头的表格。超长的消息，Vibe Seller 会在发送前拦下来并
提示拆分（例如 `WeCom limit is 2048 bytes; message is 3000 — split it
into several messages.`）。让任务里的 AI 助手把长报告拆成几条发，或者
用企业微信发文件。

</details>

<details markdown="1"><summary><b>一个机器人能发多个群吗？</b></summary>

不能。一个 Webhook 固定对应创建它的那个群（Slack 是创建时选的频道）。
要发多个群，就在每个群里各建一个机器人，在 Vibe Seller 里各添加一条。

</details>

<details markdown="1"><summary><b>Webhook 地址泄露了怎么办？</b></summary>

Webhook 地址就是发消息的凭证：拿到地址的人都能往你的群里发消息。处理
办法：到群里**删除并重建机器人**（钉钉、飞书也可以只**重置**密钥），然后
在 Vibe Seller 的「编辑」里换成新地址，或者删掉重建。平时列表里只显示
打码地址，完整地址只有管理员能查看；截图分享前也记得检查画面里没有
完整地址。

</details>

<details markdown="1"><summary><b>钉钉提示被限流？</b></summary>

钉钉每个机器人每分钟最多 20 条，超过会限流 10 分钟（错误码 410100，
`发送速度太快而限流`）。大量告警建议合并成一条 Markdown 摘要再发。

</details>
