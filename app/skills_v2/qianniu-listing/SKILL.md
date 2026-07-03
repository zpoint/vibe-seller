---
name: qianniu-listing
description: "Taobao/Tmall 千牛 listing (商品) CRUD via the Excel bulk round trip — 商品管理 → 更多批量操作 → excel商品批量导出 (export) → edit the workbook → excel商品批量编辑 / 批量导入 (import). One round trip creates/edits many 商品 at once (price, stock, title, attributes). Load before any browser-use action that lists, edits, prices, restocks, or bulk-updates 商品 on a 千牛 store. Prefer this bulk flow over per-item clicking; the single-item 发布商品 wizard is the fallback. Requires qianniu-shared for login + navigation."
allowed-tools: Bash(browser-use:*)
requires: [qianniu-shared]
---

# Qianniu (Taobao/Tmall) — Listing CRUD (Excel bulk round trip)

> **PREREQUISITE:** `../qianniu-shared/SKILL.md` (chrome login is a HUMAN
> step; the popup-close rule; page map; per-store download dir; GB18030 CSV;
> the 0.13 heredoc CLI + `cdp()`-not-in-`js()` gotcha).

千牛 **商品管理** has a first-class **Excel bulk** surface — the batch
equivalent of the per-item 发布/编辑 wizard, and the default for touching
more than one 商品. Entry point (verified live 2026-07-03): 商品管理 (`https://
myseller.taobao.com/home.htm/SellManage/on_sale`) → **更多批量操作** →

| Menu item | Purpose |
|---|---|
| **excel商品批量导出** | **download** the selected/all 商品 as an `.xlsx` workbook |
| **excel商品批量编辑** | download the editable workbook (edit → re-upload) |
| **批量导入** | **upload** the edited workbook to apply changes |
| 批量编辑商品属性 / 批量设置运费 | scoped batch edits (attributes / shipping) |

## Work it like a human: export → inspect → edit → import → verify

The workbook's exact columns are **category-specific and change over time** —
do NOT hardcode them. Run the loop a human runs (same philosophy as
`amazon-listing`):

1. **Export a fresh workbook** (`excel商品批量导出`) for the target 商品. It
   lands in `~/.vibe-seller/downloads/<slug>/`.
2. **`inspect` it** — `listing_bulk.py inspect WORKBOOK.xlsx` dumps the header
   row + the columns actually present (商品ID, 商品标题, 价格, 库存, the SKU
   sub-table, etc.). The header names are the ground truth, not this doc.
3. **`fill`** — set values **by header name** for the rows you're changing
   (`listing_bulk.py fill … --spec spec.json --out out.xlsx`). Touch only the
   columns you mean to change; leave the rest as exported.
4. **Import** the edited workbook (`批量导入`) — **a WRITE; only after the user
   has reviewed the diff.** Then read the import result.
5. **Verify on the source of truth, not the submit toast:** re-open 商品管理
   (or the 商品 detail) and confirm the change landed. Import results also
   surface a per-row success/failure list — read it and fix exactly the rows/
   fields it flags, then re-import (self-correct loop).

> **Read-only vs write.** Export + inspect + verify are reads. **Import
> (`批量导入`) is a write** — surface the intended change for the user to
> review before uploading; never auto-import.

## The script

```bash
S=<skills>/qianniu-listing/scripts
PY=<project-venv>/bin/python3     # needs openpyxl
```

- **`listing_bulk.py`** — header-keyed Excel round-trip helper (locale-robust:
  keys by the export's own column headers, never by position):
  - `inspect WORKBOOK.xlsx` — find the header row, dump every column name + the
    row count; flags the identity column (商品ID/宝贝ID) and the SKU sub-table.
  - `fill WORKBOOK.xlsx --spec SPEC.json --out OUT.xlsx` — for each spec row
    (matched by 商品ID), set the named columns; preserve every other cell
    verbatim so the import only changes what you intended.
  - `parse-import RESULT.xlsx` — summarise the import result workbook into
    per-row 成功/失败 + the failure reason (the self-correct signal).

`SPEC.json` shape (values keyed by the workbook's **own** header names, from
`inspect`; use realistic placeholders — never a real 商品ID/SKU/brand):

```json
{
  "rows": [
    { "商品ID": "600000000001", "fields": { "价格": "199.00", "库存": "50" } },
    { "商品ID": "600000000002", "fields": { "价格": "89.00" } }
  ]
}
```

## Single-item fallback

For a one-off change with no bulk column, use the UI: 商品管理 → the row's
编辑, or 发布商品 for a new 商品 (a multi-step wizard). Drive it with the 0.13
CLI (`click_at_xy` after a screenshot; `js(...)` to read state) per
`qianniu-shared`. Still a **write** → user review first.

## What this skill is NOT

- Not ads — that's `qianniu-ads` (万相台无界).
- Not 生意参谋 reports — that's `qianniu-reports`.
- Never auto-imports or publishes; the human reviews every write.
