# Ad data contract

The server checks an ad task from tab-separated files with fixed headers,
never from the report's prose. Write the report for the user in whatever
shape reads best; put the numbers and decisions in these files. Headers
are matched by name: every listed column must be present (extra columns
are fine). Numbers are plain (`12.5`, not `SAR 12.5` or `12.5%`); one
currency column per row instead.

| File | Where | Written by |
|---|---|---|
| `CAMPAIGNS.tsv` | task dir | audit |
| `<campaign_id>.tsv` | `store-data/<slug>/ads/<platform>/<country>/` | audit (+ execution fills `applied_*`) |
| `<campaign_id>.searchterms.tsv` | same dir | audit |
| `CHANGES.tsv` | task dir | audit |
| `EXECUTION_LOG.tsv` | task dir | execution |

`<platform>` is lower-case (`amazon`, `noon`), `<country>` the market
code (`SA`, `AE`).

## CAMPAIGNS.tsv — what you enumerated

```
platform country campaign_id name type state currency window_start window_end spend clicks orders sales drill_status note
```

One row per campaign in every marketplace the declaration covers — read
the whole list (scroll, page, or use the export). `drill_status` is
`drilled` (both per-campaign files written), `inactive` (not live, not
drilled) or `blocked` (live but could not be read: say why in `note`).
A marketplace with no campaigns gets one row with an empty
`campaign_id` and a `note`. Dates are `YYYY-MM-DD`; `window_*` is the
date range the figures cover.

## Per-campaign files — the drill

Targeting, one row per keyword/target (not a campaign total):

```
ad_group target match_type state bid currency clicks spend orders sales acos roas applied_action applied_at previous_bid
```

Search terms, the full set (use the export when the page shows only the
top rows):

```
ad_group search_term source_target match_type currency clicks spend orders sales roas applied_action applied_at previous_bid
```

The search terms' spend should add up to the targeting spend for the
same window, and `CAMPAIGNS.tsv`'s spend to the targeting total. Leave
`applied_*` blank in an audit — execution fills them.

## CHANGES.tsv — what you propose

```
platform country campaign_id ad_group target match_type action current_bid target_bid acos roas cpc reason
```

`action` is one of `raise`, `lower`, `pause`, `negate`, `hold`.
`target_bid` is required for `raise`/`lower`. `cpc` is the row's actual
cost per click (spend / clicks). This file is the decision the user
approves; once the audit result is accepted the server keeps a frozen
copy, and execution is checked against that copy.

An `investigate` task proposes nothing: no `CHANGES.tsv` rows other than
`hold`.

## Store rules the server checks on CHANGES.tsv

Defaults, overridden by a `key: value` line in `stores/<slug>/notes.md`.
Those lines hold standards the user stated — never a value you derived
from another number: turning a 40% margin into `acos_no_lower: 40`
silently changes what the server enforces on every later run, and no
one ever chose it. When the user states a standard, write it there with
`vibe_seller_write_workspace_file` so this and later runs use it.

| key | default | rule |
|---|---|---|
| `acos_no_lower` | 30 | no `lower` while ACOS is under this % (or ROAS above 100/this) |
| `cpc_floor_multiplier` | 1.1 | a new bid is at least `cpc` x this |
| `scale_roas` | 5 | a `hold` on a row with higher ROAS needs a `reason` |
| `change_cooldown_days` | 7 | a target changed within this window is held, with the date in `reason` |
| `margin` | 25 | gross margin % assumed when unknown — say so in the report |
| `reconcile_tolerance` | 0.15 | search-term vs targeting spend may differ by this fraction |
| `noon_reconcile_floor` | 0.85 | search terms must cover at least this share of targeting spend |

## EXECUTION_LOG.tsv — what you changed

```
campaign_id ad_group target match_type action from_bid to_bid applied_at
```

One row per change actually made, after it is visible on the console.
Only approved rows may appear, with `to_bid` equal to the approved
`target_bid` (within 0.01). Also record each change in the campaign's
targeting file (`applied_action`, `applied_at` as ISO time,
`previous_bid`). A change you could not make is left out of the log and
explained in your result.
