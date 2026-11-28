---
description: One page for connecting BitBrowser to Vibe Seller — download, create a sub-account with profile permissions, what to fill in, one account per machine, and common statuses and errors.
---

# BitBrowser: Setup Guide

BitBrowser (比特浏览器) is an anti-detect browser ([official site](https://www.bitbrowser.net/)). Once bound, Vibe Seller opens profiles through its local API and controls the store page through CDP to run tasks.

**The BitBrowser backend supports Windows and macOS; WSL is also supported.**

Not using BitBrowser? Skip this page; Vibe Seller supports other browsers too — see [Browsers](browsers.en.md).

## Download and install

1. Open the [download page](https://www.bitbrowser.net/download/), download the client for your system (Windows / Mac) and install it. Any install folder works — Vibe Seller finds it on its own.
2. Start and sign in once by hand to confirm the account works and you can see your profiles. After that Vibe Seller launches the client and signs in again when the session expires.

## Create an account (if needed)

An existing BitBrowser account works directly; a sub-account is recommended. To create one:

1. As the admin, open **员工管理 → 成员** (Users → Members) and create a sub-account.
2. Authorise the profiles this sub-account may operate. It sees only its own profiles and those authorised by the main account; with none authorised, Vibe Seller lists no profiles.
3. Write down its **username and password** — that is what you fill into Vibe Seller.

!!! warning "Stores must use a Chrome kernel"

    Set the profile's browser kernel to **Chrome**: a profile on a Firefox or other non-Chrome kernel cannot be opened by a task. Choose it under 指纹 (fingerprint) when creating or editing the profile — see [Stores must use a Chrome kernel](browsers.en.md#stores-must-use-a-chrome-kernel).

## What to fill in when binding

When creating a store, pick BitBrowser as the browser backend — the same form as Ziniao:

1. Click **[+]** next to the account row and fill in two fields:

| Field | What it is |
|---|---|
| Username | Your BitBrowser account or sub-account username |
| Password | That account's password |

2. After saving, Vibe Seller loads the profiles this account can see. If the client was closed or its session expired, it signs back in, so a task never stalls on "not logged in".
3. Pick the matching profile in **Select a profile...** and click **Create**. Firefox-kernel profiles are greyed out and cannot be selected.

## One account per machine

Like Yuehulian, Vibe Seller keeps ONE BitBrowser account per machine, so the account row has **only a [+]** — no edit, no delete. Clicking [+] with a new account **overwrites** the stored one.

If any BitBrowser store is already bound on this machine, adding a new account is refused. To switch accounts: delete every BitBrowser store under Settings → Stores, then click [+] and bind the stores again.

Also, **one BitBrowser account can be signed in on one computer at a time**. Signing in on another computer kicks this one out; Vibe Seller then refuses to auto-login so the two computers cannot keep kicking each other.

## Common statuses / errors

<details id="port-auth">
<summary>Do I need to configure the local API port or auth?</summary>

No — Vibe Seller reads them from the client's own settings. Changing the port or enabling 鉴权控制 does not affect the binding.
</details>

<details id="workbench-tab">
<summary>A workbench tab appears when opening a store</summary>

Nothing to do — Vibe Seller closes it over CDP before the task and focuses the store page. If only a blank page appears, it navigates that page to the store.
</details>

<details id="closed-mid-task">
<summary>BitBrowser was closed during a task</summary>

Nothing to do: the next browser use relaunches the client, opens the store and restores the CDP connection.
</details>

<details id="hand-opened-profile">
<summary>A profile was opened by hand in the client</summary>

The task attaches to that already-open profile; there is no need to reopen it. Vibe Seller confirms it in the client's running-profile list and attaches to its CDP connection.
</details>

<details id="not-logged-in">
<summary>Told the client is not logged in, or auto-login failed</summary>

Check the username / password saved in [+] and that the client can sign in by hand, then Refresh. If the account signed in on another computer, this one was kicked out: Vibe Seller refuses to auto-login so the computers cannot keep kicking each other. Use the account on one computer and sign in again by hand.
</details>

<details id="login-verification">
<summary>Wrong password, or SMS / email / 2FA / slider verification appears</summary>

Re-enter a wrong password in Settings. Complete login verification in BitBrowser by hand, then retry. Repeated wrong passwords can also trigger a slider; Vibe Seller never solves these checks automatically.
</details>

<details id="cannot-start">
<summary>Told the BitBrowser client could not be started</summary>

Open BitBrowser by hand. For a non-standard install location, save the client path in Settings, then click Refresh.
</details>

<details id="firefox-kernel">
<summary>Greyed-out rows in the profile list</summary>

That profile does not use a Chrome kernel in BitBrowser. Switch it to Chrome under 指纹 (fingerprint) in the client, then refresh.
</details>

<details id="one-account">
<summary>Switching to another BitBrowser account</summary>

See [One account per machine](#one-account-per-machine) above: delete every BitBrowser store first, then add the new account.
</details>

<details id="where-credentials">
<summary>Where the credentials are stored</summary>

The username and password live in your local `~/.vibe-seller/data/vibe_seller.db` (SQLite). The password is encrypted and only decrypted in memory at the moment of auto-login; the API never returns it to the frontend. Since a machine has one BitBrowser account, the credentials are shared by all its stores. The local API port and token are read automatically and not stored separately.
</details>

| Message | Meaning / what to do |
|---|---|
| Not logged in — sign in to the BitBrowser client once (that is enough), then Refresh. | The session expired. Check the saved credentials, or sign in by hand once. |
| 比特浏览器未登录且没有保存的账号密码。请在设置里填写比特浏览器账号和密码，或在比特浏览器里手动登录 | Save the account and password, or sign in by hand once. |
| 自动登录没有成功，请手动打开比特浏览器登录一次，然后重试任务 | Sign in by hand once, then retry the task. |
| 这个比特浏览器账号在另一台电脑登录了，本机已退出。请在比特浏览器里重新登录（或确认只在一台电脑上使用） | Use the account on one computer only, then sign in again. |
| 比特浏览器账号或密码不对，请在设置里重新填写 | Re-enter the account and password. Repeated wrong passwords can trigger a slider. |
| 需要在比特浏览器里完成登录验证（短信/邮箱/二次验证），完成后重试 | Complete the login or slider verification by hand, then retry. |
| Could not start the BitBrowser client — open it by hand (or check the install), then Refresh. | Open the client by hand, or save the correct client path. |
| 请在比特浏览器 → 设置 → Local API 里检查「鉴权控制」，再重试任务。 | Check the auth toggle, then retry. |
| 这个窗口用的不是 Chrome（Chromium）内核，任务无法打开它；在比特浏览器里把这个窗口的内核切换为 Chrome（指纹 → 内核）后重试 | Switch the profile's kernel to Chrome. |
| The BitBrowser client opened this store but says it cannot be used right now. Check the store's status in the BitBrowser client, then click Retry. | Check the profile's status, fix it, then retry. |
| This store was not found in the BitBrowser client. Check it still exists there and is visible to the signed-in account, then click Retry. | Check that the profile exists and is visible. |
| No visible profiles for this login — a sub-account needs profiles authorized by the main account. | Ask the main account to grant profile permissions. |
| This browser doesn't support this system. | The BitBrowser backend supports Windows and macOS; WSL is also supported. |
