# Noon Ads — applying approved changes

The noon click paths for an `execute` phase. The contract — which rows
you may apply, verifying on the console, `EXECUTION_LOG.tsv` and the
`applied_*` columns — is
[`../../amazon-ads/references/execution.md`](../../amazon-ads/references/execution.md);
the columns are in [`data-contract.md`](data-contract.md).

- [Before you click](#before-you-click)
- [Bid edit](#bid-edit)
- [Pausing a target](#pausing-a-target)
- [Negative keywords](#negative-keywords)
- [Rows you cannot apply](#rows-you-cannot-apply)

## Before you click

- **Only rows the user approved.** Before declaring `execute`, bring
  `CHANGES.tsv` in line with the user's reply, a changed value ("2a at
  0.95 instead") included; the server freezes it at the declaration and
  checks every logged bid against the frozen `target_bid`.
- **Navigate by campaign id.** Open
  `…/en-{cc}/campaign/details/{C_ID}?mpCode=noon&project=PRJ{project_id}`;
  the targets grid is `…&tab=target&target_filter=all`. Report names
  often differ from live names.
- **Match text and match type.** The same keyword often has several rows
  (Phrase, Exact, a longer variant). Touch only the row whose keyword
  *and* match type equal the approved row's `target` and `match_type`.
- **Trusted clicks for Save / Update / toggles.** These React handlers
  ignore a `js("…click()")`; find the control (DOM rect, or a screenshot
  if the DOM gives nothing usable) and use `click_at_xy`. Keep `js()` for
  navigation, reading and the native-setter fills below.

## Bid edit

The row's **Edit Bid** button opens a modal: **`BidUpdateModal`** on a
manual campaign's keyword/category row (one number input, placeholder =
current bid, **Update** disabled until the value changes), or the
**Targeting & BIDDING** modal's **Bid Amount** box
(`placeholder="Enter Bid Amount"`) for an auto campaign's `auto keyword`
/ `auto category` row.

The input does not reliably clear with Ctrl+A / Delete: typed digits
concatenate, so a `1.30` target can land as `11.3` — a tenfold
overspend. So:

1. Focus the input; set it to `''` with the prototype `value` native
   setter and dispatch `input`.
2. Read it back; it should be exactly `""`.
3. Set the target the same way and read it back. It should read exactly
   the approved `target_bid` (`1.30`, not `11.3`, `1.301` or `11.30`);
   anything else means the clear failed — clear again rather than
   pressing Update.
4. Press **Update**, wait for the modal to close, reload the Targets tab
   and re-read the row's bid (the figure in its `editWrapper` button).

The approved bid is an exact value, not a floor: set exactly it. If the
live bid already equals it, there is nothing to apply.

## Pausing a target

A Live row has a **Pause** button in its Actions cell; a paused row has
**Activate**. Click, then re-read the row's status word.

Pause only the rows approved as `pause`. Count the rows you paused per
campaign afterwards; it should equal the approved count for that
campaign. A larger number means a sweep caught extra rows — re-activate
the ones you paused by mistake (only state changes you made in this run).
Auto `auto keyword` / `auto category` rows have no per-row switch; they
live and die with the campaign.

## Negative keywords

Not inline. Open the campaign's **NEGATIVE TARGETING** card (or section 4
of the full editor) and add the approved terms to the **Exact Keywords**
or **Phrase Keywords** box according to the row's `match_type` — the
mechanics, the 100 + 100 limit and the Save button to use are in
[`campaign-editor.md`](campaign-editor.md) § Editing an existing campaign.
Re-open the editor to confirm the tags persisted.

Negate only clean query terms. Skip collapsed summary rows
(`…（含变体 N 条）`, `… 等 ASIN 匹配`) and noon product ids (`z…` /
truncated) — they are not single negative keywords — and never negate a
term the same audit marks as converting in that campaign.

## Rows you cannot apply

Don't block on a row you cannot match to a live row with confidence (the
named value doesn't appear live, a label is unclear): skip it, finish
every other row, and list each skipped row with its reason in your
result. Skipped rows stay out of `EXECUTION_LOG.tsv` — the log records
only changes visible on the console.

- A bid row on a target or campaign that is already paused is moot: skip
  it and say so. Don't re-enable an ad to make a row applicable.
- Before calling the run complete, re-open each touched campaign's live
  Targets tab (and negatives editor) and confirm every logged row matches
  the console. The log is your claim; the console is the evidence.
