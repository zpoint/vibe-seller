---
description: 在你已经在用的 AI 工具里直接指挥 Vibe Seller——MCP 接入：建 API Key、Claude / Codex / WorkBuddy / Qoder 桌面端接入、验证和试用。
---

# MCP 接入

Vibe Seller 可以作为 MCP 服务暴露给你**已经在用的 AI 工具**：Claude、Codex、
WorkBuddy、Qoder，任何支持 MCP 的工具都行。给它一个地址和一把 API Key，这个
工具就能替你创建任务、跟进进度、回答任务的提问、审批计划——就像你在网页上
点按钮一样，只不过换成了对话。

外部 Agent 全程**通过任务干活**，它不直接操作浏览器，所以服务端的所有闸门
（计划审批、广告范围声明、完整性审查）都照常生效。开发者视角的完整参考
（协议、32 个工具、curl 示例）见 [Connect an External Agent](agents.md)。

> 命令行用户（Claude Code CLI、Codex CLI 等）见 [Connect an
> External Agent](agents.md)，本文只讲桌面端。

## 能问什么

接入后，你可以在对话里直接说人话：

- 「现在 vibe-seller 里有几个店铺？」
- 「帮我看看 acme 过去一周的广告花费，给点优化建议，先别改任何东西。」
- 「这个任务现在在干什么，到哪一步了？」
- 「把这周的巡检安排成每周一早上跑，写完计划先给我看。」

工具会自己去调 `list_stores`、`create_task`、`get_task` 这些接口，
你不用记任何工具名。

## 能做什么、不能做什么

| | |
|---|---|
| ✅ 能 | 创建任务（可带附件）、跟进任务、回答任务的提问、审批 / 打回计划、处理图像确认卡、发跟进消息、停止 / 重试 / 删除任务、读任务产出的文件、读写工作区知识、管理技能和排程 |
| ❌ 不能 | 直接操作浏览器（点击、导航、截图）、直接改你的店铺——店铺层面的操作只发生在 Vibe Seller 内部的任务里，受服务端闸门约束 |

任务在执行中的广告改动范围由任务自己声明，声明之后的完整性审查照常拦截
漏掉的站点；报告型任务不给改动建议之外的指令。这些规则不因为外部 Agent
接入而放松。

## 第一步：拿一个 API Key

网页端 **设置 → 集成 → API Key** 卡片，创建一个。起个你以后认得出的名字（比如 `claude-desktop`），选一个有效期（30 / 90 /
365 天或永不过期），创建后**复制这个 Key**——它只显示这一次，服务端只存
哈希。

- Key 以 `vst_` 开头，**当密码对待**：它以你的身份、按你的权限干活；
- 用完或者怀疑泄露，就在同一张卡片里撤销；
- 就算服务端关了登录验证，Key 一样生效、一样可撤销——照样建一个，
  免得以后打开验证把工具锁在门外。

## Claude 桌面版（Claude Code）

### 安装

到 [claude.com/download](https://claude.com/download) 下载 macOS 版安装即可。

### 接入

建完 API Key 的弹窗里直接给了 Claude Code 的可粘贴命令，在终端跑一次即可
（桌面版的 **Code 标签页就是 Claude Code**，共用同一份配置，所以跑完
桌面版里就有）：

```bash
claude mcp add --transport http vibe-seller http://HOST:7777/api/mcp \
  --header "Authorization: Bearer vst_…"
```

> 建议补一个 `--scope user`：不加的话这条命令只在**当前目录**生效，
> Code 标签页换个文件夹就看不到服务器了。

弹窗里的服务器地址可以改（预填的是网页所在的地址）；工具跑在别的机器上时，
把它改成那台机器能访问到的地址。

更进一步可以装 `vibe-seller` **插件**（`claude plugin marketplace add` +
`claude plugin install`，见 [agents.md](agents.md)）：插件额外带一个技能，
教会 Agent 怎么用这些工具（什么时候用计划模式、怎么跟进任务、怎么答题、
怎么审计划、为什么绝不能编造结果）。

> 服务器**公网可达**时，聊天页也可以在 **设置 → 连接器（Connectors）→
> 添加自定义连接器**里填服务器地址 + 请求头 `Authorization: Bearer vst_…`。
> 注意这条路由 Claude 的云端代发请求，`localhost` / 内网地址的服务器连不上
> ——本地部署请走上面的命令。

### 验证

新建一个 Code 会话，问一句「vibe-seller 里有几个店铺」，或者直接看插件的
工具列表——能看到 `list_stores`、`create_task` 等工具就通了。

### 试一试

在会话里依次试这几句：

1. 「现在 vibe-seller 里有几个店铺？」
2. 「帮我看看 acme 过去一周的广告花费，给点优化建议，按常见标准来就行，
   不用问我，先别改任何东西。」
3. 「这个任务现在在干什么，到哪一步了？」
4. 「好，继续跟进，跑完了给我总结。」

## Codex 桌面版

### 安装

到 [chatgpt.com/codex](https://chatgpt.com/codex) 或 OpenAI 的 Codex 页面
下载 macOS 桌面应用（Codex 已并入 ChatGPT 桌面应用，安装后在应用里选
Codex）。首次启动需要登录 ChatGPT 账号。

### 接入

Codex 桌面版和 Codex CLI 共用 `~/.codex/config.toml`。把弹窗给的这段贴进去
（`http://HOST:7777/api/mcp` 换成你的服务器）：

```toml
[mcp_servers.vibe-seller]
url = "http://HOST:7777/api/mcp"
bearer_token_env_var = "VIBE_SELLER_TOKEN"
```

⚠️ **桌面版看不到终端里的 `export`**：`bearer_token_env_var` 走的是环境变量，
命令行里 `export VIBE_SELLER_TOKEN=vst_…` 后能用，但 Dock 启动的桌面应用
读不到这个变量，服务器会一直连不上。桌面版请二选一：

- 在配置里直接写请求头（推荐，不依赖环境变量）：

```toml
[mcp_servers.vibe-seller]
url = "http://HOST:7777/api/mcp"
http_headers = { "Authorization" = "Bearer vst_…" }
```

- 或者把变量设到 GUI 环境：`launchctl setenv VIBE_SELLER_TOKEN "vst_…"`
  （然后重启 Codex）。

想让 Codex 用你自己的模型（而不是 ChatGPT 额度），同一个配置文件里加一个
`[model_providers.…]`（OpenAI 兼容 / Responses 接口的服务商都行），
`model_provider` 指过去即可。

### 验证

在 Codex 会话里问「vibe-seller 里有几个店铺」，或者看 MCP Servers 列表里
`vibe-seller` 的工具数。

### 试一试

在会话里依次试这几句：

1. 「现在 vibe-seller 里有几个店铺？」
2. 「帮我看看 acme 过去一周的广告花费，给点优化建议，按常见标准来就行，
   不用问我，先别改任何东西。」
3. 「这个任务现在在干什么，到哪一步了？」
4. 「好，继续跟进，跑完了给我总结。」

## WorkBuddy（腾讯）

### 安装

到 [workbuddy.cn](https://www.workbuddy.cn)（国内版）或
[workbuddy.ai](https://www.workbuddy.ai)（国际版）下载 macOS 版。首次启动需要登录。

### 接入

弹窗里给了 WorkBuddy 的 MCP 配置 JSON，两个地方二选一：

- **文件**：把 JSON 原样写进 `~/.workbuddy/mcp.json`；
- **界面**：**连接器 → 自定义连接器（Custom connectors）→ MCP Server Management →
  + Add MCP**，粘贴同一段 JSON。

```json
{
  "mcpServers": {
    "vibe-seller": {
      "url": "http://HOST:7777/api/mcp",
      "headers": {
        "Authorization": "Bearer vst_…"
      }
    }
  }
}
```

保存后列表里会出现 `vibe-seller`，但它**必须先点「Trust」（信任）并打开开关**才会
连上——文件改过之后信任会失效，要重新点一次。

### 验证

MCP Server Management 里 `vibe-seller · 32/32 tools enabled` 即为接入成功；对话里问
一句「vibe-seller 里有几个店铺」：

![WorkBuddy 接入成功](images/mcp_workbuddy_verify_zh.png)

### 试一试

在会话里依次试这几句：

1. 「现在 vibe-seller 里有几个店铺？」
2. 「帮我看看 acme 过去一周的广告花费，给点优化建议，按常见标准来就行，
   不用问我，先别改任何东西。」
3. 「这个任务现在在干什么，到哪一步了？」
4. 「好，继续跟进，跑完了给我总结。」

![演示：用 WorkBuddy 驱动 Vibe Seller](images/mcp_workbuddy_demo_zh.gif)

## Qoder（阿里）

### 安装

到 [qoder.com/download](https://qoder.com/download) 下载 macOS 版（中国版应用名
**Qoder CN**）。首次启动需要登录 Qoder 账号（Google / GitHub / 邮箱注册）。

### 接入

**扩展（Extensions）→ 连接器（Connectors）→ 右上角「Add Connector」**，选 Form 或
JSON 标签页，粘贴弹窗给的配置（JSON 形态如下）：

```json
{
  "mcpServers": {
    "vibe-seller": {
      "type": "streamable-http",
      "url": "http://HOST:7777/api/mcp",
      "headers": {
        "Authorization": "Bearer vst_…"
      }
    }
  }
}
```

「Preview import」确认后点「Import」即可。配置保存在 **`~/.qoder-cn/settings.json`**
的 `mcpServers` 里（应用自己的弹窗就是这么写的；Qoder 国际版 / CLI 用的是
`~/.qoder/settings.json`，两版路径不同）。

> Qoder 触发建任务时常常只带标题、不带描述——服务端的 `create_task` 已支持
> 仅标题调用，不用在对话里刻意补描述。

### 验证

**设置 → 扩展管理（Extension management）→ 连接器** 里看到 `vibe-seller · Ready ·
HTTP` 就通了；展开能看到全部工具。然后对话里问一句「vibe-seller 里有几个店铺」。

### 试一试

在会话里依次试这几句：

1. 「现在 vibe-seller 里有几个店铺？」
2. 「帮我看看 acme 过去一周的广告花费，给点优化建议，按常见标准来就行，
   不用问我，先别改任何东西。」
3. 「这个任务现在在干什么，到哪一步了？」
4. 「好，继续跟进，跑完了给我总结。」

## 常见问题

**401 Unauthorized**
API Key 不对、已撤销或已过期，或者属于被停用的用户。到 设置 → 集成 →
API Key 确认状态。撤销或过期的 Key 即使在关掉登录验证的服务器上也会被拒
——这是故意的，撤销就要真的失效。

**405 on /api/mcp**
服务器答的是「Method Not Allowed」：路径落到了网页静态文件兜底上，说明
这台服务器还没有 `/api/mcp`（版本太旧）或者地址填错了。端点是 `/api/mcp`。

**连接被拒 / TLS 报错**
检查主机和端口，以及工具所在机器能不能访问到服务器。绑在 `127.0.0.1` 的
服务器只有本机能连；远程使用请套一层 HTTPS 反向代理。

**工具列表有了，但每次调用都失败**
配置里的 `url` 一般结尾多了 `/api/mcp` 或者斜杠。这里要填的是**根地址**
（Claude 插件）——不同客户端要求不一样，见上面各节的表格。

**Agent 回答了，但没有建任务**
它用自己的知识回答了，没真正跑任务。插件里的技能专门防这个：绝不编造
结果，只报任务自己的数字。没装插件（裸连 MCP）时更容易出现。

**API Key 会不会被截图看到？**
配置界面里 Key 一般显示为掩码；截图前把 `vst_` 字样遮掉即可。报告和
文档里永远只用 `vst_…` 占位符。
