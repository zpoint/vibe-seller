---
description: 'Steps to create a Feishu / Lark group bot: the custom-bot entry, copying the webhook URL, keyword / signature / IP allowlist security, plus rate limits and card markdown.'
---

# Feishu / Lark (飞书)

## Create the bot in Feishu

1. In the group, click **More → Settings → Group bots → Add bot** at
   the top right and pick the **custom bot**.
2. Set avatar, name and description, click **Add**, copy the
   **Webhook URL** and click **Done**:

    ```
    https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx
    ```

    A custom bot works in that one group only and needs no admin
    approval.

3. In the bot's detail page, set up **security** (you can combine
   them):
    - **Custom keyword**: up to 10; the message must contain at least
      one. Put one of them into Vibe Seller's "Keyword" field.
    - **Signature check**: copy the secret into Vibe Seller's "Signing
      secret" field — Vibe Seller computes the signature for you.
    - **IP allowlist**: only these IPs / ranges may call the webhook,
      up to 10 entries. Leave empty for none.

"Official guide: [Custom bot guide](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)"

Good to know:

- Rate limit: **100 messages per minute, 5 per second**. Around exact
  hours and half hours the platform is busy and may answer 11232
  (throttled) — avoid those moments for important messages.
- The request body must stay under 20 KB.
- Markdown is sent as a **card**.
- No file sending.

Then add the bot in Vibe Seller — see [Add the bot in Vibe Seller](chat-bots.md#add-the-bot-in-vibe-seller).
