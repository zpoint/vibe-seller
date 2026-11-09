# Filling the ad TSVs and writing the report

Shared by `amazon-ads` and `noon-ads`. The columns themselves are in
[`data-contract.md`](data-contract.md); this page covers how to fill
them so they mean the same thing every run, and what the human report
should contain. Scope comes from your declaration
(`vibe_seller_declare_ad_task`), not from this page.

- [Filling the TSVs](#filling-the-tsvs)
- [The report](#the-report)
- [Report language](#report-language)

## Filling the TSVs

These files are read by the server, by the execution phase, and by next
week's audit, so the same column has to mean the same thing every time.

**General**

- Tab-separated, header line first, columns named exactly as in
  `data-contract.md`. A pipe- or comma-delimited file reads as one
  column and every figure in it is lost.
- Plain numbers (`12.5`, not `SAR 12.5`, `12.5%` or `1,234`); the
  currency goes in its own `currency` column.
- One row per item. No "other N terms" rows — they hide exactly the
  rows a decision might be about.
- Search-term text is what the customer typed, or an ASIN in upper case
  (`B0EXAMPLE1`) for a product-page placement — never a raw DOM attribute
  or a lower-cased `b0…` string.

**Targeting file (`<campaign_id>.tsv`)**

- One row per keyword, product target, or auto-target group (SP Auto: the
  four groups; noon Auto: its auto keyword / auto category groups from
  the Targets sheet). A single row restating the campaign
  total is not a drill — bids, pauses and negatives are decided per
  target.
- Include paused targets that spent in the window. Their spend is part of
  the campaign total; filtering to `state=enabled` makes the targeting
  layer smaller than the search terms it contains, which is impossible
  and sends you looking for a platform defect that isn't there. The rows'
  spend should add up to the campaign's spend in `CAMPAIGNS.tsv`; when it
  doesn't, a state filter is the first suspect.
- `ad_group`: Amazon's `Ad Group Name` (one campaign can have several, and
  bids and negatives are set per group). noon has no ad groups: use the
  SKU and keep it consistent. Never blank.
- `bid` is the target's own bid, not Amazon's suggested bid (the
  suggested range sits in a different grid cell —
  `mechanics-reading.md` §6).
- `acos` / `roas` come from `spend` and `sales`. When a row has spend and
  no sales, leave `acos` empty and write `roas` `0`: Amazon prints
  `ACOS 0.00` there, which reads as excellent but means every unit of
  spend was wasted.
- `applied_action`, `applied_at`, `previous_bid` stay blank on rows an
  audit writes fresh. When you rewrite a file that already exists, carry
  forward the values on rows you aren't changing — they are the history
  of what was done, and the cooldown reads them.

**Search-term file (`<campaign_id>.searchterms.tsv`)**

- The full set for the window, not the top rows a page shows: Amazon from
  the bulk export's `SP/SB Search Term Report` sheet or the Search Terms
  page's Export CSV; noon from the Customer Queries Export.
- `source_target`: Amazon's search-term report carries `Keyword Text`
  and `Match Type` on every row, so fill it from there. noon's Queries
  sheet has only campaign, SKU and query — write `—` and don't guess.
- `ad_group`: as in the targeting file (noon: the SKU).

**When a column didn't render.** If the clicks column came back empty or
zero while spend did not (Amazon AE's Manual targeting page does this
under load), there is no actual CPC, so there is no bid floor: propose no
`lower` for that campaign, and leave `cpc` empty. Raises (based on ROAS),
pauses and negates don't depend on CPC and stay valid. Say in the report
which campaign lost the column.

## The report

`AD_AUDIT_<YYYY-MM-DD>.md` is for the person who will approve the
changes, reading without the console in front of them. The server
doesn't parse it, so choose the shape that reads best — but every figure
in it should come from the TSVs, so the two never disagree.

What it needs:

- **A title and the analysis window** on the first line or two.
- **Assumptions**: margin, target ACOS / ROAS, and any store-notes
  override in force. When margin is unknown, use the default and say
  `margin assumed 25% (default) — confirm` (see
  [`tuning-recommendation-format.md`](tuning-recommendation-format.md)
  § Missing margin).
- **Per marketplace**: how many campaigns, how many live, how many
  drilled, and totals (spend, sales, orders, ROAS). Name any `blocked`
  campaign and why.
- **Per drilled campaign**: its real name and id, a targeting table with
  the decision next to each row, the search terms that matter (top
  spenders, wasters, converters), and any note on drift since the last
  run. Inactive campaigns get one line each.
- **The proposed changes**, numbered so the user can reply "1, 3, 5" or
  "all of campaign X": every non-`hold` row of `CHANGES.tsv`, with
  current → target bid and the reason. Then, separately, the proposals
  that aren't `CHANGES.tsv` rows (harvest a term as a keyword, budget,
  placement modifier, bidding strategy, pause a campaign).
- **A short summary**: the few highest-impact actions, ordered by impact,
  and what to watch over the next 7–14 days.

Do the in-scope work now rather than deferring it to "next audit" — a
report that says "to be analysed later" leaves the person with nothing
to approve. Two things that look out of reach but aren't: Brand
Analytics (Seller Central → Brands → Brand Analytics) opens without an
OTP, and cross-campaign or cross-platform comparisons for the same SKU
can be done from the TSVs already on disk.

## Report language

Write in the language of the user's request — action words, column
headers, labels, the summary. Text that belongs to the platform (UI
labels, button names, export column names) stays as the platform shows
it, in quotes. Mixing languages in one table makes it harder to scan.

| Action | 中文 | English |
|---|---|---|
| raise a bid | `提高至 <bid>` | `Raise to <bid>` |
| lower a bid | `下调至 <bid>` | `Lower to <bid>` |
| pause a target | `暂停定向词` | `Pause target` |
| hold | `维持` | `Keep` |
| negate a keyword query | `否定关键词（精准\|词组）` | `Negative keyword (exact\|phrase)` |
| harvest a query | `添加为关键词（精准\|词组，建议出价 X）` | `Add as keyword (exact\|phrase, bid X)` |
| negate a product placement | `否定投放` | `Negative target` |
