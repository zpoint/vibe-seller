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

## 2. Sign-in flow (browser-side)

The first hit per session typically redirects through Amazon sign-in
(`/ap/signin`) even if a previous session is still good. The modern
flow is multi-step: **email → Continue → password → (optional 2FA)**.
Ziniao pre-fills the email and password; the agent advances each step
and lets the redirect settle.

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
`验证码获取成功`). Submit (`#signInSubmit` is usually in the page DOM
here; coordinate-click the "Sign in" button if not). This path is the
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
`sellercentral.amazon.ae/...` renders the sibling marketplace. The
label is the only truth; read it back on every page you act on. Note
the store display name can DIFFER per marketplace on one account
(brand/storefront names vary) — reconcile by catalog contents, not by
name alone.

When the label shows the wrong marketplace and you need to switch,
use the account-switcher PAGE — it is directly addressable (verified
live; do NOT fight the header dropdown, whose kat/Vue rows ignore JS
clicks and render off-viewport):

1. `new_tab('https://sellercentral.amazon.<tld>/account-switcher/default/merchantMarketplace')`
   (the old `/gp/account/switcher` path 404s).
2. Click the target ACCOUNT's button (it shows the store display
   name, with “(当前)/(current)” when active) — it expands that
   account's marketplace list.
3. Click the target marketplace by its LOCALIZED FULL name — on a
   Chinese session AE is “阿拉伯联合酋长国” (NOT the short “阿联酋”),
   SA is “沙特阿拉伯”; unregistered rows say “（待注册）”. Read the
   expanded list and match flexibly rather than assuming one string.
4. Click the confirm button “选择账户 / Select account”, wait for the
   redirect (`/home?mons_sel_dir_mcid=…`), then RE-READ the header
   label to verify it now shows the target marketplace.
5. If the page or any step is missing, open the target subdomain in a
   fresh tab and re-read the label; only after that fails too, ask the
   user — with what you observed. (The aux browser is NOT an option
   for any of this — it has no seller login; all seller-central work
   stays in the MAIN session.)

### 4b. Detecting the version

Load `/home` and check where you land / what renders:

- **New Seller Central (NGS)** — `/home` redirects to
  **`/amazonsell/business`** (`?ref=homepage_redirect_ngs`); the page has
  a **"New Seller Central" toggle** in the top bar, a dashboard
  **channel-tab row** (My business / Products / Supply chain / Orders /
  Finance / Customers / Marketing) and `casino-*` web components
  (`document.querySelector('casino-greeting-header, navigation-favorites-bar')`).
- **Classic** — plain `/home` dashboard, no `casino-*` elements; the old
  hamburger hover-menu (§4d).
- **Force classic** when a page misbehaves under NGS: append
  **`?ngs_do_not_redirect_flag=1`** to `/home` to stay on the classic home.

### 4c. NGS chrome (for discovery only)

- **Favorites / quick-nav bar** (`navigation-favorites-bar`, top,
  horizontal): direct links (Manage All Inventory, Payments, Business
  Reports, …). Read their real hrefs from the bar's (shadow) DOM and open
  them.
- **Hamburger** (`navigation-hamburger-menu`, top-left) opens a
  full-height **left drawer** with categories (Catalog, Inventory,
  Pricing, Orders, Advertising, Stores, Growth, Reports, Payments,
  Performance, Apps and Services, Brands, Learn). Categories expand to a
  flyout; if a stable selector isn't obvious, `capture_screenshot()` then
  `click_at_xy(x, y)`.

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
