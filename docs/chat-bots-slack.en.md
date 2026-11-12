---
description: 'Steps to create a Slack bot: new app, Incoming Webhooks toggle, authorizing a channel and copying the webhook URL, plus channel lock-in and message limits.'
---

# Slack

## Create the bot in Slack

1. Open [api.slack.com/apps](https://api.slack.com/apps) and **Create
   New App** for your workspace.
2. In the app settings open **Incoming Webhooks** and toggle it on.
3. Click **Add New Webhook to Workspace**, pick the channel, and
   **Authorize**.
4. Copy the **Webhook URL** from the settings page:

    ```
    https://hooks.slack.com/services/T00000000/B00000000/xxxxxxxxxxxx
    ```

"Official guide: [Sending messages using incoming webhooks](https://api.slack.com/messaging/webhooks)"

Good to know:

- Messages always go to the channel chosen when the webhook was
  created; it cannot be changed later.
- Posted messages cannot be deleted. The URL is the credential — Slack
  revokes leaked ones.
- Plain text up to 40,000 characters, markdown (Block Kit) up to
  12,000 characters.
- No file sending and no signing secret — leave the secret field empty.

Then add the bot in Vibe Seller — see [Add the bot in Vibe Seller](chat-bots.md#add-the-bot-in-vibe-seller).
