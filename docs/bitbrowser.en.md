---
description: "One page on connecting BitBrowser to Vibe Seller: download, sign in once, nothing to fill in, the local API port and token read automatically, the workbench tab closed for you, any install location, and every error message."
---

# BitBrowser: Setup Guide

BitBrowser (比特浏览器) is a Chinese anti-detect browser ([site](https://www.bitbrowser.net/), [API docs](https://doc.bitbrowser.net/)). Once bound, Vibe Seller opens your stores through its local API and launches the client automatically when it is not running.

**The BitBrowser backend is Windows-only** (including WSL on Windows). A macOS client exists, but Vibe Seller does not drive it yet.

Not using BitBrowser? Skip this page — Vibe Seller supports other browsers, see [Browsers](browsers.en.md).

## Download and install

1. Download the Windows (64-bit) client from the [download page](https://www.bitbrowser.net/download/) and install it. The default location is `C:\Users\<user>\AppData\Local\Programs\bitbrowser`; any other location is found too.
2. **Start it and sign in once by hand**, and check that you can see your browser profiles.

## Sign in once — that is all

Vibe Seller stores **no BitBrowser credentials**. Sign in once in the client and it stays signed in — across restarts and even a forced kill — so tasks never fail over a missing login.

- You only sign in again when the session expires (the UI says "not logged in").
- Sub-accounts work: a sub-account sees its own profiles plus the ones the main account authorized to it. An empty list usually means "not authorized", not broken — ask the main account to grant profile permissions under 员工管理 (Users).
- The free plan includes 10 browser profiles, permanently.

## What to fill in when binding

Nothing. Create a store with the browser backend "BitBrowser":

1. The profile list loads automatically (as long as the client is signed in).
2. Pick this store's profile in the "Select store..." dropdown (shown as `seq + name`, e.g. `#1 test1`) and click Create.

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
- **Opened the store by hand in the client**: the task attaches to that already-open profile (same debug port) and closes its workbench tab; no close-and-reopen needed.

## Stores must use a Chrome kernel

As with every anti-detect browser, **Vibe Seller can only drive profiles on a Chrome (Chromium) kernel**; Firefox-kernel profiles expose no debug interface. In BitBrowser, set a profile's kernel to Chrome in its 指纹 (fingerprint) settings when creating or editing it.

Firefox-kernel profiles are greyed out with the reason in the store picker, and selecting one is refused. If a bound profile's kernel was changed, you do not need to rebind — just run the task again. See [Stores must use a Chrome kernel](browsers.en.md#stores-must-use-a-chrome-kernel).

## Common statuses / errors

| Message | Meaning / what to do |
|---|---|
| Not logged in — sign in to the BitBrowser client, then Refresh | The client's session expired. Open BitBrowser and sign in once; it stays signed in. |
| Could not start the BitBrowser client — open it by hand (or check the install), then Refresh | Vibe Seller could not find or launch the client. Open BitBrowser by hand; if it is installed somewhere unusual, save the client path in Settings. |
| BitBrowser Local API auth failed … toggle 鉴权控制 off and on | The client's Local API Token was reset and the config has not caught up. In BitBrowser → Settings → Local API, toggle 鉴权控制 off and on (or restart BitBrowser), then retry. |
| This store does not use a Chrome kernel | The profile runs a Firefox or other non-Chrome kernel. Switch it to Chrome in BitBrowser (see the section above). |
| Open failed: kernel update failed, please restart the client | BitBrowser failed to download a kernel (typically the Firefox one). Restart BitBrowser so it retries, then run the task. |
| Open failed: the service was called successfully but no matching data was found | The profile was deleted in BitBrowser, or the signed-in account cannot see it. Check the profile still exists and is visible. |
| No visible stores for this login | The signed-in (sub-)account has no profiles or none authorized to it. See the sub-account note above. |
| Unsupported platform | The BitBrowser backend is Windows-only (incl. WSL on Windows). |

## Where credentials are stored

**Nowhere.** The BitBrowser login lives in its own client; the local API port and token are read live from its config file (`%APPDATA%\BitBrowser\config.json`) — never saved or returned by Vibe Seller.
