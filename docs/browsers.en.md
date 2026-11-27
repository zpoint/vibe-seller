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
| Ziniao | Third-party anti-detect browser (developer / WebDriver mode, driven over CDP) | One team managing many accounts; strict risk control | macOS / Linux / Windows | [ziniao.com](https://www.ziniao.com) · [download](https://www.ziniao.com/download) | Register an enterprise account → BOSS enables developer mode on the [Open Platform](https://open.ziniao.com/docSupport?docId=99) and ticks WebDriver login for the sub-account | [Ziniao setup](ziniao.en.md) |
| Yuehulian | Chinese anti-detect e-commerce browser | One team managing many accounts (Windows) | Windows only (incl. WSL-on-Windows) | [kj.chinafic.com](https://kj.chinafic.com/) · client download on the site | Register an enterprise account → create sub-accounts and grant store permissions in the console (team login = team name + sub-account + password) | [Yuehulian setup](yuehulian.en.md) |
| BitBrowser | Chinese anti-detect browser (driven over its local API) | One team managing many accounts (Windows); the free plan works | Windows only (incl. WSL-on-Windows) | [bitbrowser.net](https://www.bitbrowser.net/) · [download](https://www.bitbrowser.net/download/) | Register an account (10 windows on the free plan); sub-accounts need windows authorized by the main account | [BitBrowser setup](bitbrowser.en.md) |

> Windows also has a fourth backend, `winchrome` (set at deployment, not a
> choice in the store form) — see the end of "Which browser backend?".

## Stores must use a Chrome kernel

Anti-detect browsers such as Ziniao, Yuehulian and Wenmai usually let you choose a browser kernel per store. **Vibe Seller can only drive stores that use a Chrome (Chromium) kernel.** Stores on a Firefox kernel, or any other non-Chrome kernel, are not supported: a task cannot open them and reports that the store does not use a Chrome kernel.

Before you bind a store, open its settings in the anti-detect browser and set the browser kernel to Chrome (some apps call it Chromium or "Chrome kernel"). If the store is already bound, you do not need to bind it again after changing the kernel; just run the task again.

## Which browser backend?

| | Ziniao (Browser Backend) | Yuehulian (Browser Backend) | Chrome |
|---|---|---|---|
| What it is | A third-party anti-detect fingerprint browser; driven over CDP through its developer (WebDriver) mode | A Chinese anti-detect e-commerce browser; Vibe Seller opens and signs in to its client itself | The system's own Chrome / Chromium, launched by Playwright with a profile dedicated to this store |
| Good for | One team managing many accounts that must not share fingerprints; strict risk control | One team managing many accounts (Windows) | Running a single store yourself, no extra software; public-web work |
| What you need | A Ziniao enterprise account whose BOSS enabled developer mode | A Yuehulian team account (team + sub-account + password) | Nothing extra; on a headless Linux box you need a virtual display such as Xvfb |
| Where logins live | Inside Ziniao's own profile — Vibe Seller only opens and drives it | Inside the Yuehulian client (Vibe Seller auto-signs-in) | In this store's own Chrome profile directory, kept across tasks |
| Several stores at once | A machine can connect to only one Ziniao account at a time | One Yuehulian account per machine (stricter: a second account can't even be added) | No such limit |
| Platforms | macOS / Linux / Windows; Windows via WSL needs a one-line `.bat` launcher (see [Ziniao: Setup & Common Errors](ziniao.en.md)) | Windows only (incl. WSL-on-Windows) | macOS / Linux / WSLg; under a Windows service, use native Windows Chrome instead (next paragraph) |

Ziniao is a third-party anti-detect browser; Vibe Seller drives it over CDP
through its developer (WebDriver) mode. Fingerprints are isolated between
Ziniao accounts, which suits stores on marketplaces with strict risk
control. What to enable and how to handle errors: [Ziniao: Setup & Common
Errors](ziniao.en.md).

Yuehulian is an anti-detect browser like Ziniao. Vibe Seller opens and
signs in to its client itself, and it is **Windows-only**; a machine keeps a single Yuehulian account and
auto-signs-in when the session expires. Download, team-account creation,
binding and errors: [Yuehulian: Setup Guide](yuehulian.en.md).

BitBrowser is an anti-detect browser driven through its own local API,
**Windows-only**. Sign in once in its client — Vibe Seller stores no
credentials and reads the API port and token from its settings
automatically. Download, binding and errors: [BitBrowser: Setup
Guide](bitbrowser.en.md).

The Chrome backend has Playwright bring up a profile dedicated to the
store — no extra software, and no handing the system an anti-detect
browser password; on a headless Linux box you need a virtual display such
as Xvfb.

**Windows has a fourth backend, `winchrome`.** A Vibe Seller running as a
systemd service inside WSL2 can't put a Chrome window on screen, so it can
drive **native Windows Chrome** instead — launched through Task Scheduler,
window visible, so a human can finish a login. It isn't a choice in the
store form; you set it up at deployment time. See the "Browser automation —
native Windows Chrome (`winchrome` backend)" section of the [Windows setup
guide](windows-setup.md).

## Per-browser setup guides

- [Ziniao: Setup & Common Errors](ziniao.en.md) — enable developer mode,
  binding, errors, the WSL launcher, credential storage.
- [BitBrowser: Setup Guide](bitbrowser.en.md) — download, sign in once,
  binding, port and token read automatically, Windows-only.
- [Yuehulian: Setup Guide](yuehulian.en.md) — download, create a team
  account, binding, one account per machine, auto-login, Windows-only.
- Chrome — no extra setup; pick "Chrome" as the backend when you create a
  store, see [Stores & Browsers → Creating a store](stores.en.md#creating-a-store).
