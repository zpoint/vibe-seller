---
description: 在 Pi 里接入 Vibe Seller：执行弹窗给的 pi mcp add 命令、确认接好、修改或移除。
---

# 在 Pi 里接入 Vibe Seller

接好之后，在 Pi 的对话里就能让 Vibe Seller 建任务、跟进进度、回答提问。

需要准备两样东西：

1. 一个 API Key：见[第一步：创建 API Key](mcp.md#第一步创建-api-key)。创建
   成功后弹出的窗口里有你的服务器地址和 Pi 的现成命令；
2. 已安装 Pi。

## 接入

在终端里执行弹窗「命令行 / 配置文件」标签给的这条命令，一整行原样复制：

```bash
# 默认连本机；Vibe Seller 在另一台电脑上时，把 localhost 换成那台电脑的地址
pi mcp add vibe-seller --url http://localhost:7777/api/mcp --header "Authorization=Bearer vst_…"
```

它会写入 `~/.pi/agent/mcp.json`。已经打开的 Pi 会话里执行一次 `/reload`
即可生效。

## 确认接好了

新建一个对话，问“现在 vibe-seller 里有几个店铺？”，它把你的店铺列出来
就接好了。也可以在终端执行 `pi mcp list`，看到
`vibe-seller: connected, 32 tools` 说明已经连上。

## 修改或移除

要改地址或换 Key，编辑 `~/.pi/agent/mcp.json` 里的 `vibe-seller` 那一段，
删掉它就是移除；改完在已打开的会话里执行一次 `/reload`。

## 接下来

接入之后怎么用，见[可以这样用](mcp.md#可以这样用)；一步步的完整示例见
[使用示例](examples.md)。
