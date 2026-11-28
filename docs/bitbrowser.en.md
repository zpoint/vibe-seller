---
description: "Connect BitBrowser to Vibe Seller: install, account and auto-login, bind a store, local API and auth, common errors, and credential storage."
---

# BitBrowser: Setup Guide

BitBrowser (比特浏览器) is an anti-detect browser ([official site](https://www.bitbrowser.net/), [API docs](https://doc.bitbrowser.net/)). Once bound, Vibe Seller opens profiles through its local HTTP API and controls the store page through CDP (Chrome DevTools Protocol). It launches the client automatically when it is not running.

**The BitBrowser backend supports Windows and macOS; WSL is also supported.**

Not using BitBrowser? Skip this page — Vibe Seller supports other browsers, see [Browsers](browsers.en.md).

## Download and install

- **Windows**: download the Windows (64-bit) client from the [download page](https://www.bitbrowser.net/download/) and install it. The default location is `C:\Users\<user>\AppData\Local\Programs\bitbrowser`; other locations are found too.
- **macOS**: download the macOS (Apple Silicon or Intel) client from the same page and drop it into `/Applications` (`比特浏览器.app`). For a non-standard location, save the client path in Settings.

Start BitBrowser and sign in once by hand, and check that you can see your browser profiles.

## The account and auto-login

Save your BitBrowser **account and password** in Vibe Seller (a sub-account is recommended). When the session expires, Vibe Seller signs in again through the client's own login page, so tasks do not stop over a missing login. The password is stored encrypted on this machine (see "Where credentials are stored" below).

- The client usually stays signed in across restarts, even after a forced kill; auto-login is only needed when the session actually expires.
- **One BitBrowser account is signed in on one computer at a time.** Signing in on another computer kicks this one out. Vibe Seller then does **not** auto-login — two computers would keep kicking each other. It asks you to sign in again or to use the account on only one computer. After your manual sign-in, ordinary expiry can auto-login again.
- SMS, email, 2FA and slider verification (which can appear after repeated wrong passwords) must be completed by hand in BitBrowser.
- Sub-accounts work: a sub-account sees its own profiles plus the ones authorized by the main account. An empty list usually means "not authorized", not broken — ask the main account to grant profile permissions under 员工管理 (Users).
- The free plan includes 10 browser profiles, permanently.

## What to fill in when binding

Create a store with the browser backend "BitBrowser":

1. Click [+] next to the account row, enter the **BitBrowser account and password**, and save (one account per machine; delete every store created with it before changing the account).
2. The profile list loads automatically.
3. Pick this store's profile under "Select a profile..." (shown as `sequence + profile name`) and click Create.

Binding uses the profile ID, not its name, so renaming a profile in BitBrowser does not break a bound store.

## Port and auth: read automatically

BitBrowser's local API defaults to `127.0.0.1:54345`. Vibe Seller uses it to list, open and close profiles. Its address and auth switch live in the client's Settings → Local API, and Vibe Seller **reads them from the client's config file**:

- Change the Local API port and Vibe Seller picks up the new one on the next connect; nothing to rebind.
- With 鉴权控制 (auth control) enabled, Vibe Seller sends the token automatically — no copy-pasting.

!!! warning "Enable 鉴权控制 on shared networks"
    BitBrowser's local API listens on every network interface. On a shared network, enable 鉴权控制 in its settings so other devices on the LAN cannot drive your BitBrowser.

## The workbench tab when a profile opens

Opening a profile in BitBrowser usually shows a workbench tab (IP and fingerprint info) next to the store's own page. Vibe Seller **closes the workbench tab over CDP** before a task starts and focuses the store page. Your profile settings are never touched — when you open a profile by hand in the client, the workbench tab still shows.

If the profile shows only a blank page, Vibe Seller navigates that page to the store instead of handing the task an empty browser.

## Closed the client / opened a store by hand

- **Closed BitBrowser during a task**: nothing to do. The next browser use relaunches the client, opens the store and restores the CDP connection.
- **Opened the store by hand in the client**: the task attaches to that already-open profile; there is no need to reopen it. Vibe Seller checks the client's running-profile list, attaches to its CDP connection and closes the workbench tab.

## Stores must use a Chrome kernel

!!! warning "Only Chrome-kernel profiles can be driven"
    Vibe Seller controls pages through CDP, so it can only drive Chrome (Chromium) kernel profiles. Firefox-kernel profiles do not expose this interface and tasks cannot open them. When creating or editing a profile, choose Chrome under 指纹 (fingerprint) in BitBrowser.

Firefox-kernel profiles are greyed out with the reason in the store picker, and selecting one is refused. After changing a bound profile to Chrome, you do not need to rebind — just run the task again. See [Stores must use a Chrome kernel](browsers.en.md#stores-must-use-a-chrome-kernel).

## Common statuses / errors

| Message | Meaning / what to do |
|---|---|
| Not logged in — sign in to the BitBrowser client once (that is enough), then Refresh. | The session expired. Check the saved credentials, or sign in by hand once. |
| 比特浏览器未登录且没有保存的账号密码。请在设置里填写比特浏览器账号和密码，或在比特浏览器里手动登录 | No saved credentials are available for auto-login. Save the account and password (see above), or sign in by hand once. |
| 自动登录没有成功，请手动打开比特浏览器登录一次，然后重试任务 | The auto-login did not take. Sign in by hand once, then retry the task. |
| 这个比特浏览器账号在另一台电脑登录了，本机已退出。请在比特浏览器里重新登录（或确认只在一台电脑上使用） | The same account signed in on another computer and kicked this one out. Use the account on one computer only, then sign in again. |
| 比特浏览器账号或密码不对，请在设置里重新填写 | The saved account or password is wrong. Re-enter it in Settings. Repeated wrong passwords can trigger a slider verification. |
| 需要在比特浏览器里完成登录验证（短信/邮箱/二次验证），完成后重试 | The account needs login verification; repeated wrong passwords can also trigger a slider. Complete it in BitBrowser by hand, then retry. |
| Could not start the BitBrowser client — open it by hand (or check the install), then Refresh. | Vibe Seller could not find or launch the client. Open BitBrowser by hand; for a non-standard location, save the client path in Settings. |
| 请在比特浏览器 → 设置 → Local API 里检查「鉴权控制」，再重试任务。 | The local API's auth toggle or token is off. Check 鉴权控制 in BitBrowser → Settings → Local API, then retry. |
| 这个窗口用的不是 Chrome（Chromium）内核，任务无法打开它；在比特浏览器里把这个窗口的内核切换为 Chrome（指纹 → 内核）后重试 | The profile runs a Firefox or other non-Chrome kernel. Switch it to Chrome. |
| The BitBrowser client opened this store but says it cannot be used right now. Check the store's status in the BitBrowser client, then click Retry. | Check the profile's status in BitBrowser, fix it, then click Retry. |
| This store was not found in the BitBrowser client. Check it still exists there and is visible to the signed-in account, then click Retry. | The profile may have been deleted, or the current account cannot see it. Check that it exists and is visible. |
| No visible profiles for this login — a sub-account needs profiles authorized by the main account. | This account has no profiles or none authorized. See the sub-account note above. |
| This browser doesn't support this system. | The BitBrowser backend supports Windows and macOS; WSL is also supported. |

## Where credentials are stored

The BitBrowser **account and password** are stored in your local `~/.vibe-seller/data/vibe_seller.db` (SQLite). The password is encrypted at rest with a key derived from this machine's install, and decrypted in memory only at the moment of an auto-login; the API never returns it to the frontend.

The local API port and token are read from BitBrowser's own config file (Windows: `%APPDATA%\BitBrowser\config.json`; macOS: `~/Library/Application Support/BitBrowser/config.json`) and not stored separately.
