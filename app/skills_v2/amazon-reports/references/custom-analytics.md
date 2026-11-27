# Custom Analytics (定制分析)

Business Reports' replacement: dashboards of tiles, each tile a table or a
chart over one dataset. Every fact here was exercised live on 2026-11-18
on four stores (SA, AE and US marketplaces; `zh-CN` and `en-AE` UIs).

## Contents

- [Scope and freshness](#scope-and-freshness)
- [Opening it](#opening-it)
- [Dashboards](#dashboards)
- [Before reading a tile](#before-reading-a-tile)
- [Export a tile with the helper](#export-a-tile-with-the-helper)
- [Tile controls](#tile-controls)
- [Time period](#time-period)
- [Filters](#filters)
- [Edit metrics](#edit-metrics)
- [The exported file](#the-exported-file)

## Scope and freshness

- A tile covers the marketplace the account switcher has selected, never
  the whole account: the same week read on the SA and AE domains of one
  unified account gave each marketplace's own figures. Switch first
  (`amazon-shared` §4a-bis) and check `ue_mid`.
- Each tile states its data date — "Data last updated on: …" /
  「数据上次更新时间：…」 — a few days behind today. Presets end on that
  date, not on today.
- Sessions and page views equal the SP-API Sales and Traffic report
  (`GET_SALES_AND_TRAFFIC_REPORT`) summed over the same days.

## Opening it

| Route | How |
|---|---|
| Direct URL | `/custom-analytics` lands on `…/dashboard/overall-business-performance-template`; any dashboard is `/custom-analytics/dashboard/<slug>` |
| New Seller Central home | top-right **All reports** / 「所有报告」 button → **Custom analytics** / 定制分析 (its first entry) |
| Classic UI | the hamburger's Reports section; its links sit in `navigation-hamburger-menu`'s shadow root (`/custom-analytics?ref_=…`) — read the href and open it |
| Business Reports | the retirement banner's **Move now** / 「立即迁移」 link opens the matching dashboard (table below) |

## Dashboards

Curated ("Amazon curated" / 亚马逊精选) dashboards, by slug:

| Slug | Covers |
|---|---|
| `real-time-sales-template` | today's sales by hour (Real Time Sales / 实时销量) |
| `br-sales-traffic-seller-performance-template` | Sales, Traffic & Seller Performance / 销售、流量和卖家绩效 |
| `detail-page-sales-traffic-template` | Detail Page Sales and Traffic by parent / child item / 详情页面销售和流量 |
| `overall-business-performance-template` | Overall Business Performance / 整体业务绩效 |
| `traffic-and-sales-template` | Search Performance, Traffic, and Sales / 搜索绩效、流量和销量 |
| `sales-and-pricing-template` | Sales and Pricing / 销售额和定价 |
| `conversion-analysis-template` | Conversion Analysis / 转化分析 |
| `promos-performance-template` | coupons, deals, discounts, promos / 优惠券，特惠，折扣，促销绩效 |
| `customer-satisfaction-dashboard` | Customer Satisfaction / 客户满意度 |

Seasonal recap dashboards (Prime event, Black Friday) carry the year in
their slug. The "See all (N)" count differs per marketplace (13 on SA, 14
on US), so list the left navigation rather than assume.

Business Reports → dashboard, as the banner links them:

| Business Reports page | Dashboard |
|---|---|
| Sales Dashboard (`#/dashboard`) | `real-time-sales-template` |
| `SalesTrafficTimeSeries`, `DetailSalesTrafficByTime`, `SellerPerformanceForMerchants` | `br-sales-traffic-seller-performance-template` |
| `DetailSalesTrafficBySKU`, `…ByParentItem`, `…ByChildItem` | `detail-page-sales-traffic-template` |

`detail-page-sales-traffic-template` holds five tiles, in order: By
Parent Item (按父商品), By Child Item (按子商品), 搜索查询绩效, 价格和流量趋势（按时间）
(a chart), 已订购商品数量与退款率. Tiles are addressed by that 0-based order.

## Before reading a tile

1. **Foreground the tab** — `cdp('Page.bringToFront')`. In a background
   tab (`document.visibilityState === 'hidden'`) the tiles render as grey
   placeholders and never request their data.
2. **Scroll the tile into view.** Tiles load lazily; one below the fold
   has no controls until it is scrolled to. Wait for its Total row.
3. **Dismiss the onboarding tour** on a first visit (popovers with
   「跳过演示」 / "Close") — it sits over the tiles.

## Export a tile with the helper

`scripts/bh_ca_export.py` does all of the above, sets an optional custom
range, clicks Export and confirms the file landed:

```bash
SC_HOST=sellercentral.amazon.<tld> CA_TILE=0 \
  CA_START=2026/10/01 CA_END=2026/10/31 \
  CA_DOWNLOAD_DIR=$VIBE_STORE_DOWNLOADS \
  browser-use < .claude/skills/amazon-reports/scripts/bh_ca_export.py
```

If `$VIBE_STORE_DOWNLOADS` is empty (an older Vibe Seller), use the
store's downloads folder your store context names.

`CA_DASHBOARD` defaults to `detail-page-sales-traffic-template`; omit
`CA_START`/`CA_END` to keep the tile's default (YTD); `CA_EXPORT=0` only
reads the tile. It prints one `RESULT {json}` line: `ok`, `marketplace`
(`ue_mid`), `tile`, `period`, `totals`, `empty`, `exported`, `file`.
`needs` names the stop: `signin` (run `amazon-shared`'s `bh_signin.py`),
`range` (refused range — at most 92 days), `chart_tile`, `no_tile`,
`download`. Move `metric-data.xlsx` out of the downloads dir before the
next export (SKILL.md, Downloads) — every export has that name.

## Tile controls

Tiles are Cloudscape (`awsui_*`) components with no shadow DOM, so
`querySelector` reaches everything. Address controls by `data-testid`,
which does not change with the UI language:

| Control | Selector |
|---|---|
| Tile menu | `button[aria-label^="Actions for "]` — English in every UI language; the rest of the label is the tile title. Items: `[data-testid=delete]`, `editName`, `save`, and `export` on table tiles |
| Time period | `[data-testid=btn-tp-menu]` → popover |
| Filters | `[data-testid=btn-filter]` |
| Export | `[data-testid=btn-export]` (table tiles only) |
| Edit metrics | `[data-testid=edit-metrics-button]` |
| Column header | `[data-testid=<METRIC_ID>-column-header]`, e.g. `SESSION_CNT`, `PAGE_VIEW_CNT`, `TOTAL_ORDERED_UNITS` |
| Chart tile | `[data-testid=caw-metric-trend-tile]`: cadence, primary and comparison metric selects, Filters, Edit metrics — **no Export**, and its menu has no Export either |

The table is an AG Grid: the Total row is in `.ag-floating-top`; rows
page 25 at a time (「1 到 25，共 N」 in the pager).

A table tile with no data has no rows (chart tiles say 「没有可用数据」) and
still exports — a header-only file, which is a completed download.

## Time period

`btn-tp-menu` opens a popover holding: the preset dropdown
(`[data-testid=time-period-dropdown]`), the custom range
(`[data-testid=custom-date-range-picker]`), "Compare" / 「比较时间段」, the
comparison checkboxes and the popover's own Cancel / Apply.

- **Presets** — `T7D`, `T15D`, `T30D`, `YTD`, `QTD`, `MTD`, `WTD`,
  `T12M`, `T4W`, `T13W`, whole years, quarters, recent months
  (localized: 「10月 2026」), recent weeks (`Week 46`), and event windows
  (Prime Day, Black Friday). Click the option, then the popover's Apply.
- **Custom range** — its dialog takes two inputs in `YYYY/MM/DD` in
  every UI language, and **at most 92 days**: a longer range is refused
  inside the dialog ("Custom date ranges cannot exceed 92 days" /
  「自定义日期范围不能超过 92 天」). Type both dates, press the
  **dialog's** Apply, then the **popover's** Apply. With only the first,
  the label changes and the data does not. A one-day range works.
- **Compare** — YOY, QOQ, MOM, WOW, or a custom range; which are
  enabled depends on the period (YTD allows only YOY).
- **Verify** — after applying, `[data-testid=time-period-row]` reads
  `YYYY/MM/DD — YYYY/MM/DD` for the range and the Total row changes. A
  page reload resets every tile to its default period and no filter.

## Filters

`btn-filter` opens a panel of rows, each **metric/dimension** (a
searchable dialog listing the tile's own columns) → **condition**
(Equals / Not Equal / Contains; 等于 / 不等于 / 包含) → **value**
(placeholder `value1, value2, …`), plus Add / 添加. Apply with
`[data-testid=btn-filter-apply]` (cancel: `btn-filter-cancel`); the
Filters button then carries the count, e.g. 「筛选条件 (1)」. Export
honours the filter.

**A filter or a metric edit makes the next navigation stop on a
leave-page dialog.** `goto_url` then fails with "Page.navigate timed out
after 5s" and screenshots time out. `cdp('Page.handleJavaScriptDialog',
accept=True)` accepts it and the blocked navigation carries on — wait a
few seconds before navigating again. A date change alone does not raise
it.

## Edit metrics

`edit-metrics-button` opens 「添加或更改指标和维度」: a search box, categories, a dimensions/metrics
list and the selected list with a counter `(n/20)` — **20 columns per
tile at most**.

- Each item is `[data-testid=selectable-metric-<ID>]`. Clicking it
  **toggles**: an unselected item is added, a selected one removed.
- An item with `aria-disabled="true"` cannot combine with the tile's
  data. `RECORD_DAY` and `RECORD_MONTH` are disabled on the detail-page
  tiles, so they have no per-day split: for daily figures export one
  one-day custom range per day.
- Apply with `[data-testid=apply-metrics-button]`
  (`cancel-metrics-button`, `selected-metrics-container__clear-all-btn`);
  the new column appears as its `-column-header`.

## The exported file

- `metric-data.xlsx`, written to the store's downloads dir the moment
  Export is clicked; one sheet, `metric-data`.
- Row 1: headers in the UI language; money headers carry the currency
  (`… (SAR)`). Row 2: `Total`. Then one row per item — **every page**,
  not only the 25 on screen.
- **The ASIN columns come out empty** (parent and child, in both UI
  languages); only the name columns are filled. The grid shows each ASIN
  under its name; to key rows by ASIN, read them from the grid or use the
  Business Reports CSV while it lasts.
- Percentages are fractions (12.5 % on screen → `0.125`).
- The file does not state its period: name it by the period you set.
