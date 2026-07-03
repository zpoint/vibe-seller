# 万相台无界 — mechanics (routes, date window, plan-list read, 报表 export)

How to *read* live 万相台 state on `one.alimama.com`. All APPLY actions
(toggle / 预算 edit / delete) are at the end and are **writes — forbidden
during analysis; surface for review, never auto-execute.** Drive everything
with the 0.13 heredoc CLI (`qianniu-shared`): `new_tab`, `js(...)`,
`click_at_xy`, `cdp(...)`. Run the popup-close loop after every load.

## 1. Routes (hash SPA — `new_tab` then wait ~5-8 s)

| Page | Route |
|---|---|
| 万相台 home | `https://one.alimama.com/index.html` |
| 报表 (reports) | open from the home nav 报表 (route varies by version) |
| 货品全站推广 plan list | `#!/manage/onesite` |
| 关键词推广 plan list | `#!/manage/search` |
| 人群推广 plan list | `#!/manage/display` |
| 店铺直达 plan list | `#!/manage/shop` |
| 内容营销 plan list | `#!/manage/content` |
| plan detail | `#!/manage/onesite-detail?...&campaignId=<计划ID>` |

SSO from the 千牛 session (`qianniu-shared`); no separate login. A redirect to
the Taobao login means the session died → **human step** (QR / slider+SMS).

## 2. Date window

Above the KPI block: 今日 / 昨日 / 过去 7 天 / 过去 15 天 / 自定义. Select the
one matching the audit window **before** reading the plan table — the per-plan
metric columns follow this selector. Export the 报表 for the same window.

## 3. 报表 export (the performance source of truth — DEFAULT)

The plan list warns 「页面数据仅供参考，历史推广数据以报表数据为准」, so treat
the **报表 export** as truth for performance and the plan list as *live state*
(状态 / 预算 / 出价). Flow:

1. Open 报表 from the 万相台 nav; pick the report (e.g. 计划报表 / 商品报表) and
   the date window.
2. Trigger the export/download (a **read**). It lands in
   `~/.vibe-seller/downloads/<slug>/` — CSV is **GB18030** (`qianniu-shared § 5`),
   `.xlsx` is fine.
3. Parse with `scripts/ads_report.py FILE` → normalised TSV (计划/宝贝 ×
   花费/成交金额/ROI/点击/展现…), keyed by the export's own headers.

## 4. Plan-list extraction (fallback — for state the report lacks)

The list renders as TWO `<table>`s: the one whose text contains `宝贝信息` is
the header; the **next** table is the body. Data rows contain `计划ID`; total
is `共 N 项数据` (page size 40 — paginate for N > 40). Run via `js(...)`:

```bash
~/.vibe-seller/bin/<slug>/browser-use <<'PY'
print(js(r"""
var tables=[...document.querySelectorAll('table')];
var hi=tables.findIndex(function(t){return (t.innerText||'').indexOf('宝贝信息')>=0});
var body=tables[hi+1]; var out=[];
[...body.querySelectorAll('tbody tr')].forEach(function(r){
  var t=r.innerText; if(t.indexOf('计划ID')<0) return;
  var td=[...r.querySelectorAll('td')].map(c=>c.innerText.replace(/\s+/g,' ').trim());
  out.push({
    product_id:(t.match(/宝贝ID：(\d+)/)||[])[1],
    campaign_id:(t.match(/计划ID：(\d+)/)||[])[1],
    name:(t.match(/计划：\d+丨([^\n]+)/)||[])[1]||'',
    budget:td[5], bid_mode:td[6], goal:td[7], roi:td[8], spend:td[10]
  });
});
return JSON.stringify({count:out.length, plans:out});
"""))
PY
```

**§4's snippet is `#!/manage/onesite` ONLY.** Other 场景 have different DOM:

| Route | Body table | Plan ↔ product |
|---|---|---|
| `onesite` 货品全站推广 | `tables[hi+1]`; row text has 宝贝ID：/计划ID： | 1 plan ↔ 1 宝贝 |
| `search` 关键词推广 | no 宝贝信息 header; plan_id = trailing digits | keywords, not one 宝贝 |
| `display` 人群推广 | `tables[3]`; plan_id in `campaignId=` attr | group / multi-product |
| `shop` 店铺直达 | `tables[1]`; plan_id in `campaignId=` attr | store-level |
| `content` 内容营销 | `tables[1]`; plan_id in `campaignId=` attr | content |

## 5. Promo overlays

万相台 pops 大促/公告 overlays that eat clicks — run the `qianniu-shared § 4`
close loop after every route change; if a synthetic `.click()` fails, dispatch
a trusted `cdp('Input.dispatchMouseEvent', ...)` at the X's centre.

## 6. APPLY actions — WRITES (surface for review, never auto-execute)

Toggling 状态, editing 每日预算 / 出价, adding/removing a product or keyword,
新建计划, and 自动规则 changes are all **writes**. During an audit they are
**forbidden**: produce a recommendation (which 计划, what change, why, per the
store's `ads-rules.md`) and let the human apply it in the visible window.
