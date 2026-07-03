# Tuning history — per-campaign TSV under git

The workspace at `~/.vibe-seller/` is a git repository (see
`workspace/manager.py`). Every file under `stores/<slug>/` written via
`vibe_seller_write_workspace_file` is auto-committed by the workspace
manager. This skill leverages that to keep a **per-campaign,
git-versioned record** of each ad's state (bid, status, suggested
range, recent metrics) so future audits know what changed, by whom,
and when.

The same file is read in three different phases:

| Phase | Direction | Purpose |
|---|---|---|
| Phase 2 (Drill) | scrape → diff against file → write | Detect `OBSERVED_DRIFT` between the file and the live page; update file with new metrics |
| Phase 3 (Compose) | read git log of file | Recency check before proposing any state change |
| Phase 4 (Apply) | edit file after each verified UI action | Cause-and-effect ledger; the audit trail of what the agent (or user) actually did |

## File layout

One TSV per campaign:

```
stores/<store-slug>/ads/<platform>/<country>/<campaign_id>.tsv
```

- `<platform>` ∈ `amazon` / `noon`
- `<country>` is the lowercase marketplace code (e.g. `us`, `uk`, `eg`)
- `<campaign_id>` is the platform's canonical id (Amazon: `A33333333`,
  Noon: `C_FAKE0001`) — do NOT include the long prefix Amazon URLs
  attach (`A33333333LONGFORM` — only the first 9 chars are the
  stable id; the rest is a per-account scramble).

Worked path: `stores/acme-store/ads/amazon/<country>/A33333333.tsv`.

**Split threshold**: if a single campaign exceeds **200 keyword/target
rows**, split per ad group:
`stores/<slug>/ads/amazon/<country>/A33333333/<ad_group_id>.tsv`. Most
campaigns have 30–60 rows and stay in the single-file form.

## Schema

Tab-separated, with a header row. Columns in fixed order — keep the
ordering stable across edits so git diffs read cleanly:

```
row_id  status  match_type  bid  suggested_low  suggested_mid  suggested_high  rule_id  | clicks_30d  spend_30d  orders_30d  sales_30d  acos_30d  roas_30d  | scraped_at
```

- `row_id`: the keyword text (for SP-Manual-Keyword), target id
  (for SP-Manual-Product / Category), auto-target-group name
  (`close-match` / `loose-match` / `substitutes` / `complements` for
  SP-Auto), or noon target/query string. Must be unique within the
  campaign.
- `status`: `delivering` / `paused` / `archived` / `ineligible`.
- `match_type`: `broad` / `phrase` / `exact` / `auto` / `category` /
  `asin` / `noon-target` / `noon-query`. Use `—` when not applicable.
- `bid`: the actual keyword/target bid in the campaign's marketplace
  currency, with symbol (e.g. `USD 3.00`). Keep whatever currency the
  marketplace renders — do NOT hardcode a currency. For noon targets
  use the marketplace currency (e.g. `USD 0.45`).
  This is the cell at **column position 5** in Amazon's ag-Grid
  Targeting tab (see `mechanics.md § Canonical column layout`) — NOT
  the suggested-bid midpoint at position 2. Misreading this is the
  single defect class this file is designed to catch.
- `suggested_low` / `suggested_mid` / `suggested_high`: Amazon's
  recommended range. Parse all three from the position-2 cell. For
  Noon use `—` (Noon doesn't expose a suggested range).
- `rule_id`: id of any rule attached to the row (rule-based bidding,
  placement modifier). `—` if none.
- `clicks_30d` / `spend_30d` / `orders_30d` / `sales_30d` /
  `acos_30d` / `roas_30d`: rolling-30-day metrics at scrape time.
  Use `—` for cells the platform rendered as `—`. Currencies keep
  their symbol on monetary cells.
- `scraped_at`: ISO 8601 UTC timestamp of the scrape (e.g.
  `2026-05-20T07:30:00Z`).

The vertical bars (`|`) in the schema are visual section markers
**only** — they are not in the file. Sections (config block / metric
block / scrape stamp) help future readers spot what kind of change
each diff is, but the on-disk format is plain tab-separated.

Header row (verbatim, no extra columns):

```
row_id	status	match_type	bid	suggested_low	suggested_mid	suggested_high	rule_id	clicks_30d	spend_30d	orders_30d	sales_30d	acos_30d	roas_30d	scraped_at
```

Sort rows by `row_id` (case-insensitive Unicode) so identical
row-id sets produce identical diffs across audits — re-ordering must
NEVER appear in a commit.

## Phase 2 — diff + write

> 🛑 **Write IMMEDIATELY after drilling, not in a batched
> end-of-session sweep.** A long audit (10+ campaigns × 30+ rows
> × multiple platforms) routinely exceeds one Claude Code context
> window and the runtime compacts mid-run. **Anything sitting in
> "I'll write all the TSVs at the end" is lost on compaction.**
> Drilled-and-persisted campaigns survive; drilled-but-pending
> ones disappear. Field-observed: an audit that drilled every
> campaign across multiple platforms wrote TSVs for the last
> platform (~30 minutes before completion) but lost all the pending
> TSV writes for the platforms it hadn't persisted yet when the
> session was compacted at ~80% context.
>
> The contract is: **for each campaign you drill, the TSV write
> for that campaign happens BEFORE you move to the next
> campaign.** Not after all drills. Not after the report. Per
> campaign, write-then-move.

After scraping each campaign:

```
1. Read existing file: read_file("stores/<slug>/ads/<platform>/<country>/<campaign_id>.tsv")
   If missing → this is the first audit; skip diff, just write.

2. Parse the file into a dict {row_id: {col: value}}.

3. For each scraped row:
   a. Look up row_id in the file dict.
   b. Compare the CONFIG BLOCK only (status, match_type, bid,
      suggested_*, rule_id). Metric drift is expected daily and is
      NOT a drift signal.
   c. If config block matches → update metric block + scraped_at,
      continue.
   d. If config block differs → flag OBSERVED_DRIFT for this row.
      Include in the Phase 3 report under a "Drift since last audit"
      subsection per campaign:

      | row_id | field | was | now | last commit |
      |---|---|---|---|---|
      | wireless mouse | bid | USD 3.00 | USD 3.50 | apply: ... (2026-05-15) |

      Do NOT silently overwrite. The drift may be (a) the agent
      misreading the column layout (the bid-cliff defect class), (b)
      an external change (Amazon auto-adjust, manual user action),
      (c) a real change from a Phase 4 apply commit we have on
      record. The `last commit` column resolves which it is.

4. After processing all rows: write the updated file in full (header
   + sorted rows). Auto-commit fires with subject:

     audit: <store-slug> <platform>-<country> <campaign_id> — <N> updates, <M> drift

   The workspace manager already commits files written via the MCP
   tool; you do not invoke git directly.
```

**Verification step (must run before publishing the audit)**: after
writing the file, re-read it and re-compare to the scraped data. If
any cell still differs, the write didn't take — fix before
proceeding (this catches MCP write-symlink issues — see
`docs/workspace.md § symlink-write-caveat`).

## Phase 3 — recency check

Before emitting any `Trim` / `Pause` / `Raise` / `Scale` /
`bidding-strategy change` recommendation for a row, query the file's
git history for the **most recent commit that changed this row's
config block**:

```bash
git -C ~/.vibe-seller log -1 --format='%H %ci %s' -S '<row_id_substring>' \
    -- stores/<slug>/ads/<platform>/<country>/<campaign_id>.tsv
```

(The `-S` flag finds commits that added or removed the row_id text,
which is the cheapest proxy for "this row was touched". For exact
field-level recency, parse `git log -p` and look for the column
position you care about.)

Recency table (override only if explicit user instruction):

| Last config change | Recommendation gate |
|---|---|
| Never (no prior audit) | Normal recommendation logic applies |
| ≥ 14 days ago | Normal recommendation logic applies |
| 7–14 days ago | Allowed, but downgrade trims by one tier (Standard → Soft) |
| < 7 days ago | **Downgrade to `Hold`** — cell text: `Hold (recent change — last edit N days ago, wait for 7-day data)`. The original (would-have-been) action verb is surfaced in the trailing narrative ("would have proposed Trim −20%; deferred pending observation window"). |

The 7-day window matches Amazon's documented "give changes time to
take effect" guidance. Apply the same rule to noon.

## Phase 4 — apply ledger

After EACH state-modifying browser-use action in Phase 4:

1. Confirm the change took effect (re-read the bid cell / status
   toggle / placement modifier — see `mechanics.md` per-action
   verification steps).
2. Update the corresponding row in the TSV file. Set `scraped_at` to
   the post-action timestamp.
3. The workspace auto-commit fires with subject:

     apply: <store-slug> <campaign_id> <row_id> — <field>: <old> → <new>

   One commit per state change, not one per Phase 4 batch. This keeps
   the cause-and-effect record at action granularity — if a bid
   change later turns out to have been wrong, `git revert <hash>`
   targets exactly that one row's old value.

If the verification step fails (cell didn't update, modal stayed
open, etc.), do NOT update the file. The TSV represents observed
state; an unverified action is not observed state.

## Cross-references

- Column layout for Amazon Targeting tab: `mechanics.md § Canonical
  column layout for SP-Manual-Keyword Targeting tab` — the source of
  truth for which DOM cell is bid vs suggested.
- PROTECT rules (which override Phase 3 recency downgrade): see
  `tuning-thresholds.md § Protect-zone`. A PROTECT row is `Hold`
  regardless of recency; the recency rule only matters for rows that
  WOULD have been touched.
- File-history HTTP API (for UI surfacing of the audit trail):
  `/api/workspace/file/history`, `/api/workspace/file/at-commit` —
  these read the same git history programmatically.

## Anti-patterns

- **Writing only when something changed** — always write at end of
  Phase 2 to refresh `scraped_at` and metric block. A missing
  scrape-stamp is indistinguishable from a missing audit; explicit
  is better.
- **Hand-editing the TSV** — the workspace symlink doesn't accept
  direct edits via the built-in Write tool (see project
  CLAUDE.md). Always use `vibe_seller_write_workspace_file`.
- **Embedding TSV in the audit report** — the audit report is for
  human reading; the TSV is the structured ground truth. Reference
  the file (`see stores/acme-store/ads/amazon/<country>/A33333333.tsv`), don't
  inline it.
- **Multi-row commits in Phase 4** — one action = one commit. A
  multi-row commit hides which action caused which row to change.

## Worked example — first-time audit creates the file

After Phase 2 drill on A33333333:

```
stores/acme-store/ads/amazon/<country>/A33333333.tsv  (new file)

row_id	status	match_type	bid	suggested_low	suggested_mid	suggested_high	rule_id	clicks_30d	spend_30d	orders_30d	sales_30d	acos_30d	roas_30d	scraped_at
cable organizer	delivering	broad	USD 3.00	USD 0.47	USD 0.62	USD 0.78	—	44	USD 101.04	8	USD 302.61	33.39%	2.99	2026-05-20T07:30:00Z
phone stand	delivering	broad	USD 2.00	USD 0.61	USD 0.70	USD 0.78	—	3	USD 5.24	1	USD 33.29	15.74%	6.35	2026-05-20T07:30:00Z
wireless mouse	delivering	broad	USD 3.00	USD 0.45	USD 0.60	USD 0.75	—	200	USD 500.00	30	USD 1,200.00	41.67%	2.40	2026-05-20T07:30:00Z
... (13 paused rows omitted for brevity, sorted alphabetically)
```

Note: `bid = USD 3.00`, not `USD 0.60`. The suggested midpoint
(`USD 0.60`) is in `suggested_mid`, where it belongs. This file is
the structural defense against the suggested-vs-bid confusion class.

## Worked example — next audit detects drift

A week later (2026-05-27), an audit re-scrapes A33333333. Suppose
the user had manually lowered `wireless mouse` to USD 1.50 in the
Amazon UI but did not log it. The scrape returns `bid = USD 1.50`.
Phase 2 compares to the file's `bid = USD 3.00` → OBSERVED_DRIFT.

The Phase 3 report includes:

```
### A33333333 — drift since last audit (2026-05-20)

| row_id | field | was | now | last commit on file |
|---|---|---|---|---|
| wireless mouse | bid | USD 3.00 | USD 1.50 | audit: acme-store amazon-<country> A33333333 — 16 updates, 0 drift (2026-05-20) |
```

The "last commit on file" being an `audit:` (not `apply:`) commit
tells the agent: *we recorded USD 3.00 from a scrape last week; this
week's USD 1.50 was not applied by us, so it was either an external
change or our column-read was wrong*. Phase 3 also runs the recency
check — the apparent change is recent (< 7 days), so any
recommendation against `wireless mouse` downgrades to `Hold (recent
external change — verify with user, then wait for 7-day data)`.

After publishing the drift report, the TSV file gets updated to the
new observed values; the `audit:` commit captures the new state. The
file is the source of truth going forward.
