---
name: amazon-shared
description: "Common Amazon Seller Central / advertising-console mechanics — marketplace domains and account switching; version-aware navigation (New Seller Central 'NGS' vs classic, navigate by direct URL); sign-in as a challenge loop with Ziniao auto-fill of password / OTP (紫鸟验证码服务) / hosted passkey (已托管账号Passkey, native overlay → coordinate-click), Ziniao-stored-else-human rule; ad-console vs seller-central account caveat; capture rule. Prerequisite: every other amazon-* skill (amazon-ads, amazon-reports, amazon-invoice, amazon-listing, …) expects this loaded for cross-cutting auth and navigation."
---

# Amazon — Shared (auth, navigation, common patterns)

What every Seller Central or advertising-console task needs: where each
marketplace lives, how to sign in, how to move between marketplaces, how
to navigate on both UI generations, and where captured data goes.
Operation-specific skills (`amazon-ads`, `amazon-reports`,
`amazon-invoice`, `amazon-listing`, …) load this first.

All browser work goes through the store's `browser-use` wrapper, in the
main session. The wrapper is the only entry point that talks to the
store's own browser; a bare binary would attach to the user's local
Chrome, which must never be touched.

## 1. Marketplaces and accounts

Seller Central is per-country; page paths are usually identical across
countries. The per-country base URLs and the common path table live in
`knowledge/project/common/amazon-sites.md` — look the country up there
rather than guessing a domain (`sellercentral.amazon.com.sa` does not
exist). The advertising console follows `advertising.amazon.<tld>`.

**Unified multi-marketplace accounts** hold several marketplaces under
one seller id. Their inventories differ (often 10–20% of ASINs are listed
on one marketplace only), and so do some listing-status enums — read the
live dropdown rather than assuming. Custom Reports' All-Listings TSV is
account-level (identical across subdomains), and Stranded Inventory is a
single pool. When you talk about a listing, name its marketplace;
"inventory for store X" is ambiguous.

**To reach another marketplace, switch the account — do not open that
marketplace's domain and sign in.** Opening
`sellercentral.amazon.<other-tld>` directly lands on a fresh `/ap/signin`
pre-filled with the *current* marketplace's email. It looks like a
separate account you lack credentials for, and signing in there would
authenticate the wrong account. Use the account switcher (§4a-bis)
instead, then confirm the switch changed the data, not just the chrome:
report pages cache aggressively, so compare something content-bearing
(the newest report row's date, a row count) before trusting a second
export. A store's notes may map one email per platform and so imply
separate accounts where one unified account holds several — check the
switcher before concluding a marketplace is unreachable.

## 2. Sign-in

The first page load in a session usually redirects through `/ap/signin`
even when a previous session is still good. The flow is multi-step
(email → Continue → password → optional challenges); Ziniao pre-fills
what it stores and you advance each step. You never type, read out, or
ask for a credential yourself.

Run the helper first — one call walks the DOM part of the loop:

```bash
SC_HOST=sellercentral.amazon.<tld> browser-use < .claude/skills/amazon-shared/scripts/bh_signin.py
```

It presses Continue, submits the password Ziniao filled, waits for
Ziniao's OTP on `/ap/mfa` and submits it, and returns `ok: true` once the
page leaves `/ap/`. When it returns `needs`, continue from the matching
section: `passkey` → §2c, `broken_env` → stop (see below), `stuck` /
`human` → read its screenshot and resolve the challenge shown. Bulk
pages (upload, upload status) want a sign-in from the last few minutes,
so a session can lapse while you wait for a batch; when a listing helper
reports `signed_out`, run this and then re-run that helper.

The manual steps, for what the helper hands back:

```bash
browser-use <<'PY'
new_tab("https://sellercentral.amazon.<tld>/home")
wait_for_load()
# Email step: pre-filled by Ziniao. Continue is not always #continue —
# if a JS click does nothing, coordinate-click it.
js("var c=document.querySelector('#continue,input#continue'); if(c) c.click();")
wait_for_load()
# Password step: Ziniao pre-fills #ap_password. Submit.
js("var b=document.querySelector('#signInSubmit'); if(b) b.click();")
wait_for_load()
print(page_info())
PY
```

`page_info()` does not show auto-filled values, so fields look empty even
when filled. Check with
`js("return document.querySelector('#ap_password')?.value ? 'filled':'empty'")`
(the `?.` keeps it safe on steps where the field is absent).

### The challenge loop

Amazon's risk control decides which challenges appear — password, OTP,
passkey, or a combination — and it varies run to run (a passkey can be
followed by OTP; a trusted session skips both). So don't hard-code an
order: after each submit, re-read the page and resolve whatever is
shown, until the URL reaches `/home` or `/amazonsell/business`.

At every challenge: **if Ziniao stores it, use Ziniao; otherwise ask a
human** (with `AskUserQuestion`). Ziniao "has it" when the field is
pre-filled (password) or a Ziniao panel appears (`紫鸟验证码服务` for
OTP, `已托管账号Passkey` for passkey). If the expected affordance never
appears for a required step, stop and ask rather than looping.

If *nothing* Ziniao-ish works — email and password not pre-filled, no
OTP panel, no passkey overlay — suspect the environment rather than the
account. Check `js("return navigator.webdriver")`: an anti-detect browser
reports `false`. If it returns `true`, the browser itself is broken; say
so and stop. Asking for a password or hand-typing your way in would not
help — the platform already treats the session as a bot.

### 2a. Ziniao panels are native overlays

The OTP service (`紫鸟验证码服务`) and the hosted-passkey picker
(`已托管账号Passkey`) are drawn by Ziniao, not by the page, so
`querySelector` cannot find them and a JS `.click()` does nothing. Drive
them by sight: `capture_screenshot(path)` → read the button's position →
`click_at_xy(x, y)`.

### 2b. OTP / 2FA

On `/ap/mfa` Ziniao fetches and fills the code (its panel shows
`验证码获取成功`). Submit with `#auth-signin-button` (not the password
page's `#signInSubmit`); coordinate-click it if a JS click doesn't move
the page.

### 2c. Passkey

Some accounts are forced onto passkeys. The passkey is hosted by Ziniao,
not by the OS, so there is no Touch ID / Windows Hello dialog and login
stays automatable:

1. At the password step click **"Sign in with a passkey"** (a normal
   page link — JS or coordinate click both work).
2. Ziniao shows its `已托管账号Passkey` overlay with a blue
   `使用该Passkey登录` button. It is native (§2a): screenshot, then
   `click_at_xy()` on the button. The overlay lands in a different place
   each time, so read the coordinates fresh on every attempt.
3. Continue the challenge loop — this often routes on to OTP (§2b).

If the URL is still on `/ap/signin` about 8 s after the click,
re-screenshot (the button may have moved) and click again. The only real
blocker is the overlay never appearing after "Sign in with a passkey" —
the passkey isn't provisioned in Ziniao for this store; ask the user.

A successful passkey navigation sometimes drops the browser-use session
(the next call fails with a socket `FileNotFoundError`). That is not a
login failure: the next call through the store wrapper re-derives the
session, so re-open the home URL and confirm you are signed in.

## 3. Ad console vs Seller Central — possibly different accounts

For some merchants the advertising console runs on a different Amazon
account from Seller Central (different email, different `entityId`),
with SSO usually bridging them. When a page "shows the wrong account",
check the email in Ziniao's password fill and the `entityId` in the
ad-console URL (`?entityId=ENTITY…`) — that is the account actually
active.

## 4. Navigation — two UI generations

Amazon is rolling out **New Seller Central** ("NGS"). Its chrome differs
from classic, but the page paths underneath do not, so direct URLs work
on both.

### 4a. Navigate by direct URL first

`/reportcentral/...`, `/payments/reports-repository`, `/orders-v3`,
`/business-reports`, `/myinventory/inventory` and the rest load directly
on both UIs. For a known destination just `new_tab(<url>)` with the path
from `amazon-sites.md`. Use the menus (§4c/§4d) only to discover a page
whose URL you don't have.

### 4a-bis. Marketplace context and the account switcher

The marketplace context is **per domain**: each
`sellercentral.amazon.<tld>` renders the marketplace last selected on
IT, and the domains of one account are pinned independently — observed
live, `.sa` served AE while `.ae` served SA on the same session. What
a page shows follows the header switcher label (store name + country),
not the URL subdomain you typed. Read the label (or better
`js("return ue_mid")`) on every page you act on. Store display names
can differ per marketplace on one account, so reconcile by catalog
contents, not by name.

The per-domain pins **converge asynchronously**: a switch settles on
the domain you ran it on immediately (the confirm redirects to the
target's own domain, so the helper's verification right after it is
reliable), but sibling domains update with a lag of tens of seconds —
and a late propagation once transiently reverted a domain I had just
verified back (observed 2026-11-11). Never treat a cross-domain pin
read minutes after a switch as settled; re-read the domain you are
about to work on.

To switch, run the helper — it selects the row, confirms, and reads
the marketplace back **on the target marketplace's own domain**:

```bash
SC_MARKETPLACE=<CC> SC_HOST=sellercentral.amazon.<tld> browser-use < .claude/skills/amazon-shared/scripts/bh_switch_marketplace.py
```

(`SC_HOST` may also be a full origin, `http://127.0.0.1:PORT` — used
verbatim; that is how the integration tests drive it.)

It matches the target row by the country's name in the session's own
language (`Intl.DisplayNames`), presses the confirm button, and returns
`ok: true` only once `ue_mid` on the target domain's `/home` is the
target. It refuses a row whose label carries a non-"current" marker
((pending registration) in any language) — selecting one would START a
registration. The RESULT names the click methods that worked
(`row_method`, `confirm_method`); on failure it says what it tried and
leaves a screenshot.

**Which click method works is per account.** Live 2026-11-11, four
stores: rows tick with a JS `.click()` on the
`button.full-page-account-switcher-account-details` itself and the
confirm fires only from a JS click on the **shadow-root inner
`<button>`** of `kat-button[data-test=confirm-selection]` — a
coordinate click on either did nothing there. Older field notes record
accounts where only real coordinate clicks count. Never assume a
method: click, then VERIFY the observable state (the row gains class
`full-page-account-switcher-account-selected`; the page leaves the
switcher), and fall back to the other method when it did not change.

If the helper fails, the manual route is the full-page switcher. Drive
it by structure, not by label strings — its language follows the
session and its rows are whatever this account holds. (The header
dropdown's rows ignore JS clicks and render off-viewport; the classic
header chip's "Switch Accounts" entry leads to the same page.)

1. `new_tab('https://sellercentral.amazon.<tld>/account-switcher/default/merchantMarketplace')`
   (the old `/gp/account/switcher` path 404s).
2. Rows are `button.full-page-account-switcher-account-details`; the
   active row's label has `data-test="current-account"`. The country
   rows load asynchronously under the account row (a spinner is
   visible) — wait for the row count to settle, don't sleep. If the
   target row is missing, click the account row to expand its list.
   Rows carrying a non-"current" marker (e.g. `(pending
   registration)`) cannot be selected.
3. Click the row naming your target country (its localized full name) —
   JS click first, verify the `-selected` class, fall back to a
   coordinate click as above.
4. The confirm button, `kat-button[data-test=confirm-selection]`
   ("Select account"), sits below the rows. It is a custom element —
   a search over `button` / `a` will not find it; its real button is
   in its shadow root. The session redirects to the target
   marketplace's own domain (sometimes via a `mons_sel_mkid=`
   parameter); confirm with `ue_mid` on a fresh `/home` of the TARGET
   domain before trusting later pages.
5. If the page or a step is genuinely absent, open the target subdomain
   in a fresh tab and re-read the label; if that fails too, ask the user
   with what you observed. The aux browser has no seller login, so all
   Seller Central work stays in the main session.

### 4b. Detecting the UI generation

Load `/home` and see where you land (Amazon renames this chrome often —
re-check on a stale session):

- **NGS** — redirects to `/amazonsell/business?ref=homepage_redirect_ngs`,
  title "New Amazon Selling Experience", chrome is `ngstrim-*`:
  `nav.ngstrim-top-navigation`, `button[aria-label="Menu"]` (drawer
  toggle), `div#ngstrim-nav-menu` (drawer), `nav#ngstrim-bookmarks`
  (favorites row), `nav#ngstrim-breadcrumbs`. A "Classic Seller Central"
  link (`/home?ngs_do_not_redirect_flag=1`) sits at the bottom of the
  drawer.
- **Classic** — plain `/home` (title "Amazon"), `#sc-navbar-container` /
  `#navbar`, the `navigation-hamburger-menu` web component, and a
  top-right "New Seller Central" link (`a[href^="/amazonsell"]`).

`casino-*` web components appear on both, so don't use them as a signal;
key on the URL, the title, and `ngstrim-*` vs `#sc-navbar-container`.

To switch generation: on NGS open the drawer and click "Classic Seller
Central"; on classic click the top-right "New Seller Central" link. When
a page misbehaves under NGS, `/home?ngs_do_not_redirect_flag=1` keeps you
on the classic home.

### 4c. NGS chrome (discovery)

- **Favorites row** (`nav#ngstrim-bookmarks`): plain
  `navigation-favorites-button` anchors — Manage products, Manage
  shipments, Manage orders, Ads console (`/cm?source=ngs`), Coupons,
  Payments, Business reports, Fulfillment, Manage Amazon inventory, … Read
  the href and open it.
- **Drawer** (`button[aria-label="Menu"]` → `div#ngstrim-nav-menu`):
  sections Products / Supply chain / Orders / Marketing / Finance /
  Account health / Programs and apps, each with a "Workspace" link and
  plain-anchor items (e.g. Supply chain → Send to Amazon
  `/fba/sendtoamazon`).
- On NGS the trim wraps the classic page bodies (`/myinventory/inventory`,
  `/orders-v3`, report pages); only `/home` is a different page.

### 4d. Classic chrome — hamburger hover-to-reveal

Categories open on `mouseover`; a click navigates away:

```bash
browser-use <<'PY'
js("document.querySelector('navigation-hamburger-menu').shadowRoot.querySelector('[role=button]').click()")
wait_for_load()
js("""
var cat = [...document.querySelectorAll('[aria-expanded=false]')]
  .find(e => e.textContent.trim() === 'Reports');
cat.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));
""")
js("""[...document.querySelectorAll('a, [role=button]')]
  .find(e => e.textContent.trim() === 'Business Reports').click();""")
PY
```

Without a stable selector, `capture_screenshot()` + `click_at_xy(x, y)`.

### 4e. Store type — per-country vs aggregated

Independently of the UI generation, a store is either an **older
per-country** account (marketplaces walled off) or a **newer aggregated**
one. The same page renders differently on each, so check before trusting
any per-country assumption.

- **Ads console** (the clearest signal) — the top-right account chip:
  - per-country store: `Sponsored ads, <Country>`, a switcher between
    separate per-country ad accounts. The campaign list and every export
    cover only the selected country.
  - aggregated store: `Sponsored ads, multiple countries/regions`
    (localized, e.g. 搜索广告，多个国家/地区). The campaign list has a
    Country/Region column spanning every marketplace, one export covers
    all of them, and Campaign Builder's country dropdown chooses each new
    campaign's country.
- **Seller Central** — the switcher and inventory pages look the same on
  both; what differs is scope. On a per-country store a marketplace's
  data appears only after switching; on an aggregated one a page can
  carry cross-marketplace state.
- `ue_mid` tells you the current marketplace, never the store type, and
  the subdomain tells you less (a `.sa` URL can serve `ue_mid=AE`). The
  ads chip and a Country column are the reliable tells.

So for a task naming a country: on a per-country store, switch (ads) or
verify `ue_mid` (Seller Central) before reading anything; on an
aggregated store, filter to the requested country (Country column /
Campaign Builder dropdown) rather than assuming the view is scoped.

## 5. Where captured data goes

Per-run live data — campaign captures, search-term exports, screenshots,
spend snapshots — goes to your task directory (CWD) (or the run-data paths an
operation skill names). Files you upload come from the store's downloads
folder. Keep private business data out of `~/.vibe-seller/knowledge/`:
that tree holds reusable platform facts and is synced into other agents'
context.

## 6. Load order

Before any `browser-use` call against Amazon:

1. the `browser-harness` skill (heredoc helpers + wrapper rules);
2. this skill;
3. the operation skill (`amazon-ads`, `amazon-reports`, …) and the
   references the task needs.

The wrapper starts the browser on the first `new_tab(...)`; open the URL,
`wait_for_load()`, then read state with `print(page_info())`. Read page
data from the DOM; screenshots are for locating native overlays and
buttons, not for reading figures.

## See also

- `amazon-ads` — Sponsored Products / Brands / Display + Coupons
- `amazon-reports` — Business / Fulfillment / Tax / Payments / Advertising reports
- `amazon-invoice` — tax-invoice PDFs from order data
