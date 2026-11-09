# Amazon Ads — editing live campaigns

Click recipes for changing a running campaign one entity at a time.
Apply only changes the user approved; the approve → apply → verify →
log procedure, and what to record afterwards, is in
[`execution.md`](execution.md). For many bids or a new campaign the bulk
round trip in [`bulk-operations.md`](bulk-operations.md) is the default;
these recipes are for single edits, fields with no bulk column, or after
two failed imports.

## Contents

1. [Use trusted input, not `js()` clicks](#1-use-trusted-input-not-js-clicks)
2. [SP keyword / target bid](#2-sp-keyword--target-bid)
3. [Pause or enable a target or campaign](#3-pause-or-enable-a-target-or-campaign)
4. [Negating search terms and products](#4-negating-search-terms-and-products)
5. [The React textarea workaround](#5-the-react-textarea-workaround)
6. [Harvesting a search term as a keyword](#6-harvesting-a-search-term-as-a-keyword)
7. [Placement, bidding strategy and budget edits](#7-placement-bidding-strategy-and-budget-edits)
8. [Sponsored Brands (SB / SBV) bid edit and pause](#8-sponsored-brands-sb--sbv-bid-edit-and-pause)
9. [Confirm every change on the console](#9-confirm-every-change-on-the-console)

## 1. Use trusted input, not `js()` clicks

Console cells are React components. An event raised from `js()`
(`element.click()`, `dispatchEvent`, a native value setter + `input`)
has `isTrusted=false`; React ignores it and the edit reverts on blur.
The helpers `click_at_xy`, `type_text`, `press_key` and `fill_input`
go through CDP `Input.dispatchMouseEvent` / `dispatchKeyEvent` /
`insertText`, which React treats as a real user. So the pattern for
every edit is: use `js("return …getBoundingClientRect()")` only to find
coordinates, then act with the trusted helpers. Re-read the rect if the
DOM shifts (opening edit mode moves things).

## 2. SP keyword / target bid

The bid input is a shadow-DOM `kat-number-input`, not a plain `<input>`
in the cell. browser-use 0.13 has no element indices, so target it by
position:

1. `js(...)` — walk shadow roots to that row's `kat-number-input` (its
   value is the current bid) and return the inner input's rect centre.
   Clicking the outer cell instead puts the text in the wrong node.
2. `click_at_xy(x, y)` — focus it.
3. `press_key("Control+a")` — select the old value. Typing alone
   sometimes appends (`3.00` + `2.50` → `3.002.50`), which on a live
   store is a real-money error.
4. `type_text("4.00")`.
5. `press_key("Enter")` — commits. A `js()`-dispatched Enter is
   untrusted, so React restores the old value on blur.
6. Re-read the cell and confirm it shows exactly the new value.

## 3. Pause or enable a target or campaign

`click_at_xy` the row's state toggle (read its rect first), then re-read
the state. Change on/off state only where the approved change says so;
a paused target with an approved bid change stays paused (the bid
change is moot, see [`execution.md`](execution.md)).

## 4. Negating search terms and products

The ad console loads in English; the Bulk Operations page may show a
localized error on its own, which says nothing about this UI flow.

**Ad-group tabs are buttons without hrefs.** Campaign → ad group lands
on `Ads | Targeting | Negative targeting | Search terms | Ad group
settings | History`; click tabs by rect + `click_at_xy`.

**A known list of terms — the "Add negative keyword" form** (preferred:
batches, no virtualized grid, no portal):

1. Ad group → **Negative targeting** tab → **Negative keywords**
   sub-tab (`click_at_xy`).
2. Click **Add negative Keyword**.
3. Pick the match-type radio: `NEGATIVE_EXACT` (default, blocks only
   that query) or `NEGATIVE_PHRASE`. Match type is per batch, so group
   terms by type.
4. Put the terms in the textarea (`…:kwp:kwp-enter-list-text-input-area`),
   one per line:
   `fill_input("textarea[id$='kwp-enter-list-text-input-area']", "term a\nterm b")`.
   If the Add button stays disabled, use § 5.
5. Click **Add keywords** (stages them), then **Save** (commits) — both
   by rect + `click_at_xy`.
6. Re-read the Negative keywords list and confirm the terms are there.

**One term at a time — the `Add as ⌄` dropdown** on the Search terms
tab. Each row's button has a stable id
`…:spSearchTerms:cell-actions-<N>:actions`; `click_at_xy` its rect to
open three `role=option` entries: `Add as keyword`, `Add as negative
exact`, `Add as negative phrase` (no negative broad on SP). Click the
one matching the approved match type; it commits at once and the button
flips `Add as` → `Added as`. The row stays in place, so you can walk
down the list. Rows below the viewport (y > ~839) are not in the DOM:
scroll the grid, re-read, then click. The dropdown sometimes renders in
a `#hmhFlyoverRoot` portal where the option click does not reach React
(no flip to `Added as`) — switch to the form above.

**Negating an ASIN — a different UI.** A `B0…` (10-character) row is a
product placement, not a keyword, and the keyword form cannot take it:

1. Ad group → Negative targeting → **Negative products** sub-tab.
2. **Add negative product targets** → **Exclude products** → **Enter
   list**.
3. Paste the ASIN(s), one per line (`fill_input`) → **Add** → **Save**
   (`click_at_xy` each).
4. Re-read the Negative products list.

Route a mixed list by shape: `B0` + 8 characters → negative product;
anything else → negative keyword. For an auto campaign whose search
terms come from many ad groups, campaign-level negatives (campaign →
Negative targeting) cover them all.

**The search box on the Search terms tab persists across campaigns.**
Clear it when you enter each campaign's tab, or you act on the wrong
terms (or none).

## 5. The React textarea workaround

The Add keywords / Add negative keywords modals use a React-controlled
`<textarea>`; the Add button enables only when React's state matches.
`fill_input`, `type_text`, a native setter + `input`, clipboard paste and
CDP `insertText` have all been seen to leave the state unchanged there.
`execCommand('insertText')` works, because it fires the
`beforeinput`/`input` events with `inputType: 'insertText'` that React's
handler listens to:

```javascript
const ta = document.getElementById('<textarea-id>');
ta.focus();
ta.select();   // replace any pre-fill instead of appending
document.execCommand('insertText', false, 'term one\nterm two');
```

Procedure:

```bash
# 1. Open the modal (Add keywords / Add negative Keyword button).
# 2. Click the "Enter list" tab. Some marketplaces open on another tab;
#    then the textarea exists but is invisible and insertText does
#    nothing. Clicking it when already selected is harmless.
browser-use <<'PY'
js("""
  var clicked = false;
  document.querySelectorAll('span, button, [role=tab]').forEach(function(el){
    if ((el.innerText||'').trim() === 'Enter list' && !clicked) {
      var r = el.getBoundingClientRect();
      if (r.width > 0) { el.click(); clicked = true; }
    }
  });
  return 'enter-list tab clicked: '+clicked;
""")
PY

# 3. Find the textarea id and confirm it is visible.
browser-use <<'PY'
js("""
  var ta = document.querySelectorAll('textarea')[0];
  if (!ta) return 'no textarea';
  var r = ta.getBoundingClientRect();
  return JSON.stringify({id: ta.id, visible: r.width>0});
""")
PY

# 4. Insert. The id contains colons, so use getElementById (or escape
#    the colons for querySelector).
browser-use <<'PY'
js("""
  var ta = document.getElementById('<id-from-step-3>');
  ta.focus(); ta.select();
  document.execCommand('insertText', false, 'keyword1\\nkeyword2');
  return 'inserted: '+ta.value;
""")
PY
```

5. Check the match-type radios (Exact / Phrase / Broad; for negatives
   Exact is the default) by their `aria-checked`, and set the approved
   one.
6. Click **Add keywords** (moves the staging list to the confirmed
   list), then **Save** if the modal has one — some marketplaces submit
   on Add alone, others need Add then Save; read the DOM. Click both by
   rect + `click_at_xy`.
7. Close the modal and confirm the keywords appear in the targeting /
   negative-targeting table.

## 6. Harvesting a search term as a keyword

**Manual campaign:** the row's `Add as ⌄` → `Add as keyword`. The modal
pre-ticks all three match types; untick the ones not approved, then
**Add keywords**. The row's `Added as` becomes `Keyword`.

**Auto campaign:** an auto ad group cannot hold keywords, so the term
goes into a manual campaign for the same product, in a dedicated
harvest ad group named `<campaign-name-slug>-harvest-kw` (e.g.
`widget-006-auto-sa-harvest-kw`) so agent-managed harvest groups stand
apart from the user's own:

1. Read the auto campaign's product(s) from its Ads tab.
2. Find a manual campaign advertising the same product; if there is
   none, creating one is a separate approved change
   ([`mechanics-create.md`](mechanics-create.md)).
3. On that campaign's ad-groups page, look for an existing harvest
   group:
   ```javascript
   JSON.stringify(
     Array.from(document.querySelectorAll('a[href*="/ad-groups/"]'))
       .map(a => ({name: a.textContent.trim(), href: a.href}))
       .filter(o => o.name.includes('harvest-kw')))
   ```
4. If none exists, create it ([`mechanics-create.md` § 10](mechanics-create.md#10-new-ad-group-in-an-existing-campaign)):
   same product(s), **keyword** targeting, default bid at the
   suggested-bid midpoint.
5. Add each term as an **Exact** keyword at the approved bid.
6. Add the same term as **negative exact at the auto campaign level**
   (not the ad group) so the two campaigns don't compete for it.

Before re-adding a keyword, check whether it already exists paused in
the target ad group; the preflight for that is in
[`execution.md`](execution.md).

## 7. Placement, bidding strategy and budget edits

**Placement %** (campaign → Bid adjustments): click the `0%` button; a
`kat-numberinput` popup opens with Save / Cancel and its validation text.
Range 0–900%, increase only — this lever cannot cut a placement's spend
(use a bid trim, the bidding strategy or negative products instead).
Some rows show `Recommended: X%`; clicking it applies it.

**Bidding strategy** (Campaign settings → Campaign bidding strategy):
radios **Fixed bids**, **Dynamic bids - down only**, **Dynamic bids - up
and down** (Amazon moves bids up to ±100%), **Rule-based bidding**
(target ROAS; sub-form `Drive sales while seeking to keep ROAS at or
above <n>` with an `Edit` link). Some accounts show a confirmation modal
on save.

**Daily budget** (Campaign settings → Budget): a `kat-numberinput`
showing the amount. Click the value, select-all, type the new amount,
then click the inline **Save** under it — it does not save on blur, and
a value set + `input`/`change`/`blur` without Save does not persist.
The campaign header updates after Save. `Add budget rule` next to it is
for scheduled event budgets, not routine tuning.

## 8. Sponsored Brands (SB / SBV) bid edit and pause

**Navigate by clicking through.** Deep-linking to
`…/cm/sb/campaigns/<id>/keywords?entityId=…` leaves the keyword grid
empty (the grid's XHR gets no auth token), and
`window.history.pushState` from `js()` does not move the React router.

```bash
# 1. The all-campaigns list
browser-use <<'PY'
new_tab("https://advertising.amazon.<tld>/campaign-manager/all-campaigns?entityId=ENTITY…")
wait_for_load()
PY

# 2. Filter by campaign name. This shadow-DOM input accepts the native
#    setter + input event (type_text into it does not work):
browser-use <<'PY'
js("""
(function() {
  const inp = document.getElementById('globalBetaAllCampaigns:quickFilter:input');
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
  setter.call(inp, 'CAMPAIGN NAME');
  inp.dispatchEvent(new Event('input', {bubbles:true}));
})()
""")
PY

# 3. After ~2 s, open the campaign by its anchor text:
browser-use <<'PY'
js("""
[...document.querySelectorAll('a')]
  .find(a => a.innerText.trim() === 'CAMPAIGN NAME' && a.getBoundingClientRect().y > 0)
  ?.click()
""")
PY

# 4. On the ad-groups list, open the targeting link:
browser-use <<'PY'
js("""
[...document.querySelectorAll('a')]
  .find(a => a.href?.includes('/targeting') && a.getBoundingClientRect().y > 0)
  ?.click()
""")
PY
# 5. After 2–3 s: document.title contains 'Ad Group: …' and the URL
#    contains '/targeting?entityId=…'.
```

Step 2 types into the persisted campaign search box described in
[`mechanics-reading.md` § 1](mechanics-reading.md#1-the-campaign-list-clear-filters-trust-the-grids-total);
clear it with its ✕ when you are done, or the next enumeration reads a
filtered list. To go
back from inside a campaign, `click_at_xy` the `Campaigns` breadcrumb
(`<a aria-label="Campaigns.">`). SB URLs need the alphanumeric campaign
id; get it from the result link's `href` (see
[`mechanics.md` § 2](mechanics.md#2-urls)).

**Reading the rows** (live bid per row). The `currency_renderer_input`
`value=` attribute can be stale; trust the parsed text:

```javascript
// pass to js("""…""") (double each \n → \\n)
(function() {
  const grids = [...document.querySelectorAll('[role=grid]')];
  const kwLines = (grids[2]?.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
  const metLines = (grids[3]?.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
  const keywords = kwLines.filter(l => l !== 'select');
  const MATCH = new Set(['Phrase', 'Exact', 'Broad']);
  const rows = [];
  let i = 0, rowNum = 0;
  while (i < metLines.length && rowNum < 200) {
    if (!MATCH.has(metLines[i])) { i++; continue; }
    const mt = metLines[i++], st = metLines[i++];
    if (metLines[i] === 'Details') i++;
    if (metLines[i] === 'No current data') i++;
    if (metLines[i] === 'adjust value') i++;
    const bid = metLines[i++];
    if (/^[A-Z]{3}$/.test(metLines[i])) i++;
    rows.push({kw: keywords[rowNum]||null, rowNum, mt, bid});
    i += 7; rowNum++;
  }
  return JSON.stringify({kwCount: keywords.length, rows});
})()
```

All rows are in `grids[2]/grids[3].innerText` even below the fold, and a
below-the-fold row's shadow input still appears in `page_info()`, so
`click_at_xy` on its rect works without scrolling.

**Install PATCH monitoring first** (once per page load; a `new_tab` or
hard navigation loses it, so prefer in-app navigation):

```javascript
window._patches = [];
var origOpen = XMLHttpRequest.prototype.open;
var origSend = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open = function(m,u){this._url=u;this._method=m;return origOpen.apply(this,arguments);};
XMLHttpRequest.prototype.send = function(b){
  if(this._method==='PATCH'){
    var self=this;
    this.addEventListener('load',function(){
      window._patches.push({url:self._url,s:self.status,resp:self.responseText.substring(0,800)});
    });
  }
  return origSend.apply(this,arguments);
};
```

**Bid edit** — SB uses a popover with a Save button (SP commits inline
on Enter):

1. Find the row's shadow input by its stable id
   `CAMPAIGN_KEYWORDS:table_cell_<ROW>-3_keywordBid:currency_renderer_input`
   and read its rect:
   `js("return document.getElementById('CAMPAIGN_KEYWORDS:table_cell_<ROW>-3_keywordBid:currency_renderer_input').getBoundingClientRect()")`.
2. `click_at_xy(x, y)` — a popover (`AACChromePortal`) opens with an
   `<input type=number>` and Save / Cancel.
3. Set the value — this popover input accepts the native setter:
   ```bash
   browser-use <<'PY'
   js("""
   (function() {
     const inp = document.querySelector('input[type=number]');
     const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;
     setter.call(inp, '1.30');
     inp.dispatchEvent(new Event('input', {bubbles:true}));
     inp.dispatchEvent(new Event('change', {bubbles:true}));
     return 'set to ' + inp.value;
   })()
   """)
   PY
   ```
4. Read the popover's Save rect and `click_at_xy` it.
5. Check `js("return JSON.stringify(window._patches)")`: success is HTTP
   207 with `"successfulCount":1`. The grid cell keeps showing the old
   bid after a successful PATCH; the PATCH response is the
   confirmation, and a fresh page load shows the new value.

**Pause / enable** — the row's toggle is
`<button role=switch id=CAMPAIGN_KEYWORDS:table_cell_<ROW>-1_state:state-switch-renderer>`.
Read its rect, `click_at_xy`, then confirm `aria-checked=false
title=Paused` in `page_info()` and a PATCH with `"state":"PAUSED"`
answered 207 / `successfulCount:1`.

## 9. Confirm every change on the console

`fill_input`, a coordinate Save, or a portal click can fail silently. A
change counts as made only once the console shows it: re-read the bid
cell, the state toggle, or the negative-keyword / negative-product list
(or, on SB, the PATCH response). Record it as
[`execution.md`](execution.md) describes only after that read-back.
