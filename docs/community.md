---
description: 加入 Vibe Seller 用户群和 Slack，或者在 GitHub 上反馈问题、提建议；附反馈时要带上哪些信息。
---

# 加入社区与问题反馈

遇到问题、想交流用法，或者有新功能建议，可以通过下面几种方式找到我们。

## 加入用户群

**微信用户群**：用微信扫码加入（个人微信可以直接扫，不需要安装企业微信）。

<img alt="Vibe Seller 企业微信用户群" src="images/wecom_group_qr.png" width="200" />

**Slack**：海外用户可以加入 [Vibe Seller Slack](https://join.slack.com/t/vibeseller/shared_invite/zt-4fjzkwkqs-L0pM6VS7~_2WsPCxTeLbew)。

## 反馈问题或提建议

在 GitHub 上[新建一个 Issue](https://github.com/zpoint/vibe-seller/issues/new)，或者先[搜索已有的 Issue](https://github.com/zpoint/vibe-seller/issues)，看看有没有人遇到过同样的问题。也可以直接发到用户群里。

## 反馈时请带上这些信息

信息越完整，问题解决得越快：

1. **版本号**：网页左上角「Vibe Seller」旁边显示的版本，例如 `v0.3.1`。
2. **系统**：macOS、Linux、Windows 安装包，还是 Windows + WSL2。
3. **你做了什么、期望什么、实际发生了什么**：比如「新建店铺时选了紫鸟，点创建后提示……」。
4. **截图**：报错提示、任务详情页的截图。
5. **日志**（可选）：服务日志在 `~/.vibe-seller/logs/` 目录下，例如 `backend_7777.log`（Windows 上是 `C:\Users\<你的用户名>\.vibe-seller\logs\`）。

!!! warning "发出去之前先检查"
    截图和日志里可能有店铺名、邮箱、订单数据或 API Key。公开发到 GitHub 之前，请先把这些信息打码或删掉。

紫鸟绑定报错这类常见问题，可能已经有现成的解决办法，可以先看[常见问题](faq.md)。
