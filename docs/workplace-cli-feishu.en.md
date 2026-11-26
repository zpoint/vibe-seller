---
description: Let the AI use Feishu / Lark — click Connect in Vibe Seller, create a Feishu app (once), then authorize with your Feishu account. The AI can then read and write Feishu docs, sheets and Base, schedule events and send messages. Covers user vs app identity and the FAQ.
---

# Feishu / Lark (飞书)

Once connected, the AI can use Feishu: messages and chats, docs, sheets,
Base, drive, wiki, calendar, meetings and minutes, tasks, mail,
approvals, contacts. The overview is at
[WeCom / Feishu / DingTalk](workplace-cli.en.md).

## Steps

Two steps; the first is needed only once.

1. Open Vibe Seller **Settings → Integrations** and, under **WeCom / Feishu / DingTalk**, click **Connect** on the Feishu row.
   The first time it downloads Feishu's official program (about 14 MB).
2. **Step 1 of 2: create a Feishu app.** A link appears; open it, sign
   in to Feishu and follow the prompts to create the app. The AI
   connects to Feishu through it:

    ![Feishu step 1 of 2: open the link and create a Feishu app (the code in the link blurred)](images/workplace_feishu_step1_en.png)

3. **Step 2 of 2: authorize with your Feishu account.** Once the app
   exists, the page shows a second link and a code. Open the link,
   check the code matches, and approve:

    ![Authorize with your Feishu account: open the link and check the code matches (shown here from Sign in again; the first time it is labelled step 2 of 2; code blurred)](images/workplace_feishu_auth_en.png)

4. Back in Settings, the Feishu row shows "Signed in. Finishing the
   setup…", then "The AI can use it" a few seconds later. Done.

## Two identities

- **User identity**: acts as the Feishu account that authorized. Your
  own calendar, mail and documents need it.
- **App identity**: acts as the Feishu app (bot) created in step 1.

The AI picks the identity as the official skills describe; you don't
choose.

**Messages**: whether the AI can message people as you depends on the
Feishu app's "send messages as user" permission
(`im:message.send_as_user`):

- **Granted**: the AI sends messages as you; people see you as the
  sender.
- **Not granted**: the AI cannot send as you. It sends as the Feishu
  app (bot) instead, into the app's chat with you.

To let the AI message people as you, the Feishu app's admin turns it on:

1. Open the [Feishu developer console](https://open.feishu.cn/app) and
   open the app created in step 1.
2. Under 开发配置 → 权限管理 (Development → Permissions), click 开通权限
   (Add permissions), switch to the user-identity permissions, search for
   `im:message.send_as_user`, tick it and click 确认开通权限 (Confirm).
3. This permission needs review: under 应用发布 → 版本管理与发布 (App
   release → Version management), click 创建版本 (Create version), save,
   then 申请线上发布 (Submit for release), and wait for your Feishu
   organization's admin to approve.
4. Once approved, click **Sign in again** on the Feishu row in Vibe
   Seller's Settings.

Feishu's official guide: [Apply for API permissions](https://open.feishu.cn/document/server-docs/application-scope/introduction)
(in Chinese).

## FAQ

<details markdown="1" id="admin-restricted-permissions"><summary><b>"Your Feishu admin has restricted these permissions"?</b></summary>

The sign-in worked and Feishu is enabled; nothing else is affected.

Many organizations see this message the first time they connect, and the
permission listed is usually `vc:meeting.realtime:read`. It is used for
one thing only: **taking a screenshot of a Feishu video meeting in
progress**. Feishu has removed that feature from its official skills, so
the AI doesn't use it and you can ignore the message.

If you do need it, the Feishu app's admin turns it on:

1. Open the [Feishu developer console](https://open.feishu.cn/app) and
   open the app created in step 1.
2. Under 开发配置 → 权限管理 (Development → Permissions), click 开通权限
   (Add permissions), search for the permission the message lists (for
   example `vc:meeting.realtime:read`), tick it and click 确认开通权限
   (Confirm). If it is already in the list, go to the next step.
3. Under 应用发布 → 版本管理与发布 (App release → Version management),
   click 创建版本 (Create version), save, then 申请线上发布 (Submit for
   release), and wait for your Feishu organization's admin to approve.
4. Once approved, click **Sign in again** on the Feishu row in Vibe
   Seller's Settings.

If the message lists a different permission, the steps are the same.
</details>

<details markdown="1" id="no-refresh-token"><summary><b>"This sign-in can't renew itself"?</b></summary>

The sign-in worked, but Feishu did not issue the credential used to
renew it: what the AI does as your Feishu account stops working at the
time shown (about 2 hours after signing in), and you have to sign in
again; what it does as the Feishu app is not affected. The reason is
that the Feishu app has not turned on "Refresh user_access_token" under
its security settings in the developer console, or has not published a
version since.

To fix it, ask the admin of the Feishu app to open the link in the
notice, turn on "Refresh user_access_token", create and publish a new
version, then click **Sign in again**.
</details>

<details markdown="1"><summary><b>The link asks me to sign in to Feishu?</b></summary>

That's expected. Both steps happen on Feishu's web pages: sign in by
scanning or with your password first, then follow the prompts.
</details>
