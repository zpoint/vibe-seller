---
name: amazon-shared
description: "Common Amazon Seller Central / advertising-console mechanics — marketplace TLD map, hamburger-menu hover navigation, sign-in / Ziniao auto-fill / OTP redirect handling, ad-console vs seller-central account caveat, capture rule. Prerequisite: every other amazon-* skill (amazon-ads, amazon-reports, amazon-invoice, future amazon-listing / amazon-fbn / etc.) expects this loaded for cross-cutting auth and navigation patterns."
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
even if a previous Seller Central session is still good. Ziniao
auto-fills the password and the OTP / 2FA code (via the 紫鸟验证码
service when configured for the store). The agent's job is to wait
for the auto-fill to land, click the submit button, and let the
redirect settle.

```bash
browser-use open "https://advertising.amazon.<tld>/campaign-manager"
sleep 3 && browser-use state                  # password + OTP usually pre-filled
browser-use click <signInSubmit>              # or <mfaSubmit> after OTP
sleep 5 && browser-use state                  # redirect settles
```

`browser-use state` does NOT show auto-filled input values — fields
appear empty even when filled. Use `browser-use get value <index>`
to confirm a password / OTP field was auto-filled before deciding
whether to ask the user.

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

## 4. Hamburger-menu hover navigation

Many seller-central pages (Reports, Performance, Inventory submenus)
are reachable only via the **hamburger menu** at top-left. The menu
uses a **hover-to-reveal** pattern — you must `hover`, not `click`,
on a parent category to reveal its submenu items.

```bash
# 1. Open the hamburger menu (opens the full sidebar overlay)
browser-use state                # find menu button (role=button inside
                                 # navigation-hamburger-menu shadow DOM)
browser-use click <menu-btn>

# 2. Reveal submenu by hovering
browser-use state                # find category (aria-expanded=false)
browser-use hover <category-idx> # changes aria-expanded to true; submenu appears

# 3. Click the submenu item
browser-use state                # find target item
browser-use click <item-idx>     # navigates to the page
```

**Do NOT click the category name directly** — clicking navigates
away instead of revealing the submenu. Always `hover` first.

## 5. Capture rule

Per-run live data (campaign captures, search-term exports, screenshots,
ad-spend snapshots) goes to `/tmp/<run-slug>/`. **Never** under
`~/.vibe-seller/knowledge/` — that path is for codified, reusable
facts about the platform, NOT per-run private business data. The
catalog gets synced into agent contexts, so private data there leaks
across sessions.

## 6. Common "always load X first" pattern

Before any browser-use call against Amazon:

1. Load `browser-use` skill (CLI syntax + wrapper rules).
2. Load this skill (`amazon-shared`) for the mechanics above.
3. Load the operation-specific skill (`amazon-ads`, `amazon-reports`,
   …) and any of its references that the task needs.

The wrapper auto-starts the browser on first `open`, so you don't
need to pre-warm the CDP proxy. Just open the URL and wait ~3s for
state.

## See also

- `amazon-ads` — Sponsored Products / Brands / Display + Coupons
- `amazon-reports` — Business / Fulfillment / Tax / Payments / Advertising reports
- `amazon-invoice` — Tax-invoice PDF generation from order data
