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
| Inventory | `/inventory/manageInventory` |
| Listings | `/listings/manage` |
| Pricing Dashboard | `/pricing/dashboard` |
| Business Reports | `/business-analytics` |
| Account Health | `/performance/dashboard` |
| Feedback Manager | `/performance/feedback` |
| Payments | `/payments/transaction-view` |
| Settings | `/settings/account-info` |
| A+ Content | `/aplus-manager` |
| Brand Registry | `/brandregistry` |
| Help/Case Log | `/help/hub/support` |
| FBA Shipments | `/fba/inbound-shipments` |

## How to Determine the Correct URL

Given a store with `countries: [SA]`, the seller center base URL is
`https://sellercentral.amazon.sa`. **Do NOT guess** — always look up the
country in the table above.

Common mistakes:
- **WRONG**: `sellercentral.amazon.com.sa` (not a valid Amazon domain)
- **RIGHT**: `sellercentral.amazon.sa`
- **WRONG**: `sellercentral.amazon.co.uk` (old pattern)
- **RIGHT**: `sellercentral-europe.amazon.com` (unified EU account)

## Country Switching (In-Session)

Amazon Seller Central supports **global country switching** without re-login
for accounts that have multiple marketplaces linked (e.g. SA + AE).

The country switcher button is near the store name at the top of the page
(`button aria-label=Switch Accounts`). It shows the current country name.

Workflow (see the browser-use skill for exact commands):
1. Inspect the page to find the "Switch Accounts" button.
2. Click it.
3. Inspect the page again to find the country options.
4. Click the target country option — the page reloads with the new
   country context.

After switching, all pages load data for the new country automatically.
You do NOT need separate login sessions for different countries under the
same account.

**Note**: Some accounts may have the switcher inside the
`#ngstrim-account-switcher-dropdown` div. If the button is not visible,
try clicking on the store name / country text area to reveal it.

## Order Search URL Patterns

Navigate to these URLs with the browser-use skill (see it for exact
commands):

```
# Direct order detail page
https://sellercentral.amazon.sa/orders-v3/order/{orderId}

# Order list with search
https://sellercentral.amazon.sa/orders-v3?searchTerm={orderId}
```
