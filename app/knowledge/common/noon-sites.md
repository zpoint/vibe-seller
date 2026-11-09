# Noon Seller Center URLs & Navigation

## Portal Domains

Noon uses **separate subdomains** for each feature area, all under
`noon.partners`. Most post-login portal URLs require a
`project=PRJ{project_id}` query param. Exceptions: the login and
welcome pages accept the URL without it (the welcome page adds it
on redirect after auth).

| Feature | Domain | Direct URL works? |
|---------|--------|-------------------|
| Login | `login.noon.partners` | Yes |
| Welcome / Home | `welcome.noon.partners` | Yes |
| Store Dashboard | `noon-store.noon.partners` | Yes |
| Catalog Management | `noon-catalog.noon.partners` | Yes |
| FBN (Fulfilled by noon) | `fbn.noon.partners` | Yes |
| Sales & Account Health | `reports.noon.partners` | Yes |
| Payments & Transactions | `noon-payments.noon.partners` | Yes |
| Ad Manager | `admanager.noon.partners` | Yes (via sidebar first to discover) |
| Vantage Analytics | `vantage.noon.partners` | Yes |
| Toolbar / Sidebar | `toolbar.noon.partners` | No (iframe only) |
| Support Center | Unknown subdomain | **No** — sidebar only |
| Help Center | Unknown subdomain | **No** — sidebar only |

## URL Patterns

### Store Dashboard

```
https://noon-store.noon.partners/en/STR{project_id}-N{CC}/home/?project=PRJ{project_id}
```

The path segment `STR{project_id}-N{CC}` is the **store identifier**.
The `project` query param is the **project identifier**. These are
distinct concepts, but based on observed accounts they share the
**same numeric value** (the `STR` prefix + country suffix plus the
`PRJ` prefix are the only difference). Substitute the same number
into both placeholders.

| Country | Suffix | Example |
|---------|--------|---------|
| (per marketplace) | `N{CC}` | `STR123456-N{CC}` |

where `{CC}` is the marketplace's ISO country code (so the suffix is
`N` + the code, e.g. the value you read from the live store selector).

### Catalog

```
https://noon-catalog.noon.partners/en/{page}?project=PRJ{project_id}
```

| Page | Path |
|------|------|
| My Catalog | `/catalog` (redirects to `?tab=noon`) |
| Add Product (create SKU) | `/catalog/create` |
| Product Detail (edit) | `/catalog/{noon_sku}/d?code={code}&offerTab=noon` |
| Imports | `/imports` |
| Exports | `/exports` |

### FBN

Country code is part of the path (not query). The inbound pages (My
ASN & Storage, Create ASN) live on their own host,
`fbn-inbound.noon.partners`; the old `fbn.noon.partners/en-{cc}/asn`
path no longer loads. The rest of FBN stays on `fbn.noon.partners`:

```
https://<host>/en-{cc}/{page}?project=PRJ{project_id}
```

| Page | Host | Path |
|------|------|------|
| My ASN & Storage | `fbn-inbound.noon.partners` | `/` (the `en-{cc}` root) |
| Create ASN | `fbn-inbound.noon.partners` | `/asn/create` |
| My Inventory | `fbn.noon.partners` | `/inventory` |
| My Returns (RTV) | `fbn.noon.partners` | `/rtv/requests` |
| FBN Fees | `fbn.noon.partners` | `/fees` |
| Reports | `fbn.noon.partners` | `/fbnreports` |

### Sales & Reports

```
https://reports.noon.partners/en/sales/?project=PRJ{project_id}
```

Supports query params for date filtering:
```
?from_date=2026-03-01&to_date=2026-03-31&project=PRJ{project_id}
```

### Payments & Transactions

```
https://noon-payments.noon.partners/en/{page}?project=PRJ{project_id}
```

| Page | Path |
|------|------|
| Statements | `/statements` |
| SOA (Statement of Account) | `/soa` |
| Transaction View | `/transaction-view` |
| Invoices & Creditnotes | `/invoices` |
| Legacy Exports | `/legacy-exports` |

### Ad Manager

```
https://admanager.noon.partners/en-{cc}/{page}?mpCode=noon&project=PRJ{project_id}
```

| Page | Path |
|------|------|
| Campaigns Overview | `/home` |
| Campaign Detail | `/campaign/details/{campaign_id}` |
| Create Campaign | `/campaign/start` |
| Budget | `/budget` |
| Billing | `/billing` |
| Vantage | `/vantage` |
| Settings | `/settings` |

### Vantage Analytics

```
https://vantage.noon.partners/en/?project=PRJ{project_id}
```

First-time visit shows a country selector (one option per marketplace)
and an account picker.

## Country Switching

Two mechanisms, and the URL does not always say which one is in force
(the `noon-shared` skill § 2 has the flag-reading recipe):

- **Country in the path** (`/en-{cc}/` — Ad Manager, FBN): navigate
  with the target code in the path and you are in that country. The
  project ID stays the same.
- **Sidebar store switcher** (the `/en/` portals — My Catalog, Imports,
  Exports, Sales, Transaction View, Vantage): the URL is identical for
  every country, so read the flag on the page before trusting any data.
  The Sales page's "My Stores" control and Transaction View's flag /
  "Contracts" dropdown show this same scope — they are not filters (the
  Sales "Destination" column, for one, only shows where orders shipped).

The Store Dashboard embeds the country in its path suffix (`-N{CC}`),
like the path-scoped portals.

## Login Flow — OTP via Email MCP, User Only as Fallback

Noon uses **email OTP only** (no password). If the store has an
email MCP integration bound and the bound email matches the Noon
login email, the agent should fetch the OTP itself. Only fall back
to asking the user when no bound email matches.

### Decision

1. Open `https://login.noon.partners/en/`. Ziniao may auto-fill or
   show a "Log in with OTP sent to `<email>`" screen for a saved
   session. Record that `<email>`.
2. Look at the **"## Email System"** section injected into the task
   prompt (present when the store has connected email accounts).
   Compare those bound addresses to `<email>`.
   - **Match** → Case A (fetch the OTP).
   - **No section** or **mismatch** → Case B (ask the user).

### Case A — Fetch OTP yourself

```
vibe_seller_sync_email_now(account_email="<bound-email>")
vibe_seller_email_info(store_id="<store-id>")
# → gives db_path for the account
```

Then extract the 6-digit OTP from the most recent Noon verification
email. Use `body_html` (not `body_text`) — the HTML wraps the code
in a styled `<div>`. Skip all-identical-digit matches (e.g.
`000000`, `333333`) — those are hex colors in inline styles, not
the OTP.

```bash
sqlite3 <db_path> \
  "SELECT body_html FROM emails \
    WHERE folder='INBOX' \
      AND sender LIKE '%verify@noon.com%' \
      AND subject='Verify your email' \
    ORDER BY date DESC LIMIT 1" \
| python3 -c "
import sys, re
for m in re.findall(r'>\s*(\d{6})\s*<', sys.stdin.read()):
    if len(set(m)) > 1:
        print(m); break
"
```

Type the extracted code into the OTP field via the browser skill (see
it for exact commands). Click Continue → dismiss "Set
up a passkey" with Maybe Later.

### Case B — Ask the user

Use `AskUserQuestion` **with 2+ options** (1 option fails
validation). Offer at minimum an "Entered, continue" and a
"Didn't arrive" option so the tool call is valid and the user has
a retry path.

### Do NOT

- Ask the user for the OTP when an email MCP binding can retrieve
  it. That wastes the user's time and defeats the integration.
- Attempt to skip or bypass OTP — there is no password fallback.
- Proceed past Continue without confirming the OTP was accepted.

### Session Persistence

Once logged in, the session is cookie-based and stable across pages
on `*.noon.partners` domains for the duration of the browser session.
The project ID (`PRJ{project_id}`) appears as a query param in all post-login
URLs.

## Sidebar Navigation (Cross-Origin)

The sidebar menu lives inside a cross-origin `toolbar.noon.partners`
iframe. It opens via a hamburger button inside the iframe. The menu
items are rendered by the toolbar, and clicking them navigates the
**parent page** to the target URL.

**Important**: Sidebar sub-items (e.g. "Ad Manager" under "Ads") are
text nodes inside the iframe without clickable element indices when you
inspect the page — so they can't be clicked by index.

### Clicking Iframe Sidebar Items

1. Open the hamburger (a `div role=button` near the iframe logo) via
   browser-use.
2. Click the parent category (e.g. "Ads") to expand — this one IS
   clickable by element index.
3. Read the category's bounding box via browser-use to get its
   position (returns `{x, y, width, height}`).
4. The sub-items appear below the category (each ~24px tall). Do a
   coordinate click below it — offset y+~50px per sub-item, x+offset
   for indent — to hit the desired sub-item.

See the browser skill for the exact commands to open elements,
read a bounding box, and coordinate-click.

Prefer **direct URL navigation** whenever possible (see URL Patterns
above).

## Navigation Cheat Sheet

```
noon.com Seller Center
├── Home                     noon-store.noon.partners/en/STR{project_id}-N{CC}/home/
├── Catalog                  noon-catalog.noon.partners/en/
│   ├── Add Product          /catalog/create
│   ├── My Catalog           /catalog
│   ├── Product Detail       /catalog/{sku}/d?code={code}
│   ├── Imports              /imports
│   └── Exports              /exports
├── Fulfilled by noon        fbn.noon.partners/en-{cc}/
│   ├── My ASN & Storage     fbn-inbound.noon.partners/en-{cc}/
│   ├── Create ASN           fbn-inbound.noon.partners/en-{cc}/asn/create
│   ├── My Inventory         /inventory
│   ├── My Returns (RTV)     /rtv/requests
│   ├── FBN Fees             /fees
│   └── Reports              /fbnreports
├── Sales & Reports          reports.noon.partners/en/sales/
├── Payments & Fees          noon-payments.noon.partners/en/
│   ├── Statements           /statements
│   ├── SOA                  /soa
│   ├── Transaction View     /transaction-view
│   ├── Invoices & CN        /invoices
│   └── Legacy Exports       /legacy-exports
├── Ads                      admanager.noon.partners/en-{cc}/
│   ├── Campaigns            /home
│   ├── Campaign Detail      /campaign/details/{id}
│   ├── Create Campaign      /campaign/start
│   ├── Budget               /budget
│   ├── Billing              /billing
│   ├── Vantage (analytics)  /vantage
│   └── Settings             /settings
├── Vantage Analytics        vantage.noon.partners/en/
└── Support/Help             [sidebar only, no direct URL]
```

All post-login URLs take `?project=PRJ{project_id}` — the login and
welcome pages are the only exceptions (see the top of this file).
