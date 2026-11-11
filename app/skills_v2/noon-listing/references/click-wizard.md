# noon — click-wizard create and the techniques it needs

Read from `noon-listing` § 2. Use the wizard for a one-off SKU when the
NIS import (`nis-import.md`) is not practical. Every Ant-Design select on
this path needs a trusted `click_at_xy`; a JS `.click()` does not open
them. If a required dropdown will not open with § 3's technique, switch to
the NIS import.

## Contents

1. The minimal path to a live listing
2. Wizard steps (Category → Brand → Identity)
3. Techniques: category tree, virtual-list selects, Arabic text, content URL, image upload, saving

## 1. The minimal path to a live listing

Do these, in order, and then stop:

1. Wizard create (Category → Brand → Identity), `fill_input` the SKU.
2. **Offer tab**: `fill_input` the Base Price, set **Warranty = No
   Warranty**, **Save Changes**, confirm **Submit**.
3. **Content** on `/d?code=…&tab=content`: Product Title (English with
   `fill_input`, Arabic with `type_text` — § 3) and **Gender**, then Save
   Changes → Submit. Content goes to async QC; the status counters lag by
   minutes, so do not re-fill because they still show issues.
4. **Image** (at least one): upload the seller's real product photo
   (§ 3). noon's async image validation rejects placeholders, and the
   listing never goes live without an image.
5. **Seller Status → ON**.

**Warranty** is required for the offer to save (`Save failed — Warranty /
No Offer Created` otherwise), and "No Warranty" is enough — it needs no
service centre or duration. Configuring a real warranty is a long chain
of further selects; skip it unless the seller asks.

**Gender** is required: Content Check reports `Missing Department` until
it is set, and Department has no field of its own. Setting the English
`Gender *` select (e.g. `Men`) auto-fills the Arabic gender and clears the
Department requirement. It is the same virtual-list select as Warranty
(§ 3).

Optional unless the seller asked: Size Unit, Feature Bullets, Long
Description, Material, Colour and the other detailed attributes. If the
seller wants one and its dropdown will not open, report it as a manual
follow-up rather than looping.

**Offer tab snippet (price + No Warranty).** The Warranty select is below
the fold, so this grows the viewport, trusted-clicks the Warranty card's
select and picks "No Warranty":

```bash
browser-use <<'PY'
import time
fill_input('input[name="new_price"]', '59.90')
cdp("Emulation.setDeviceMetricsOverride", width=1500, height=2200,
    deviceScaleFactor=1, mobile=False)
time.sleep(2)
p = js("""(function(){
  var hdr=Array.from(document.querySelectorAll('*')).find(e=>e.textContent.trim()==='Warranty'&&e.children.length<3);
  var card=hdr.closest('div'); for(var i=0;i<5&&card;i++){if(card.querySelector('.ant-select'))break;card=card.parentElement;}
  var sel=card.querySelector('.ant-select'); sel.scrollIntoView({block:'center'});
  var r=sel.getBoundingClientRect(); return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};
})()""")
click_at_xy(p['x'], p['y']); time.sleep(2)
js("""(function(){var dd=document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden)');
  var o=Array.from(dd.querySelectorAll('.ant-select-item')).find(x=>x.textContent.trim()==='No Warranty'); if(o)o.click();})()""")
time.sleep(1); cdp("Emulation.clearDeviceMetricsOverride")
js("(function(){var b=Array.from(document.querySelectorAll('button')).find(x=>/save changes/i.test(x.textContent)&&x.textContent.length<20); if(b)b.click();})()")
time.sleep(3)
js("(function(){var b=Array.from(document.querySelectorAll('button')).find(x=>/^submit$/i.test(x.textContent.trim())); if(b)b.click();})()")
time.sleep(3)
print(js("var t=document.body.innerText; return 'Save failed: '+/Save failed/i.test(t)+' | saved: '+/have been saved/i.test(t)"))
PY
```

If the price box still shows the old value appended to the new one
(`59.59.9`), the select-all clear did not take on that input — clear it
first as in `noon-listing` § 3.5.

## 2. Wizard steps

**URL**: `https://noon-catalog.noon.partners/en/catalog/create?project=PRJ{project_id}`

Create a new product through this wizard, not through the "paste a noon
PDP URL / copy SKU link" shortcut. The shortcut links your SKU to an
existing catalog parent you don't own, and every later Offer save then
fails with `Invalid sku_parents: {...}`; the wizard mints a clean
standalone parent.

**Input method.** Fill Latin-text fields with `fill_input` — it sends real
key events plus input/change, which is what React ingests. Setting
`.value` directly (or `type_text`) changes the DOM only, so "Final Price"
stays `-` and the save drops the field. Confirm a save by reloading and
re-reading the value; a green toast is not proof, and the hidden
`pricing-errors.undefined` string in the DOM is not a real error.

### Step 1 — Category

A hierarchical tree (e.g. Electronics > Accessories > Cables). Drill to a
leaf; it shows a "Selected" badge (see § 3 for the scrolling list). The
**Next** button often sits off-screen bottom-right; a JS click works on it
even there:

```bash
browser-use <<'PY'
js("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Next')?.click()")
PY
```

### Step 2 — Brand

```bash
browser-use <<'PY'
fill_input("input[placeholder*='Brand']", "<brand-name>")
wait_for_load()
js("Array.from(document.querySelectorAll('.ant-select-item-option')).find(o=>/<brand-name>/i.test(o.textContent))?.click()")
js("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Next')?.click()")
PY
```

The checkbox "This product does not have a brand name" covers unbranded
products.

### Step 3 — Identity

Enter the partner SKU, or click "Generate Partner SKU" (which fills
`PSKU_{project}_{digits}_X`). The SKU box has no stable `name=`, so target
the visible text input, then click **Create** (the final step, not Next):

```bash
browser-use <<'PY'
fill_input("input[type=text]", "WIDGET-006")
js("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.trim()==='Create')?.click()")
PY
```

Success redirects to
`/en/catalog/{noon_sku}/p?code={code}&project=PRJ{project_id}` with a
freshly minted noon SKU.

## 3. Techniques

- **Category tree.** Clicking a top category shows its product types as a
  scrollable list, and the leaf you want is usually below the fold.
  `scrollIntoView({block:'center'})` the leaf's text, re-read its rect,
  then `click_at_xy`. A product type can expand into sub-types, each with
  a **Select** button; clicking it enables Next.

- **Ant-Design selects are virtual lists.** The option you want often
  renders below the clipped dropdown panel, and a click at its reported
  rect lands outside the panel and closes it unselected. Grow the
  viewport, open the select, scroll the list holder, and click only an
  option that is inside the panel (Warranty, Gender, any long select):

  ```bash
  browser-use <<'PY'
  import time
  cdp("Emulation.setDeviceMetricsOverride", width=1500, height=3200, deviceScaleFactor=1, mobile=False); time.sleep(2)
  s = js("(function(){var s=Array.from(document.querySelectorAll('.ant-select')).find(e=>/Gender/.test(e.textContent)); var b=s.getBoundingClientRect(); return {x:Math.round(b.x+b.width/2),y:Math.round(b.y+b.height/2)};})()")
  click_at_xy(s['x'], s['y']); time.sleep(1.5)
  js("(function(){var h=document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .rc-virtual-list-holder'); if(h)h.scrollTop=h.scrollHeight;})()"); time.sleep(1)
  o = js("(function(){var el=Array.from(document.querySelectorAll('.ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item')).find(e=>e.textContent.trim()==='Men'); var dd=document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden)').getBoundingClientRect(); var b=el.getBoundingClientRect(); return {x:Math.round(b.x+b.width/2),y:Math.round(b.y+b.height/2),inpanel:(b.y>=dd.top&&b.bottom<=dd.bottom)};})()")
  if o['inpanel']: click_at_xy(o['x'], o['y'])
  cdp("Emulation.clearDeviceMetricsOverride")
  PY
  ```
  Confirm by the field showing the value after a reload; the
  `selection-item` selector is unreliable for this.

- **Arabic and other non-Latin text: `type_text`, not `fill_input`.**
  `fill_input` types character by character and hangs the daemon on
  Arabic (no keycode mapping). Focus the field, then `type_text("…")`
  (it is `Input.insertText`) inserts the whole string. The English and
  Arabic Product Title boxes share `placeholder="Product Title *"`; tag
  each with `setAttribute('data-fill', …)` to target it.

- **Content edits are on `/d?...&tab=content`.** The "Content" tab on the
  `/p` page does not switch to editable fields. Navigate within the tab
  (`js("location.href='…'")`) rather than calling `new_tab` repeatedly:
  piled-up tabs make the next wrapper invocation attach to whichever tab
  is active.

- **Image upload.** The file input is hidden; set it, fire `change`, then
  confirm the "Review Images" modal. The photo must be in
  `~/.vibe-seller/downloads/<slug>/` so the browser can read it:

  ```bash
  browser-use <<'PY'
  import time
  r = cdp("Runtime.evaluate", expression="document.querySelector('input[type=file][accept*=\"image\"]')")
  cdp("DOM.setFileInputFiles", files=["/abs/path/in/downloads/photo.jpg"], objectId=r['result']['objectId']); time.sleep(1)
  js("document.querySelector('input[type=file][accept*=\"image\"]').dispatchEvent(new Event('change',{bubbles:true}))"); time.sleep(4)
  b = js("(function(){var x=Array.from(document.querySelectorAll('button')).find(e=>e.textContent.trim()==='Upload All'); var r=x.getBoundingClientRect(); return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)};})()")
  click_at_xy(b['x'], b['y']); time.sleep(12)
  PY
  ```
  noon then validates the image asynchronously. A placeholder or
  low-quality image stays at `Validating…` and `Missing Image` never
  clears, so use the seller's actual photo; if there is none, report
  "Missing Image" as the remaining item for the seller.

- **Saving.** Every Save opens a **Submit** confirmation ("sent for
  review" / "are you sure"). Click Submit, then reload and re-read. A
  disabled **Save Changes** means there is nothing unsaved.
