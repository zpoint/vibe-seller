---
description: "Connect BitBrowser to Vibe Seller: install, account and auto-login, bind a store, and fix common messages."
---

# BitBrowser: Setup Guide

BitBrowser (比特浏览器) is an anti-detect browser ([official site](https://www.bitbrowser.net/)). Once bound, Vibe Seller opens your stores through it to run tasks.

**The BitBrowser backend supports Windows and macOS; WSL is also supported.**

## Download and install

- **Windows**: download the Windows (64-bit) client from the [download page](https://www.bitbrowser.net/download/) and install it. The default location is `C:\Users\<user>\AppData\Local\Programs\bitbrowser`; another location works too.
- **macOS**: download the macOS (Apple Silicon or Intel) client from the same page and drop it into `/Applications` (`比特浏览器.app`). For another location, save the client path in Settings.

Open BitBrowser, sign in, and check that you can see your browser profiles.

## The account and auto-login

Save your BitBrowser **account and password** in Vibe Seller (a sub-account is recommended). When the session expires, Vibe Seller signs in again automatically. The password is stored encrypted on this machine.

- **One BitBrowser account can be signed in on one computer at a time.** Signing in on another computer signs this one out. Vibe Seller then refuses to auto-login, so two computers cannot keep kicking each other out.
- SMS, email, 2FA and slider verification must be completed by hand in BitBrowser.
- A sub-account sees its own profiles plus the profiles authorized by the main account. An empty list usually means no profiles have been authorized: ask the main account to grant profile permissions under 员工管理 (Users).
- The free plan includes 10 browser profiles, permanently.

## What to fill in when binding

Create a store with the browser backend "BitBrowser":

1. Click [+] next to the account row, enter the **BitBrowser account and password**, and save. One account is kept per machine; delete every store created with it before changing the account.
2. The profile list loads automatically.
3. Pick this store's profile under "Select a profile..." and click Create.

Binding uses the profile ID, not its name, so renaming a profile in BitBrowser does not break a bound store.

The local API port and auth need no configuration — Vibe Seller reads them itself.

!!! warning "Enable 鉴权控制 on shared networks"
    BitBrowser's local API listens on every network interface. On a shared network, enable 鉴权控制 in BitBrowser's settings so other devices on the LAN cannot call it.

## Stores must use a Chrome kernel

!!! warning "Only Chrome-kernel profiles can be driven"
    Vibe Seller can only drive Chrome-kernel profiles; tasks cannot open Firefox-kernel profiles. When creating or editing a profile, choose Chrome under 指纹 (fingerprint) in BitBrowser.

Firefox-kernel profiles are greyed out with the reason in the picker. After changing a bound profile to Chrome, you do not need to rebind — just retry the task. See [Stores must use a Chrome kernel](browsers.en.md#stores-must-use-a-chrome-kernel).

## Common statuses / errors

<details id="login-required">
<summary>Not logged in — sign in to the BitBrowser client once (that is enough), then Refresh.</summary>

The session expired. Check the saved account and password, or sign in by hand once, then click Refresh.

</details>

<details id="no-credentials">
<summary>比特浏览器未登录且没有保存的账号密码。请在设置里填写比特浏览器账号和密码，或在比特浏览器里手动登录</summary>

No account and password are saved. Save them as described above, or sign in by hand once.

</details>

<details id="auto-login-failed">
<summary>自动登录没有成功，请手动打开比特浏览器登录一次，然后重试任务</summary>

The auto-login did not take. Sign in by hand once, then retry the task.

</details>

<details id="device-kick">
<summary>这个比特浏览器账号在另一台电脑登录了，本机已退出。请在比特浏览器里重新登录（或确认只在一台电脑上使用）</summary>

The same account signed in on another computer, so this one was signed out. Use the account on only one computer, sign in again in BitBrowser, then retry.

</details>

<details id="wrong-password">
<summary>比特浏览器账号或密码不对，请在设置里重新填写</summary>

The saved account or password is wrong. Re-enter it in Settings, then retry. Repeated wrong passwords can also trigger a slider verification that must be completed by hand.

</details>

<details id="verification-required">
<summary>需要在比特浏览器里完成登录验证（短信/邮箱/二次验证），完成后重试</summary>

The account needs login verification; repeated wrong passwords can also trigger a slider. Vibe Seller does not solve these checks. Complete the verification in BitBrowser by hand, then retry.

</details>

<details id="client-not-running">
<summary>Could not start the BitBrowser client — open it by hand (or check the install), then Refresh.</summary>

Open BitBrowser by hand. For a non-standard install location, save the client path in Settings, then click Refresh.

</details>

<details id="api-auth">
<summary>请在比特浏览器 → 设置 → Local API 里检查「鉴权控制」，再重试任务。</summary>

Check the 鉴权控制 toggle in BitBrowser → Settings → Local API, then retry the task.

</details>

<details id="kernel">
<summary>这个窗口用的不是 Chrome（Chromium）内核，任务无法打开它；在比特浏览器里把这个窗口的内核切换为 Chrome（指纹 → 内核）后重试</summary>

Switch this profile's kernel to Chrome, then retry the task.

</details>

<details id="store-unavailable">
<summary>The BitBrowser client opened this store but says it cannot be used right now. Check the store's status in the BitBrowser client, then click Retry.</summary>

Check the profile's status in BitBrowser, fix it, then click Retry.

</details>

<details id="store-not-found">
<summary>This store was not found in the BitBrowser client. Check it still exists there and is visible to the signed-in account, then click Retry.</summary>

The profile may have been deleted, or the current account cannot see it. Check that it exists and is visible, then click Retry.

</details>

<details id="no-stores">
<summary>No visible profiles for this login — a sub-account needs profiles authorized by the main account.</summary>

Ask the main account to grant profile permissions under 员工管理 (Users), then refresh the list.

</details>

<details id="unsupported-platform">
<summary>This browser doesn't support this system.</summary>

The BitBrowser backend supports Windows and macOS; WSL is also supported.

</details>

<details id="workbench-tab">
<summary>A workbench tab opens with the profile</summary>

Vibe Seller closes it automatically before the task and focuses the store page; nothing to do.

</details>

<details id="client-killed">
<summary>BitBrowser was closed during a task</summary>

Nothing to do: the next browser use relaunches the client and opens the store.

</details>

<details id="hand-opened-profile">
<summary>A profile was opened by hand</summary>

Vibe Seller attaches to the already-open profile; there is no need to reopen it. The workbench tab is closed automatically too.

</details>

<details id="credentials-storage">
<summary>Where credentials are stored</summary>

The account and password are stored in Vibe Seller's local database. The password is encrypted, decrypted only for auto-login, and never shown in full in the UI. The local API port and token are read automatically and not stored separately.

</details>

| Message | What to do |
|---|---|
| Not logged in — sign in to the BitBrowser client once (that is enough), then Refresh. | Check the saved credentials, or sign in by hand once. |
| 比特浏览器未登录且没有保存的账号密码。请在设置里填写比特浏览器账号和密码，或在比特浏览器里手动登录 | Save the account and password, or sign in by hand. |
| 自动登录没有成功，请手动打开比特浏览器登录一次，然后重试任务 | Sign in by hand once, then retry the task. |
| 这个比特浏览器账号在另一台电脑登录了，本机已退出。请在比特浏览器里重新登录（或确认只在一台电脑上使用） | Use the account on one computer only, then sign in again. |
| 比特浏览器账号或密码不对，请在设置里重新填写 | Re-enter the account and password. |
| 需要在比特浏览器里完成登录验证（短信/邮箱/二次验证），完成后重试 | Complete the login or slider verification by hand. |
| Could not start the BitBrowser client — open it by hand (or check the install), then Refresh. | Open the client by hand, or save the correct client path. |
| 请在比特浏览器 → 设置 → Local API 里检查「鉴权控制」，再重试任务。 | Check the auth toggle, then retry. |
| 这个窗口用的不是 Chrome（Chromium）内核，任务无法打开它；在比特浏览器里把这个窗口的内核切换为 Chrome（指纹 → 内核）后重试 | Switch the profile's kernel to Chrome. |
| The BitBrowser client opened this store but says it cannot be used right now. Check the store's status in the BitBrowser client, then click Retry. | Check the profile's status, fix it, then retry. |
| This store was not found in the BitBrowser client. Check it still exists there and is visible to the signed-in account, then click Retry. | Check that the profile exists and is visible. |
| No visible profiles for this login — a sub-account needs profiles authorized by the main account. | Ask the main account to grant profile permissions. |
| This browser doesn't support this system. | Use Windows, macOS or WSL. |
