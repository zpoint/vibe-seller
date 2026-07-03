---
name: noon-shared
description: "Common noon Seller Center mechanics — login (OTP auto-fetch), page-structure URL map, My Catalog read access, common modals, button-click patterns, project-ID discovery. Prerequisite: every other noon-* skill (noon-listing, noon-fbn, noon-exports, noon-ads) expects this loaded for auth and shared UI patterns."
---

# Noon — Shared (login, navigation, common patterns)

This skill covers what every noon Seller Center task needs:
authentication, the page-structure URL map, My Catalog read access,
and shared UI patterns (modals, button clicks). Operation-specific
skills (`noon-listing`, `noon-fbn`, `noon-exports`, `noon-ads`)
load this first.

## 1. Login — Auto-fetch OTP When Email Is Bound

Noon uses **email OTP only** (no password). Before doing ANYTHING,
decide how the OTP will be retrieved — do NOT bother the user if an
email MCP integration can do it for you.

### Decision flow (follow in order, do NOT skip steps)

1. Open the login page and look at the email it's about to send OTP to:
   ```bash
   browser-use <<'PY'
   new_tab("https://login.noon.partners/en/")
   wait_for_load()
   print(page_info())    # find the email on the "We've sent..." screen
                         # OR the prefilled channelIdentifier
   PY
   ```
   Ziniao often auto-fills or remembers a session. The page shows
   `Log in with OTP sent to <email>`.

2. Check whether the task prompt includes a **"## Email System"**
   section (the runner injects it when the store has connected
   email accounts). Compare the bound emails to the Noon login
   email shown on screen:
   - **Match** → fetch the OTP yourself via email MCP (Case A).
   - **No email system section**, OR **emails do not match** →
     ask the user (Case B). Never guess — mismatched emails mean
     a different inbox.

### Case A — Auto-fetch OTP via email MCP (preferred)

This works when the bound email address equals the Noon login
email. One round trip, no user prompt:

```python
# 1. Trigger immediate IMAP poll (don't wait 5 min for auto-sync):
vibe_seller_sync_email_now(account_email='<bound-email>')

# 2. Get the per-account DB path:
vibe_seller_email_info(store_id='<store-id>')
# Returns: { accounts: [{ email, db_path, ... }] }
```

Then read the latest Noon OTP email. The body is HTML — the 6-digit
code sits on its own line inside a styled `<div>`. Extract it with
a regex on `body_html` (not `body_text`, which has escaped markup):

```bash
sqlite3 <db_path> "SELECT body_html FROM emails \
  WHERE folder='INBOX' \
    AND sender LIKE '%verify@noon.com%' \
    AND subject='Verify your email' \
  ORDER BY date DESC LIMIT 1" \
| python3 -c "
import sys, re
html = sys.stdin.read()
# The OTP is a standalone 6-digit number inside a styled div.
# Reject repeated digits (000000, 333333 etc. — those are hex colors).
for m in re.findall(r'>\s*(\d{6})\s*<', html):
    if len(set(m)) > 1:
        print(m); break
"
```

Then fill it and continue:
```bash
browser-use <<'PY'
fill_input("input[name='otp']", "<code>")   # OTP field (adjust selector to the live input)
js("document.querySelector('button[type=submit]').click()")   # Continue
wait_for_load()
print(page_info())
# dismiss passkey prompt (button has no stable id; match by text):
js("Array.from(document.querySelectorAll('button')).find(b=>/maybe later/i.test(b.textContent))?.click()")
PY
```

> **Don't ask the user at all in Case A.** The whole point of the
> binding is that the agent can finish login unattended.

### Case B — Ask the user (only when Case A doesn't apply)

Trigger the OTP, then ask via `AskUserQuestion` (which requires
**2+ options**, not 1 — a single-option call fails validation):

```
AskUserQuestion(questions=[{
  "question": "I've triggered the Noon OTP email to <email>. Please enter the 6-digit code in the browser, then tell me when you're ready.",
  "header": "OTP",
  "options": [
    {"label": "Entered, continue", "description": "I typed the OTP"},
    {"label": "Didn't arrive",     "description": "Resend or switch account"}
  ],
  "multiSelect": false
}])
```

If the user can't receive the OTP, don't guess or retry — escalate
and let them resolve.

### After login — discover `{project_id}` from the URL

```bash
browser-use <<'PY'
# dismiss passkey prompt (match by text — no stable id)
js("Array.from(document.querySelectorAll('button')).find(b=>/maybe later/i.test(b.textContent))?.click()")
wait_for_load()
print(page_info())    # URL now carries ?project=PRJ{project_id}
PY
# Redirects to welcome.noon.partners/... with project=PRJ{project_id}
```

**Do NOT ask the user for the project ID** if the store profile
(`stores/<slug>/STORE.md`) doesn't have it. Read it from the post-
login URL — the welcome / store-home page always carries
`?project=PRJ{project_id}` (e.g. `NNNNNN`). Capture it once, then
reuse for the rest of the task. A project's countries share the same
numeric project ID; only the URL country suffix (`/en-<cc>/`) differs.

Optionally persist what you learned back to the store profile:

```
vibe_seller_write_workspace_file(
  path="stores/<slug>/metadata.json",
  content='{"platform_countries": {"noon": ["EG", "KW"]}, "noon_project_id": "<project_id>"}'
)
```

## 2. Page Structure Cheat Sheet

Most post-login URLs require `?project=PRJ{project_id}` (the login
and welcome pages are the only exceptions). `{project_id}` is the
numeric project identifier. The store identifier in path segments
like `STR{project_id}-N{CC}` reuses the same numeric value with
`STR` prefix and country suffix. Direct URL navigation works for
most pages; sidebar only for Support/Help.

| Page | URL |
|------|-----|
| Create listing | `noon-catalog.noon.partners/en/catalog/create` |
| Edit listing | `noon-catalog.noon.partners/en/catalog/{sku}/d?code={code}&offerTab=noon` |
| My Catalog | `noon-catalog.noon.partners/en/catalog` |
| Catalog Imports | `noon-catalog.noon.partners/en/imports` |
| Catalog Exports | `noon-catalog.noon.partners/en/exports` |
| FBN My ASN & Storage | `fbn.noon.partners/en-{cc}/asn` |
| FBN Create ASN | `fbn.noon.partners/en-{cc}/asn/createasn` |
| FBN My Inventory | `fbn.noon.partners/en-{cc}/inventory` |
| Sales | `reports.noon.partners/en/sales/` |
| Transaction View | `noon-payments.noon.partners/en/transaction-view` |
| Ad Manager | `admanager.noon.partners/en-{cc}/home?mpCode=noon` |
| Campaign Detail | `admanager.noon.partners/en-{cc}/campaign/details/{id}?mpCode=noon` |
| Create Campaign | `admanager.noon.partners/en-{cc}/campaign/start?mpCode=noon` |
| Vantage | `vantage.noon.partners/en/` |

## 3. My Catalog (read access)

**URL**: `https://noon-catalog.noon.partners/en/catalog?project=PRJ{project_id}`

Tabs: `noon` (default), `supermall`, `global`.

Each row has:
- Product title + Brand
- PSKU (Partner SKU) + SKU (noon ID), copyable
- Price, Sale/Promo badge
- Estimated Fees (FBN + FBP separately)
- Active Net Stock (FBN + FBP, links to inventory)
- Performance (Views, Units Sold, Sales)
- Seller Status toggle
- Live Status + "View Issues" link

Click the product title anchor to open the edit page (see
`noon-listing` skill).

For ad-tuning audits, this catalog read is the prerequisite step
that establishes "what the SKU actually is" before reading any
campaign — see `noon-ads/references/ads-tuning.md`.

## 4. Common Modals

### Save Changes Warning

When leaving a page with unsaved changes:
> "Do you want to save the changes you made to this page?"
> [Discard Changes] [Save Changes]

Always click **Save Changes** unless intentionally reverting.

### Penalty Warning (ASN)

Before reserving FBN quota — see `noon-fbn` skill. Click **Cancel**
to exit without commitment.

### Tour Modal

First-time visits show tours. Dismiss with "Skip for now".

### Hotjar Survey

NPS survey at bottom-right. Dismiss with `aria-label="Hide survey"`.

### Passkey Prompt (After Login)

Dismiss with "Maybe Later".

## 5. Button Clicking Patterns

### JS Click for Off-Screen Buttons

Listing wizard "Next" buttons are often outside viewport. Enumerate
the buttons first (a JS click works even off-screen — no scroll needed):
```bash
browser-use <<'PY'
# list every button with its index, label, and disabled state
print(js("""
  return Array.from(document.querySelectorAll('button')).map(
    (b,i) => i + ':' + b.textContent.trim().substring(0,30) + ':d=' + b.disabled
  ).join(' | ');
"""))
PY
# Find the right index, then click it deterministically:
browser-use <<'PY'
js("document.querySelectorAll('button')[0].click()")
PY
```

### Iframe Sidebar Clicks

Toolbar iframe is cross-origin; read the parent category button's
bounding box, then click below it at an estimated offset:
```bash
browser-use <<'PY'
# expand the category, then read its bounding box
js("document.querySelector('<category-css>').click()")
box = js("var r=document.querySelector('<category-css>').getBoundingClientRect(); return {x:r.x, y:r.y, width:r.width, height:r.height};")
print(box)
# Sub-items are ~24-30px tall below, indented ~35px from left
click_at_xy(box['x'] + 35, box['y'] + 50)   # first sub-item
PY
```

## 6. Tips (general)

- **Always use direct URLs** for known pages (faster, more reliable).
- **Login OTP — prefer email MCP over asking the user.** If the
  store has a bound email account matching the Noon login email,
  fetch the OTP yourself (Case A). Only ask when no binding applies.
- **Project ID is required** in the URL: `?project=PRJ{project_id}`.
- **Country switching varies by page** — Sales/Transaction use bottom-left
  store dropdown; Ad Manager uses URL country suffix; FBN paths embed
  the country code directly. Check per-page.
- **Save changes before navigating** — warning modal can silently
  lose data if you click Discard.
- **`fill_input(sel, text)` vs `type_text(text)`.** `fill_input`
  targets an element by CSS selector and sets its value; `type_text`
  types into whatever is currently focused. They are not
  interchangeable — pick `fill_input` when you have a selector, and
  `type_text` (usually after a click/focus) when you don't.
- **`AskUserQuestion` needs 2+ options** — a single-option call
  fails schema validation; always give at least a confirm and a
  "didn't work" option.
- **Captures → `/tmp/<run-slug>/`.** Per-run live data goes to a
  temp dir, never under `~/.vibe-seller/knowledge/`.

## See also

- `noon-listing` — create / edit SKUs (price, stock, content)
- `noon-fbn` — FBN/ASN, inventory, barcode print
- `noon-exports` — Sales report, Transaction View, Catalog Exports
- `noon-ads` — Ad Manager (campaigns, tuning, keyword research, negatives)
