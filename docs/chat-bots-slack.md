---
description: Slack 群机器人的创建步骤：新建 App、打开 Incoming Webhooks、授权频道并复制 Webhook URL，以及频道固定、不可撤回等注意事项。
---

# Slack

## 在 Slack 里创建机器人

1. 打开 [api.slack.com/apps](https://api.slack.com/apps)，点击 **Create New
   App**，选择一个 workspace。
2. 在应用设置里打开 **Incoming Webhooks**，把开关打开。
3. 点击 **Add New Webhook to Workspace**，选择要发消息的频道，点击
   **Authorize**。
4. 回到设置页复制 **Webhook URL**：

   ```
   https://hooks.slack.com/services/T00000000/B00000000/xxxxxxxxxxxx
   ```

「官方说明：[Sending messages using incoming webhooks](https://api.slack.com/messaging/webhooks)」

使用时注意：

- 消息固定发送到创建 Webhook 时选择的那个频道，事后不能更改；
- 发出去的消息不能撤回；URL 本身就是凭证，泄露后 Slack 会撤销它；
- 纯文本不超过 40 000 字符，Markdown（Block Kit）不超过 12 000 字符；
- 不支持发文件；没有签名密钥，「签名密钥」栏不要填写。

复制好地址后，回到 Vibe Seller，按[在 Vibe Seller 里添加](chat-bots.md#在-vibe-seller-里添加)继续。
