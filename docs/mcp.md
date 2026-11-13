---
description: 直接在你常用的 AI 工具里指挥 Vibe Seller——MCP 接入：创建一个 API Key，选择 Codex、WorkBuddy、Qoder、Hermes Agent、Pi 或 Claude 的接入页面，以及常见问题。
---

# MCP 接入

MCP 接入是让**外部的 AI 工具**——Codex、Claude、WorkBuddy、Qoder、Hermes Agent、
Pi——来调用 Vibe Seller。你在 Codex 的对话框里说话，Codex 通过 MCP 调用 Vibe Seller
提供的工具：创建任务、查看进度、回答任务的提问、取回结果和文件。真正进店铺后台干活
的，仍然是 Vibe Seller 里的任务；Codex 负责替你下达、跟进和整理。

```mermaid
sequenceDiagram
    participant U as 你
    participant C as Codex（外部 AI 工具）
    participant V as Vibe Seller
    participant S as 店铺后台
    U->>C: 用 Vibe Seller 看看店铺A上周的广告
    C->>V: MCP 调用：创建任务
    V->>S: 任务用店铺A的浏览器读取广告数据
    V-->>C: 任务的提问（例如要不要包含 AE 站）
    C-->>U: 转达提问
    U->>C: 在对话里回答
    C->>V: MCP 调用：回答提问
    V-->>C: 任务完成：结论和文件
    C-->>U: 整理好的结论
```

下面以 Codex 为例，其他工具用法相同。

!!! note "这一页里的话，都是对 Codex 说的"
    下面的示例句都打在 **Codex 的对话框**里，由 Codex 决定调用哪个 Vibe Seller
    工具；它们不是写进 Vibe Seller 任务描述的内容。Codex 建任务时，会自己把要求
    写成任务描述。

任何支持 MCP 的 AI 工具都能接入，只需要准备一个 API Key。
各集成分别配在哪里、彼此怎么分工，见[集成总览](integrations.md)。

## 可以这样用

接好之后，在 Codex 里像给同事交代工作一样直接说。句子里提到 Vibe Seller，
Codex 更容易想到去调用它：

| 你在 Codex 里说 | Codex 在 Vibe Seller 里做的事 |
|---|---|
| “Vibe Seller 里现在有几个店铺？” | 查店铺列表，直接回答，不建任务 |
| “用 Vibe Seller 看看店铺A过去一周的广告花费，给点优化建议，先别改任何东西。” | 在店铺A下创建任务，等它跑完，把结论带回对话 |
| “刚才那个任务到哪一步了？” | 读取任务进度，转述给你 |
| “把这个巡检在 Vibe Seller 里设成每周一早上执行，写完计划先给我看。” | 创建定时任务，把计划拿回对话给你审 |

不用记任何命令，Codex 会自己判断调用哪个工具。任务中途要问你问题、要你确认计划，
问题会出现在 Codex 的对话里，在那里回答就行，不需要切回 Vibe Seller 网页。
一个完整的例子见[用 Codex 导出紫鸟店铺财务数据进行分析](example-mcp.md)。

## 能做什么、不能做什么

- **能做**：创建任务（可以附带文件）、查看进度、回答任务的提问、通过或
  打回计划、确认生成的图片、给任务补充要求、停止、重试或删除任务、查看
  任务产出的文件、管理知识库、技能和定时任务。
- **不能做**：直接操作浏览器（点击、打开网页、截图），也不能直接修改
  你的店铺。

店铺上的操作都在 Vibe Seller 的任务里完成，计划审批、广告改动范围检查
这些把关，和你在网页上操作时完全一样。这样既保证各店铺的数据互相隔离，
也降低让 AI 直接动手的风险。

从 AI 工具创建的广告任务，和在网页上创建的一样守规矩：开工前先说明要
查哪些、改哪些，漏查的站点会被拦下来补齐；只要求报告的任务，不会改动
你的广告。

## 第一步：创建 API Key

打开 Vibe Seller 网页，进入「设置 → 集成 → API Key」，点击「新建 API Key」：

1. 起一个好认的名字，例如 `claude-desktop`，以后一眼就知道是给谁用的；
2. 选择有效期：30 天、90 天、365 天或永不过期；
3. 创建后立即**复制 Key**。它只显示这一次，关掉弹窗就再也看不到了。

![在「设置 → 集成」里新建 API Key](images/mcp_apikey_create_zh.png)

同一个弹窗还按工具给出可以直接复制的配置：能在应用里配好的放在
「界面操作」标签，需要命令行或配置文件的放在「命令行 / 配置文件」标签，
和各接入页面一一对应。先把「服务器地址」改成 AI 工具能访问到的地址，
下面的配置会跟着自动更新：

![创建成功的弹窗：Key 和各工具的配置](images/mcp_apikey_created_zh.png)

使用时请注意：

- Key 以 `vst_` 开头，**持有它的工具能以你的身份、按你的权限操作
  Vibe Seller**；
- 不再使用或担心泄露时，随时可以在同一个地方撤销；
- 即使你的 Vibe Seller 没有开启登录验证，也建议照常创建一个。以后开启
  验证时，已接入的工具不会突然连不上。

## 选择你的 AI 工具

创建好 API Key 后，打开你所用工具的接入页面：

| 工具 | 接入方式 |
|---|---|
| [Codex](mcp-codex.md) | 界面填表 |
| [WorkBuddy](mcp-workbuddy.md) | 粘贴 JSON |
| [Qoder](mcp-qoder.md) | 粘贴 JSON |
| [Hermes Agent](mcp-hermes.md) | 粘贴 JSON |
| [Pi](mcp-pi.md) | 命令行 |
| [Claude](mcp-claude.md) | 命令行 |

使用其他支持 MCP 的工具，或想看技术细节（协议、全部 32 个工具、curl
示例），请阅读[连接外部 Agent](agents.md)。

## 常见问题

**提示 401 Unauthorized**

Key 填写错误、已撤销或过期，也可能是这个 Key 所属的账号被停用了。到
「设置 → 集成 → API Key」查看 Key 的状态。撤销或过期的 Key 一律无法
使用，即使服务器没有开启登录验证也一样——撤销了就是真的作废了。

**提示 405**

说明地址不对，或者你的 Vibe Seller 版本太旧、还没有 MCP 接口。确认地址
以 `/api/mcp` 结尾，必要时升级 Vibe Seller。

**连不上，或者报证书（TLS）错误**

先检查地址和端口，再确认 AI 工具所在的电脑能访问到 Vibe Seller。如果
Vibe Seller 只监听 `127.0.0.1`，就只有本机能连；要从其他电脑使用，建议
在前面加一层 HTTPS 反向代理。

**能看到工具，但一调用就报错**

通常是地址写错了。各接入页面的配置里，地址都要写到 `/api/mcp` 为止；
只有 Claude **插件**例外，它只需要服务器根地址（例如 `http://localhost:7777`），
会自己补上 `/api/mcp`。另外检查一下结尾有没有多余的斜杠。

**AI 回答了，但没有真的创建任务**

说明它根据自己的知识直接回答了，没有让 Vibe Seller 去执行。安装 Claude
插件能明显改善，插件会要求它只汇报任务真实执行出的结果。也可以在对话
里明确说“用 vibe-seller 建个任务查一下”。
