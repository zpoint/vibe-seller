# ruff: noqa: F821 — browser-harness globals (new_tab, js, cdp, ...)
"""Generate + download ONE marketplace's product-spreadsheet template.

The generated template is REGION-STAMPED by the store checkboxes (they
default to the account's home marketplace), so this drives the whole
generator flow with the TARGET store ticked (other ticked stores are
left alone). Run through the STORE WRAPPER:

  SC_HOST=sellercentral.amazon.ae PRODUCT_TYPE=kettle \
  STORE_LABEL=Amazon.ae DOWNLOADS_DIR=~/.vibe-seller/downloads/<slug> \
  browser-use < .claude/skills/amazon-listing/scripts/bh_download_template.py

Prints exactly one ``RESULT {json}`` line:
  ok / template (downloaded path) / picked (product type) /
  stores (label -> ticked, read back AFTER ticking) / viewport /
  landed (the keyword read back out of the search box) / candidates
  (the product types the page actually offered) / tried (what was
  clickable at each viewport tried) / reason

Every reason names the fact it rests on, because the old one did not: a
keyword that never reached the search box reads exactly like a category
Amazon does not offer, and a run was reported as ``no product-type
result for 'phone case'`` for a category the page resolves on screen.
``landed`` / ``viewport`` / ``tried`` tell those apart — a layout problem
is fixed by ``GENERATOR_WIDTH=<px>`` or a wider window, not by re-wording
the query. ``PRODUCT_TYPE`` is matched against the page's own result rows
(a Chinese console still labels them with the English product type too),
and when nothing matches it lists the candidates instead of picking one.

The store tick is VERIFIED, never assumed: kat-checkbox often ignores a
JS ``.click()`` (shadow DOM), and a template generated WITHOUT the
target store ticked is region-stamped for the wrong marketplace — the
whole upload then succeeds on the wrong storefront. After ticking, the
checkbox states are read back; an unticked target is retried with a
trusted coordinate click; if the target STILL isn't ticked, the result
is ``ok: false`` with the observed states. Other leaves left ticked are
fine — a bundled multi-marketplace template works (``fill`` routes the
offer by marketplace), so we never fight the other checkboxes.

This downloads a template and NOTHING ELSE. It arms no upload check and
writes no marker: fetching a template is step 1 of reading a category
(inspect it before filling), and plenty of tasks stop there — an upload
obligation has to start from the upload itself, which
``bh_upload_flatfile.py`` records when it runs.

On any miss it screenshots and reports the step that failed — explore
from the screenshot rather than blind-retrying.
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
    # What the flow actually reached, so ONE call classifies its own
    # failure: which viewport was in force, whether the keyword really
    # landed, and which product-type rows the page offered.
    'viewport': None,
    'landed': None,
    'candidates': None,
    'tried': None,
}

# ── PURE: reachability + product-type matching (no browser globals) ──
# These four functions make every judgement the flow relies on — can a
# trusted click land on this box at all, and which of the page's own
# result rows IS the product type that was asked for. They are pure
# because tests/unit/test_seller_central_helpers.py execs this block out
# of the source and pins it against captured page data: the alternative
# was a helper that reports a MISSING CATEGORY when what actually
# happened is that its text never reached the box (observed live — the
# keyword input's rect sat beyond the emulated viewport, the click hit
# nothing, `InsertText` went nowhere, and the reason blamed the query).
_WORD = re.compile(r'[^0-9a-z぀-ヿ㐀-䶿一-鿿가-힯]+')


def _norm(text):
    """Lowercase, everything non-word to a single space, CJK preserved.

    A Chinese console still labels a result row with its English product
    type (observed live: 手机壳 / 分类节点 (1/9) / Mobile Phone Basic
    Cases), so a plain-language query in EITHER language can meet a label
    in the other — which is what makes the page's own list the right
    source instead of a remembered internal name.
    """
    return ' '.join(_WORD.split(str(text).lower())).strip()


def row_score(query, labels):
    """How well `query` matches ONE row of the page's result list.

    3 = the query is one of the row's labels; 2 = one contains the other;
    1 = every query token is carried by one label, prefix-matched so
    'phone case' reaches 'Mobile Phone Basic Cases' (case → cases);
    0 = this row is not the one asked for.
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

    `rows` is `[{'labels': [...], 'btn': {'x':…,'y':…}}, …]` in DOM order.
    Equal scores keep the EARLIER row: Amazon ranks its own results, and a
    query that legitimately covers two product types is the agent's call
    to re-narrow — the helper never guesses which one it meant. With no
    match at all, `row` is None and `candidates` names what the page did
    offer, which is actionable; "no product-type result" was not.
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


def reachable(box, viewport):
    """True when a trusted click at the box's centre can actually land.

    Both shapes below were observed live on the generator modal: a
    ZERO-SIZE rect (a hidden duplicate of the same keyword placeholder —
    the header's own search box, `x=0,y=0,w=0`) and a rect whose right
    edge lies past the emulated viewport (the modal's input at x=2220 with
    the viewport at 1920). Clicking either is a silent no-op, and every
    conclusion drawn afterwards — "no result for this product type" — is
    then about the helper's own miss, not about the page.
    """
    if not box or not viewport or not viewport.get('w'):
        return False
    # Coordinates are read from the RECT keys only. `t` is the element's
    # TEXT (a label like 下载商品电子表格) — a first version of this
    # function read `t` as "top", and the live run died comparing that
    # string to 0. Never let one payload's field do two jobs.
    width = box.get('w', 0)
    height = box.get('h', 0)
    if not isinstance(width, int | float) or width <= 0:
        return False
    if not isinstance(height, int | float) or height <= 0:
        return False
    left = box.get('l', box.get('x', 0) - width / 2.0)
    top = box.get('tp', box.get('y', 0) - height / 2.0)
    return (
        left >= 0
        and top >= 0
        and left + width <= viewport['w']
        and top + height <= viewport['h']
    )


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
    'a.push({t:t.slice(0,80),x:Math.round(r.x+r.width/2),'
    'y:Math.round(r.y+r.height/2),l:Math.round(r.x),'
    'tp:Math.round(r.y),rt:Math.round(r.right),bt:Math.round(r.bottom),'
    'w:Math.round(r.width),h:Math.round(r.height)});}'
    'return JSON.stringify(a);'
)


def _find(test):
    raw = js(_BOXES_JS.replace('__TEST__', test))
    return json.loads(raw) if raw else []


def _find_text(pattern):
    return _find(f'/{pattern}/i.test(t) && t.length < 60')


def _find_inputs():
    """Keyword inputs by placeholder — including the ones to AVOID.

    The header's own search box carries 关键词 too, and a hidden
    duplicate reports width 0; both were matched live and both swallow a
    click silently, so the caller filters on `reachable`.
    """
    return _find('/product keyword|商品关键|关键词|keyword/i.test(t)')


def _keyword_value():
    """What is actually IN the visible keyword input right now."""
    return js(
        _WALK + 'for(const e of w(document)){'
        'if((e.tagName||"").toLowerCase()!=="input") continue;'
        'const ph=String(e.placeholder||"");'
        'if(!/product keyword|商品关键|关键词|keyword/i'
        '.test(ph)) continue;'
        'const r=e.getBoundingClientRect();'
        'if(r.width>0 && r.x>=0 && r.right<=window.innerWidth) '
        'return e.value;}'
        'return null;'
    )


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


def _click_box(box, vp=None):
    """(clicked, viewport) — and NEVER click what is not inside it."""
    vp = vp or _viewport()
    if not reachable(box, vp):
        return False, vp
    click_at_xy(box['x'], box['y'])
    return True, vp


def _click_text(pattern):
    """Click the first element matching *pattern* THAT CAN BE CLICKED.

    Returns the box it clicked, or None. The old version returned True for
    any element with a non-zero width — including one whose rect lies
    past the right edge of the emulated viewport, where a coordinate click
    lands on nothing. That is how a failed click turned into a report
    about the product type.
    """
    vp = _viewport()
    for box in _find_text(pattern):
        if reachable(box, vp) and _click_box(box, vp)[0]:
            return box
    return None


def _fail(reason):
    capture_screenshot()
    out['reason'] = reason
    print('RESULT ' + json.dumps(out))
    # Every caller is a terminal verdict on this run; raising keeps the
    # flow in a straight line instead of a pyramid of else-blocks.
    raise SystemExit(0)


t0 = time.time()
new_tab(f'https://{HOST}/product-search/bulk/generate')
time.sleep(13)
# The seller-central UI language follows the SESSION (ZH sessions are
# common) — every text match below carries the Chinese variant too.
_DPS = '^(download product spreadsheet|下载商品电子表格)$'
_BLANK = '^(download blank template|下载空白模板)$'
# The wizard is laid out WIDER than a default window. Observed live on
# this very flow: with the viewport at 1920 the entry control measured
# x 2299-2444 and the keyword input x 1959-2526 — both past the right
# edge, so the trusted click the old code sent landed on nothing, the
# typed text reached no input at all, and the run still reported
# `no product-type result for 'phone case'`: a verdict about the
# CATEGORY, drawn from the helper's own miss. Amazon ships several
# front-end versions of this page and the panel moves differently in each
# (widening brings an OPENED panel inboard but pushes a closed one
# further out), so no single width is right: every candidate is tried and
# each step runs only on a control measured INSIDE the viewport it is
# about to be clicked in. GENERATOR_WIDTH pins it when an operator knows.
_WIDTHS = [0, 2560, 3200, 3840]
_gen_width = os.environ.get('GENERATOR_WIDTH', '').strip()
if _gen_width.isdigit():
    _WIDTHS.insert(0, int(_gen_width))
tried = []


def _set_viewport(w):
    """Apply the emulated viewport (always tall — the modal's controls sit
    low as well as wide) and report what the page now has."""
    cdp(
        'Emulation.setDeviceMetricsOverride',
        width=w or _viewport()['w'],
        height=1600,
        deviceScaleFactor=1,
        mobile=False,
    )
    time.sleep(1.5)
    vp = _viewport()
    cands = _find_inputs()
    return vp, [c for c in cands if reachable(c, vp)], cands


noted = set()


def _note(viewport, entry, alls, ok):
    """Record ONE line per (viewport, entry) attempt — the nested spans of
    a single control must not repeat it."""
    key = (viewport, entry)
    if key in noted:
        return
    noted.add(key)
    tried.append({
        'w': viewport,
        'entry': entry,
        'inputs_seen': len(alls),
        'inputs_clickable': len(ok),
        'boxes': [{'l': a['l'], 'rt': a['rt'], 'w': a['w']} for a in alls[:3]],
    })


def _open_wizard():
    """(viewport, clickable keyword inputs) — verified, never assumed.

    Returns (None, []) when no viewport tried can show the wizard's search
    box; the caller then reports the MEASUREMENTS, because the one thing
    this must not do is call that a missing product type.
    """
    for w in _WIDTHS:
        vp, ok, alls = _set_viewport(w)
        _note(vp['w'], None, alls, ok)
        if ok:
            return vp, ok  # this layout renders the wizard already open
        for pattern in (_DPS, _BLANK):
            for box in _find_text(pattern):
                if not reachable(box, vp):
                    continue
                _click_box(box, vp)
                time.sleep(6)
                vp2, ok2, alls2 = _set_viewport(0)
                _note(vp2['w'], box['t'][:22], alls2, ok2)
                if ok2:
                    return vp2, ok2
    return None, []


vp, inputs = _open_wizard()
out['viewport'] = vp['w'] if vp else None
out['tried'] = tried
if not inputs:
    _fail(
        'the product-type search box was never clickable at any viewport '
        f'tried — measurements: {json.dumps(out["tried"])}. NOTHING has '
        f'been typed yet, so this says nothing about whether {PTYPE!r} '
        'exists: the generator wizard is laid out beyond the window this '
        'session renders (every keyword input the page has reported a '
        '0-size or off-canvas rect). Re-run with GENERATOR_WIDTH=<px>, '
        'widen the store browser window, or open the wizard from the '
        'on-screen Add Products flow and hand-drive THAT farthest step — '
        'not the upload.'
    )

box = inputs[0]
clicked, vp = _click_box(box)
if not clicked:
    _fail(
        f'the keyword input moved between measuring and clicking '
        f'(viewport {vp["w"]}, box {box}) — re-run'
    )
# Product-type search wants TRUSTED input (a JS value set does not stick
# on the kat predictive input), and then PROOF it took: reading the value
# back is what separates "this category does not exist" from "my text
# never got there" — the confusion that produced the F1 report.
cdp('Input.insertText', text=PTYPE)
time.sleep(3)
landed = _keyword_value()
out['landed'] = landed
if not landed or _norm(PTYPE) not in _norm(landed):
    _fail(
        f'the keyword {PTYPE!r} never reached the search box (read back '
        f'{landed!r} at viewport {vp["w"]}). The click did not land; '
        'that is a layout/state problem, NOT evidence the product type '
        'is missing. Re-run, or widen with GENERATOR_WIDTH.'
    )
for t in ('keyDown', 'keyUp'):
    cdp(
        'Input.dispatchKeyEvent',
        type=t,
        key='Enter',
        code='Enter',
        windowsVirtualKeyCode=13,
    )
time.sleep(6)
# The page's OWN result list decides what was found: rows are matched on
# their labels (a Chinese console still carries the English product-type
# name) and the pick is reported, never guessed. The old code clicked the
# topmost Select whatever it belonged to.
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
        f'{vp["w"]} (its Select is off-screen: {row["btn"]}) — widen '
        'with GENERATOR_WIDTH and re-run; NOTHING was selected.'
    )
time.sleep(4)
# The viewport the wizard became reachable in stays in force for the rest
# of the run (the chooser set it tall as well as wide) — re-narrowing it
# here is what pushed the modal's own controls back off-screen.
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
time.sleep(2)
states, tgt, needs_click = _target_unticked()
tick_unreachable = False
for _attempt in range(2):
    if not needs_click:
        break
    # A tick outside the viewport is the same silent no-op as the search
    # box was — say so, instead of reporting a tick that never took.
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
    for _ in range(25):
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
        out['reason'] = 'Generate clicked but no new .xlsm landed in ' + DL
        capture_screenshot()
    print('RESULT ' + json.dumps(out))
