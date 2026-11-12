---
description: 飞书（Lark）群机器人的创建步骤：自定义机器人入口、Webhook 地址、关键词与签名校验等安全设置，以及限流和卡片式 Markdown 注意事项。
---

# 飞书（Feishu / Lark）

## 在飞书里创建机器人

1. 进入群聊，点击右上角「更多 → 设置 → 群机器人 → 添加机器人」，选择
   **自定义机器人**。
2. 设置头像、名称和描述，点击**添加**，复制 **Webhook 地址**并点击**完成**
   （自定义机器人只能用在这一个群里，不需要管理员审批）：
   ```
   https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
   ```

3. 建议接着在机器人详情页配置**安全设置**（可以多选）：
    - **自定义关键词**：最多 10 个，消息要包含至少一个。把其中一个填进
      Vibe Seller 的「关键词」栏。
    - **签名校验**：复制密钥填进 Vibe Seller 的「签名密钥」栏，签名由
      Vibe Seller 自动计算。
    - **IP 白名单**：只允许指定 IP 或网段调用，最多 10 条。留空表示不使用。

「官方说明：[自定义机器人使用指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)」

使用时注意：

- 频率限制为**每分钟 100 条、每秒 5 条**；整点、半点系统繁忙，容易触发
  限流（11232），重要消息尽量避开这些时刻；
- 请求体不超过 20 KB；
- Markdown 以**卡片**形式发送；
- 不支持发文件。

复制好地址后，回到 Vibe Seller，按[在 Vibe Seller 里添加](chat-bots.md#在-vibe-seller-里添加)继续。
