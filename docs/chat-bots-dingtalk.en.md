---
description: 'Steps to create a DingTalk group bot: the custom-robot entry, keyword / signing / IP security settings, copying the webhook URL, plus throttling and size limits.'
---

# DingTalk (钉钉)

## Create the bot in DingTalk

1. Open DingTalk, enter the group and click the **group settings** gear
   at the top right → **Robots → Add robot**, choose the **custom
   robot** (some clients call the entry "Smart group assistant").
   Normal and external groups both work; the bot posts to the group
   only, never to one-to-one chats.
2. Name it (an avatar is optional), then set up **security** — DingTalk
   requires at least one of:
    - **Custom keyword**: up to 10; every message must contain at least
      one of them. Note one down for the Vibe Seller "Keyword" field —
      Vibe Seller appends it to any message that lacks it.
    - **Signing (加签)**: copy the `SEC…` secret into Vibe Seller's
      "Signing secret" field; every message is signed automatically.
    - **IP range**: only these public egress IPs may call the webhook.
      Leave both fields empty if you don't use this one.
3. Accept the terms, click **Done** and copy the **Webhook URL**:

    ```
    https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx
    ```

"Official guide: [Custom robot access](https://open.dingtalk.com/document/group/custom-robot-access) ·
[Custom robot security settings](https://open.dingtalk.com/document/robots/customize-robot-security-settings)"

Good to know:

- At most **20 messages per minute** per bot; going over throttles the
  bot for 10 minutes.
- Markdown messages get a title derived from the first line — that is
  what notifications and the chat list show.
- No file sending; keep messages under ~20 KB.

Then add the bot in Vibe Seller — see [Add the bot in Vibe Seller](chat-bots.md#add-the-bot-in-vibe-seller).
