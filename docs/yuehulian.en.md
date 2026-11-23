---
description: Yuehulian in Vibe Seller — download and install, create a team account, what to fill in when binding, one account per machine, auto-login, Windows-only, and where credentials are stored.
---

# Yuehulian: Setup Guide

Yuehulian (YueHuLian / 悦互联) is a Chinese anti-detect browser for
e-commerce sellers ([official site](https://kj.chinafic.com/)). Once a
store is bound, Vibe Seller opens and signs in to the Yuehulian client
itself and uses it to open your stores for tasks; you don't need to
operate the client by hand.

**The Yuehulian backend is Windows-only** (including WSL running on
Windows). On macOS / plain Linux the option does not appear in the
"Browser Backend" dropdown — the Yuehulian client itself is Windows-only.

Not using Yuehulian? Skip this whole page — pick "Chrome" or "Ziniao" as
the backend when you create a store.

## Download and install

1. Open the Yuehulian site <https://kj.chinafic.com/>, go to the client
   download on the home page, and install the Windows client. (The client
   is Windows-first; per the vendor a Mac build is still in development,
   and Vibe Seller's Yuehulian backend only talks to the Windows side.)
2. Launch and sign in to the Yuehulian client by hand at least once, so
   you can confirm the account works and your store list shows up. Vibe
   Seller will then auto-launch and auto-sign-in to this client (see
   "Auto-login" below).

Any install folder works: besides the default
`C:\Program Files (x86)\YueHuLian`, an install on another drive or in a
Chinese/space-containing folder (e.g. `D:\悦互联\用户端.exe`) is found
too — Vibe Seller checks the running client, the registry (App Paths /
Uninstall), Desktop and Start-Menu shortcuts, then the conventional
folders on every fixed drive, and validates the hit is the client's
main exe (`用户端.exe`), never an uninstaller or the `chrome.exe`
kernel. If nothing is found the error lists what was searched; open
the client by hand once and retry, or reinstall to the default folder.

## Create a team account

Yuehulian uses a **team login**: team name + sub-account + password.

1. Register an enterprise account on <https://kj.chinafic.com/>.
2. In the console / admin, create a sub-account for your operator under
   team (member) management and grant it per-store permissions —
   Yuehulian supports role authorization, device-login control, and
   multi-person collaboration.
3. Note three things: the **team name**, the **sub-account login**, and
   **that sub-account's password**. Those are the "team login" you enter
   in Vibe Seller.

## What to fill in when binding

When creating a store, pick "**Yuehulian**" as the browser backend and
bind it in the same form, just like Ziniao:

1. Click the "**+**" next to the account row and fill three fields, then
   save:

| Field | What it is |
|---|---|
| Team name | Your Yuehulian team identifier |
| Sub-account login | The login name of the sub-account under that team |
| Password | That sub-account's password |

2. After saving, Vibe Seller signs the client in with this team login and
   loads the stores under the account.
3. Pick the store this binding is for under "**Select a store...**" and
   hit "**Create**".

The password is encrypted on this machine and the API never returns it to
the frontend (see "Where credentials are stored" below).

## One account per machine

Unlike Ziniao, a machine can be signed in to exactly one Yuehulian
account at a time, so the account row has **only a
"+"** — no edit or delete. Clicking "+" and entering a new account
**overwrites** the current one.

If this machine already has Yuehulian stores bound, adding a new account
is refused with:

> 悦互联一台机器只支持一个账号，请先在设置页面删除所有悦互联店铺，再添加新账号。
> (Yuehulian supports only one account per machine — delete all Yuehulian
> stores in Settings first, then add the new account.)

To switch to a different Yuehulian account: delete every Yuehulian store
under `Settings → Stores` first, then click "+" to add the new account and
re-bind the stores. All Yuehulian stores on one machine share this one
account.

## Auto-login

If the client has exited or its session has expired, Vibe Seller signs
back in with your stored team login, so a task is never stranded on "not
logged in". You don't have to sign in to the Yuehulian client by hand each
time — just configure the team login once.

## Two browsers working together

Like a Ziniao store, a Yuehulian store's tasks also get a Chrome auxiliary
browser for the logistics / overseas sites Yuehulian can't open. See
[two browsers working together](browsers.en.md#store-browser-and-auxiliary-browser).

## Closing Yuehulian while a task runs

Nothing to do by hand. The next time the task uses the browser, Vibe Seller reopens the Yuehulian client, signs in and opens the store again. A client that was closed forcibly often still marks the store as open (its button reads Opened); Vibe Seller then closes it with the client's own "close browser" first, and opens it again.

Likewise, if you opened a store in the Yuehulian client yourself, a task that needs that store's browser closes it and opens it again (so Vibe Seller can drive it). A task that doesn't use the browser leaves it alone.

## Common statuses / errors

| UI message | Meaning / what to do |
|---|---|
| Not logged in — log into the 悦互联 client, then Refresh | No team login configured, or auto-login didn't take. Check the team name / sub-account / password in "+", and that the client can log in by hand. |
| Couldn't start the 悦互联 client — open it manually (or check it's installed), then Refresh | Vibe Seller couldn't find / launch the client. Open the Yuehulian client manually, or confirm it's installed per "Download and install". |
| No stores visible for this login | This sub-account has no stores yet, or no store permissions. Confirm in the Yuehulian admin that the sub-account can see stores. |
| Yuehulian supports only one account per machine… | See "One account per machine" above. |
| Unsupported platform | The Yuehulian backend is only available on Windows (including WSL-on-Windows). |

## Where credentials are stored

The team login (team name / sub-account / password) is saved in your local
`~/.vibe-seller/data/vibe_seller.db` (SQLite). The password is encrypted,
with a key derived from this machine's install, and is only decrypted in
memory at the moment of auto-login; the API never returns it to the
frontend. Because a machine has exactly one Yuehulian account, this
credential is machine-level and shared by all Yuehulian stores. For the
encryption details see the [developer guide](dev-guide.md).
