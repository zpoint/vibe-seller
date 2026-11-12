---
description: Login and accounts — setting up or recovering the login password, and why there's no vibe-seller command on Windows.
---

# Login & Accounts

## How do I set up or recover the login password?

**There's no login by default.** A fresh install drops you straight into the main UI — login is opt-in and only activates after someone turns on **Require Login** in **Settings → Account**. If you're seeing a login page, someone (probably whoever set this machine up for you) enabled it.

The factory account is `admin` / `admin` (email `admin@vibe-seller.local`), but **those defaults only hold while the password has never been changed.** The moment someone changes it in **Settings → Account**, they're void — passwords are stored as one-way bcrypt hashes and can't be read back out of the database, and the username may have been renamed too (the admin account on your machine isn't necessarily called `admin`).

Not sure which state you're in? One command tells you:

```bash
vibe-seller reset-auth status
```

It reports whether login is required, whether the default password still works, which accounts can log in, and which one is the default administrator.

Locked out? Reset the password — with no arguments it prompts for the new one (what you type isn't echoed) and targets the default administrator account, whatever it's been renamed to:

```bash
vibe-seller reset-auth password
```

It writes straight to the database: no server restart, running tasks unaffected. Another account: `--user <name>`; rename the default admin while you're at it: `--rename`.

Note that **resetting the password does not log anyone out**: sessions ride on a JWT cookie in the browser (renewed on activity, up to 24 hours), and old sessions stay valid after a reset. To force everyone back to the login page: stop the server, delete `~/.vibe-seller/data/jwt_secret`, start it again.

!!! warning "Deleting that file has side effects"
    The same secret is used to encrypt the Ziniao, mailbox and Ads API credentials you've saved. After it's replaced, those passwords can't be decrypted and have to be entered again; task MCP tokens are invalidated too (tasks re-issue them). If the goal is just to lock one person out, changing their password and turning Require Login on is the safer move. Installs that pin the key with a `JWT_SECRET` environment variable should change that variable instead.

**Don't want the login page** (emergency off):

```bash
vibe-seller reset-auth disable    # turn the login page off
vibe-seller reset-auth enable     # and back on
```

With login off, anyone who can reach the port acts as the default administrator — fine on a trusted LAN, never on the public internet. Reload the browser page for it to take effect.

Running from a source checkout (no `vibe-seller` command)? Use `scripts/reset_auth.sh` (Windows: `scripts\reset_auth.bat`) — same arguments — or run `python -m app.auth_reset status` inside the checkout.

## Why is there no vibe-seller command on Windows?

Because the native installer doesn't put it on your PATH — and on Windows **you don't need it**: the tray owns the server (right-click the icon → Restart server / Quit), it starts when you log in, and upgrades run from tray → Check for updates.

If you do want the command line (for the `reset-auth` commands above, say), the executable is inside the install:

```powershell
& "$env:LOCALAPPDATA\Programs\VibeSeller\.venv\Scripts\vibe-seller.exe" reset-auth status
```

`vibe-seller start` / `stop` / `upgrade` live on that same file, but the installer handles autostart and upgrades on Windows, so you rarely reach for it.

The `vibe-seller` command is the entry point for the macOS / Linux (and WSL2 Ubuntu) install, where `install.sh` puts it in `~/.local/bin`.
