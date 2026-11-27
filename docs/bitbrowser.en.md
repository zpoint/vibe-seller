---
description: "One page on connecting BitBrowser to Vibe Seller: download, the account and auto-login, what to fill in, the local API port and token read automatically, the workbench tab closed for you, any install location, and every error message."
---

# BitBrowser: Setup Guide

BitBrowser (比特浏览器) is a Chinese anti-detect browser ([site](https://www.bitbrowser.net/), [API docs](https://doc.bitbrowser.net/)). Once bound, Vibe Seller opens your stores through its local API and launches the client automatically when it is not running.

**The BitBrowser backend runs on Windows, macOS, and also WSL.**

Not using BitBrowser? Skip this page — Vibe Seller supports other browsers, see [Browsers](browsers.en.md).

## Download and install

- **Windows**: download the Windows (64-bit) client from the [download page](https://www.bitbrowser.net/download/) and install it. The default location is `C:\Users\<user>\AppData\Local\Programs\bitbrowser`; any other location is found too.
- **macOS**: download the macOS (Apple Silicon or Intel) client from the same page and drop it into `/Applications` (`比特浏览器.app`). Vibe Seller finds it by its app identity; for a non-standard location, save the client path in Settings.

**Start it and sign in once by hand**, and check that you can see your browser profiles.

## The account and auto-login

Save your BitBrowser **account and password** in Vibe Seller: when the session expires, Vibe Seller signs in again automatically, so tasks never fail over a missing login. The password is stored encrypted on this machine (see "Where credentials are stored" below).

- The client stays signed in across restarts and even a forced kill; auto-login only runs when the session actually expires.
- **One BitBrowser account is signed in on one computer at a time.** On a device kick (the same account signed in elsewhere) Vibe Seller does **not** auto-login — two computers would kick each other; it asks you to sign in again or to use the account on only one computer.
- If the account requires a new-device verification (SMS / email code / 2FA), Vibe Seller cannot complete it; finish it in BitBrowser by hand, then retry.
- Sub-accounts work: a sub-account sees its own profiles plus the ones the main account authorized to it. An empty list usually means "not authorized", not broken — ask the main account to grant profile permissions under 员工管理 (Users).
- The free plan includes 10 browser profiles, permanently.

## What to fill in when binding

Create a store with the browser backend "BitBrowser":

1. Click [+] next to the account row, fill in the **BitBrowser account and password** and save (one account per machine; adding again overwrites it).
2. The profile list loads automatically (as long as the client is signed in).
3. Pick this store's profile in the "Select store..." dropdown (shown as `seq + name`, e.g. `#1 test1`) and click Create.

Binding uses the profile's internal ID (not its name), so renaming a profile in BitBrowser does not break a bound store.

## Port and auth: read automatically

The local API address and the auth switch live in BitBrowser's own settings (Settings → Local API). Vibe Seller **reads them from the client's config file**:

- Change the Local API port and Vibe Seller picks up the new one on the next connect; nothing to rebind.
- With 鉴权控制 (auth control) enabled, Vibe Seller sends the token automatically — no copy-pasting.

> Tip: BitBrowser's local API listens on every network interface. On a shared network, enable 鉴权控制 in its settings so other devices on the LAN cannot drive your BitBrowser.

## The workbench tab when a profile opens

Opening a profile in BitBrowser shows a workbench tab (IP and fingerprint info) next to the store's own page. Vibe Seller **closes the workbench tab automatically** before a task starts and focuses the store page. Your profile settings are never touched — when you open a profile by hand in the client, the workbench tab still shows.

## Closed the client / opened a store by hand

- **Killed BitBrowser mid-task**: nothing to do. The next browser use relaunches the client (it comes back signed in) and reopens the store. The store browser dies with the client — no orphan processes to clean up.
- **Opened the store by hand in the client**: when Vibe Seller has no old session for the store, the task attaches to that already-open profile (same debug port) and closes its workbench tab. But if an old session is still recorded (for example the window was closed by hand after the last task), Vibe Seller closes and reopens the profile first — the hand-opened window and its page state are lost.

## Stores must use a Chrome kernel

As with every anti-detect browser, **Vibe Seller can only drive profiles on a Chrome (Chromium) kernel**; Firefox-kernel profiles expose no debug interface. In BitBrowser, set a profile's kernel to Chrome in its 指纹 (fingerprint) settings when creating or editing it.

Firefox-kernel profiles are greyed out with the reason in the store picker, and selecting one is refused. If a bound profile's kernel was changed, you do not need to rebind — just run the task again. See [Stores must use a Chrome kernel](browsers.en.md#stores-must-use-a-chrome-kernel).

## Common statuses / errors

| Message | Meaning / what to do |
|---|---|
| Not logged in — sign in to the BitBrowser client, then Refresh | The session expired and no account + password are saved (or the auto-login did not take). Check the saved credentials in Settings, or sign in by hand once. |
| 比特浏览器未登录且没有保存的账号密码。请在设置里填写…… ("BitBrowser is not signed in and no account + password are saved; fill them in in Settings…") | The session expired with no saved credentials to auto-login with. Save the BitBrowser account and password (see "The account and auto-login"), or sign in by hand once. |
| 自动登录没有成功，请手动打开比特浏览器登录一次，然后重试任务（……） ("The auto-login did not succeed; open BitBrowser and sign in by hand once, then retry the task (…)") | The auto-login ran but did not take — the login form's inputs were not found, the inspection timed out, etc. (the code in parentheses says which). Sign in by hand once, then retry the task. |
| 这个比特浏览器账号在另一台电脑登录了，本机已退出…… ("This BitBrowser account was signed in on another computer; this one is signed out") | A device kick: the same account signed in elsewhere. Vibe Seller will not auto-login (the two computers would kick each other). Sign in again in BitBrowser, or use the account on only one computer. |
| 比特浏览器账号或密码不对，请在设置里重新填写 ("The BitBrowser account or password is wrong; re-enter it in Settings") | The saved credentials were rejected. Check and re-enter them in Settings. |
| 需要在比特浏览器里完成登录验证（短信/邮箱/二次验证），完成后重试 ("Complete the login verification in BitBrowser — SMS/email/2FA — then retry") | The account needs a new-device verification. Finish it in BitBrowser by hand, then retry the task. |
| Could not start the BitBrowser client — open it by hand (or check the install), then Refresh | Vibe Seller could not find or launch the client. Open BitBrowser by hand; if it is installed somewhere unusual, save the client path in Settings. |
| 请在比特浏览器 → 设置 → Local API 里检查「鉴权控制」，再重试任务。 ("Check 鉴权控制 in BitBrowser → Settings → Local API, then retry the task") | The local API's auth toggle or token is off. Check the 鉴权控制 toggle in BitBrowser → Settings → Local API, then retry the task. |
| 这个窗口用的不是 Chrome（Chromium）内核…… ("This profile does not use a Chrome (Chromium) kernel") | The profile runs a Firefox or other non-Chromium kernel. Switch it to Chrome in BitBrowser (see the section above). |
| Open failed: kernel update failed, please restart the client | BitBrowser failed to download a kernel (typically the Firefox one). Restart BitBrowser so it retries, then run the task. |
| Open failed: the service was called successfully but no matching data was found | The profile was deleted in BitBrowser, or the signed-in account cannot see it. Check the profile still exists and is visible. |
| No visible stores for this login | The signed-in (sub-)account has no profiles or none authorized to it. See the sub-account note above. |
| Unsupported platform | The BitBrowser backend runs only on Windows, macOS, and WSL. |

## Where credentials are stored

The BitBrowser **account and password** are stored in your local `~/.vibe-seller/data/vibe_seller.db` (SQLite). The password is encrypted at rest with a key derived from this machine's install, and decrypted in memory only at the moment of an auto-login; the API never returns it to the frontend. The local API port and token are read live from BitBrowser's own config file (Windows: `%APPDATA%\BitBrowser\config.json`; macOS: `~/Library/Application Support/BitBrowser/config.json`) and not stored separately.
