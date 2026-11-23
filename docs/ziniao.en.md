---
description: Ziniao with Vibe Seller — four steps in order to bind a store; which step each of the three common errors points back to; the WSL launcher, why Ziniao gets reopened, and where the credentials are stored.
---

# Ziniao: Setup & Common Errors

If your store lives in Ziniao, do the four steps below once and it's connected to Vibe Seller. The first two are done by Ziniao's **main (BOSS) account** on the Ziniao website, one time only.

If you don't use Ziniao, skip this page: pick "Chrome" when you create a store.

## Setup steps

1. **Main account: enable developer mode.** Sign in to the [Ziniao Open Platform](https://open.ziniao.com/docSupport?docId=99) with the main account and enable developer mode for your company, following the page's instructions.
2. **Main account: tick "WebDriver login" for the sub-account.** In the Open Platform console, tick WebDriver login for the sub-account that Vibe Seller will use, and save.

    ![Ziniao Open Platform — enable WebDriver login](images/ziniao_webdriver_login.png)

3. **Save what you're doing in Ziniao.** In the next step Vibe Seller closes Ziniao and reopens it; store windows you opened by hand close with it, and unsaved work is lost.
4. **Create the store in Vibe Seller:**
    1. In the sidebar click **+ New Store**, enter a **Store Name**, and pick **Ziniao (Browser Backend)**;
    2. Click the `+` next to the account field, fill in **Company / Username / Password** (the same ones you use to log into Ziniao), and save;
    3. Pick it under **Select Ziniao account...** and wait for the store list (reopening Ziniao takes a few seconds to a couple of minutes);
    4. Pick this store under **Select store profile...** and click **Create**.

That's it — you can create tasks in this store now. If step 4 shows an error, see [If you see an error](#if-you-see-an-error); each error points back to one of the steps above.

!!! note "Vibe Seller runs inside WSL?"
    Step 4 has one extra action: after you pick the Ziniao account, download `ziniao_webdriver.bat` as the app tells you, double-click it on Windows, then click **Refresh**. Details: [Windows (WSL): getting Ziniao into developer mode](#windows-wsl-getting-ziniao-into-developer-mode-ziniao_webdriverbat).

## If you see an error

### statusCode -10003

Developer mode isn't enabled for the company; this is Ziniao's own error code. Have the main account do **step 1** above, then click **Refresh** in Vibe Seller.

### "无权限通过 webdriver 登录" when binding a store

The main account hasn't ticked WebDriver login for this sub-account. Have the main account do **step 2** above (detailed walkthrough: [Ziniao's official guide](https://open.ziniao.com/docSupport?docId=99#%E7%AC%AC%E4%B8%80%E6%AD%A5%EF%BC%9A%E7%99%BB%E5%BD%95%E5%BC%80%E6%94%BE%E5%B9%B3%E5%8F%B0%E6%8E%A7%E5%88%B6%E5%8F%B00)); once it's saved, click **Refresh** in Vibe Seller and the store binds.

In the app this shows as "WebDriver is not enabled for this account." followed by "The BOSS account must enable WebDriver in the developer console.", with a **How to enable WebDriver** button that opens the official guide.

### "检测到您在新终端登录" when binding a store

Ziniao's new-device check: this account is logging in on this computer for the first time, and
Ziniao wants one manual approval first. Do this:

1. **Quit Ziniao**: right-click the Ziniao icon in the taskbar (the menu bar / Dock on a Mac) and quit;
2. **Open Ziniao yourself** by double-clicking it — that starts the Ziniao you use by hand, not
   the one Vibe Seller launches;
3. **Log in to the same account by hand**: Ziniao says it's a new device — submit the approval
   request it offers;
4. **Have the admin (BOSS) account approve** the device in the Ziniao back office;
5. **Once the login works**, go back to Vibe Seller and click **Refresh**.

From then on Ziniao trusts this computer and the prompt doesn't come back.

## Windows (WSL): getting Ziniao into developer mode (ziniao_webdriver.bat)

Ziniao is installed on the Windows side while Vibe Seller runs inside WSL, and WSL can't restart a Windows app across the VM boundary — so a launcher does it on Windows:

1. Once you pick the Ziniao account, a "**Ziniao is not running or not in WebDriver mode.**" banner appears — click "**Download ziniao_webdriver.bat**" inside it.
2. Double-click it on Windows.
3. Back in WSL, hit "**Refresh**" on the Ziniao page in Vibe Seller — your accounts pop in automatically, no manual fields to fill. The on-screen steps read "Steps: 1) Download the .bat file below 2) Double-click it on Windows 3) Wait until it shows "ready" 4) Click Refresh".

![Download ziniao_webdriver.bat](images/setup_store_wsl_en.png)

If a task starts before Ziniao is ready, the error reads "Ziniao is not running. Please download and run ziniao_webdriver.bat on Windows, then retry." When Ziniao is open but in normal mode, Vibe Seller closes it for you — but WSL can't open it again, so double-click the `.bat` once more. Full Windows + WSL2 deployment, including this backend, is in the [Windows setup guide](windows-setup.md).

## More details

How it works under the hood. You don't need any of this to follow the steps above.

### Picking a Ziniao account reopens Ziniao

Ziniao runs in exactly one mode at a time:

- **Normal mode** — what you get when you double-click the Ziniao icon. UI-only, designed for manual daily work. No remote control.
- **Developer mode (WebDriver)** — same Ziniao install, launched with extra flags so it exposes an HTTP API on `127.0.0.1:16851`. This is the only mode Vibe Seller (or any external tool) can drive.

The two modes share one Ziniao process, so they can't run side by side, and switching means restarting Ziniao. On macOS and native Windows, Vibe Seller does that itself: when you pick a Ziniao account while creating or editing a store (or click **Refresh**), and when a task first uses a Ziniao store's browser, if Ziniao isn't answering in developer mode (closed, opened by hand in normal mode, or leftovers from a crash) it closes every Ziniao process and relaunches it with `--run_type=web_driver` — no questions asked. A task that never opens the browser leaves Ziniao alone.

Reopening usually takes from a few seconds to a couple of minutes; the page shows how long it has waited, and the store list appears when Ziniao is ready. It waits up to 2 minutes, and if Ziniao doesn't answer, closes it and tries once more. A Ziniao already running in developer mode is not restarted. On Windows (WSL), Vibe Seller can't reopen Ziniao itself; follow the [launcher steps](#windows-wsl-getting-ziniao-into-developer-mode-ziniao_webdriverbat) instead.

### What you fill in when binding a store

| Field | What it is |
|---|---|
| Company | Your Ziniao enterprise company identifier |
| Username | The sub-account username under that company — Ziniao's own console calls it the "enterprise username" |
| Password | That sub-account's password |

Same credentials you use to log into Ziniao — Vibe Seller passes them to the developer-mode HTTP API to list your browser profiles and start sessions.

Why the password at all: developer-mode auth is plain account-and-password — no OAuth / token flow exists on the Ziniao side today. If that's a deal-breaker, use plain Chrome instead (new-store form, backend "Chrome").

### Any install folder works (Windows)

On Windows, Vibe Seller needs the full path of `ziniao.exe` to launch it with flags. The default `C:\Program Files\ziniao` works, and so does anywhere else you installed it — `D:\紫鸟\ziniao`, a folder with spaces or Chinese characters, another drive. The lookup order:

1. the install path of the Ziniao process that was alive just before launch (recorded before the auto-restart closes it);
2. the registry App Paths / Uninstall entries (`InstallLocation`, `DisplayIcon`, …);
3. what the Desktop and Start-Menu shortcuts point at (Ziniao's own shortcut points wherever you installed it);
4. the conventional folders on every fixed drive (`X:\Program Files\ziniao`, `X:\Program Files (x86)\ziniao`, `X:\ziniao`, …).

Every hit is validated to really be `ziniao.exe` — an uninstaller is never mistaken for the client. If nothing checks out, the error lists everything that was searched; **open Ziniao once by hand from its shortcut and retry** (the next start records where it lives), or reinstall to the default folder.

### The Chrome auxiliary browser

A Ziniao store's tasks also get a Chrome auxiliary browser for the logistics and overseas sites Ziniao can't open — see [Two browsers working together](browsers.md#store-browser-and-auxiliary-browser).

### Where the credentials are stored

Everything lives in `~/.vibe-seller/data/vibe_seller.db` (local SQLite). Nothing is uploaded, and no telemetry path carries credentials out.

- **Password** is encrypted at rest with a key derived from your local install — decrypted in memory only when Vibe Seller calls the Ziniao HTTP API. The field is a password box that never echoes it back, and normal endpoints never hand it out: the one way to recover it is an admin clicking "Show / Copy" inside **Edit Ziniao Account**; ordinary users don't get that control and an external agent holding an API key is refused.
- **Company** and **username** are plain strings — identifiers, not secrets (same shape as an email address).
- If you ever see "The stored password can't be decrypted on this machine…" (for example after the database was copied to another install), click "**Edit Ziniao Account**" and type the password again.

Contributors curious about the exact encryption implementation can check the [developer guide](dev-guide.md).
