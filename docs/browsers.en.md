---
description: Which browsers Vibe Seller supports and how to choose. The "store browser + auxiliary browser" design, and Ziniao and Yuehulian setup, each have their own page.
---

# Browsers

Vibe Seller drives a **real browser** over CDP (Chrome's low-level
remote-control protocol) — the AI acts on the page exactly as you would.
Each store binds one browser backend; an anti-detect store (Ziniao /
Yuehulian) also gets a Chrome auxiliary browser automatically. This page
covers which browsers are supported and how to choose. How the two
browsers split the work is in [Store Browser & Auxiliary
Browser](store-and-aux-browser.en.md); each browser's download and setup
lives in its own guide.

## Supported browsers

Three browser backends are supported (plus a Windows-native Chrome set up
only at deployment). Download and team-account registration go through each
vendor's own site:

| Backend | What it is | Good for | Platforms | Site / download | Create a team account | Setup guide |
|---|---|---|---|---|---|---|
| Chrome | The system's own Chrome / Chromium (launched by Playwright) | Running a single store yourself; public-web work | macOS / Linux / WSLg | Built in, no install | — | [Stores & Browsers → Creating a store](stores.en.md#creating-a-store) |
| Ziniao | Third-party anti-detect browser (developer / WebDriver mode, driven over CDP) | One team managing many accounts; strict risk control | Windows / macOS / Linux, plus WSL | [ziniao.com](https://www.ziniao.com) · [download](https://www.ziniao.com/download) | Register an enterprise account → BOSS enables developer mode on the [Open Platform](https://open.ziniao.com/docSupport?docId=99) and ticks WebDriver login for the sub-account | [Ziniao setup](ziniao.en.md) |
| Yuehulian | Chinese anti-detect e-commerce browser | One team managing many accounts | Windows / macOS / WSL | [kj.chinafic.com](https://kj.chinafic.com/) · client download on the site | Register an enterprise account → create sub-accounts and grant store permissions in the console (team login = team name + sub-account + password) | [Yuehulian setup](yuehulian.en.md) |
| BitBrowser | Chinese anti-detect browser | One team managing many accounts; the free plan works | Windows and macOS; WSL is also supported | [bitbrowser.net](https://www.bitbrowser.net/) · [download](https://www.bitbrowser.net/download/) | Register an account (10 profiles on the free plan); sub-accounts need profiles authorized by the main account | [BitBrowser setup](bitbrowser.en.md) |

> WSL deployments also have a fourth backend, `winchrome` (set at deployment, not a
> choice in the store form) — see the end of "Which browser backend?".

## Stores must use a Chrome kernel

Anti-detect browsers such as Ziniao, Yuehulian and Wenmai usually let you choose a browser kernel per store. **Vibe Seller can only drive stores that use a Chrome (Chromium) kernel.** Stores on a Firefox kernel, or any other non-Chrome kernel, are not supported: a task cannot open them and reports that the store does not use a Chrome kernel.

Before you bind a store, open its settings in the anti-detect browser and set the browser kernel to Chrome (some apps call it Chromium or "Chrome kernel"). If the store is already bound, you do not need to bind it again after changing the kernel; just run the task again.

## Which browser backend?

| | Ziniao (Browser Backend) | Yuehulian (Browser Backend) | Chrome |
|---|---|---|---|
| What it is | A third-party anti-detect fingerprint browser; driven over CDP through its developer (WebDriver) mode | A Chinese anti-detect e-commerce browser; Vibe Seller opens and signs in to its client itself | The system's own Chrome / Chromium, launched by Playwright with a profile dedicated to this store |
| Good for | One team managing many accounts that must not share fingerprints; strict risk control | One team managing many accounts | Running a single store yourself, no extra software; public-web work |
| What you need | A Ziniao enterprise account whose BOSS enabled developer mode | A Yuehulian team account (team + sub-account + password) | Nothing extra; on a headless Linux box you need a virtual display such as Xvfb |
| Where logins live | Inside Ziniao's own profile — Vibe Seller only opens and drives it | Inside the Yuehulian client (Vibe Seller auto-signs-in) | In this store's own Chrome profile directory, kept across tasks |
| Several stores at once | A machine can connect to only one Ziniao account at a time | One Yuehulian account per machine (stricter: a second account can't even be added) | No such limit |
| Platforms | Windows / macOS / Linux, plus WSL; WSL needs a one-line `.bat` launcher (see [Ziniao: Setup & Common Errors](ziniao.en.md)) | Windows / macOS / WSL | macOS / Linux, plus WSL (WSLg); under a Windows service, use native Windows Chrome instead (next paragraph) |

Ziniao is a third-party anti-detect browser; Vibe Seller drives it over CDP
through its developer (WebDriver) mode. Fingerprints are isolated between
Ziniao accounts, which suits stores on marketplaces with strict risk
control. What to enable and how to handle errors: [Ziniao: Setup & Common
Errors](ziniao.en.md).

Yuehulian is an anti-detect browser like Ziniao. Vibe Seller opens and
signs in to its client itself, on **Windows and macOS, plus WSL**; a machine keeps a single Yuehulian account and
auto-signs-in when the session expires. Download, team-account creation,
binding and errors: [Yuehulian: Setup Guide](yuehulian.en.md).

BitBrowser is an anti-detect browser, on **Windows and macOS; WSL is
also supported**. Vibe Seller can store the BitBrowser account and
password and signs in again when the session expires (one account on one
computer at a time). Download, binding and errors:
[BitBrowser: Setup Guide](bitbrowser.en.md).

The Chrome backend has Playwright bring up a profile dedicated to the
store — no extra software, and no handing the system an anti-detect
browser password; on a headless Linux box you need a virtual display such
as Xvfb.

**WSL deployments have a fourth backend, `winchrome`.** A Vibe Seller running as a
systemd service inside WSL2 can't put a Chrome window on screen, so it can
drive **native Windows Chrome** instead — launched through Task Scheduler,
window visible, so a human can finish a login. It isn't a choice in the
store form; you set it up at deployment time. See the "Browser automation —
native Windows Chrome (`winchrome` backend)" section of the [Windows setup
guide](windows-setup.md).

## A task cannot open the store: error quick reference

A task opens the store's browser first. When that fails, the task ends
with a red banner saying why (the AI gets the same message and is
stopped — it does not keep trying on its own). Match the banner text:

<details markdown="1" id="error-login-required"><summary><b>"The browser client is signed out"</b></summary>

The browser client the store lives in (Ziniao, Yuehulian and the like)
was signed out, and no saved login exists to sign it back in
automatically. Open that client on this machine, sign in once, then
retry the task.

</details>

<details markdown="1" id="error-client-not-running"><summary><b>"The browser client is not running"</b></summary>

The browser client the store lives in isn't open, or is still starting.
Open it and retry the task; if it keeps happening, restart the client.

</details>

<details markdown="1" id="error-account-mismatch"><summary><b>"The client is signed into a different account than the saved one"</b></summary>

The client is signed into a different account than the one saved when
the store was bound (a teammate may have switched accounts). Sign out
in the client and sign back into the account used at binding time; or
delete the store here and re-bind it under the current account.

</details>

<details markdown="1" id="error-api-not-enabled"><summary><b>"The client's local API is switched off"</b></summary>

These clients ship a "local API" for automation, usually switched off
by default. Turn it on in the client's settings (look for API /
automation / local API), then retry the task.

</details>

<details markdown="1" id="error-plan-required"><summary><b>"The client's local API needs a paid plan"</b></summary>

This browser's local API is a paid feature the current account's plan
does not include. Upgrade the account on the vendor's site (or pick a
plan with API access), then retry the task.

</details>

<details markdown="1" id="error-store-unavailable"><summary><b>"The client opened this store but says it cannot be used"</b></summary>

The store exists in the client but cannot be opened — for example no
device is bound to it, or it is expired or disabled. The banner quotes
the client's own words; open the client, find the store, fix what it
asks for and retry.

</details>

<details markdown="1" id="error-store-not-found"><summary><b>"The store was not found in the browser client"</b></summary>

The store bound here no longer exists in the client (deleted, or not
visible to the signed-in account). Check it is still there and visible;
if not, delete the store in Vibe Seller and bind it again.

</details>

## Per-browser setup guides

- [Ziniao: Setup & Common Errors](ziniao.en.md) — enable developer mode,
  binding, errors, the WSL launcher, credential storage.
- [BitBrowser: Setup Guide](bitbrowser.en.md) — download, sign in once,
  binding, Windows and macOS.
- [Yuehulian: Setup Guide](yuehulian.en.md) — download, create a team
  account, binding, one account per machine, auto-login, Windows and
  macOS plus WSL.
- Chrome — no extra setup; pick "Chrome" as the backend when you create a
  store, see [Stores & Browsers → Creating a store](stores.en.md#creating-a-store).
