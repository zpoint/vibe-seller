---
description: 钉钉群机器人的创建步骤：自定义机器人入口、关键词与加签等安全设置、复制 Webhook 地址，以及限流和长度注意事项。
---

# 钉钉（DingTalk）

## 在钉钉里创建机器人

1. 打开钉钉，进入群聊，点击右上角**群设置**（齿轮图标）→「机器人 →
   添加机器人」，选择**自定义机器人**（有的版本入口叫「智能群助手」）。
   普通群和外部群都可以；机器人只发群消息，不发单聊。
2. 起一个名字（可选头像），然后完成**安全设置**——钉钉要求至少选择一种：
    - **自定义关键词**：最多 10 个，发出的消息必须包含其中至少一个。
      记下其中一个，稍后填进 Vibe Seller 的「关键词」栏——未包含
      关键词的消息，Vibe Seller 会自动在末尾补上。
    - **加签**：复制 `SEC` 开头的密钥，稍后填进 Vibe Seller 的
      「签名密钥」栏，每条消息的签名由 Vibe Seller 自动带上。
    - **IP 地址（段）**：只允许指定出口 IP 调用。填写 Vibe Seller 所在
      机器的公网出口 IP；不使用这种方式的就留空。
3. 勾选《自定义机器人服务及免责条款》，点击**完成**，复制 **Webhook 地址**：

   ```
   https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx
   ```

「官方说明：[自定义机器人接入](https://open.dingtalk.com/document/group/custom-robot-access) ·
[自定义机器人安全设置](https://open.dingtalk.com/document/robots/customize-robot-security-settings)」

使用时注意：

- 每个机器人**每分钟最多 20 条**，超过会被限流 10 分钟；
- Markdown 消息会自动用正文首行做标题（会话列表和通知里显示的就是它）；
- 不支持发文件；消息过长可能被拒，建议控制在 20 KB 以内。

复制好地址后，回到 Vibe Seller，按[在 Vibe Seller 里添加](chat-bots.md#在-vibe-seller-里添加)继续。
