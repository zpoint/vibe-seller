# Noon Ads — campaign editor mechanics

Click paths for the create form and for changing an existing campaign's
keyword or negative set. What to choose is in
[`ads-creation.md`](ads-creation.md); the keyword list itself comes from
[`ads-keyword-research.md`](ads-keyword-research.md).

- [Create: step 1 — ad type](#create-step-1--ad-type)
- [Create: step 2 — the form](#create-step-2--the-form)
- [Launch and post-launch check](#launch-and-post-launch-check)
- [Editing an existing campaign](#editing-an-existing-campaign)
- [The editor as a read-only research tool](#the-editor-as-a-read-only-research-tool)
- [UI quirks that fail silently](#ui-quirks-that-fail-silently)

## Create: step 1 — ad type

**URL**: `admanager.noon.partners/en-{cc}/campaign/start?mpCode=noon&project=PRJ{project_id}`

Two radios — **Product Ads** (search terms and categories) and **Brand
Ads** (logo, brand name, products) — then **Continue**.

## Create: step 2 — the form

One page of numbered sections: **1. Product Selection** → **2. Bidding
Strategy** → **3. Targeting** → **4. Negative Targeting (Optional)** →
**5. Top-of-search placement (Optional)** → **6. General Settings**,
then the action buttons.

**1. Product Selection** — `Manual Selection` / `Bulk Upload`. Type into
`Search products` (SKU or title words). Each result row is a
`div[class*="ProductCard_cardContent"]`; the add control is an
`img[alt="add"]` at the row's right edge, outside `cardContent`, so
locate it by nearest y to the card. The panel opens at **Selected
Products (0)** — whatever is listed there at launch is what the campaign
advertises, so re-check the counter before launching. Pick the variant
that actually sells (a family `…Z-3/-4/-5` usually has one carrying the
traffic): check an existing campaign's Products tab or a prior export's
`SKUs` sheet; for a product with no history, noon's `High Potential`
badge is a reasonable tiebreak.

**2. Bidding Strategy** — three radios:

| Strategy | Behaviour |
|---|---|
| Dynamic Bid (Up & Down) — *Maximize Orders* | noon bids up to 1.5× your bid when it expects an order |
| Dynamic Bid (Down Only) — *Maximize ROAS* | noon lowers your bid when conversion looks unlikely |
| Fixed | your per-target bids are used as-is |

**3. Targeting** — **Auto Targeting** (default) or **Manual Targeting**
(keywords / categories / products). Negative Targeting is available
under Auto too, and is the main lever an Auto campaign has.

*Keywords tab.* Under **Manual Targeting Settings**, the `Search for
keywords` box returns cards carrying `searches/month: <range>` plus
`Exact` and `Phrase` buttons; clicking one adds the keyword at that match
type. Each added target gets a narrow `input[type=number]` (`step=0.01`,
width < 60 px) pre-filled with noon's suggested bid. To set bids,
native-set every narrow number input in the targeting block — scroll the
block into view first, since the inputs are not in the DOM until it
renders — then read the values back.

*Categories tab.* The category tree's `Refine` control does not expand in
the current build. Use the **`Search by Category name`** input instead:
a product word returns matching categories at every depth as a full path
plus a level name:

```
fashion/<gender>/clothing/<mid-category>            Mid Category
fashion/<gender>/clothing/<mid-category>/<leaf>     Leaf Category
```

Each result's add control is `img[alt="addButton"]` (the product picker
uses `alt="add"`). An added category gets its own bid and appears on the
Targets tab as `<path> Category Match`. A top-level department and a
leaf product type are one click apart and perform very differently, so
choose the depth deliberately and record it.

**4. Negative Targeting** — two `ant-select` boxes in tags mode, labelled
**Exact Keywords** and **Phrase Keywords**, each with an
`N/100 … Selected` counter (limit 100 + 100). Their `<input>` is ~4 px
wide, so a `width > 60` filter skips it; find each box by its label, not
by document order (the keyword section has a select of its own that can
precede them). Each box takes a comma-separated list committed with one
Enter: native-set the inner input's value, dispatch `input`, then
`keydown`/`keypress`/`keyup` for `Enter`. A word that itself contains a
comma goes in on its own. Confirm the `.ant-select-selection-item` tags
appear.

**5. Top-of-search placement** — `Increase bid by` fields for top of
search and product pages, 0–900%, blank = 0
(see [`ads-creation.md`](ads-creation.md) § Top-of-Search bid boost).

**6. General Settings** — Campaign Name, Marketplace (NOON), Start / End
date ("No end date"), Budget Details (**Shared Budget** or **Campaign
Budget**), **Maximum Daily Budget**. Buttons: **Cancel & Go Back**,
**Launch Campaign**.

## Launch and post-launch check

**Launch Campaign** sits below the fold (around y≈1378 in an 839 px
viewport). A coordinate click on its reported position hits nothing and
the form just stays put, with no error banner because nothing was
submitted. `scrollIntoView({block: "center"})`, re-read the rect, then
click. Success: the header switches to `Edit - <name> | Product Ad |
Live`. Launching straight from the form works; no draft save is needed.

Then re-read the campaign list and confirm for each new campaign: status
`Live`, the intended daily budget, `Manual targeting - Fixed` (if that
was the choice), and that it carries only the SKU you chose.

## Editing an existing campaign

The Targets tab has no "Add target" / "Add negative" button — it reads
and edits bids only (and its row button pauses, not removes). To change
the keyword or negative set:

1. **The detail page's setting cards.** The **NEGATIVE TARGETING** card's
   title button opens the negatives editor: the same two labelled boxes
   and counters as the create form, plus **Cancel / Update**. Words
   already listed are left alone; **Update** saves.
2. **The full editor**:
   `…/campaign/v2?project=PRJ{project_id}&mpCode=noon&campaignCode={campaign_id}&mode=edit`
   — section 3 adds/removes keyword, category or product targets;
   section 4 holds the negatives. Removing a row and saving deletes it.
   The page is one form with three buttons at the bottom: `Cancel & Go
   Back`, `Save and Pause`, `Save and Launch`. Click the one that matches
   the campaign's current status (`Save and Launch` for a Live campaign,
   `Save and Pause` only for a paused one), so the save does not flip its
   status. The save persists the targeting/negatives without touching
   budget, name, dates, strategy or status.
   - The TOS field's "Top bid percentage must be between 10% and 900%"
     text is a static hint, not an error: leave TOS empty.
   - If a real validation error appears (`.ant-form-item-explain-error`,
     seen on some Draft / Product-Ad campaigns), don't enter a TOS or
     other value to get past it. `Cancel & Go Back` changes nothing;
     report that campaign's change as not made.

Re-open the editor afterwards to confirm the change persisted.

Changing a live campaign's targeting or negatives is an ad change: it
happens only for rows the user approved
([`applying-changes.md`](applying-changes.md)). An add → verify → remove
round-trip for testing belongs on a designated test or paused campaign.

## The editor as a read-only research tool

Opening the editor changes nothing until you save, so it is the cheapest
way to get what the Targets tab can't show:

- **Untruncated keyword text** in the selected-target list.
- **`searches/month` per target** plus noon's suggested range. A target
  with no `searches/month` line is below noon's reporting threshold — a
  dead keyword. Scanning for missing lines is the fastest dead-weight
  check available.

Parse `document.body.innerText`: a target block reads `<keyword>` →
`searches/month` → `<range>` → `Keyword <Exact|Phrase>` → `Suggested` →
`<low>` → `-` → `<high>`. Leave without saving — navigating away discards
everything.

## UI quirks that fail silently

- **ant radios ignore coordinate clicks.** The strategy and targeting
  groups replace the real `<input>` visually; `click_at_xy` lands on the
  skin and the group keeps its old value. Click the input with JS, then
  read `checked` back. Indices shift once Manual Targeting expands, so
  re-query before each click:
  ```bash
  browser-use <<'PY'
  print(js("""var rs=[].slice.call(document.querySelectorAll('input[type=radio]'))
    .filter(function(e){return e.getBoundingClientRect().width>=5;});
    rs[2].click(); return JSON.stringify(rs.map(function(e){return e.checked;}));"""))
  PY
  ```
- **Off-screen keyword buttons.** The keyword card list grows past the
  viewport bottom (~839 px), and a coordinate click there fails silently
  while the run believes the keyword was added. `scrollIntoView({block:
  "center"})` the button, re-read its rect, reject `y < 0 || y > 800`,
  click, then count the per-target bid inputs to confirm.
- **Bulk bid path.** An older `Manual Upload` → Add Keywords → Select All
  → **Apply Bids to Targets** → **Set Custom bid** path exists, with a
  quirk where `step="1"` turned `1.00` into `10`/`100`. It has not been
  re-verified; if you use it, read every resulting bid back before
  launching.
