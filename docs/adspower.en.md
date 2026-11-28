---
description: AdsPower in Vibe Seller — download and install, API permission, generate the API key, binding, one login per machine, the SunBrowser (Chromium) kernel requirement, and common questions.
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

When the AdsPower client is not running, Vibe Seller opens it
automatically; when it is not signed in, the task tells you to sign in
in the client first (see
["The AdsPower client needs you to sign in"](#the-adspower-client-needs-you-to-sign-in)).
Keep this computer's firewall on.

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

## What to fill in when binding

Pick "AdsPower" as the browser backend for the new store:

| Field | What it is |
|---|---|
| API Key | The key generated above |

After saving, Vibe Seller loads the signed-in account's profile list
(shown as "number + name + group"); pick the one this store uses in
the "Select profile..." dropdown and click "Create".

## One AdsPower login per machine

The API key must match the account signed in to the AdsPower client: one AdsPower login per machine, shared by every AdsPower store. To switch accounts: delete every AdsPower store first, then generate the new key and re-bind.

!!! warning "Profiles must use SunBrowser (the Chrome kernel)"
    Vibe Seller can only drive SunBrowser profiles. When creating a profile, choose SunBrowser under "浏览器" (Browser).
    A profile that already uses the FlowerBrowser (Firefox) kernel cannot be switched: create a new SunBrowser profile, move the store account over, then re-bind the store to the new profile in Vibe Seller's store settings.
    [Browsers → Stores must use the Chrome kernel](browsers.en.md#stores-must-use-the-chrome-kernel)

## Common messages / errors

<details id="the-adspower-client-needs-you-to-sign-in">
<summary>"The AdsPower client needs you to sign in" — what to do?</summary>
<a id="sign-in-needed"></a>

The AdsPower client on this machine is **not signed in** (or is signed
into an account other than the one the API key belongs to). What to
do:

1. Open the AdsPower client window and sign in with **the account the
   API key belongs to**;
2. if the sign-in asks for a verification code (a new-device check),
   complete it — for a sub-account that cannot receive the code, see
   ["A sub-account never receives the verification code"](#a-sub-account-never-receives-the-verification-code);
3. back in Vibe Seller, click Refresh or retry the task.

</details>

<details id="a-sub-account-never-receives-the-verification-code">
<summary>A sub-account never receives the verification code — what to do?</summary>
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

Only a **human signing in inside the AdsPower window** meets this
code — Vibe Seller never signs in on your behalf, so meeting it means
someone needs to complete that sign-in in the client.

Official docs: [Members](https://help.adspower.com/docs/members) ·
[Two-factor authentication](https://help.adspower.com/docs/two_factor_authentication)

</details>

<details>
<summary>"Does not have this profile's browser kernel" — what to do?</summary>

This machine's AdsPower does not have that kernel installed, and it
does not download it by itself. Open the AdsPower app and download
that kernel in its settings (the kernel-management page), or switch
the profile to an already-installed kernel version.

</details>

<details>
<summary>Closed AdsPower mid-task — what to do?</summary>

Nothing to fix by hand — the next use reopens the client
automatically. A profile you opened **by hand** in AdsPower is used
by the task as-is, never closed.

</details>

<details>
<summary>Where are credentials stored?</summary>

The API key is stored in your local `~/.vibe-seller/data/vibe_seller.db`
(SQLite), Fernet-encrypted with a key derived from this machine's
installation; the API never returns it in plaintext. It is
machine-level (one AdsPower login), shared by every AdsPower store.
See the [developer guide](dev-guide.md) for the encryption details.

Also note: **AdsPower itself writes the API key into its own log
files** (under `%APPDATA%\adspower_global` on this machine). If the
computer is shared, treat that folder as sensitive and reset the key
under "API & MCP" when needed.

</details>

| Message | Meaning / what to do |
|---|---|
| AdsPower has no API key yet: open API & MCP in AdsPower, generate a key and paste it into Vibe Seller. | No key saved. See "Generate the API key". |
| The key must come from the account currently signed into AdsPower; re-copy it after an account or key change. | The key does not match the signed-in account. Re-copy it. |
| The AdsPower client needs you to sign in: open AdsPower, sign in with the account the API key belongs to, finish the verification it asks for (an email/SMS code), then refresh or retry the task. | The client is not signed in. See ["The AdsPower client needs you to sign in"](#the-adspower-client-needs-you-to-sign-in). |
| AdsPower does not know this profile: deleted, or not authorized for the signed-in account. | The profile was deleted, or this account lacks authorization for it. |
| This profile uses the FlowerBrowser (Firefox) kernel… | The profile uses the Firefox kernel. Create a SunBrowser profile and re-bind the store to it (see the warning box above). |
| AdsPower is downloading the browser kernel; retry once it finishes. | First use of a kernel version; AdsPower is downloading it. Wait and retry. |
| AdsPower refused the call: … (vendor text). Team members need their admin to grant API permission. | The API refused the call (the vendor text is included). Usually a permission or plan issue. |
| Could not find AdsPower… | Not installed, or installed somewhere undetectable. |
| Unsupported platform | The AdsPower backend works on Windows, macOS and WSL-on-Windows. |
