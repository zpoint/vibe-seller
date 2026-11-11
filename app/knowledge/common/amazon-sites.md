# Amazon Seller Central URLs

## Seller Central Base URLs by Country

### North America

| Country | Code | Seller Central URL |
|---------|------|--------------------|
| United States | US | `https://sellercentral.amazon.com` |
| Canada | CA | `https://sellercentral.amazon.ca` |
| Mexico | MX | `https://sellercentral.amazon.com.mx` |
| Brazil | BR | `https://sellercentral.amazon.com.br` |

### Europe (Unified)

UK, DE, FR, IT, ES share a unified URL:

| Country | Code | Seller Central URL |
|---------|------|--------------------|
| United Kingdom | UK | `https://sellercentral-europe.amazon.com` |
| Germany | DE | `https://sellercentral-europe.amazon.com` |
| France | FR | `https://sellercentral-europe.amazon.com` |
| Italy | IT | `https://sellercentral-europe.amazon.com` |
| Spain | ES | `https://sellercentral-europe.amazon.com` |

### Europe (Country-specific domains)

| Country | Code | Seller Central URL |
|---------|------|--------------------|
| Netherlands | NL | `https://sellercentral.amazon.nl` |
| Belgium | BE | `https://sellercentral.amazon.com.be` |
| Sweden | SE | `https://sellercentral.amazon.se` |
| Poland | PL | `https://sellercentral.amazon.pl` |
| Ireland | IE | `https://sellercentral.amazon.ie` |
| Turkey | TR | `https://sellercentral.amazon.com.tr` |

### Middle East & Africa

| Country | Code | Seller Central URL |
|---------|------|--------------------|
| UAE | AE | `https://sellercentral.amazon.ae` |
| Saudi Arabia | SA | `https://sellercentral.amazon.sa` |
| Egypt | EG | `https://sellercentral.amazon.eg` |
| South Africa | ZA | `https://sellercentral.amazon.co.za` |

### Asia-Pacific

| Country | Code | Seller Central URL |
|---------|------|--------------------|
| India | IN | `https://sellercentral.amazon.in` |
| Japan | JP | `https://sellercentral.amazon.co.jp` |
| Australia | AU | `https://sellercentral.amazon.com.au` |
| Singapore | SG | `https://sellercentral.amazon.sg` |

## Common URL Paths

Append these to any base URL above:

| Function | Path |
|----------|------|
| Home/Dashboard | `/home` |
| Manage Orders | `/orders-v3` |
| Order Details | `/orders-v3/order/{orderId}` |
| Inventory (Manage All Inventory) | `/myinventory/inventory` |
| Inventory — FBA view | `/amazonsell/fba-inventory` |
| Pricing Dashboard | `/pricing/dashboard` |
| Business Reports | `/business-reports` |
| Fulfilment / FBA reports | `/reportcentral/WelcomePage` |
| Custom (listing) reports | `/listing/reports` |
| Tax Document Library | `/tax/seller-fee-invoices` |
| Manage promotions (coupons & deals) | `/manage/promotions` |
| Account Health | `/performance/dashboard` |
| Feedback Manager | `/performance/feedback` |
| Payments | `/payments/dashboard/index.html` |
| Payments reports | `/payments/reports-repository` |
| Settings | `/settings/account-info` |
| A+ Content | `/aplus-manager` |
| Brand Registry | `/brandregistry` |
| Help/Case Log | `/help/hub/support` |
| Send to Amazon (FBA inbound) | `/fba/sendtoamazon` |
| FBA shipments queue | `/fba/inbound-shipments` |

These paths render under BOTH UI generations (New Seller Central and
classic) — the New Seller Central trim (`ngstrim-*` chrome) wraps the
same page bodies. Verified live 2026-10-31 on `sellercentral.amazon.sa`.
`/business-analytics` (seen in older docs) is not the current path —
use `/business-reports`.

## How to Determine the Correct URL

Given a store with `countries: [SA]`, the seller center base URL is
`https://sellercentral.amazon.sa`. Don't guess — look the country up in
the table above.

Common mistakes:
- **WRONG**: `sellercentral.amazon.com.sa` (not a valid Amazon domain)
- **RIGHT**: `sellercentral.amazon.sa`
- **WRONG**: `sellercentral.amazon.co.uk` (old pattern)
- **RIGHT**: `sellercentral-europe.amazon.com` (unified EU account)

## Country Switching (In-Session)

Accounts with several linked marketplaces (e.g. SA + AE) switch country
in-session, without a new login: use the full-page switcher
`/account-switcher/default/merchantMarketplace`. The recipe and its
helper script are in the `amazon-shared` skill (marketplace switcher
section). After switching, pages load the new country's data; do not
open the other marketplace's domain and sign in again.

## Order Search URL Patterns

Navigate to these URLs with the browser skill (see it for exact
commands):

```
# Direct order detail page
https://sellercentral.amazon.sa/orders-v3/order/{orderId}

# Order list with search
https://sellercentral.amazon.sa/orders-v3?searchTerm={orderId}
```
