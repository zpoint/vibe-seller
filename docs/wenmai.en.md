---
description: One page for connecting Wenmai (稳卖) to Vibe Seller — download, create an enterprise sub-account with store permissions, bind a device to each store, what to fill in, one account per machine, and how to read the common statuses and errors.
---

# Wenmai (稳卖): Setup Guide

Wenmai (稳卖) is an e-commerce anti-detect browser ([official site](https://www.wenmai-ai.com)). Once bound, Vibe Seller opens and logs into the Wenmai client for you, then opens your store through it to run tasks — you never touch the client by hand.

**The Wenmai backend supports Windows and macOS; WSL is also supported.**

Not using Wenmai? Skip this page; Vibe Seller supports other browsers too — see [Browsers](browsers.en.md).

## Download and install

1. Open the official site <https://www.wenmai-ai.com>, download the client for your system (Windows / Mac) and install it. Any install folder works — Vibe Seller finds it on its own.
2. After installing, start and log into the Wenmai client once by hand to confirm the account works and you can see your store list. After that Vibe Seller launches and logs in for you.

## Create an enterprise account (and authorise stores)

Wenmai logs in with the **enterprise login**: company name + company username + company password. **Phone-number login is not supported** — use the enterprise login.

1. In the Wenmai client, as the admin, open **团队管理 → 成员** (Team management → Members) and create an enterprise sub-account.
2. Under **店铺权限** (store permissions), tick the stores this sub-account may operate. A sub-account only sees the stores authorised to it — authorise none and it lists zero stores in Vibe Seller. If devices (network environments) need their own grant, tick those here too.
3. Write down three things: the **company name**, the **company username**, and that **user's password**. That is what you fill into Vibe Seller.

## Bind a device to each store

Every store in Wenmai needs a device (network environment) bound before it can be opened. A store without one still shows in the list but reads "no device bound" and cannot be selected — bind a device to it in the Wenmai client, then refresh.

!!! warning "Stores must use a Chrome kernel"

    Set the store's browser kernel to **Chrome**: a store on a Firefox or other non-Chrome kernel cannot be opened by a task — see [Stores must use a Chrome kernel](browsers.en.md#stores-must-use-a-chrome-kernel).

## What to fill in when binding

When creating a store, pick Wenmai as the browser backend — the same form as Ziniao:

1. Click **[+]** next to the account row and fill in three fields:

| Field | What it is |
|---|---|
| Company name | Your Wenmai enterprise identifier |
| Company username | The sub-account's username |
| Company password | That sub-account's password |

2. After saving, Vibe Seller logs the client in with this enterprise login and loads the stores that account can see. If the client was closed or its session expired, it signs back in the same way, so a task never stalls on "not logged in".
3. Pick the one matching this store in **Select a store...** and click **Create**. The list shows "store · platform"; stores without a device are greyed out.

## One account per machine

Like Yuehulian, a machine runs ONE Wenmai account at a time, so the account row has **only a [+]** — no edit, no delete. Clicking [+] with a new account **overwrites** the stored one.

If any Wenmai store is already bound on this machine, adding a new account is refused (see the matching row under [Common statuses / errors](#common-statuses--errors) below). To switch accounts: delete every Wenmai store under Settings → Stores, then click [+] and bind the stores again. All Wenmai stores on this machine share the one account.

## Common statuses / errors

<details id="middle-page">
<summary>A store-info page appears when opening a store</summary>

Nothing to do — Vibe Seller clicks 打开店铺 automatically and enters the real store backend.
</details>

<details id="closed-mid-task">
<summary>Closed Wenmai mid-task / opened a store by hand</summary>

Nothing to do: the next browser use reopens the client and signs in; a store opened by hand is reused, and the browser is closed normally when the task ends.
</details>

<details id="not-logged-in">
<summary>Told the client is not logged in</summary>

No enterprise login configured, or auto-login failed. Check the company name / username / password in [+] and that the client can sign in by hand, then Refresh.
</details>

<details id="cannot-start">
<summary>Told the Wenmai client could not be started</summary>

Vibe Seller could not find or launch the client. Open the Wenmai client once by hand, or confirm the install per [Download and install](#download-and-install), then Refresh.
</details>

<details id="no-device">
<summary>Greyed-out rows in the store list</summary>

That store has no device bound in Wenmai (it reads "no device bound"). Bind one in the Wenmai client, then refresh.
</details>

<details id="firefox-kernel">
<summary>A store on a Firefox kernel cannot be opened by a task</summary>

The store runs a Firefox kernel in Wenmai, which tasks cannot open. Switch it to a Chrome kernel in the Wenmai client.
</details>

<details id="one-account">
<summary>Switching to another Wenmai account</summary>

See [One account per machine](#one-account-per-machine) above: delete every Wenmai store first, then add the new account.
</details>

<details id="unsupported-platform">
<summary>Unsupported platform</summary>

The Wenmai backend supports Windows and macOS; WSL is also supported.
</details>

<details id="where-credentials">
<summary>Where the credentials are stored</summary>

The enterprise login (company name / username / password) lives in your local `~/.vibe-seller/data/vibe_seller.db` (SQLite). The password is encrypted and only decrypted in memory at the moment of auto-login; the API never returns it to the frontend. Since a machine has one Wenmai account, the credentials are machine-level and shared by all Wenmai stores. For the crypto details see the [developer guide](dev-guide.md).
</details>

| Message | Meaning / what to do |
|---|---|
| Could not auto-sign-in to Wenmai — check that the company name / username / password saved here are correct (the client can sign in by hand), then Refresh. | The saved enterprise login is wrong, or the client cannot sign in. Check the three fields in [+] and that the client signs in by hand. |
| Log into the Wenmai client first, then click Refresh. | No enterprise login configured, or auto-login failed. See "Told the client is not logged in" above. |
| Couldn't start the Wenmai client — open it manually (or check it's installed), then click Refresh. | Vibe Seller could not find or launch the client. See "Told the Wenmai client could not be started" above. |
| no device bound | A greyed-out row in the store list: the store has no device bound in Wenmai. See "Greyed-out rows in the store list" above. |
| This store doesn't use a Chrome kernel, so it can't be automated. Switch its kernel to Chrome in the client. | See "A store on a Firefox kernel cannot be opened by a task" above. |
| This browser allows one account per machine. Delete every store created with it before changing the account. | See [One account per machine](#one-account-per-machine) above. |
| The store's Wenmai client is not signed in. Open the Wenmai client on this machine and sign in, then click Retry. | The client's login expired mid-task: open the client, sign in, and retry. |
| The Wenmai client is not running. Start the Wenmai client on this machine, then click Retry. | The client was closed mid-task: reopen it and retry. |
| The Wenmai client opened this store but says it cannot be used right now. Check the store's status in the Wenmai client, then click Retry. | Usually an unbound or disabled device: check the store's status in the Wenmai client. |
