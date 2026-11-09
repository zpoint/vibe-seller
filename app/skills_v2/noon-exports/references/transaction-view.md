# noon Transaction View — download flow and CSV shape

Read from `noon-exports` § 2.

## Contents

1. Download flow
2. Recovering a running export after a disconnect
3. CSV structure and splitting by country

## 1. Download flow

Each heredoc is one short unit of work; the polling loop runs in the
shell so no single `browser-use` call approaches the wrapper's time
limit.

```bash
# 1. Navigate and set the date range, then read it back.
browser-use <<'PY'
new_tab("https://noon-payments.noon.partners/en/transaction-view?project=PRJ{project_id}")
wait_for_load()
fill_input('input[placeholder="Start date"]', "YYYY-MM-DD")
fill_input('input[placeholder="End date"]',   "YYYY-MM-DD")
# Optional: filter the Contracts dropdown to one country's contract.
print(js("return ['Start date','End date'].map(function(p){"
         "var i=document.querySelector('input[placeholder=\"'+p+'\"]');"
         "return p+'='+(i?i.value:'MISSING');});"))
PY

# 2. Click the MAIN Export button once to queue the job. Its text flips
#    to "Exporting" and back to "Export" when the job is done; clicking
#    it again queues another job rather than downloading.
browser-use <<'PY'
print(js("var b=Array.from(document.querySelectorAll('button')).find(b=>/^(export|download)$/i.test(b.textContent.trim())); if(b){b.click(); return 'clicked: '+b.textContent.trim();} return 'main export button not found';"))
PY

# 3. Poll for "Processed" — up to ~35 min for data-heavy months. Leave
#    the panel open and do not re-click the main button.
for i in $(seq 1 70); do
  sleep 30
  browser-use <<'PY' | grep -q "Processed" && break
print(js("return document.body.innerText.includes('Processed') ? 'Processed' : 'waiting'"))
PY
done

# 4. Click the Export button INSIDE the results panel — the one that
#    writes the file. Scope to the ExportBox_content container so the
#    main button is not matched. The panel renders below the table.
browser-use <<'PY'
js("window.scrollTo(0, document.body.scrollHeight)")
print(js("var p=document.querySelector('[class*=ExportBox_content]'); if(!p) return 'panel not rendered yet — poll longer'; var b=Array.from(p.querySelectorAll('button')).find(b=>/export/i.test(b.textContent)); if(b){b.click(); return 'clicked panel export';} return 'no export button inside ExportBox panel';"))
PY
```

The file lands in `~/.vibe-seller/downloads/<slug>/` with a name starting
`noon_financeweb_transactionviewreportonitemlevelwithcontractselection`.
Repeat downloads get ` (1)`, ` (2)` suffixes; take the newest by mtime
and move it into the task workspace under the name the task needs
(`noon-exports` § "Where files go").

## 2. Recovering a running export after a disconnect

If the page refreshes or the browser session drops during the wait, the
job is still running on noon's side. Query its status instead of clicking
the main button again (which would queue a second job):

```bash
browser-use <<'PY'
print(js("""
  var xhr = new XMLHttpRequest();
  xhr.open('POST', '/_svc/mp-partner-impex-api/export/status', false);
  xhr.setRequestHeader('Content-Type', 'application/json');
  xhr.send(JSON.stringify({exportCode: '<EXP_CODE>'}));
  return xhr.responseText;
"""))
PY
# → {"export": {"status_code": "COMPLETE", "download_url": "https://storage.googleapis.com/..."}}
curl -sL -o transaction_view.csv "<download_url>"
```

## 3. CSV structure and splitting by country

Header columns, in order:

```csv
Contract,Contract Title,Reference Nr,Order Nr,Item Nr,Order Date,
Transaction Date,Title,SKUs,Partner SKUs,Transaction Type,Currency,
Net Proceeds,Referral Fee including VAT,
Fullfilment & Logistics Fees including VAT,  ← sic, noon's spelling
Shipping Credits including VAT,Other Order Fees including VAT,
Order Subsidies including VAT,Non-Order Fees including VAT,
Non-Order Subsidies including VAT,Others including VAT,Total
```

- `Contract` is the contract code: 12 characters, `MP` prefix,
  country-code suffix (e.g. `MPXXXXXXXXEG`). The last two characters
  bucket rows by country.
- `Contract Title` is the readable name (`Noon EG`, `Noon KW`, …).
- `SKUs` is noon's internal key (`Z…Z-<n>`) and `Partner SKUs` the
  seller's own code, on the same row — the bridge between the two
  keyspaces used by the FBN fee and Ad Manager exports.
- Observed `Transaction Type` values: `order`, `order_update`,
  `statement_fee`, `payment`, `balance_transfer`. noon may add more, so
  filter by the values you need rather than assuming the set is closed.

```python
import pandas as pd

df = pd.read_csv('<exported-file>.csv')
for cc in df['Contract'].str[-2:].unique():
    df[df['Contract'].str.endswith(cc)].to_csv(
        f'transactions_{cc}.csv', index=False
    )
```
