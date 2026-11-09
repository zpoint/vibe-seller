# Payments Reports Repository

## Contents

- [Page and tabs](#page-and-tabs)
- [Pick a calendar month with the Month radio](#pick-a-calendar-month-with-the-month-radio)
- [Request and download](#request-and-download)
- [Transaction report CSV structure](#transaction-report-csv-structure)

## Page and tabs

**URL:** `https://sellercentral.amazon.{tld}/payments/reports-repository`
(opens on the Reports Repository tab).

| Tab | Description |
|-----|-------------|
| Statement View | Settlement period summary: Net Proceeds, Sales, Refunds, Expenses, Balance |
| Transaction View | Individual transactions within a settlement |
| All Statements | Historical settlement statements |
| Disbursements | Bank disbursement details |
| Advertising Invoice History | Ad billing invoices |
| Reports Repository | Custom date-range CSV reports |

Report type dropdown values:

| Value | Label | Description |
|-------|-------|-------------|
| `SELLER_TRANSACTION_DATE_RANGE` | Transaction | Per-order/per-event transaction details |
| `SELLER_SUMMARY_DATE_RANGE` | Summary | Aggregated summary for the period |

## Pick a calendar month with the Month radio

`Reporting Date Range` has two modes:

| Mode | Controls | Use for a whole month? |
|---|---|---|
| `Custom Date Range` (default) | From/To pickers, calendar three shadow roots down | No |
| **`Month`** | two `kat-dropdown`s: month + year | **Yes** |

`Month` replaces the pickers with two dropdowns, which removes the calendar
walk and the silent one-day-range failure.

The radio needs a coordinate click **on its circle**, not its label: the
element box spans the label, so its centre lands on the text and does
nothing, and JS `.click()` doesn't work on `kat-radiobutton`. Take the left
edge:

```bash
browser-use <<'PY'
import time
new_tab("https://sellercentral.amazon.{tld}/payments/reports-repository")
wait_for_load(); time.sleep(6)
r = js("""
  var rs = [].slice.call(document.querySelectorAll('kat-radiobutton'));
  for (var i=0;i<rs.length;i++){
    if (/Month/.test(rs[i].getAttribute('label')||rs[i].innerText||'')){
      var b = rs[i].getBoundingClientRect();
      return {x: Math.round(b.x + 9), y: Math.round(b.y + b.height/2)};
    }}
  return null;
""")
click_at_xy(r["x"], r["y"]); time.sleep(2.5)
dd = js("""
  var d = document.querySelectorAll('kat-dropdown');
  return [].slice.call(d).map(function(x){ return x.getAttribute('value'); });
""")
print("dropdowns:", dd)   # e.g. ['PAYABLE','SELLER_TRANSACTION_DATE_RANGE','6','2026']
PY
```

If `dd` still shows only the report dropdowns (pickers still present), the
radio didn't take — re-read coordinates and click again. A request fired in
`Custom Date Range` mode with empty pickers silently uses today.

**The month value is 0-indexed** (`Jan=0`, so `6` is July) and defaults to
last month. Confirm against the visible label rather than the number.

To pick another month, the options are inside the dropdown's own
`shadowRoot` — `document.querySelectorAll('kat-option')` returns `[]`, and
`selectOption('6')` leaves the value unchanged. Open it and click the
option:

```bash
browser-use <<'PY'
import time
dd = "document.querySelectorAll('kat-dropdown')[2]"   # 3rd = month (see readback)
b = js(f"var b={dd}.getBoundingClientRect(); return {{x:Math.round(b.x+b.width/2), y:Math.round(b.y+b.height/2)}}")
click_at_xy(b["x"], b["y"]); time.sleep(1.5)
opts = js(f"""return [].map.call({dd}.shadowRoot.querySelectorAll('kat-option'), o => {{
  var r = o.getBoundingClientRect();
  return {{v: o.getAttribute('value'), x: Math.round(r.x+r.width/2), y: Math.round(r.y+r.height/2)}};
}})""")
o = next(o for o in opts if o["v"] == "6")            # July
click_at_xy(o["x"], o["y"]); time.sleep(1.5)
print(js(f"return {dd}.shadowRoot.querySelector('.selection-text').innerText.trim()"))  # → July
PY
```

The same open → read shadow options → `click_at_xy` method selects the
report type (Transaction / Summary).

Any reload (re-login, `new_tab()` on this URL) resets the form to
`Custom Date Range` — re-click the `Month` radio and re-read `dd` before the
next request.

## Request and download

1. Select the report type and month as above.
2. Click **Request Report** (a `kat-button`; host → inner-button walk from
   SKILL.md).
3. Poll every ~30-60 s (one check per invocation). Generation takes ~1-3
   min. Row status: spinner = generating; green check
   (`kat-icon name=check_circle`) + "Ready" = downloadable.
4. Scroll down (`js("window.scrollTo(0, document.body.scrollHeight)")`) and
   download the Ready row with the download snippet, matching the
   "Download CSV" label.

"Download CSV" fires an XHR GET to
`/payments/reports/api/download-report?reportId={UUID}`; the browser saves
the CSV to the store downloads dir. If nothing lands, that endpoint can be
fetched from the page context.

The monthly transaction file downloads as `<YYYY><Mon>MonthlyTransaction.csv`
(e.g. `2026JulMonthlyTransaction.csv`) — the same name for every
marketplace, so move it out before switching marketplace (SKILL.md,
"Downloads").

## Transaction report CSV structure

7 preamble lines, then the header row, then data:

```
Line 1: "Includes Amazon Marketplace, Fulfillment by Amazon (FBA), and Amazon Webstore transactions"
Line 2: "All amounts in {currency}, unless specified"  (e.g. AED, USD, EUR)
Lines 3-7: Column definitions
Line 8: Column headers
Line 9+: Data rows
```

Columns:

```csv
"date/time","settlement id","type","order id","sku","description","quantity",
"marketplace","fulfillment","order city","order state","order postal",
"product sales","shipping credits","gift wrap credits","promotional rebates",
"sales tax collected","Marketplace Facilitator Tax","selling fees","fba fees",
"other transaction fees","other","total"
```

| Column | Type | Example | Description |
|--------|------|---------|-------------|
| date/time | datetime | `15 Mar 2026 3:22:08 PM UTC` | Transaction timestamp |
| settlement id | string | `10000000001` | Settlement period ID |
| type | enum | `Order` | See types below |
| order id | string | `123-1234567-1234567` | Amazon order ID |
| sku | string | `WIDGET-001` | Your SKU |
| description | string | Product title | Full product name |
| quantity | int | `2` | Units |
| marketplace | string | `amazon.<tld>` | Marketplace domain — exactly one value per file |
| fulfillment | string | `Amazon` | `Amazon` (FBA) or `Seller` |
| order city / state / postal | string | | Buyer location (city may be in the local language; postal may be empty) |
| product sales | decimal | `89.00` | Product revenue |
| shipping credits | decimal | `5.00` | Shipping collected |
| gift wrap credits | decimal | `0` | Gift wrap revenue |
| promotional rebates | decimal | `-4.45` | Promotions (negative) |
| sales tax collected | decimal | `4.45` | Sales tax / VAT |
| Marketplace Facilitator Tax | decimal | `0` | Amazon-collected tax |
| selling fees | decimal | `-13.35` | Referral fees (negative) |
| fba fees | decimal | `-18.50` | FBA fees (negative) |
| other transaction fees | decimal | `0` | Misc fees |
| other | decimal | `0` | Non-order amounts |
| total | decimal | `62.15` | Net amount |

Transaction types (`type`): `Order` (sale, the bulk of rows), `Refund`,
`Adjustment`, `Service Fee` (subscription/service charges), `Transfer`
(bank disbursements), `FBA Inventory Fee` (storage/removal).

Parsing hint:

```python
import pandas as pd

df = pd.read_csv(
    'transaction_report.csv', skiprows=7, dtype={'order postal': str}
)
profit = df.groupby('sku')['total'].sum().sort_values()
df['month'] = pd.to_datetime(
    df['date/time'], format='%d %b %Y %I:%M:%S %p %Z'
).dt.to_period('M')
monthly = df[df['type'] == 'Order'].groupby('month')['product sales'].sum()
```
