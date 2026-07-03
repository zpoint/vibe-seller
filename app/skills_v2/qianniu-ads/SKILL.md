---
name: qianniu-ads
description: "Taobao/Tmall 万相台无界 (one.alimama.com) ad analysis + campaign (计划) CRUD for a 千牛 store. Load before any browser-use action that reviews, audits, tunes, or reports on 万相台 计划 — 广告优化 / 每日(每周)广告优化 / 'review the ads' / adjust 计划. Analysis is bulk-first: download 报表 exports and parse them, rather than scraping the UI. Plan create/edit/delete is surfaced for HUMAN review, never auto-executed. Decision thresholds live in the store's ads-rules.md, never in this skill. Requires qianniu-shared for login + navigation."
allowed-tools: Bash(browser-use:*)
requires: [qianniu-shared]
---

# Qianniu (Taobao/Tmall) Ads — 万相台无界 catalog

> **PREREQUISITE:** `../qianniu-shared/SKILL.md` — 万相台 is reached by **SSO
> from the 千牛 session** (no second login); the popup-close rule and the 0.13
> heredoc CLI apply.

This is a **catalog**: procedure + report shape live in the references. The
loop is — pull the performance data, find the 计划 worth scaling and the
products bleeding money, and **recommend** the change; **the human applies
it**. **Analysis is read-only; this skill never auto-executes a 计划 change.**

## Bulk-first: analyze from 报表 exports, not the UI

万相台's **报表** exports are the performance source of truth (the plan list
itself warns 「页面数据仅供参考，历史推广数据以报表数据为准」). Default flow:

1. **Download the 报表** for the audit window (`报表` on `one.alimama.com`;
   export lands in `~/.vibe-seller/downloads/<slug>/`, GB18030 CSV or `.xlsx`).
2. **Parse it** — `scripts/ads_report.py FILE` normalises the rows (计划/宝贝
   × 花费/成交/ROI/点击…) into TSV for the analysis.
3. Read the **live plan list** (`references/mechanics.md`) only for *current
   state* (状态 / 每日预算 / 出价 / target) that the report doesn't carry.

The plan-list DOM scrape in `mechanics.md` is the **fallback** for state the
report lacks — not the primary data path.

## References — load what the task needs

| File | When |
|---|---|
| [`references/mechanics.md`](references/mechanics.md) | any 万相台 read: routes (`#!/manage/onesite` etc.), date-window selector, plan-list extraction per 场景, the 报表 export flow, and the APPLY-phase (write) actions kept for review. |

## Decision thresholds are per-store, not here

What counts as "scale" (加投) or "cut" (删除) — ROI floors, spend caps,
lookback — is **store-specific** and lives in `stores/<slug>/ads-rules.md`
(same pattern as `amazon-ads`). This skill carries **mechanics only**; read
the store's rules for the numbers. Do not bake thresholds into the skill.

## Safety rails ⚠️

- **Analysis/audit phases are read-only.** Toggling a 计划, editing 每日预算,
  changing 出价, deleting a product — all **writes**. Surface them as a
  recommendation for the user to review; **never auto-execute.**
- Never click a promo CTA (开通/升级/领取/报名…) — see `qianniu-shared § 4`.
- Save exports/captures to `/tmp/<task>/`; never `knowledge/` or `stores/`.

## Default analysis window

Last 30 days unless the user specifies otherwise. Set the 万相台 date selector
(今日 / 昨日 / 过去 7 天 / 过去 15 天 / 自定义) to match **before** reading the
plan table, and export the 报表 for the same window.

## What this skill is NOT

- Not listings — that's `qianniu-listing` (商品 Excel round trip).
- Not 生意参谋 retail reports — that's `qianniu-reports`.
- Never applies a 计划 change; the human reviews and executes every write.
