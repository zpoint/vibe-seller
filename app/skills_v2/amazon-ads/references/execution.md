# Executing approved ad changes

Shared by `amazon-ads` and `noon-ads`. An `execute` phase applies rows
of an audit's `CHANGES.tsv` that the user approved, verifies each one on
the console, and records it. File and column definitions:
[`data-contract.md`](data-contract.md).

- [What is approved](#what-is-approved)
- [The loop](#the-loop)
- [Recording a change](#recording-a-change)
- [Harvest preflight](#harvest-preflight)
- [When a change won't apply](#when-a-change-wont-apply)

## What is approved

When the user replies with the changes to make ("do 1, 3, 5", "all of
campaign X", "execute the plan"):

1. Bring `CHANGES.tsv` (this task's, or the parent audit's) in line with
   what they approved. Map their words to rows using the audit's report.
   If they named a different value ("lower 2 to 0.95 instead"), set that
   row's `target_bid` to it. Rows they did not approve become `hold`.
2. Declare `execute` with `vibe_seller_declare_ad_task`, scoped to those
   campaigns. The server freezes `CHANGES.tsv` and answers with the
   changes it froze; execution is checked against that copy. If the
   list is not what the user approved, fix `CHANGES.tsv` and declare
   `execute` again before changing anything — it re-freezes until the
   first change is in `EXECUTION_LOG.tsv`, and not after.
3. Execute exactly the approved rows at exactly their `target_bid`.

Applying ad changes spends the user's money and is hard to undo, so
never apply a change the user did not approve — not a neighbouring row,
not a "while I'm here" tidy-up, not a different bid than the approved
one. If the approval is unclear (which row, which value), ask before
declaring.

- **What is live differs from what was approved** (a change went in at
  the wrong value, or a check flags it): do not make another live change
  to fix it. Tell the user what is live, and keep or revert it as they
  decide.
- **The approval names proposals outside `CHANGES.tsv`** (harvest a term,
  budget, placement modifier, bidding strategy, campaign pause): apply
  them as approved, verify them the same way, and list them in your
  result. They don't go in `EXECUTION_LOG.tsv`, which takes only
  `CHANGES.tsv` rows.
- **No audit to execute against** (no report or `CHANGES.tsv` in this
  task or its parent): ask with `AskUserQuestion` whether to run an audit
  first, rather than re-auditing silently.
- `hold` rows are not executed and not logged.

## The loop

Long executions get compacted, and compaction blurs which rows were
done. Anchor progress in the task list, which survives:

1. Right after reading the approval, create one task-list item per row
   to apply (subject = campaign + target + action). All start pending.
2. Take the next pending item and mark it in progress.
3. Apply it — one row at a time. For many bid changes in one campaign,
   the bulk path (`bulk-operations.md`, `ads_bulk.py bid-update` /
   `negate`) is the default on Amazon; click paths are in `mechanics-edit.md`
   (noon: `noon-ads/references/applying-changes.md`). Keep a before/after capture in your task directory (CWD).
4. Read the change back from the console: the bid cell, the state toggle,
   the negative in the negatives list, a re-export after a bulk upload.
   A bulk `Success` only means the file was accepted; rule-based bidding
   can also move a bid back, so read after a short wait.
5. Only once it is visible on the console, record it (next section) and
   mark the item completed.

After a compaction, check the task list first and resume from the first
item not completed; the summary's prose about what was done is not
reliable. The phase is done when no items are pending or in progress.

## Recording a change

For each change confirmed on the console:

1. **Append a row to `EXECUTION_LOG.tsv`** (task root): `campaign_id`,
   `ad_group`, `target`, `match_type` and `action` exactly as in the
   approved row; `from_bid` (the bid before) and `to_bid` (the bid now
   showing, equal to the approved `target_bid` within 0.01) for bid
   changes, blank otherwise; `applied_at` as an ISO time.
2. **Update the campaign's TSV** in
   `store-data/<slug>/ads/<platform>/<country>/` via
   `vibe_seller_write_workspace_file`: on the matching row set
   `applied_action` to the action word (`raise`, `lower`, `pause`,
   `negate`), `applied_at` to the same ISO time, `previous_bid` to the
   old bid, and update `bid` / `state` to what the console now shows.
   Negates go on the search-term file's row for that term. Leave other
   rows' `applied_*` values as they are.

Write these only after the console shows the change. A record written on
intent outlives a failed apply: the next audit would read a change the
account never saw and hold that target through a cooldown for nothing.

## Harvest preflight

Amazon rejects a new keyword whose words near-match an existing keyword
in the same campaign — even a paused one — and reports only "X of Y
keywords failed to add". Before adding a harvested keyword:

1. Find the manual campaigns for this SKU in the same marketplace (the
   audit usually names them; otherwise read the per-campaign TSVs in
   `store-data/<slug>/ads/<platform>/<country>/`).
2. Look for the term and its obvious variants (word order, singular /
   plural, extra qualifiers) in their targeting files.
3. A live variant anywhere → the goal is already met; skip and say which
   campaign serves it.
4. A paused variant in the target campaign → re-enable it at the harvest
   bid instead of adding a duplicate, and read the bid back.
5. No variant → add normally. A rejection now is likely an account or
   brand restriction worth reporting.

## When a change won't apply

Retry once by the other path (bulk vs click). If it still fails, leave
it out of `EXECUTION_LOG.tsv` and the TSV, and explain in your result
what you tried and what the console said, so the user can decide. A
campaign type that doesn't support the action (e.g. keyword targets in
an auto ad group) is a reason to report, not to improvise a different
change.
