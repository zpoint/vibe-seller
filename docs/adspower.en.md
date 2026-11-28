---
description: AdsPower in Vibe Seller — download and install, API permission, generate the API key, what to fill in when binding, one login per machine, auto-start, automatic port discovery, the SunBrowser (Chromium) kernel requirement, and where credentials are stored.
---

# AdsPower: Setup Guide

[AdsPower](https://www.adspower.net) is an anti-detect browser. Once a
store is bound, Vibe Seller drives it through AdsPower's
own local API and uses it to open your profiles for tasks; you don't
need to operate the client by hand.

**The AdsPower backend works on Windows and macOS (WSL is also supported).**

Not using AdsPower? Skip this page. Vibe Seller supports other
browsers too; see [Browsers](browsers.md).

## Download and install

1. Open <https://www.adspower.net/download>, download the Windows
   client (AdsPower Global) and install it. Any install folder works —
   Vibe Seller finds it; if it ever can't, you can point the setting
   at the install location by hand.
2. Launch and sign in to the client at least once, so you can confirm
   the account works and your profile list shows up.

## Paid plan and API permission

AdsPower's local API requires a **paid plan**; team members do not
have API permission by default — an admin must grant it in the team
settings (super admins have it by default). If API calls fail with a
permission error, ask your team admin to enable the "API" permission.

A sub-account sees only the profiles **authorized to it**: an empty
profile list usually means "not authorized", not broken. Assign the
profiles in AdsPower's team management.

## Generate the API key

With "security verification" on, every API call needs an API key
(one per member, all different):

1. Open **"API & MCP"** in the AdsPower client's left menu.
2. Confirm the API status shows **Success**.
3. Click **"Generate Key"** and copy the key.

Two things to remember:

- **The key belongs to the account currently signed in to AdsPower.**
  Signing into a different account invalidates the old key ("API Key
  mismatch"); resetting the key in the client invalidates it
  immediately.
- The key is only shown in the client; reset it if you lose it.

## One AdsPower login per machine

The API key must match the account signed in to the AdsPower client: one AdsPower login per machine, shared by every AdsPower store. To switch accounts: delete every AdsPower store first, then generate the new key and re-bind.

## What to fill in when binding

Pick "AdsPower" as the browser backend for the new store:

| Field | What it is |
|---|---|
| API Key | The key generated above |

After saving, Vibe Seller loads the signed-in account's profile list
(shown as "number + name + group"); pick the one this store uses in
the "Select profile..." dropdown and click "Create".

**One AdsPower login per machine**: the key must match the account
signed in to the client, so every AdsPower store shares that one key.

## Profiles must use the Chrome kernel (important)

As with other anti-detect browsers, **Vibe Seller can only drive
SunBrowser (Chromium-kernel) profiles**. AdsPower also offers
FlowerBrowser (a Firefox kernel); those profiles expose no CDP
endpoint and cannot be opened by tasks — the task tells you to switch
the kernel.

- When **creating a profile** in AdsPower, choose **SunBrowser** under
  "浏览器" (Browser). Do not pick FlowerBrowser / Beta / SunBrowser
  Flash.
- For a profile that already uses the FlowerBrowser (Firefox) kernel:
  AdsPower's API refuses to change an existing profile's kernel ("The
  browser kernel cannot be updated"), so the **reliable fix is a NEW
  profile with 浏览器 set to SunBrowser**, then move the store account
  over.
- After switching to the new profile, **re-bind the store**: the new
  profile has a new Profile ID, so update the binding in the store's
  settings. This differs from the [browsers chapter](browsers.en.md#stores-must-use-the-chrome-kernel)'s
  general "no need to re-bind after changing the kernel" — that advice
  is for apps where the kernel CAN be changed; AdsPower's cannot.

See [Browsers → Stores must use the Chrome kernel](browsers.en.md#stores-must-use-the-chrome-kernel).

## Auto-start and sign-in

- Client not running: Vibe Seller launches the AdsPower client (the
  GUI). The client remembers the sign-in, so the API is available
  right after.
- Client running but **signed out** (sitting on its login page): Vibe
  Seller does not sign in for you — it shows
  ["The AdsPower client needs you to sign in"](#the-adspower-client-needs-you-to-sign-in)
  and someone has to sign in once in the AdsPower window.
- Signing in through the AdsPower window itself (typing the account
  password in the client) can ask for a verification code (a
  new-device check); for a sub-account that cannot receive the code,
  see ["A sub-account never receives the verification code"](#a-sub-account-never-receives-the-verification-code).

**Security note**: AdsPower's own client serves its local API on
**all network interfaces** (that is AdsPower's design, not Vibe
Seller's) — keep this machine's firewall on.

## The API port is discovered automatically

The AdsPower local API's port **can change on every launch** (the
default is 50325, with automatic fallback when it is taken). You never
configure a port: Vibe Seller reads AdsPower's own port file before
every connection and finds the current API by itself.

## "Does not have this profile's browser kernel" message

When a task reports AdsPower does not have the profile's browser
kernel (e.g. SunBrowser 156) and that it does not download by itself:
this machine's AdsPower does not have that kernel installed, and it
will NOT fetch it in the background (verified live on macOS). Fix it
by:

1. opening the AdsPower app and downloading that kernel in its
   settings (the kernel-management page);
2. or switching the profile to an already-installed kernel version.

Note: Vibe Seller never changes a profile's kernel settings for you —
that is your fingerprint configuration.

## Closing AdsPower mid-task

Nothing to fix by hand. The next browser use relaunches the client
(the sign-in is remembered) and reopens the profile.

One detail: if the client is force-killed (e.g. Task Manager), an
already-open profile browser **survives** it, and the relaunched
client cannot see it — reopening that profile would fail. Vibe Seller
recognizes this, clears the leftover browser process automatically and
opens the profile fresh.

If you opened a profile **by hand** in AdsPower, a task needing it
attaches to it directly, without closing and reopening.

## The two-browser cooperation

Like the other anti-detect browsers, an AdsPower store's tasks also
get a plain Chrome auxiliary browser for sites AdsPower cannot open
(logistics, overseas pages); see [Store and auxiliary browser](store-and-aux-browser.en.md).

## The AdsPower client needs you to sign in

<a id="sign-in-needed"></a>

When you see "The AdsPower client needs you to sign in: open AdsPower,
sign in with the account the API key belongs to…", the AdsPower client
on this machine is **not signed in** (or is signed into an account
other than the one the API key belongs to), so its API never came up.
What to do:

1. Open the AdsPower client window and sign in with **the account the
   API key belongs to**;
2. if the sign-in asks for a verification code (a new-device check),
   complete it — for a sub-account that cannot receive the code, see
   ["A sub-account never receives the verification code"](#a-sub-account-never-receives-the-verification-code);
3. back in Vibe Seller, click Refresh or retry the task.

## A sub-account never receives the verification code

<a id="verification-code"></a>

An **internal member** (created with an email and password) signing
into AdsPower on a new computer is asked for a verification code. The
code goes to the member's OWN email, phone or authenticator — but an
internal member's email is a virtual `@***.adspower.com` address that
**cannot receive real mail**, so the member often cannot get the code.
Three ways out:

1. **Just this once**: the admin opens Member management in AdsPower,
   finds the member's row, opens Authenticator under the Security
   management column, copies the current code (or generates a
   one-time code), and types it into the "Enter verification code"
   box on the sign-in page.
2. **Permanently**: in the same Authenticator dialog, put the secret
   (or QR code) into the member's own authenticator app (Google
   Authenticator etc.); from then on the member generates the code.
3. **Team-wide** (affects every member): the admin turns
   two-factor verification off — or to Low (asked only on a new
   device or after 90 days) — under Global settings → Account
   security → Two-factor verification.

Note: only a **human signing in inside the AdsPower window** meets
this code — Vibe Seller never signs in on your behalf, so meeting it
means someone needs to complete that sign-in in the client.

Official docs: [Members](https://help.adspower.com/docs/members) ·
[Two-factor authentication](https://help.adspower.com/docs/two_factor_authentication)

## Common messages / errors

| Message | Meaning / what to do |
|---|---|
| AdsPower has no API key yet: open API & MCP in AdsPower, generate a key and paste it into Vibe Seller. | No key saved. See "Generate the API key". |
| The key must come from the account currently signed into AdsPower; re-copy it after an account or key change. | The key does not match the signed-in account. Re-copy it. |
| The AdsPower client needs you to sign in: open AdsPower, sign in with the account the API key belongs to, finish the verification it asks for (an email/SMS code), then refresh or retry the task. | The client is not signed in (or not as the key's account), so its API never came up. See ["The AdsPower client needs you to sign in"](#the-adspower-client-needs-you-to-sign-in). |
| AdsPower does not know this profile: deleted, or not authorized for the signed-in account. | The profile was deleted, or this account lacks authorization for it. |
| This profile uses the FlowerBrowser (Firefox) kernel… | See "Profiles must use the Chrome kernel": create a SunBrowser profile. |
| AdsPower is downloading the browser kernel; retry once it finishes. | First use of a kernel version; AdsPower is downloading it. Wait and retry. |
| AdsPower refused the call: … (vendor text). Team members need their admin to grant API permission. | The API refused the call (the vendor text is included). Usually a permission or plan issue. |
| Could not find AdsPower… | Not installed, or installed somewhere undetectable. |
| Unsupported platform | The AdsPower backend works on Windows, macOS and WSL-on-Windows. |

## Where credentials are stored

The API key is stored in your local `~/.vibe-seller/data/vibe_seller.db`
(SQLite), Fernet-encrypted with a key derived from this machine's
installation; the API never returns it in plaintext. It is
machine-level (one AdsPower login), shared by every AdsPower store.
See the [developer guide](dev-guide.md) for the encryption details.

Also note: **AdsPower itself writes the API key into its own log
files** (under `%APPDATA%\adspower_global` on this machine). If the
computer is shared, treat that folder as sensitive and reset the key
under "API & MCP" when needed.
