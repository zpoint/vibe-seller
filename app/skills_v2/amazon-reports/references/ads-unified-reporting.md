# Advertising reports — Unified Reporting (统一报告)

## Contents

- [Overview](#overview)
- [Navigation and warm-up](#navigation-and-warm-up)
- [The report list page](#the-report-list-page)
- [Templates](#templates)
- [The builder](#the-builder)
- [Standardized metrics and terminology](#standardized-metrics-and-terminology)
- [The SP "Advertised product" report (monthly export)](#the-sp-advertised-product-report-monthly-export)
- [Retrieving a finished report](#retrieving-a-finished-report)
- [Per-marketplace files: split downstream](#per-marketplace-files-split-downstream)
- [Limits and troubleshooting](#limits-and-troubleshooting)
- [Legacy console](#legacy-console)

## Overview

Unified Reporting (`advertising.amazon.{tld}/reporting`) is Amazon's report
center for every ad product — Sponsored Products, Sponsored Brands,
Sponsored Display, Sponsored TV and Amazon DSP — across accounts and
marketplaces, built from a shared library of standardized **dimensions**
and **metrics**. Use it for all ad reporting.

In the console nav, **效果衡量和报告 → 报告** (Measurement & reporting →
Reports) links to `/reporting`. **搜索广告报告** (Search ads reports) still
links to the legacy `/reports` console (see [Legacy console](#legacy-console)).

## Navigation and warm-up

A **cold** direct hit to `advertising.amazon.{tld}/reporting` redirects to
`advertising.amazon.com/register` (a marketing/onboarding page). That only
means the session isn't SSO'd into the ad console yet — it is not evidence
the store has no ads. Warm up through choose-account (or open Campaign
Manager from the Seller Central menu):

```bash
browser-use <<'PY'
new_tab("https://advertising.amazon.{tld}/choose-account?destination=/reporting")
wait_for_load()
# Click the advertising account (label = the ad-account name), then the
# marketplace button (e.g. 沙特阿拉伯 / 阿联酋 / United States).
js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '<your-ad-account>').click()")
print(page_info())            # marketplace sub-buttons appear
js("[...document.querySelectorAll('button')].find(b => b.textContent.trim() === '<marketplace>').click()")
wait_for_load()
print(page_info())            # → advertising.amazon.{tld}/reporting?entityId=<ENTITY>
PY
```

The `entityId` is per account **and per marketplace**. Read it back from the
URL after warm-up rather than reusing one from an earlier run. Once warm,
direct navigation to `.../reporting?entityId=<ENTITY>` (and
`.../reporting/new?...`) works for the rest of the session.

The ad report is genuinely N/A only if, after warming up, the store shows an
advertiser onboarding / registration flow (no advertiser account).

## The report list page

Top: a **report templates** row and a **创建报告** (Create report) button.
Below: saved/generated reports with columns **报告名称** (name), **报告状态**
(status), **创建时间**, **上次修改时间**, **上次运行时间**, **下次运行时间**,
**账户**, **报告提取频率** (frequency). Empty state: "尚无报告 —
首先选择模板或创建自定义报告".

**报告状态**: 排队/正在处理 (Queued/Processing) → **已完成** (Completed,
downloadable) → 失败 (Failed). Before building a new report, check whether a
completed one already covers the date range.

## Templates

Three templates sit at the top of `/reporting`, each with a **使用模板**
(Use template) button that opens the builder at
`/reporting/new?entityId=<ENTITY>&templateId=<id>` with default columns
pre-seeded:

| Template (zh) | `templateId` | Equivalent old report |
|---------------|--------------|-----------------------|
| 广告活动 (Campaign) | `campaign` | Campaign report |
| **推广的商品 (Advertised product)** | `advertised_product` | SP "Advertised product" (adProducts) |
| 搜索词 (Search term) | `searchterm` | Search term report |

**查看所有模板** (View all templates) lists the rest.

Seeding happens only via the template **click**, asynchronously: the ~20+
default columns appear a second or two later (poll for `删除`/Delete buttons
in the column list). Opening `/reporting/new?...&templateId=...` by URL
loads the page with no columns, which then fails validation on submit.

```bash
browser-use <<'PY'
new_tab("https://advertising.amazon.{tld}/reporting?entityId=<ENTITY>")
wait_for_load()
# Click 使用模板 in the 推广的商品 card (the card mentioning 推广的商品 but not 搜索词):
js("""
  var btns=[...document.querySelectorAll('button,a')].filter(b=>b.textContent.trim()==='使用模板');
  for (var b of btns){ var p=b; for(var i=0;i<6&&p;i++){ p=p.parentElement;
    if(p && /推广的商品/.test(p.textContent) && !/搜索词/.test(p.textContent)){ b.click(); return; } } }
""")
wait_for_load()
import time; time.sleep(5)   # let default columns seed
print(js("[...document.querySelectorAll('button')].filter(b=>b.textContent.trim()==='删除').length"))
PY
```

## The builder

`/reporting/new` (title **创建报告**) is one scrolling form built on Amazon's
**storm-ui** framework — controls carry `data-takt-id="storm-ui-*"` and
most have stable ids.

The **提交** (submit) button ignores synthetic `.click()`; submit with
`click_at_xy` at its `getBoundingClientRect` centre. Most other controls
(template button, date presets, picker **保存**, column checkboxes) respond
to `js("el.click()")`; if one appears to no-op, use `click_at_xy`. Use
`fill_input(sel, text)` / `type_text(...)` for text inputs.

**A. Report name** — `id="report-name-form:report-name-control-component-0"`
(≤256 chars, auto-filled `推广的商品 - <timestamp>`). Clear the existing value
before typing yours; a single `fill_input`/`type_text` may append rather
than replace. A unique name makes the report easy to find later.

**B. 筛选条件 (Filters)** — four rows, each with an **添加/变更** button:

| Filter | Default | Change control (`id=`) | Notes |
|--------|---------|------------------------|-------|
| 账户 (Account) | current account | 添加 button (`data-takt-id=storm-ui-button`) | Dialog: **添加** (single), **添加当前和未来账户**, **添加所有显示的账户** → **保存**. |
| 广告活动 (Campaigns) | 所有广告活动 | `filter-form:campaigns-row-control-component-0-button` | Specific campaigns by name/id. |
| 国家/地区 (Country/Region) | 所有国家/地区 | `filter-form:campaign-country-row-control-component-0-button` | Does not scope the output — see [split downstream](#per-marketplace-files-split-downstream). |
| 广告产品 (Ad product) | 所有广告产品 | `filter-form:ad-product-row-control-component-0-button` | SP / SB / SD / STV / DSP. Legacy SB (no ad groups) and self-service streaming-TV campaigns aren't supported. |

**C. 定制列 (Custom columns)** — two tabs, **尺寸** (Dimensions) and **指标**
(Metrics), a category list on the left, a **Search** box, and checkbox rows
(`label[data-takt-id=storm-ui-checkbox]`, click to toggle). Chosen columns
appear as reorderable rows with a **删除** button.

Minimum to submit:
1. ≥1 time dimension (维度 → 时间),
2. ≥1 detail dimension (维度 → 详细程度: 广告主账户 / 广告活动 / 广告组 / 广告),
3. ≥1 metric (指标).

Some fields grey out depending on other choices (hover for the reason).
Empty cells for a dimension that doesn't apply to an ad product are normal.

Dimension categories:

| Category | Key fields |
|----------|-----------|
| 时间 | 日期 (daily rows), 周, 月, 年份, 一周中的某一天, 小时, **日期范围** (one start/end summary row) |
| 详细程度 | 广告主账户 / …ID / …名称, 实体编号, 广告组合(名称/编号), 广告活动(名称/编号/状态/预算/竞价方案/起止日期…), 广告组(名称/编号/状态/类型…), 广告(名称/ID/格式/尺寸/语言) |
| 商品 | **推广的商品** / 编号(=ASIN) / 名称 / 品牌 / 品类 / **SKU** / 站点; 达成转化的商品(编号/名称/品牌/品类); 商品相关度 |
| 广告库存来源 | 网站或应用程序, 广告位(名称/大小/分类) |
| 技术 | 操作系统, 浏览器名称/版本, 设备类型, 环境 |
| 地理位置 | 国家/地区, 国家/地区代码, 区域, 指定市场区域(DMA), 城市, 邮政编码 |
| 受众 | 细分受众群(ID/名称/类型/来源/国家/状态), 频次组 |
| 投放方案 | 投放目标, 目标竞价, 投放类型, 投放状态, 投放方案, 匹配类型, **搜索词**, 匹配的目标 |
| 转化 | 转化来源, 转化来源所有者, 转化定义/类型（亚马逊站外）, 归因类型 |
| 内容/直播 (STV/DSP) | 内容类型/评级/标题/创作者, 直播活动(编号/名称/广告位/广告时段…) |

Metric categories:

| Category | Key fields |
|----------|-----------|
| 投放 | 展示量, 点击量, 点击率 (CTR), CPC, CPM, 可见展示量/可见率/vCPM, 无效展示/点击 |
| 成本和费用 | **成本** (= spend/花费), 总成本, 广告库存成本, 各类费用 |
| 触达 | 用户触达量, 平均展示频率, 家庭触达量, 频次分布 |
| 展示量份额 | 展示量份额, 展示量份额排名, 搜索结果首页首位展示量份额 |
| 互动 | 视频四分位完成率/完播率/完整观看, 音频同类, 5 秒观看 |
| 亚马逊网站转化量 | **购买量, 销售额, 已售商品数量, 购买率, ROAS, 单次购买成本**; 商品详情页浏览量, 加入购物车, 加入心愿单, 品牌搜索量, … — each in base / **（推广）** / **（光环）** / **（品牌新客）** and 归因于点击 / 归因于浏览 variants |
| 亚马逊设备转化量 | 订阅注册, 应用商店启动/使用时长, Alexa 技能, Kindle KENP / KU 借阅 |
| 亚马逊视频转化量 | 加入播放列表, 下载视频播放, 租借, 预告片播放, 视频有效播放 |
| 亚马逊站外转化量 | on-Amazon metrics mirrored with **（亚马逊站外）** + 潜在客户/结账/安装/页面浏览/搜索/注册/订阅 |
| 已合并的转化量 | 购买量/销售额/ROAS **（已合并）** = on- + off-Amazon |

**D. 报告期 (Report period)** —
`id="report-range-form:date-range-row-control-component-0"` (default
最近 7 天). Opens a preset list
(`data-takt-id=storm-ui-date-range-picker-preset-selector`) and a
dual calendar. Presets: 今天, 昨天, 最近 7 天, 本周, 上周, 最近 30 天, 本月,
**上个月**, 最近 90 天, 本季度, 上个季度, 今年, 去年.

A preset applies immediately and closes the picker; the **保存** button
(`data-takt-id=storm-ui-date-picker-confirmation-control-save`) exists only
for a custom calendar start→end selection. After a preset, just confirm the
control label changed.

Lookback: daily/weekly up to 15 months; monthly/yearly up to 6 years; some
dimensions/metrics are shorter, and presets beyond a selected dimension's
limit are hidden.

```bash
browser-use <<'PY'
import time
js("document.getElementById('report-range-form:date-range-row-control-component-0').click()")
time.sleep(2)
js("[...document.querySelectorAll('[data-takt-id=storm-ui-date-range-picker-preset-selector]')].find(e=>e.textContent.trim()==='上个月').click()")
time.sleep(1)
print(js("document.getElementById('report-range-form:date-range-row-control-component-0').textContent"))  # → 上个月
PY
```

**E. 投放设置 (Delivery)**
- **发送至** (Send to): optional emails, `id=recipients-input-builder-input`.
  Anyone with the emailed link can view the report.
- **报告提取频率** (Frequency): dropdown
  (`data-takt-id=storm-ui-dropdown-trigger-button`; items
  `storm-ui-dropdown-item`) — **一次** (Once, default) / 每日 / 每周 / 每月.
  Recurring needs a weekday or day-of-month, an end date, and a time +
  timezone.
- **文件格式**: CSV.

**F. Submit** — **提交** `id=urc_frb_create_report_button` (top-right; real
click). Cancel: `id=urc_cancel_subscription_button`. On success you return
to `/reporting` with the new row in 正在处理. Missing required columns show
an inline banner "Fix the following issues and submit again".

## Standardized metrics and terminology

Unified Reporting renamed and in places redefined metrics. Read this before
comparing numbers with older reports.

- **Common terms** (DSP → unified): 订单→广告活动, 广告订单项→广告组,
  创意→广告, 点击跳转量→点击量.
- **Base conversion metrics include halo.** 购买量 / 销售额 / 已售商品数量
  (and 商品详情页浏览量 etc.) count promoted **and** brand-halo products. For
  the old SP promoted-only numbers use the **（推广）** variants (推广商品的
  购买量 / 推广商品的销量 / 已售商品数量（推广） / 推广商品的 ROAS). Base metrics
  can therefore read higher than legacy DSP reports.
- **No lookback window in names.** Base metrics use the right window
  automatically: SP seller 7-day, vendor 14-day, other campaign types 14-day
  (flexible-lookback DSP uses the advertiser's windows). So 销售额 = the old
  "7-day total sales" for an SP seller; ROAS = ROAS7d.
- **Traffic-date reporting.** Conversions are reported on the ad-interaction
  date, not the conversion date, so early-campaign conversions look higher
  than in old DSP/legacy SB reports.
- Qualifiers: **（推广）** promoted, **（光环）/（品牌光环）** halo,
  **（品牌新客）** new-to-brand, **归因于点击/归因于浏览** click/view
  attributed, **（亚马逊站外）** off-Amazon, **（已合并）** combined.

## The SP "Advertised product" report (monthly export)

A monthly report run needs a per-marketplace Sponsored Products "Advertised
product" report. In Unified Reporting that is the **推广的商品** template
(`advertised_product`) with **广告产品 = 商品推广 (SP)**. Mapping from the
old 24-column XLSX (SP seller, 7-day window):

| Old column (XLSX) | Unified dimension / metric |
|-------------------|----------------------------|
| 开始日期 / 结束日期 | dim **日期范围** (summary) — or **日期** for daily rows |
| 广告组合名称 | dim 广告组合名称 |
| 货币 | dim 广告活动货币代码 (or infer from 国家/地区) |
| 广告活动名称 | dim 广告活动名称 |
| 广告组名称 | dim 广告组名称 |
| 国家/地区 | dim 国家/地区 |
| 广告SKU | dim 推广的商品 **SKU** |
| 广告ASIN | dim 推广的商品 (编号 = ASIN) |
| 展示量 | metric 展示量 |
| 点击量 | metric 点击量 |
| 点击率 (CTR) | metric 点击率 |
| 单次点击成本 (CPC) | metric CPC |
| 花费 | metric **成本** |
| 7天总销售额 | metric **推广商品的销量** |
| ACOS 总计 | derive (成本 / 推广商品的销量) — or an ACOS metric |
| 总 ROAS | metric **推广商品的 ROAS** |
| 7天总订单数(#) | metric **推广商品的购买量** |
| 7天总销售量(#) | metric **已售商品数量（推广）** |
| 7天的转化率 | metric 购买率（推广的商品） |
| 7天内广告SKU销售量(#) | metric 已售商品数量（推广） |
| 7天内其他SKU销售量(#) | metric 已售商品数量（光环） |
| 7天内广告SKU销售额 | metric 推广商品的销量 |
| 7天内其他SKU销售额 | metric 销售额（光环） |

Header text and order differ from the legacy XLSX and the file is CSV, so a
consumer that parses legacy header strings or expects `.xlsx` needs
updating; the data (per SKU × campaign × country: spend, sales, units,
impressions, clicks) is all there.

The template seeds ~47 default columns — including `推广的商品编号` (=ASIN),
`推广的商品 SKU`, `广告活动名称`, `广告组名称`, `广告组合名称`, `预算货币`,
`展示量`, `点击量`, `总成本` and the base / `（推广）` / `（光环）` /
`（品牌新客）` conversion families — one row per SKU × campaign.

- The default set uses **`推广的商品站点`** (values like `AMAZON_SA`,
  `AMAZON_AE`), not `国家/地区`. The report is account-wide; split it per
  marketplace downstream.
- **`日期范围`** shows each row's active span within the period (a sparse SKU
  shows a sub-span). The report period is still the one you picked.

Example placeholder row: `WIDGET-001-White, B0EXAMPLE1, "example manual",
SAR, 沙特阿拉伯, 5000 impressions, 30 clicks, 30.00 spend, 250.00 sales,
6 units`.

## Retrieving a finished report

After 提交 the report runs once. Reload `/reporting` and read **报告状态**
every ~60-90 s, one check per `browser-use` invocation (each invocation has
a 120 s limit) until it reads **已完成**. Reports finish in a few minutes up
to ~30 min; data within the attribution window can still be revised.

Download from the **report detail page**, not the list row: the row's
**操作** menu (`id=report-menu-trigger`) only has 复制 / 删除 / edit. Click
the **report name** → `/reporting/history?entityId=<ENTITY>&reportId=<report-uuid>`,
which has a **下载** link (`data-takt-id=storm-ui-link`) with href:

```
/reporting/subscriptions/<report-uuid>/download-report/<run-uuid>
```

Click the `<a>` itself (JS `.click()` on the anchor works) or `new_tab()` its
href; clicking the label text fires nothing. The CSV lands in the store
downloads dir named after the report (`<report-name>.csv`). If you set an
email recipient, it also arrives by email link.

## Per-marketplace files: split downstream

Each marketplace has its own campaigns and currency (sa=SAR, ae=AED, …).
Unified Reporting is **account-wide**: neither the 国家/地区 filter (the
storm-ui dialog doesn't commit it) nor entering via a marketplace's
`entityId` scopes the output — reports created "SA-only" and "AE-only" come
out byte-identical. Produce one account-wide report and split it by
`推广的商品站点` (or add the 国家/地区 dimension and split on that):

```python
import pandas as pd

MM, Mon, slug = '07', 'Jul', '<slug>'   # reporting month + store slug
df = pd.read_csv('unified_advertised_product.csv')
col = [c for c in df.columns if '站点' in c or 'marketplace' in c.lower()][0]
for mkt, sub in df.groupby(col):  # AMAZON_SA / AMAZON_AE / ...
    cc = mkt.split('_')[-1].lower()  # sa / ae
    sub.to_csv(
        f'reports_{MM}_{cc}_{slug}/Sponsored_Products_Advertised_product_report_{Mon}.csv',
        index=False,
    )
```

After splitting, check each file's `推广的商品站点` values are all the
intended marketplace. A marketplace with no rows still gets a file (header
only) — for this report an empty file is a valid answer.

## Limits and troubleshooting

- A report over 25 GB times out: shorten the range, keep advertisers < 50,
  or select fewer dimensions.
- Blank cells and empty reports are normal for an inapplicable dimension,
  an unsupported lookback, or a permission-masked metric. A 0-row export for
  a valid request is a completed deliverable.
- Account not visible: you need access to the manager/advertiser account;
  newly linked accounts can take up to ~1 hour to appear.

## Legacy console

A few report types are not in Unified Reporting yet: MRC viewability,
benchmarks, conversion path, SP video, SP prompts, cross-retailer SP, and
legacy-SB targeting/search-term reports. For those only, the legacy console
at `advertising.amazon.{tld}/reports` (note `/reports`, not `/reporting`)
still serves them while Amazon keeps it open. Its create dialog uses
`#advertising-reports button` → report-type controls
(`button[id*=report-type-control]`; types `searchTerms`, `keywords`,
`adProducts`, `campaigns`, `placements`, `purchasedProducts`, …), an
`input[name=time-units]` Summary/Daily radio, and
`#urc_run_subscription_button` to run; the report's detail page has the
history table with the **Download** link. If the console no longer loads,
say so in the result rather than substituting a different report.
