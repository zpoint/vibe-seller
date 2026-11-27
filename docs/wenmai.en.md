---
description: One page for connecting Wenmai (稳卖) to Vibe Seller — download, create an enterprise sub-account with store permissions, bind a device to each store, what to fill in, one account per machine, auto-login, Windows only, and what happens mid-task.
---

# Wenmai (稳卖): Setup Guide

Wenmai (稳卖) is an e-commerce anti-detect browser ([official site](https://www.wenmai-ai.com)). Once bound, Vibe Seller opens and logs into the Wenmai client for you, then opens your store through it to run tasks — you never touch the client by hand.

**The Wenmai backend runs in two modes**: Windows (recommended — the server and the browser both run on Windows directly) and WSL (for users running WSL on Windows; the Wenmai client stays on the Windows side). macOS support is being verified; plain Linux is not supported — the Wenmai client itself ships for Windows and Mac only.

Not using Wenmai? Skip this page; Vibe Seller supports other browsers too — see [Browsers](browsers.en.md).

## Download and install

1. Open the official site <https://www.wenmai-ai.com>, download the Windows client and install it. Any install folder works — Vibe Seller finds it on its own (it checks the registry, Start-Menu shortcuts and the usual install locations).
2. After installing, start and log into the Wenmai client once by hand to confirm the account works and you can see your store list. After that Vibe Seller launches and logs in for you (see [Auto-login](#auto-login)).

## Create an enterprise account (and authorise stores)

Wenmai logs in with the **enterprise login**: company name + company username + company password. **Phone-number login is not supported** — that tab has an image captcha a machine cannot solve.

1. In the Wenmai client, as the admin, open **团队管理 → 成员** (Team management → Members) and create an enterprise sub-account.
2. Under **店铺权限** (store permissions), tick the stores this sub-account may operate. A sub-account only sees the stores authorised to it — authorise none and it lists zero stores in Vibe Seller.
3. Write down three things: the **company name**, the **company username**, and that **user's password**. That is what you fill into Vibe Seller.

## Bind a device to each store

Every store in Wenmai needs a device (network environment) bound before it can be opened. A store without one still shows in the list but reads "no device bound" and cannot be selected — bind a device to it in the Wenmai client, then refresh.

Also set the store's browser kernel to **Chrome**: a store on a Firefox or other non-Chrome kernel cannot be opened by a task — see [Stores must use a Chrome kernel](browsers.en.md#stores-must-use-a-chrome-kernel).

## What to fill in when binding

When creating a store, pick Wenmai as the browser backend — the same form as Ziniao:

1. Click **[+]** next to the account row and fill in three fields:

| Field | What it is |
|---|---|
| Company name | Your Wenmai enterprise identifier |
| Company username | The sub-account's username |
| Company password | That sub-account's password |

2. After saving, Vibe Seller logs the client in with this enterprise login and loads the stores that account can see.
3. Pick the one matching this store in **Select a store...** and click **Create**. The list shows "store · platform"; stores without a device are greyed out.

The password is stored encrypted on this machine and never returned to the frontend (see [Where the credentials are stored](#where-the-credentials-are-stored)).

## One account per machine

Like Yuehulian, a machine runs ONE Wenmai account at a time, so the account row has **only a [+]** — no edit, no delete. Clicking [+] with a new account **overwrites** the stored one.

If any Wenmai store is already bound on this machine, adding a new account is refused with:

> 稳卖一台机器只支持一个账号，请先在设置页面删除所有稳卖店铺，再添加新账号。
> (Wenmai supports one account per machine. Delete all Wenmai stores in Settings, then add the new account.)

To switch accounts: delete every Wenmai store under Settings → Stores, then click [+] and bind the stores again. All Wenmai stores on this machine share the one account.

If the client is currently logged into a **different** account than the saved one (someone switched it by hand), binding and tasks report "the client is logged into a different account" and never log the other session out — log out in the Wenmai client, or re-save the matching credentials.

## Auto-login

If the client was closed or its session expired, Vibe Seller logs back in with the saved enterprise login, so a task never stalls on "not logged in". Configure the enterprise login once and you never log into the Wenmai client by hand.

## The store-entry page is handled for you

Opening a store in Wenmai first shows a store-info page whose **打开店铺** button leads to the real store backend. Vibe Seller clicks it automatically — neither the task nor the AI ever sees or clicks that step.

## Closing Wenmai mid-task

Nothing to fix. The next time a task uses the browser, Vibe Seller relaunches the Wenmai client, logs in, and opens the store again. A store you opened by hand is fine too — every Wenmai store kernel exposes a debug port, so Vibe Seller attaches to the same session instead of opening a second one.

A store browser you closed by hand reopens the same way. When a task ends, Vibe Seller closes the store browser normally, and the client's running-store list matches reality.

## Common statuses / errors

| Message | Meaning / what to do |
|---|---|
| Not logged in — log into the 稳卖 client, then Refresh | No enterprise login configured, or auto-login failed. Check the company name / username / password in [+] and that the client can log in by hand. |
| Couldn't start the 稳卖 client — open it manually (or check it's installed), then Refresh | Vibe Seller could not find / launch the client. Open the Wenmai client once by hand, or confirm the install per [Download and install](#download-and-install). |
| The 稳卖 client is logged into a different account | The client's session is not the saved account. Log out in the client, or re-save the matching credentials. |
| 当前店铺未绑定可用设备，无法启动 — 在稳卖客户端给这个店铺绑定设备 | The store has no device bound in Wenmai. Bind one in the Wenmai client, then retry. |
| …Firefox kernel… only Chromium-kernel stores are supported | The store runs a Firefox kernel in Wenmai, which exposes no debug port. Switch it to a Chromium kernel in the Wenmai client. |
| 稳卖一台机器只支持一个账号… | See [One account per machine](#one-account-per-machine). |
| Unsupported platform | The Wenmai backend currently runs in two modes: Windows (native) and WSL; macOS is being verified, plain Linux is not supported. |

## Where the credentials are stored

The enterprise login (company name / username / password) lives in your local `~/.vibe-seller/data/vibe_seller.db` (SQLite). The password is encrypted with a key derived from this machine's installation and only decrypted in memory at the moment of auto-login; the API never returns it to the frontend. Since a machine has one Wenmai account, the credentials are machine-level and shared by all Wenmai stores. For the crypto details see the [developer guide](dev-guide.md).
