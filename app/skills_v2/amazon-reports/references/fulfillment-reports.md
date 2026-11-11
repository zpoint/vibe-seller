# Fulfillment (FBA) reports

## Contents

- [Report Central](#report-central)
- [Monthly Storage Fees: publication latency](#monthly-storage-fees-publication-latency)
- [FBA Customer Returns: exact dates](#fba-customer-returns-exact-dates)
- [The calendar walk (three shadow roots)](#the-calendar-walk-three-shadow-roots)

## Report Central

**URL:** `https://sellercentral.amazon.{tld}/reportcentral/WelcomePage`
(Return Reports and most inventory reports live here too.)

Left-sidebar report links carry `id=report-nav-link-url`:

| Category | Reports |
|----------|---------|
| Inventory | Inventory ledger report, Stranded Inventory, Reserved Inventory, Amazon Fulfilled Inventory, Manage FBA Inventory |
| Shipping | Inbound Performance, Outlet Deals |
| Orders | Amazon Fulfilled Shipments, Amazon Fulfilled Shipments – Tax Invoicing, All Orders |
| Fees | Fee Preview, Monthly Storage Fees, Aged Inventory Surcharge report |
| Returns | Reimbursements, FBA customer returns, Replacements, Removal Order Detail, Removal Shipment Detail |
| Other | EPR Category Reports |

Each report page has a **Download** tab with a history table: Report Type |
Date Range Covered | Date Requested | File Format | Report Status. Reuse a
row that already covers the requested range.

Flow:

```bash
browser-use <<'PY'
new_tab("https://sellercentral.amazon.{tld}/reportcentral/WelcomePage")
wait_for_load()
print(page_info())   # sidebar links carry id=report-nav-link-url
PY
```

Then: open the report from the sidebar, set the date range (see the
date-range section of SKILL.md), click **Request Report** (a `kat-button`:
use the host → inner-button walk from SKILL.md), poll until the new row is
ready (typically 1-5 min), and download it with the download snippet,
matching the row by its date-range substring. Confirm the new row's **Date
Range Covered** is what you asked for before downloading.

## Monthly Storage Fees: publication latency

Storage fees for month M are not published early in M+1. Requests made in
roughly the first two weeks of M+1 return `No Data Available` (the request
itself is accepted); the same range requested from about day 15 of M+1
returns a Download within ~30 s. Older months (M-2, M-3) request fine at
any time. Repeat requests do not make Amazon publish sooner.

The page header's "Report data 24 hours old" is about freshness of
already-published values, not about when a month appears.

When Storage Fees for the previous month comes back empty:

1. Don't keep retrying in the same task.
2. Produce every other expected file (transaction CSV, FBA customer
   returns, the ads report) — those publish on a different cadence.
3. Report it as pending in the task result. A later run picks it up as a
   backfill.

You don't have to classify this yourself: the server derives the expected
file set from the store's capabilities and the calendar, and records a
not-yet-published fee report as *pending* rather than as a failure. Just
report honestly what you got, and don't call `vibe_seller_set_task_error`
for it.

**A header-only file is not a delivered file.** An unpublished monthly fee
report downloads as a valid CSV with a header and zero data rows — a few
hundred bytes. Count rows before claiming a file.

## FBA Customer Returns: exact dates

**URL:** `https://sellercentral.amazon.{tld}/reportcentral/CUSTOMER_RETURNS/1`

The date control is a **preset dropdown** (initial title
`last day (yesterday)`; options last 1/3/7/14/30/90/180 days) plus an
**`Exact dates`** option (`kat-option value=-1`) at the bottom. Presets are
anchored to today, so "last 30 days" is not a calendar month (on June 3 it
covers May 4 → June 3). For a calendar month, use `Exact dates`.

- Pick `Exact dates` first: the `.csv` request control only appears once an
  exact range is chosen (with a preset, only a TSV button shows).
- The dropdown is a `kat-dropdown`: open it with a `click_at_xy` and
  `click_at_xy` the "Exact dates" option (options are in the dropdown's
  `shadowRoot`). `selectOption('-1')` changes `.value` without revealing
  the date fields.
- Set start and end with the calendar walk below. Programmatic value-sets
  on `kat-date-*` widgets (attributes, `startValue`/`endValue`, the inner
  `<input>.value` with events, CDP `Input.insertText`) all update the
  visible field but the report still generates for today — the submit
  reads an internal model that only real clicks update.
- Request the `.csv` (a `kat-button`), then confirm the new history row's
  date range is your month, not today, before downloading.

**UTC edge rows are normal.** The filter is marketplace-local but the CSV's
`return-date` column is UTC, so a row dated the previous month's last day
at ~21:00–23:59 UTC belongs to the requested month. Keep it.

The download lands as a numeric report id (e.g. `50000000001.csv`) in the
store downloads dir; rename it to `return.csv` for a monthly report run.

## The calendar walk (three shadow roots)

The calendar cells are three shadow roots down:
`kat-date-range-picker` → `.shadowRoot` → `kat-date-picker.start` (or
`.end`) → `.shadowRoot` → `kat-calendar` → `.shadowRoot`. A
`querySelector` from `document`, or a click aimed by eye, finds nothing and
fails silently — the field keeps today's date and you get a one-day report.

Both pickers' popovers render at the same screen position, so re-read
coordinates after every click rather than reusing them. A future month's
day cells are disabled; clicks on them no-op.

```bash
browser-use <<'PY'
def cal(which):                     # which: 'start' or 'end'
    """(header, day-cells) for one picker, through all three roots."""
    return js("""
      var rp  = document.querySelector('kat-date-range-picker');
      var dp  = rp.shadowRoot.querySelector('kat-date-picker.%s');
      var sr  = dp.shadowRoot;
      var cal = sr.querySelector('kat-calendar');
      var root = cal && (cal.shadowRoot || cal);
      if (!root) return null;
      var hdr = root.querySelector('[class*=header],[class*=title]');
      var cells = [].slice.call(root.querySelectorAll('td,[role=gridcell]'))
        .map(function(e){ var r = e.getBoundingClientRect();
          return {t:(e.innerText||'').trim(),
                  x:Math.round(r.x+r.width/2),
                  y:Math.round(r.y+r.height/2)}; })
        .filter(function(e){ return e.t && e.x > 0; });
      return {hdr: hdr ? hdr.innerText.trim() : '', cells: cells};
    """ % which)

def icon(which):                    # the calendar icon that OPENS it
    return js("""
      var rp = document.querySelector('kat-date-range-picker');
      var dp = rp.shadowRoot.querySelector('kat-date-picker.%s');
      var ic = dp.shadowRoot.querySelector('kat-icon[name=calendar-alt]');
      var r  = ic.getBoundingClientRect();
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
    """ % which)

def prev(which):                    # the "previous month" chevron
    return js("""
      var rp = document.querySelector('kat-date-range-picker');
      var c  = rp.shadowRoot.querySelector('kat-date-picker.%s')
                 .shadowRoot.querySelector('kat-calendar');
      var root = c.shadowRoot || c;
      var ch = root.querySelector('kat-icon[name=chevron-left]');
      var r  = ch.getBoundingClientRect();
      return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
    """ % which)

def pick(which, target_hdr, day):
    """Open *which*, page back to target_hdr, click *day*."""
    import time
    p = icon(which); click_at_xy(p['x'], p['y']); time.sleep(2)
    for _ in range(18):                        # bounded
        c = cal(which)
        if c and c['hdr'] == target_hdr:
            break
        p = prev(which); click_at_xy(p['x'], p['y']); time.sleep(1.5)
    else:
        raise RuntimeError('never reached ' + target_hdr)
    hit = [e for e in cal(which)['cells'] if e['t'] == str(day)]
    if not hit:
        raise RuntimeError('no cell %s in %s' % (day, target_hdr))
    click_at_xy(hit[0]['x'], hit[0]['y']); time.sleep(1.5)

def committed():
    return js("""
      var rp = document.querySelector('kat-date-range-picker');
      return {s: rp.shadowRoot.querySelector('kat-date-picker.start')
                    .getAttribute('value'),
              e: rp.shadowRoot.querySelector('kat-date-picker.end')
                    .getAttribute('value')};
    """)

pick('start', 'July 2026', 1)
pick('end',   'July 2026', 31)
print(committed())   # both dates should be set; if not, a click missed
PY
```

Check `committed()` before requesting: a missed click is otherwise
invisible, and the resulting one-day report looks like a normal file. If
paging back many months risks the wrapper's 120 s limit, split `pick('start')`
and `pick('end')` into separate invocations.
