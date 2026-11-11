# ruff: noqa: F821 — browser-harness globals (new_tab, js, cdp, ...)
"""Generate + download ONE marketplace's product-spreadsheet template.

The generated template is REGION-STAMPED by the store checkboxes (they
default to the account's home marketplace), so this drives the whole
generator flow with the TARGET store ticked (other ticked stores are
left alone). Run through the STORE WRAPPER:

  SC_HOST=sellercentral.amazon.ae PRODUCT_TYPE=kettle \
  STORE_LABEL=Amazon.ae DOWNLOADS_DIR=~/.vibe-seller/downloads/<slug> \
  browser-use < .claude/skills/amazon-listing/scripts/bh_download_template.py

Prints exactly one ``RESULT {json}`` line: ok / template / picked / stores
(label -> ticked, read back AFTER ticking) / viewport / reached
(full|centre) / landed (keyword read back out of the box) / candidates /
drawer / boxes / reason / budget

Every reason names the fact it rests on: a keyword that never reached the
search box reads exactly like a category Amazon does not offer, and that
confusion is what this file fixes (evidence pinned by
tests/unit/test_seller_central_helpers.py).

The store tick is VERIFIED, never assumed: kat-checkbox often ignores a JS
``.click()`` (shadow DOM), and a template generated WITHOUT the target
store ticked is region-stamped for the wrong marketplace. After ticking,
the states are read back, an unticked target is retried with a trusted
coordinate click, and if it STILL isn't ticked the result is ``ok: false``
with the observed states. Other leaves left ticked are fine (``fill``
routes the offer by marketplace), so other checkboxes are never fought.

This downloads a template and NOTHING ELSE — no marker written, no upload
check armed: fetching a template is step 1 of READING a category, and the
obligation has to start from the upload itself, which
``bh_upload_flatfile.py`` records. On any miss it screenshots and reports
the step that failed; explore from the screenshot, don't blind-retry.
"""

import glob
import json
import os
import re
import time


def _env(name):
    """A required parameter, or one RESULT line saying how to pass it.

    A plain `NAME=value` line before the command never reaches this
    script; an agent did exactly that and got a bare KeyError traceback.
    """
    value = os.environ.get(name, '').strip()
    if not value:
        print(
            'RESULT '
            + json.dumps({
                'ok': False,
                'reason': f'{name} is not set -- put it on the same line '
                'as browser-use (NAME=value browser-use < script) or '
                'export it',
            })
        )
        raise SystemExit(0)
    return value


HOST = _env('SC_HOST')
PTYPE = _env('PRODUCT_TYPE')
STORE = _env('STORE_LABEL')
DL = os.path.expanduser(_env('DOWNLOADS_DIR'))
out = {
    'ok': False,
    'template': None,
    'picked': None,
    'stores': None,
    # Everything below is measured, so one call classifies its own
    # failure instead of asserting a verdict about the catalogue.
    'viewport': None,
    'landed': None,
    'candidates': None,
    'reached': None,
    'boxes': None,
    'drawer': None,
    'budget': None,
}

# ── PURE: reachability + product-type matching (no browser globals) ──
# Every judgement the flow makes is pure, so
# tests/unit/test_seller_central_helpers.py execs this block and pins it
# against captured page data instead of trusting a live run to notice.
_WORD = re.compile(r'[^0-9a-z぀-ヿ㐀-䶿一-鿿가-힯]+')


def _norm(text):
    """Lowercase, non-word to one space, CJK runs preserved.

    A Chinese console still labels the row with its English product type
    (手机壳 / Mobile Phone Basic Cases), so a query in either language can
    meet a label in the other — why the page's list is the source.
    """
    return ' '.join(_WORD.split(str(text).lower())).strip()


def row_score(query, labels):
    """How well `query` matches ONE row of the page's result list.

    3 = the query is one of the row's labels; 2 = one contains the other;
    1 = every query token is carried by one label, prefix-matched ('phone
    case' → 'Mobile Phone Basic Cases'); 0 = not this row.
    """
    tokens = [t for t in _norm(query).split(' ') if t]
    nq = ' '.join(tokens)
    if not nq:
        return 0
    best = 0
    for label in labels or ():
        nl = _norm(label)
        if not nl:
            continue
        if nl == nq:
            return 3
        if nq in nl or nl in nq:
            best = max(best, 2)
            continue
        lt = [t for t in nl.split(' ') if t]
        if lt and all(
            any(t == o or o.startswith(t) or t.startswith(o) for o in lt)
            for t in tokens
        ):
            best = max(best, 1)
    return best


def pick_row(query, rows):
    """`(row, candidates)` — best match over the page's OWN rows.

    `rows` is `[{'labels': [...], 'btn': {...}}, …]` in DOM order. Equal
    scores keep the EARLIER row (Amazon ranks its own results; a query
    covering two types is the agent's call to re-narrow, never the
    helper's guess). No match → None plus `candidates`, which is
    actionable; "no product-type result" was not.
    """
    scored = []
    for i, row in enumerate(rows or ()):
        labels = [
            str(x).strip() for x in (row.get('labels') or ()) if str(x).strip()
        ]
        scored.append((row_score(query, labels), i, row, labels))
    candidates = []
    for _s, _i, _r, labels in scored:
        text = ' / '.join(labels)[:90]
        if text and text not in candidates:
            candidates.append(text)
    if not scored:
        return None, []
    best = max(scored, key=lambda s: (s[0], -s[1]))
    if best[0] <= 0:
        return None, candidates[:8]
    return best[2], candidates[:8]


def click_tier(box, viewport):
    """`'full'` / `'centre'` / None — can a trusted click reach this box?

    `full` is the whole rect inside the viewport; `centre` overflows but
    the point clicked is inside (refusing those would trade F1's false
    verdict for a new failure); None is an off-screen centre or a zero-size
    rect — a SILENT NO-OP, both shapes measured live (see the fixtures in
    TestClickMustBeAbleToLand).
    """
    if not box or not viewport or not viewport.get('w'):
        return None
    # Rect keys only: `t` is the element's TEXT (下载商品电子表格), and a
    # first version read it as "top" — the live run died comparing that
    # string to 0. Run these helpers for real; reading them is not enough.
    width = box.get('w', 0)
    height = box.get('h', 0)
    if not isinstance(width, int | float) or width <= 0:
        return None
    if not isinstance(height, int | float) or height <= 0:
        return None
    left = box.get('l', box.get('x', 0) - width / 2.0)
    top = box.get('tp', box.get('y', 0) - height / 2.0)
    cx, cy = box.get('x', left + width / 2.0), box.get('y', top + height / 2.0)
    if not (0 <= cx <= viewport['w'] and 0 <= cy <= viewport.get('h', 0)):
        return None
    if (
        left >= 0
        and top >= 0
        and left + width <= viewport['w']
        and top + height <= viewport['h']
    ):
        return 'full'
    return 'centre'


def reachable(box, viewport):
    """Strict form: the whole control is inside the viewport."""
    return click_tier(box, viewport) == 'full'


def over_budget(elapsed, wait, budget, reserve, margin):
    """True when starting a `wait`-second step could outlive the call.

    The wrapper alarms browser-use at BU_TIMEOUT and KILLS the process with
    NO RESULT printed — `[wrapper] browser-use timed out` is the silent
    failure this file exists to end (the F1 width sweep hit it: 120 s gone,
    search box never reached). `margin` covers the wrapper's clock starting
    before python. Every bound is a parameter so this is checkable without
    a clock and no default can read a constant defined lower down.
    """
    return elapsed + wait + reserve > budget - margin


# ── END PURE ─────────────────────────────────────────────────────


_WALK = (
    'function* w(r){for(const e of r.querySelectorAll("*")){yield e;'
    'if(e.shadowRoot) yield* w(e.shadowRoot);}}'
)


def _store_states():
    """Read back every Amazon.* leaf checkbox: label, state, coords.

    State comes from the component property OR the attribute —
    whichever the kat build maintains — so a stale attribute can't
    report a click that never took effect.
    """
    raw = js(
        _WALK + 'const res=[];'
        'for(const e of w(document)){'
        'if((e.tagName||"").toLowerCase()!=="kat-checkbox") continue;'
        'const lbl=(e.getAttribute("label")||e.textContent||"").trim();'
        'if(!/^Amazon\\./i.test(lbl)) continue;'
        'const on=(e.checked===true)||(e.hasAttribute("checked")&&'
        'e.getAttribute("checked")!=="false");'
        'const r=e.getBoundingClientRect();'
        'res.push({label:lbl,on:on,x:Math.round(r.x+r.width/2),'
        'y:Math.round(r.y+r.height/2),l:Math.round(r.x),'
        'tp:Math.round(r.y),rt:Math.round(r.right),'
        'bt:Math.round(r.bottom),w:Math.round(r.width),'
        'h:Math.round(r.height)});}'
        'return JSON.stringify(res);'
    )
    return json.loads(raw) if raw else []


def _target_unticked():
    """States list + the target entry when it still needs a tick.

    Only the TARGET's tick is load-bearing: an unticked target stamps
    the template for the wrong region. Extra ticked leaves are fine (a
    bundled template routes by marketplace at fill time), so they are
    reported but never fought.
    """
    states = _store_states()
    tgt = next((s for s in states if s['label'].lower() == STORE.lower()), None)
    needs_click = tgt if (tgt is not None and not tgt['on']) else None
    return states, tgt, needs_click


def _viewport():
    """The CURRENT emulated viewport — the only frame of reference a
    coordinate click has."""
    raw = js(
        'return JSON.stringify({w:window.innerWidth,h:window.innerHeight});'
    )
    return json.loads(raw) if raw else {'w': 0, 'h': 0}


# One measurement shape for every control the flow touches, so "what I
# found" and "where I click" can never be two different readings (the
# marketplace-switcher bug class: the finder and the click disagreed).
_BOXES_JS = (
    _WALK + 'const a=[];for(const e of w(document)){'
    'const tag=(e.tagName||"").toLowerCase();'
    'const t=(tag==="input"?String(e.placeholder||""):'
    'String((e.innerText||(e.getAttribute&&e.getAttribute("label"))'
    '||"").trim()));'
    'if(!t) continue;'
    'if(!(__TEST__)) continue;'
    'const r=e.getBoundingClientRect();'
    'a.push({t:t.slice(0,80),v:(tag==="input"?String(e.value||""):""),'
    'x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2),'
    'l:Math.round(r.x),tp:Math.round(r.y),rt:Math.round(r.right),'
    'bt:Math.round(r.bottom),w:Math.round(r.width),'
    'h:Math.round(r.height)});}'
    'return JSON.stringify(a);'
)


def _find(test):
    raw = js(_BOXES_JS.replace('__TEST__', test))
    return json.loads(raw) if raw else []


def _find_text(pattern):
    return _find(f'/{pattern}/i.test(t) && t.length < 60')


def _find_inputs():
    """Keyword inputs by placeholder — including the ones to AVOID.

    The header search box also carries 关键词 and a hidden duplicate
    reports width 0 (both matched live), so callers filter via click_tier.
    """
    return _find('/product keyword|商品关键|关键词|keyword/i.test(t)')


def _value_of(box):
    """The value now in THE SAME box that was clicked.

    Reading whichever input is merely visible would read a different
    element than the one typed into — defeating the point of the read-back.
    """
    for b in _find_inputs():
        if (b['l'], b['rt'], b['tp']) == (box['l'], box['rt'], box['tp']):
            return b.get('v')
    return None


def _result_rows():
    """The page's OWN product-type result rows, labels + Select coords."""
    raw = js(
        _WALK + 'const sel=[];'
        'for(const e of w(document)){'
        'const t=String(e.innerText||(e.getAttribute&&'
        'e.getAttribute("label"))||"").trim();'
        'if(!/^(select|选择)$/i.test(t)) continue;'
        'const r=e.getBoundingClientRect(); if(!(r.width>0)) continue;'
        'sel.push({el:e,btn:{x:Math.round(r.x+r.width/2),'
        'y:Math.round(r.y+r.height/2),l:Math.round(r.x),'
        'tp:Math.round(r.y),rt:Math.round(r.right),'
        'bt:Math.round(r.bottom),w:Math.round(r.width),'
        'h:Math.round(r.height)}});}'
        'const out=[];'
        'for(const s of sel){let anc=s.el,row=null;'
        'for(let i=0;i<8&&anc;i++){anc=anc.parentElement||'
        '(anc.getRootNode&&anc.getRootNode()instanceof ShadowRoot?'
        'anc.getRootNode().host:null);if(!anc) continue;'
        'const txt=String(anc.innerText||"").trim();'
        'if(txt&&txt.length<300){row=anc;break;}}'
        'const kids=row?Array.from(row.querySelectorAll("*")).map(x=>'
        'String(x.innerText||"").trim()):[];'
        'const set=[];for(const k of kids){if(k&&k.length<80&&'
        '!/^(select|选择)$/i.test(k)&&set.indexOf(k)<0&&'
        '(row.innerText||"").indexOf(k)>=0)set.push(k);}'
        'out.push({labels:set.slice(0,6),btn:s.btn});}'
        'return JSON.stringify(out);'
    )
    return json.loads(raw) if raw else []


def _click_box(box, vp=None, tiers=('full', 'centre')):
    """(clicked, viewport) — never click what the page cannot show.

    `full` first, then `centre` (a wide control whose centre is on-screen
    is a click the old code got right). Off-screen centre or zero size →
    no click, and the caller says so.
    """
    vp = vp or _viewport()
    if click_tier(box, vp) not in tiers:
        return False, vp
    click_at_xy(box['x'], box['y'])
    return True, vp


def _generate_state():
    """What the page itself says about the Generate control.

    生成电子表格 / Generate Spreadsheet stays DISABLED until a type is
    chosen, so this is the page's own proof that a Select click landed.
    """
    raw = js(
        _WALK + 'for(const e of w(document)){'
        'const t=String(e.innerText||"").trim();'
        'if(!/^(generate spreadsheet|生成电子表格)$/i.test(t)) continue;'
        'let node=e;'
        'while(node){const tag=(node.tagName||"").toLowerCase();'
        'if(tag==="kat-button"||tag==="button") break;'
        'node=node.parentElement;}'
        'if(!node) continue;'
        'const r=node.getBoundingClientRect();'
        'if(!(r.width>0&&r.height>0)) continue;'
        'const dis=node.disabled===true||(node.getAttribute&&('
        'node.hasAttribute("disabled")||'
        'node.getAttribute("aria-disabled")==="true"));'
        'return JSON.stringify({found:true,disabled:!!dis});}'
        'return JSON.stringify({found:false,disabled:true});'
    )
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {'found': False, 'disabled': True}


def _click_text(pattern):
    """Click the first matching element a click can actually reach.

    Returns the box, or None. The old version said True for any non-zero
    width, including a rect past the viewport edge where the click lands
    on nothing — how a missed click became a report about the product type.
    """
    vp = _viewport()
    for box in _find_text(pattern):
        if click_tier(box, vp) and _click_box(box, vp)[0]:
            return box
    return None


def _wait(seconds, what):
    """Sleep within budget, or stop the run with a RESULT that says why."""
    spent = time.time() - _T0
    if over_budget(spent, seconds, _BUDGET, RESERVE, MARGIN):
        out['budget'] = {
            'limit': _BUDGET,
            'used': round(spent, 1),
            'needed': seconds,
            'reserve': RESERVE,
            'margin': MARGIN,
            'at': what,
        }
        _fail(
            f'this store browser session is too slow for one call: {what} '
            f'would need {seconds}s and only '
            f'{_BUDGET - MARGIN - RESERVE - spent:.0f}s of the '
            f"wrapper's {_BUDGET:.0f}s limit is left ({_format()}). "
            'Raise BU_TIMEOUT for this store, or finish the wizard by '
            'hand from the screenshot — the upload stays script-driven.'
        )
    time.sleep(seconds)


def _format():
    return json.dumps({
        'viewport': out['viewport'],
        'reached': out['reached'],
        'landed': out['landed'],
        'picked': out['picked'],
        'boxes': out['boxes'],
    })


def _fail(reason):
    capture_screenshot()
    out['reason'] = reason
    print('RESULT ' + json.dumps(out))
    # Every caller is a terminal verdict on this run; raising keeps the
    # flow in a straight line instead of a pyramid of else-blocks.
    raise SystemExit(0)


# The seller-central UI language follows the SESSION (ZH sessions are
# common) — every text match below carries the Chinese variant too.
_DPS = '^(download product spreadsheet|下载商品电子表格)$'
_BLANK = '^(download blank template|下载空白模板)$'
# The wizard's own url renders the panel directly (how the production run
# reached it, with no entry-button click); the entry page is the fallback.
_ENTRY_URL = f'https://{HOST}/product-search/bulk/generate'
_WIZARD_URL = f'{_ENTRY_URL}/add-product'
# A tall viewport is applied before any click (Generate measured y≈1248
# against an 839-px window). No width sweep: measured live, widening
# carries the CLOSED drawer with it (l≈W+24 at 1920/3128/3840) while an
# open panel already fits; GENERATOR_WIDTH=<px> is the operator's knob.
MIN_H = 1400
# RESERVE: the download poll's share of what's left. MARGIN: the gap
# between the wrapper's clock and this script's (the alarm starts before
# python runs). _BUDGET reads the SAME env the wrapper alarms with.
RESERVE, MARGIN = 8.0, 8.0
_BUDGET = float(os.environ.get('BU_TIMEOUT', '120') or 120)
_T0 = time.time()
_gen_width = os.environ.get('GENERATOR_WIDTH', '').strip()


def _boxes(vp):
    """The keyword inputs, split by whether a click can reach them."""
    alls = _find_inputs()
    return (
        alls,
        [b for b in alls if click_tier(b, vp) == 'full'],
        [b for b in alls if click_tier(b, vp) == 'centre'],
    )


def _set_viewport(w, h):
    """Apply an emulated viewport and report what the page now measures."""
    cdp(
        'Emulation.setDeviceMetricsOverride',
        width=w,
        height=h,
        deviceScaleFactor=1,
        mobile=False,
    )
    _wait(1.5, 'applying the viewport')
    return _viewport()


def _measure():
    """(viewport, every box, full-tier, centre-tier) for the current page.

    One measurement, no growing loop (see the MIN_H note above for why).
    """
    vp = _viewport()
    alls, full, centre = _boxes(vp)
    return vp, alls, full, centre


def _click_entry(vp):
    """Click a reachable generator entry; True if one was clicked."""
    for pattern in (_DPS, _BLANK):
        for box in _find_text(pattern):
            if not click_tier(box, vp):
                continue
            _click_box(box, vp)
            _wait(6, 'opening the wizard panel')
            return True
    return False


def _drawer_state():
    """Why the search box cannot be reached, in the page's own terms.

    Measured live: the wizard's input is `visibility:hidden` at l=1944 and
    does not move over 20 s — a CLOSED drawer waiting on an affordance, not
    a slow page. "unreachable" alone would hand on the same dead end.
    """
    raw = js(
        _WALK + 'const a=[];for(const e of w(document)){'
        'if((e.tagName||"").toLowerCase()!=="input") continue;'
        'const ph=String(e.placeholder||"");'
        'if(!/keyword|关键词|相关商品/i.test(ph)) continue;'
        'const r=e.getBoundingClientRect(),cs=getComputedStyle(e);'
        'let anc=e,chain=[];'
        'for(let i=0;i<6&&anc;i++){const s=getComputedStyle(anc);'
        'chain.push((anc.tagName||"")+(s.display==="none"?"!none":"")'
        '+(s.visibility==="hidden"?"!hid":""));anc=anc.parentElement;}'
        'a.push({ph:ph.slice(0,26),l:Math.round(r.x),'
        'rt:Math.round(r.right),vis:cs.visibility,dis:cs.display,'
        'chain:chain.join(">")});}'
        'return JSON.stringify(a);'
    )
    try:
        return json.loads(raw) or []
    except (TypeError, ValueError):
        return []


def _open_wizard():
    """(viewport, boxes, full tier, centre tier) with the search open.

    The wizard's OWN url first: the production run reached the panel that
    way with no entry-button click, and on the probed version the entry is
    off-canvas — clicking it is clicking nothing, which is how a page that
    HAS the category reported `no product-type result`. The entry page is
    the fallback. One navigation per url: the wrapper SIGALMRs this call
    with no RESULT, so the loop's shape is a budget question.
    """
    for url in (_WIZARD_URL, _ENTRY_URL):
        new_tab(url)
        _wait(11, f'loading {url.rsplit("/", 1)[-1] or "generate"}')
        base = _viewport()
        _set_viewport(
            int(_gen_width) if _gen_width.isdigit() else base['w'],
            max(base['h'], MIN_H),
        )
        vp, alls, full, centre = _measure()
        if full or centre:
            return vp, alls, full, centre
        if _click_entry(_viewport()):
            vp, alls, full, centre = _measure()
            if full or centre:
                return vp, alls, full, centre
    return None, [], [], []


t0 = time.time()
vp, boxes, full, centre = _open_wizard()
inputs = full or centre
out['viewport'] = [vp['w'], vp['h']] if vp else None
out['reached'] = 'full' if full else ('centre' if centre else None)
out['boxes'] = [{k: b[k] for k in ('l', 'rt', 'w', 'h')} for b in boxes[:4]]
if not inputs:
    out['drawer'] = _drawer_state()
    entries = [b['t'][:30] for b in _find_text(_DPS) + _find_text(_BLANK)]
    _fail(
        'the product-type search box was never reachable — keyword inputs: '
        f'{json.dumps(out["boxes"])}; wizard state: '
        f'{json.dumps(out["drawer"][:2])}; entry controls in the DOM: '
        f'{json.dumps(entries[:6])}. NOTHING has been typed yet, so this '
        f'says nothing about whether {PTYPE!r} exists: the wizard is a '
        'CLOSED drawer (visibility:hidden, parked past the viewport edge) '
        'waiting on an affordance this script does not know. Open it from '
        'the screenshot, note WHICH control did it, and teach the helper '
        'that control — do not hand-drive the upload.'
    )

box = inputs[0]
clicked, vp = _click_box(box)
if not clicked:
    _fail(
        f'the keyword input moved between measuring and clicking '
        f'(viewport {vp["w"]}, box {box}) — re-run'
    )
# TRUSTED input (a JS value set does not stick on the kat predictive
# input), then PROOF it took — the read-back below is what separates "this
# category does not exist" from "my text never got there".
cdp('Input.insertText', text=PTYPE)
_wait(3, 'the keyword search query')
landed = _value_of(box)
out['landed'] = landed
if not landed or _norm(PTYPE) not in _norm(landed):
    _fail(
        f'the keyword {PTYPE!r} never reached the search box (read back '
        f'{landed!r} at viewport {vp["w"]}). The click did not land; that '
        'is a layout/state problem, NOT evidence the product type is '
        'missing. Re-run, or widen with GENERATOR_WIDTH.'
    )
for t in ('keyDown', 'keyUp'):
    cdp(
        'Input.dispatchKeyEvent',
        type=t,
        key='Enter',
        code='Enter',
        windowsVirtualKeyCode=13,
    )
_wait(6, 'the product-type result list')
# The page's OWN result list decides what was found (a Chinese console
# still carries the English product-type name); the old code clicked the
# topmost Select whatever row it belonged to.
rows = _result_rows()
row, candidates = pick_row(PTYPE, rows)
out['candidates'] = candidates
if not rows:
    _fail(
        f'the page returned no product-type rows for {PTYPE!r}, although '
        f'the text was verifiably in the box (read back {landed!r}). '
        'Amazon finds nothing for this wording on this marketplace: '
        're-run with the name the page itself suggests, or explore from '
        'the screenshot.'
    )
if row is None:
    _fail(
        f'{PTYPE!r} matches none of the {len(rows)} product types the '
        f'page offered: {candidates}. The helper will not pick a category '
        'you did not name — re-run with one of those labels as '
        'PRODUCT_TYPE, or tell the user which ones exist.'
    )
out['picked'] = ' / '.join(row.get('labels') or ())[:80]
sel_ok, vp = _click_box(row['btn'])
if not sel_ok:
    _fail(
        f'the row for {out["picked"]!r} is not clickable at viewport '
        f'{vp["w"]}x{vp["h"]} (its Select is off-screen: {row["btn"]}) — '
        'widen with GENERATOR_WIDTH and re-run; NOTHING was selected.'
    )
# Did the page notice? Generate is disabled until a type is chosen, so its
# enabled state — not "a click was sent" — proves the row was selected.
_deadline = min(time.time() + 20, _T0 + _BUDGET - MARGIN - RESERVE)
chosen = False
while time.time() < _deadline:
    chosen = not _generate_state()['disabled']
    if chosen:
        break
    time.sleep(1.5)
out['selected'] = chosen
if not chosen:
    _fail(
        f'the Select click for {out["picked"]!r} did not register: '
        f'{json.dumps(_generate_state())} — the page still reports '
        'Generate Spreadsheet as DISABLED, which is what it says while no '
        'product type is chosen. The row matched and was clicked at '
        f'{row["btn"]}; nothing was generated or downloaded. Re-run, or '
        'select it from the screenshot.'
    )
# The viewport that made the wizard reachable stays in force: re-narrowing
# it here is what pushed the modal's own controls back off-screen.
# Store checkboxes: make sure the TARGET leaf is ticked
# (never touch region parents like Europe — unticking a
# parent clears all its children; extra ticked leaves are
# fine, `fill` routes by marketplace). Fast path is a JS
# click; kat-checkbox often IGNORES it (shadow DOM), so the
# target's state is read back and retried with a trusted
# coordinate click. Never proceed on an unverified target
# tick — the template is region-stamped by these boxes.
js(
    _WALK + 'for(const e of w(document)){'
    'if((e.tagName||"").toLowerCase()!=="kat-checkbox")'
    'continue;'
    'const lbl=(e.getAttribute("label")||e.textContent||"")'
    '.trim();'
    f'if(lbl.toLowerCase()!=="{STORE.lower()}") continue;'
    'const on=(e.checked===true)||(e.hasAttribute("checked")&&'
    'e.getAttribute("checked")!=="false");'
    'if(!on) e.click();}'
    'return "stores set";'
)
_wait(2, 'the store checkboxes')
states, tgt, needs_click = _target_unticked()
tick_unreachable = False
for _attempt in range(2):
    if not needs_click:
        break
    # A tick outside the viewport is the same silent no-op — say so.
    tick_unreachable, vp = (not _click_box(needs_click, vp)[0]), vp
    if tick_unreachable:
        break
    time.sleep(1.5)
    states, tgt, needs_click = _target_unticked()
out['stores'] = {s['label']: bool(s['on']) for s in states}
if tick_unreachable:
    _fail(
        f'the store checkbox for {STORE!r} is outside the viewport at '
        f'{vp["w"]}px ({needs_click}), so no click on it can land — the '
        'template would be region-stamped for whatever IS ticked. Re-run '
        'with a wider GENERATOR_WIDTH.'
    )
elif not states:
    _fail(
        'no Amazon.* store checkboxes found in the generator '
        'modal — the page layout changed; explore from the '
        'screenshot'
    )
elif tgt is None:
    _fail(
        f'target store {STORE!r} is not among the Amazon.* '
        'checkboxes (see "stores") — wrong STORE_LABEL '
        'spelling, or this account has no such storefront. '
        'Do NOT generate: the template would be '
        'region-stamped for a different marketplace.'
    )
elif needs_click:
    _fail(
        f'store tick UNVERIFIED: {STORE!r} is still NOT '
        'ticked after JS + trusted-click retries (see '
        '"stores" for the observed states). Do NOT generate '
        'from this state — the template would be '
        'region-stamped for the wrong marketplace. Tick it '
        'by hand (trusted click on the checkbox coords), '
        'verify, then re-run.'
    )
elif not _click_text('^(generate spreadsheet|生成电子表格)$'):
    _fail(
        'Generate Spreadsheet is not clickable at this viewport — the '
        'button exists in the DOM (the EN+ZH patterns matched) but no '
        f'measurement was inside {vp["w"]}px; widen with GENERATOR_WIDTH.'
    )
else:
    template = None
    # Bounded by the wrapper's clock, not blind tries: running out is
    # reported as what it is instead of the process vanishing.
    _stop = _T0 + _BUDGET - MARGIN
    while time.time() < _stop:
        time.sleep(1)
        cands = [
            p
            for p in glob.glob(os.path.join(DL, '*.xlsm'))
            if os.path.getmtime(p) > t0
        ]
        if cands:
            template = max(cands, key=os.path.getmtime)
            break
    if template:
        # Amazon names the file after the product type alone,
        # so an AE download overwrote the SA template beside it
        # (observed live). Name it for its marketplace.
        mkt = HOST.split('amazon.', 1)[-1].strip('/')
        stem, ext = os.path.splitext(template)
        tagged = f'{stem}__{mkt.replace(".", "-").upper()}{ext}'
        os.replace(template, tagged)
        template = tagged
    out['template'] = template
    out['ok'] = bool(template)
    if not template:
        out['budget'] = {
            'limit': _BUDGET,
            'used': round(time.time() - _T0, 1),
            'needed': None,
            'reserve': RESERVE,
            'at': 'waiting for the generated .xlsm',
        }
        out['reason'] = (
            'Generate clicked but no new .xlsm landed in '
            + DL
            + f' before the wrapper budget ({_BUDGET:.0f}s) ran out — '
            'the feed may still be building; re-run, or check that '
            'directory before concluding anything.'
        )
        capture_screenshot()
    print('RESULT ' + json.dumps(out))
