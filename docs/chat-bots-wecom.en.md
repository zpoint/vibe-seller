---
description: 'Steps to create a WeCom group bot: the internal-group rule, where the Message Push entry lives, copying the webhook URL, plus rate, size and file limits.'
---

# WeCom (企业微信)

## Create the bot in WeCom

1. Open the WeCom **desktop** client (mobile works too) and enter the
   group that should receive the messages. Only **internal groups**
   support bots — external groups (ones with WeChat contacts) have no
   add entry at all, and a group set to "managed solely by the group
   owner" hides the entry from regular members.
2. Click **··· (More) → Message Push → Add** at the top right and
   choose "create a new bot" (older clients call it "Add group bot").
3. Name it, click add, then **copy the address**. It looks like:

    ```
    https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxxxxxx
    ```

4. Optional: "publish to the company" so colleagues can add the same
   bot to their own groups.

"Official guide: [How to set up Message Push](https://open.work.weixin.qq.com/help2/pc/14931) ·
[Message Push configuration](https://developer.work.weixin.qq.com/document/path/91770)"

Good to know:

- Each bot sends at most **20 messages per minute**.
- Text up to 2 KB, markdown up to 4 KB (a reduced syntax: headings,
  bold, links, inline code, quotes, three font colours — no tables).
- Files from 5 bytes up to 20 MB (uploaded first, the material is valid
  for 3 days).
- If the group has "mute all" enabled, messages are accepted but **never
  shown**.

Then add the bot in Vibe Seller — see [Add the bot in Vibe Seller](chat-bots.md#add-the-bot-in-vibe-seller).
