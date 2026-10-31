---
name: amazon-shared
description: "Common Amazon Seller Central / advertising-console mechanics — marketplace TLD map; version-aware navigation (New Seller Central 'NGS' vs classic, navigate by direct URL); sign-in as a challenge LOOP with Ziniao auto-fill of password / OTP (紫鸟验证码服务) / hosted passkey (已托管账号Passkey, native overlay → coordinate-click), Ziniao-stored-else-human rule; ad-console vs seller-central account caveat; capture rule. Prerequisite: every other amazon-* skill (amazon-ads, amazon-reports, amazon-invoice, amazon-listing / amazon-fbn / etc.) expects this loaded for cross-cutting auth and navigation patterns."
---

# Amazon — Shared (auth, navigation, common patterns)

This skill covers what every Amazon Seller Central or advertising-
console task needs: marketplace endpoints, the hamburger-menu hover
pattern, login / Ziniao / OTP handling, and the ad-console vs
seller-central account caveat. Operation-specific skills
(`amazon-ads`, `amazon-reports`, `amazon-invoice`, future
`amazon-listing` / `amazon-fbn` / etc.) load this first.

## 1. Marketplace TLDs

Amazon Seller Central is per-country. Use the country-specific TLD
in URLs — paths are usually identical.

| Marketplace | Seller Central | Advertising console |
|---|---|---|
| US | `sellercentral.amazon.com` | `advertising.amazon.com` |
| UK | `sellercentral.amazon.co.uk` | `advertising.amazon.co.uk` |
| (others) | `sellercentral.amazon.<tld>` | `advertising.amazon.<tld>` |

For the canonical per-country base URLs and path table, read
`knowledge/project/common/amazon-sites.md` — that file is the source
of truth for seller-central paths (inventory, listings, orders,
performance, etc.). Don't guess seller-central paths.

**Unified multi-marketplace accounts:** one Amazon seller-id, multiple
marketplaces. Inventory differs (~10–20% of ASINs are listed on one
but not the others). Listing-status enums differ (e.g. one marketplace
omits `DetailPageRemoved` from a filter dropdown while another includes
it — read the live dropdown). Custom Reports's All-Listings TSV is
byte-identical across the marketplaces' subdomains (account-level). Stranded Inventory is a single pool. When debugging
a specific listing, always specify which marketplace; "the inventory
for store X" is ambiguous.

> **To reach another marketplace, SWITCH the account — do not navigate to
> that marketplace's domain.** Hitting `sellercentral.amazon.{other-tld}`
> directly lands on a *fresh* `/ap/signin` that Ziniao pre-fills with the
> **current** marketplace's email. That looks exactly like "this is a
> separate account I have no credentials for", and signing in anyway
> would authenticate the wrong account. Instead:
>
> ```
> https://sellercentral.amazon.{current-tld}/account-switcher/default/merchantMarketplace
> ```
>
> Entries read `<Country>` when available and `<Country> (pending
> registration)` when not — only the former can be selected. Selection is
> two steps: click the country row (it ticks), **then** the "Select
> account" button that appears at the bottom of the list; a single click
> looks like a no-op. On success the session **redirects to the target
> marketplace's own domain** (e.g. `sellercentral.amazon.sa`), sometimes
> with a `mons_sel_mkid=`/`mons_sel_dir_mcid=` parameter on the way, and
> the header chip shows the new country. Confirm the switch by reading
> `ue_mid`, not just the chrome.
>
> **Confirm the switch changed the data, not just the chrome.** These
> report pages cache aggressively; compare something content-bearing
> (e.g. the newest report row's date differed — 09/08 on one marketplace
> vs 10/08 on the other) or a row count, before trusting a second export.
> A store's `notes.md` email-to-platform map can imply separate accounts
> per marketplace when one unified account actually holds several — check
> the switcher before concluding a marketplace is unreachable.

## 2. Sign-in flow (browser-side)

The first hit per session typically redirects through Amazon sign-in
(`/ap/signin`) even if a previous session is still good. The modern
flow is multi-step: **email → Continue → password → (optional 2FA)**.
Ziniao pre-fills the email and password; the agent advances each step
and lets the redirect settle.

**Run the helper first — one call walks the DOM part of the loop below:**

```bash
SC_HOST=sellercentral.amazon.<tld> browser-use < .claude/skills/amazon-shared/scripts/bh_signin.py
```

It presses Continue, submits the password Ziniao filled, waits for
Ziniao's OTP on `/ap/mfa` and submits it, and stops with `ok: true` once
the page leaves `/ap/`. It never types a credential. When it returns
`needs`, continue by hand from the matching section: `passkey` → §2c,
`broken_env` → stop (see below), `stuck` / `human` → read its screenshot
and resolve the challenge shown. Bulk pages (upload, upload status)
demand a sign-in within the last few minutes, so a session lapses during
any wait for a batch: when a listing helper reports `signed_out`, run
this and then re-run that helper.

The manual steps, for reference and for what the helper hands back:

```bash
browser-use <<'PY'
new_tab("https://sellercentral.amazon.<tld>/home")
wait_for_load()
# Email step: field pre-filled by Ziniao. The Continue button is not
# always #continue — if a JS click no-ops, coordinate-click it.
js("var c=document.querySelector('#continue,input#continue'); if(c) c.click();")
wait_for_load()
# Password step: Ziniao pre-fills #ap_password. Submit.
js("var b=document.querySelector('#signInSubmit'); if(b) b.click();")
wait_for_load()
print(page_info())
PY
```

`page_info()` does NOT show auto-filled input values — fields appear
empty even when filled. Confirm with
`js("return document.querySelector('#ap_password')?.value ? 'filled':'empty'")`
(null-safe `?.` — the same snippet runs on steps where the field is
absent) before deciding whether to ask the user.

### Login is a challenge LOOP, not a fixed sequence

Which challenges Amazon presents — password, OTP, passkey, or a
combination — is decided by **Amazon's risk control and varies run to
run** (a passkey can still be followed by OTP; a trusted session skips
both). So do **not** hard-code an order. After each submit, re-read the
page and resolve whatever challenge is shown, repeating until the URL
reaches `/home` or `/amazonsell/business`.

**Decision rule at every challenge — Ziniao-stored → use Ziniao;
otherwise → ask a human.** Ziniao "has it" when the field is pre-filled
(password) or a Ziniao panel appears (`紫鸟验证码服务` for OTP,
`已托管账号Passkey` for passkey). If the expected Ziniao affordance never
appears for a required step, stop and ask the user — never loop forever
on a challenge Ziniao can't satisfy.

> **Not every missing affordance is a missing credential.** If *nothing*
> Ziniao-ish works — email not pre-filled, password not pre-filled, no
> OTP panel, no passkey overlay — that is the signature of an env whose
> Ziniao layer didn't load, not of an unprovisioned account. Confirm
> with `js("return navigator.webdriver")`: an anti-detect browser must
> report `false`. If it returns `true`, the browser itself is broken —
> say so and stop; do NOT ask the user for a password, and do not try to
> hand-type your way in (the platform is already treating the session as
> a bot). The launcher now rejects such envs at start, so this should be
> rare; seeing it means the browser was wedged after launch.

### 2a. Ziniao helper panels are NATIVE overlays — screenshot, don't querySelector

When a step needs a code or a passkey, Ziniao renders its own panel:
the OTP service (**`紫鸟验证码服务`**) and the hosted-passkey picker
(**`已托管账号Passkey`**). **These are Ziniao-native overlays, NOT part
of the page DOM** — `document.querySelector` can't find them and a JS
`.click()` silently no-ops (verified: `navigator.credentials.get` hooks
never fire). Drive them by sight: `capture_screenshot(path)` → read the
button's pixel position → `click_at_xy(x, y)`. A human clicks these the
same way, so the agent can too — coordinate-click reaches them because
they render inside the browser viewport.

### 2b. OTP / 2FA

If the flow lands on `/ap/mfa` (Two-Step Verification), Ziniao fetches
the code and fills the OTP field (its `紫鸟验证码服务` panel shows
`验证码获取成功`). Submit: on this page the button is `#auth-signin-button`
(not the password page's `#signInSubmit`); coordinate-click it if a JS
click does not move the page. This path is the
long-standing default and needs no special handling beyond the submit.

### 2c. Passkey (accounts Amazon has moved to passkey login)

Some accounts no longer offer a usable password/OTP login and are
**forced onto passkeys**. The store's passkey is **hosted by Ziniao**
(not an OS/biometric credential), so login stays fully automatable —
there is no Touch ID / Windows Hello dialog:

1. At the password step, take the passkey branch: click **"Sign in with
   a passkey"** (a real page-DOM link — JS or coordinate click both work).
2. Ziniao pops its **`已托管账号Passkey`** overlay listing the hosted
   Amazon credential for the account, with a blue **`使用该Passkey登录`**
   ("sign in with this passkey") button. This overlay is native (§2a) —
   `capture_screenshot()` then `click_at_xy()` on that blue button. Do
   **not** try to querySelector it. **The overlay floats to a different
   position each time** (its Y shifts run to run), so re-screenshot and
   read the button's coordinates every attempt — never reuse hardcoded
   x/y.
3. The flow then continues the challenge LOOP above — for this account it
   routes on to OTP (§2b), which Ziniao autofills; a lower-risk session
   may go straight to `/home`. Resolve each step until you land on the
   dashboard.

> **Daemon-drop gotcha:** a successful passkey navigation sometimes tears
> down the browser-use session (next call fails with a socket
> `FileNotFoundError`). That's not a login failure — rotate `VIBE_TASK_ID`
> and re-open the home URL to confirm the logged-in state.

**Robustness / when to ask a human:** the only genuine blocker is the
hosted-passkey overlay never appearing after "Sign in with a passkey"
(passkey not provisioned in Ziniao for this store). If it *does* appear,
coordinate-click it — do not escalate. If the URL is still on
`/ap/signin` ~8s after the coordinate-click, re-screenshot (the button
may have shifted) and click again before giving up.

## 3. Ad-console vs Seller-Central — different accounts

For some merchants, the **advertising console** (`advertising.amazon.<tld>`)
is on a *different underlying Amazon account* from the **seller-
central** account (different email, different `entityId`), even
though SSO usually bridges them transparently.

When debugging an "I logged in but it shows the wrong account"
issue:
- Check the email in the Ziniao password-fill dialog.
- Check the `entityId` in the ad-console URL (`?entityId=ENTITY[A-Z0-9]+`)
  — that's what's actually active.

Both indicate which account is currently signed in.

## 4. Navigation — two UI generations (New Seller Central vs classic)

Amazon is rolling out **New Seller Central** ("NGS"). A migrated account
looks and navigates differently from a classic one, so first know which
you're on — then use the one navigation method that works on **both**.

### 4a. Navigate by DIRECT URL — the version-agnostic path (do this first)

The redesign changed the *chrome* (menus, tabs, dashboard), but the
underlying **page paths are unchanged** — `/reportcentral/...`,
`/payments/reports-repository`, `/orders-v3`, `/business-reports`,
`/myinventory/inventory`, etc. all load directly on both UIs (verified).
So for a known destination, **just `new_tab(<url>)`** — do not open the
menu at all. Canonical per-country paths live in
`knowledge/project/common/amazon-sites.md`; that is the source of truth.
Only fall back to the menu (§4c/§4d) when you need to *discover* a page
whose URL you don't have.

### 4a-bis. Marketplace context is per-SESSION-and-DOMAIN — read the
### switcher label, and know how to unstick it

What a seller-central page displays follows the **header
account/marketplace switcher label** (store name + country), NOT the
URL subdomain — a session can be pinned so that even
`sellercentral.amazon.<tld>/...` renders a SIBLING marketplace. The
label is the only truth; read it back on every page you act on. Note
the store display name can DIFFER per marketplace on one account
(brand/storefront names vary) — reconcile by catalog contents, not by
name alone.

When the label shows the wrong marketplace and you need to switch, run
the helper first -- one call does all of the steps below and reads the
marketplace back:

```bash
SC_MARKETPLACE=<CC> SC_HOST=sellercentral.amazon.<tld> browser-use < .claude/skills/amazon-shared/scripts/bh_switch_marketplace.py
```

It finds the target row by the country's name in the session's own
language (`Intl.DisplayNames`), selects it, presses the
`kat-button[data-test=confirm-selection]` (a custom element -- a search
over `button` / `a` never finds it, which cost one run ~45 calls), and
returns `ok: true` only once `ue_mid` is the target. It refuses a
pending / not-registered row by name. The manual steps, for when it
reports a failure:

Use the account-switcher PAGE — it is directly addressable (verified
live; do NOT fight the header dropdown, whose kat/Vue rows ignore JS
clicks and render off-viewport; the classic header chip opens
`#dropdown-account-switcher-container` with a "Switch Accounts" entry
that leads to the same page):

The page is the same everywhere — its LANGUAGE follows the session and
its ROWS are whatever marketplaces this account holds — so drive it by
STRUCTURE, never by hardcoded country/label strings. Read the page,
match against the target you already know (the country you were asked
for, its `sellercentral.amazon.<tld>`, its marketplace id), and click:

1. `new_tab('https://sellercentral.amazon.<tld>/account-switcher/default/merchantMarketplace')`
   (the old `/gp/account/switcher` path 404s). `<tld>` is the target
   country's TLD (see §1).
2. Rows are `button.full-page-account-switcher-account-details`; the
   ACTIVE row's label carries `data-test="current-account"`. On a
   single-account store the whole marketplace list is rendered
   immediately; if the target row is missing, click the ACCOUNT row to
   expand its list (a live agent wrongly concluded "no marketplace
   list / no confirm button" on a collapsed render). Rows the account
   does not hold read `<Country> (pending registration)` — skip those.
3. Click the target marketplace's row. Match it to the country you
   want by its full name AS RENDERED in the session's language (it is
   the localized country name, not a short form or a code) — enumerate
   the rows and pick the one naming your target country.
4. Clicking the row only TICKS it (the row gains the
   `full-page-account-switcher-account-selected` class; no navigation).
   The confirm button — **`kat-button[data-test=confirm-selection]`,
   visible text "Select account"** — appears at the BOTTOM of the list
   (below the last row) only after a row is selected, and it needs a
   real coordinate click. Wait for the redirect: the session lands on
   the TARGET marketplace's own domain (e.g.
   `sellercentral.amazon.sa/amazonsell/business?ref=homepage_redirect_ngs`),
   then RE-READ the switcher label — or better `js("return ue_mid")` —
   and confirm it names the target country before trusting anything on
   subsequent pages.
5. If the page or any step is genuinely absent, open the target
   subdomain in a fresh tab and re-read the label; only after that
   fails too, ask the user — with what you observed. (The aux browser
   is NOT an option here — it has no seller login; all seller-central
   work stays in the MAIN session.)

### 4b. Detecting the version

Load `/home` and check where you land / what renders (verified live
2026-10-31; Amazon renames this chrome often, so re-check on a stale
session):

- **New Seller Central (NGS)** — `/home` redirects to
  **`/amazonsell/business?ref=homepage_redirect_ngs`**; the page title
  reads **"New Amazon Selling Experience"**; the chrome is the
  **`ngstrim-*`** trim: `nav.ngstrim-top-navigation` (top bar),
  `button[aria-label="Menu"]` (drawer toggle, top-left),
  `div#ngstrim-nav-menu` (left drawer),
  `nav#ngstrim-bookmarks` (horizontal favorites row of
  `navigation-favorites-button` links),
  `nav#ngstrim-breadcrumbs`. A "Classic Seller Central" link
  (href `/home?ngs_do_not_redirect_flag=1`) sits at the **bottom of the
  left drawer**.
- **Classic** — plain `/home` (title just "Amazon"),
  `#sc-navbar-container` / `#navbar`, the
  `navigation-hamburger-menu` web component, and a **top-right
  "New Seller Central" link** (`a[href^="/amazonsell"]`, observed
  `href="/amazonsell?ref=sc_trim_header&path=%2Fhome"`).

> ⚠️ **`casino-*` web components are NOT an NGS signal anymore.** The
> classic home now also renders `casino-card-compact`,
> `casino-product-summary`, `casino-select-bar`, … Key on the URL
> (`/amazonsell/business` vs `/home`), the title, and the `ngstrim-*`
> vs `#sc-navbar-container` chrome instead.

- **Switching**: on NGS, open the drawer (`button[aria-label="Menu"]`)
  and click the **"Classic Seller Central"** link at its bottom
  (→ `/home?ngs_do_not_redirect_flag=1`). On classic, click the
  top-right **"New Seller Central"** link (→ `/amazonsell`). Classic
  warns "Switching back as your default will no longer be available" —
  Amazon is sunsetting the classic default; switching per session still
  works.
- **Force classic** when a page misbehaves under NGS: append
  **`?ngs_do_not_redirect_flag=1`** to `/home` to stay on the classic home
  (verified working).

### 4c. NGS chrome (for discovery only)

- **Favorites / quick-nav row** (`nav#ngstrim-bookmarks`, horizontal,
  under the top bar): direct links (Manage products
  `/amazonsell/manage-products`, Manage shipments `/manage/shipments`,
  Manage orders `/amazonsell/orders/mfn`, Ads console `/cm?source=ngs`,
  Coupons `/manage/promotions`, Manage payments
  `/payments/dashboard/index.html`, Business reports
  `/business-reports#/dashboard`, Fulfillment `/reportcentral`, Manage
  Amazon inventory `/amazonsell/fba-inventory`, …). Each is a plain
  `navigation-favorites-button` anchor — read its href and open it.
- **Hamburger drawer** (`button[aria-label="Menu"]` top-left opens
  `div#ngstrim-nav-menu`): sections **Products / Supply chain / Orders /
  Marketing / Finance / Account health / Programs and apps**, each
  expanding a sub-list with a "Workspace" link plus items (e.g.
  Supply chain → Manage Amazon inventory, Manage shipments,
  **Send to Amazon** (`/fba/sendtoamazon`), Supply chain console,
  Supply chain reports). A **"Classic Seller Central"** toggle sits at
  the drawer's bottom. Sub-list items are plain anchors with real
  hrefs — read and open them rather than clicking blind.
- On an NGS session the trim WRAPS the classic page bodies:
  `/myinventory/inventory`, `/orders-v3`, report pages etc. render their
  classic content under the `ngstrim` top bar + drawer. Only `/home`
  (→ `/amazonsell/business`) is a different page altogether.

### 4d. Classic chrome — hamburger hover-to-reveal

On a classic account the hamburger menu uses a **hover-to-reveal**
pattern — dispatch `mouseover` (not click) on a parent category:

```bash
browser-use <<'PY'
js("document.querySelector('navigation-hamburger-menu').shadowRoot.querySelector('[role=button]').click()")
wait_for_load()
js("""
var cat = [...document.querySelectorAll('[aria-expanded=false]')]
  .find(e => e.textContent.trim() === 'Reports');
cat.dispatchEvent(new MouseEvent('mouseover', {bubbles: true}));   # NOT click — click navigates away
""")
js("""[...document.querySelectorAll('a, [role=button]')]
  .find(e => e.textContent.trim() === 'Business Reports').click();""")
PY
```

If a stable selector isn't obvious, `capture_screenshot()` + `click_at_xy(x, y)`.
But prefer §4a (direct URL) over any of this whenever you know the path.

### 4e. Store TYPE is a second axis — old (per-country) vs new (aggregated)

The UI generation above (§4b) is orthogonal to **which kind of seller
account the store is**: an **old store** (registered early, marketplaces
walled off) vs a **new store** (newer unified registration, marketplaces
aggregated). The same page renders differently on each, so detect the
type before trusting any per-country assumption (both verified live
2026-10-31):

- **Ads console (the clearest signal).** Read the top-right account chip:
  - *Old store* → the chip reads `Sponsored ads, <Country>` (one
    country) and is a **switcher between separate per-country ad
    accounts** (dropdown "Sponsored Ads Accounts", one entry per
    country; the campaign list and every export cover ONLY the selected
    country).
  - *New store* → the chip reads `Sponsored ads, multiple
    countries/regions` (localized, e.g. 搜索广告，多个国家/地区); the
    campaign list has a **Country/Region column** whose one list spans
    every marketplace the entity covers, and one export covers all of
    them. The Campaign Builder's country dropdown (§ of the ads skill)
    lists every country in the entity — the campaign picks its country
    there.
- **Seller Central side — same surface on both types.** The header
  account chip (same `dropdown-account-switcher-*` component) and the
  full-page account switcher (§4a-bis) work identically; both store
  types list their marketplaces there. Inventory pages are also
  structurally identical (same tabs, same row kebab menus, same
  `+N marketplace` chip on listings that exist on more than one
  marketplace). What differs is scope: on an old store a marketplace's
  data is visible only after switching to it; on a new store one
  marketplace's pages can carry cross-marketplace state (and the ads
  side is aggregated outright).
- **Detection shortcuts**: `ue_mid` (§ above) on an ordinary page tells
  you the CURRENT marketplace, never the store type — and a URL
  subdomain tells you even less (a `.sa` inventory URL happily serves
  `ue_mid=AE`). The ads-console chip and the presence of a Country
  column in the campaign list are the reliable tell. When in doubt, ask
  the data: open the campaign list and look.

Practical consequence for any task naming a country: on an old store you
MUST switch (ads) / verify `ue_mid` (seller central) before reading
anything; on a new store you may see every country at once and must
FILTER to the requested one (ads: Country column filter / Campaign
Builder country dropdown) instead of assuming the view is scoped.

## 5. Capture rule

Per-run live data (campaign captures, search-term exports, screenshots,
ad-spend snapshots) goes to `/tmp/<run-slug>/`. **Never** under
`~/.vibe-seller/knowledge/` — that path is for codified, reusable
facts about the platform, NOT per-run private business data. The
catalog gets synced into agent contexts, so private data there leaks
across sessions.

## 6. Common "always load X first" pattern

Before any browser-use call against Amazon:

1. Load `browser-use` skill (heredoc helper interface + wrapper rules).
2. Load this skill (`amazon-shared`) for the mechanics above.
3. Load the operation-specific skill (`amazon-ads`, `amazon-reports`,
   …) and any of its references that the task needs.

The wrapper auto-starts the browser on first `new_tab(...)`, so you
don't need to pre-warm the CDP proxy. Just open the URL, call
`wait_for_load()`, then read state with `print(page_info())`.

## See also

- `amazon-ads` — Sponsored Products / Brands / Display + Coupons
- `amazon-reports` — Business / Fulfillment / Tax / Payments / Advertising reports
- `amazon-invoice` — Tax-invoice PDF generation from order data
