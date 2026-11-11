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
    # landed, which product-type rows the page offered, how close the
    # click target was to the viewport, and what was measured at each
    # viewport tried.
    'viewport': None,
    'landed': None,
    'candidates': None,
    'reached': None,
    'tried': None,
    # The wizard's own state when nothing could be reached.
    'drawer': None,
    # Set when the wrapper's per-call timeout is what stopped the run —
    # without this the only sign is the wrapper's own "timed out" line.
    'budget': None,
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


def click_tier(box, viewport):
    """`'full'` / `'centre'` / None — can a trusted click reach this box?

    * `full`   the whole rect sits inside the emulated viewport.
    * `centre` the rect overflows but its CENTRE — the point the click is
      actually sent to — is inside, so the click can still land. This
      tier is what keeps a control that the old code clicked
      successfully from being refused now: a wide input against a narrow
      window is normal here.
    * None     the centre itself is off-screen, or the rect has no size.
      Clicking it is a SILENT NO-OP — exactly what happened on the live
      page (a keyword input at x 1959..2526 with the viewport at 1920,
      and a hidden duplicate of the same placeholder measuring 0x0 at the
      origin), and the run then reported the product type as missing.
    """
    if not box or not viewport or not viewport.get('w'):
        return None
    # Coordinates come from the RECT keys only. `t` is the element's TEXT
    # (a label like 下载商品电子表格) — a first version read `t` as "top"
    # and died comparing that string to 0; the live run caught it, which
    # is why these helpers get run for real and not only read.
    width = box.get('w', 0)
    height = box.get('h', 0)
    if not isinstance(width, int | float) or width <= 0:
        return None
    if not isinstance(height, int | float) or height <= 0:
        return None
    left = box.get('l', box.get('x', 0) - width / 2.0)
    top = box.get('tp', box.get('y', 0) - height / 2.0)
    cx, cy = box.get('x', left + width / 2.0), box.get(
        'y', top + height / 2.0
    )
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

    The store wrapper alarms browser-use at BU_TIMEOUT (120 s observed)
    and KILLS the process — no RESULT line is printed at all, so the agent
    sees only `[wrapper] browser-use timed out` and every verified
    failure this script can report is bypassed. Measured live: the old
    fixed waits (13 + 7 + 6 + 4 + 1 + 2) spent 33 s before a coordinate
    was even clicked, and the F1 viewport sweep passed 120 s without ever
    reaching the search box.

    `margin` is the gap between the script's clock and the wrapper's: the
    alarm starts BEFORE python runs, and the tail (screenshot, RESULT
    print, exit) is at the end. It is not negotiable down — a budget that
    assumes an instant start is the same bug in a new costume. Every
    bound is a parameter, so this is checkable without a clock, and no
    default can silently read a constant defined further down the file.
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


def _click_box(box, vp=None, tiers=('full', 'centre')):
    """(clicked, viewport) — never click what the page cannot show.

    `full` first: the whole control inside the viewport. `centre` as the
    fallback tier, because a wide control whose CENTRE is on-screen is a
    click the old code got right and must not be refused now. Off-screen
    centre or a zero-size rect → no click, and the caller says so.
    """
    vp = vp or _viewport()
    if click_tier(box, vp) not in tiers:
        return False, vp
    click_at_xy(box['x'], box['y'])
    return True, vp


def _generate_state():
    """What the page itself says about the Generate control.

    Amazon keeps 生成电子表格 / Generate Spreadsheet DISABLED until a
    product type is chosen, so this is the page's own confirmation that a
    Select click registered — structural, no wording matched beyond the
    EN+ZH label pair, and never an assumption.
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
        'viewport': out['viewport'], 'reached': out['reached'],
        'landed': out['landed'], 'picked': out['picked'],
        'tried': tried,
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
# The wizard's own url renders the panel directly — the production run
# reached it this way, with no entry-button click at all. The entry page
# stays as the fallback for the front-end versions that gate the panel
# behind a button.
_ENTRY_URL = f'https://{HOST}/product-search/bulk/generate'
_WIZARD_URL = f'{_ENTRY_URL}/add-product'
# The wizard is laid out WIDER and TALLER than a default window. Observed
# live on this very flow: with the viewport at 1920x839 the entry control
# measured x 2299-2444 and the keyword input x 1959-2526 — both past the
# right edge, so the trusted click the old code sent landed on nothing,
# the typed text reached no input at all, and the run still reported
# `no product-type result for 'phone case'`: a verdict about the
# CATEGORY, drawn from the helper's own miss. Amazon ships several
# front-end versions and the panel sits differently in each (widening
# brings an OPENED panel inboard but pushes a closed one further out), so
# the size is MEASURED off the page's own controls and converged on
# (`_fit_viewport`), capped here because a runaway drawer would otherwise
# be chased forever. GENERATOR_WIDTH pins the first try.
MAX_W, MAX_H = 3840, 2400
# The download poll's own share of the remaining time (RESERVE) and the
# gap between the wrapper's clock and this script's (MARGIN: the alarm
# starts before python runs, the RESULT print and exit are after).
RESERVE, MARGIN = 8.0, 8.0
# The wrapper's own SIGALRM ceiling for ONE browser-use call, read from the
# same env it uses (so raising BU_TIMEOUT raises this budget too, and the
# two can never disagree). `_T0` starts when this script's work starts.
_BUDGET = float(os.environ.get('BU_TIMEOUT', '120') or 120)
_T0 = time.time()
tried = []
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


def _controls_extent():
    """How far the page's own controls actually extend.

    Measured, not guessed. The generator lays its keyword input out to
    x≈2526 and its Generate button at y≈1280 on a 1920x839 window, and
    where they land moves with the front-end version AND with whether the
    drawer is open — so the page itself says what it needs, and the only
    number that was ever right here was somebody's guess.
    """
    raw = js(
        _WALK + 'let rt=0,bt=0,n=0;'
        'for(const e of w(document)){'
        'const tag=(e.tagName||"").toLowerCase();'
        'if(tag!=="input"&&tag!=="button"&&!tag.startsWith("kat-"))'
        ' continue;'
        'const r=e.getBoundingClientRect();'
        'if(!(r.width>0&&r.height>0)) continue;'
        'n++; if(r.right>rt) rt=r.right; if(r.bottom>bt) bt=r.bottom;}'
        'return JSON.stringify({right:Math.ceil(rt),'
        'bottom:Math.ceil(bt),n:n});'
    )
    try:
        got = json.loads(raw)
    except (TypeError, ValueError):
        return {'right': 0, 'bottom': 0, 'n': 0}
    return got if isinstance(got, dict) else {'right': 0, 'bottom': 0, 'n': 0}


noted = set()


def _note(viewport, entry, alls, full, centre):
    """Record ONE line per (viewport, entry) so the verdict is auditable."""
    key = (viewport.get('w'), viewport.get('h'), entry)
    if key in noted:
        return
    noted.add(key)
    tried.append({
        'viewport': [viewport.get('w'), viewport.get('h')],
        'entry': entry,
        'inputs_seen': len(alls),
        'inputs_full': len(full),
        'inputs_centre': len(centre),
        'boxes': [
            {'l': a['l'], 'rt': a['rt'], 'w': a['w']} for a in alls[:3]
        ],
    })


def _fit_viewport(rounds=1):
    """Grow the emulated viewport to what the wizard's controls measured.

    One grow step by default: the wrapper kills this call at BU_TIMEOUT
    seconds (with no RESULT at all), so every wait here is spent on
    purpose. A fluid layout moves its controls with the viewport, so the
    size is measured off the page and converged on, never assumed — and
    capped (MAX_W/MAX_H), because a CLOSED drawer measures at ~1.16x the
    width and would otherwise be chased forever.
    """
    best = (None, [], [])
    for _ in range(rounds):
        vp = _viewport()
        alls, full, centre = _boxes(vp)
        _note(vp, None, alls, full, centre)
        if full:
            return vp, full, centre
        if centre and best[0] is None:
            best = (vp, full, centre)
        ext = _controls_extent()
        want_w = max(vp['w'], min(MAX_W, ext['right'] + 32))
        want_h = max(vp['h'], min(MAX_H, ext['bottom'] + 32))
        if not ext['n'] or (want_w, want_h) == (vp['w'], vp['h']):
            break
        _set_viewport(want_w, want_h)
    vp = _viewport()
    alls, full, centre = _boxes(vp)
    _note(vp, None, alls, full, centre)
    if full:
        return vp, full, centre
    return best if best[0] else (vp, full, centre)


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

    Measured live: on the current front-end the wizard's keyword input is
    `visibility:hidden` parked at l=1944 with the viewport at 1920, and it
    does NOT move over 20 s — it is a closed drawer waiting on an
    affordance, not a slow page. A reason line that only said "unreachable"
    would leave the next reader with the same dead end I had, so the
    failure carries the drawer's state and the on-screen controls that
    could open it.
    """
    raw = js(
        _WALK + 'const out={inputs:[],onscreen:[]};'
        'for(const e of w(document)){'
        'const tag=(e.tagName||"").toLowerCase();'
        'const r=e.getBoundingClientRect();'
        'const t=String(e.innerText||(e.getAttribute&&'
        'e.getAttribute("label"))||e.placeholder||"").trim();'
        'if(tag==="input"&&/keyword|关键词|相关商品/i.test(t||"")){'
        'const cs=getComputedStyle(e);'
        'let anc=e,chain=[];'
        'for(let i=0;i<6&&anc;i++){const a=getComputedStyle(anc);'
        'chain.push((anc.tagName||"")+(a.display==="none"?"!none":"")'
        '+(a.visibility==="hidden"?"!hid":""));'
        'anc=anc.parentElement;}'
        'out.inputs.push({ph:t.slice(0,26),l:Math.round(r.x),'
        'rt:Math.round(r.right),w:Math.round(r.width),'
        'vis:cs.visibility,dis:cs.display,chain:chain.join(">")});}'
        'else if(t&&t.length<40&&r.width>0&&r.x>=0&&'
        'r.right<=window.innerWidth&&r.bottom<=window.innerHeight){'
        'out.onscreen.push({t:t.slice(0,36),'
        'x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});}}'
        'return JSON.stringify(out);'
    )
    try:
        got = json.loads(raw)
    except (TypeError, ValueError):
        return {'inputs': [], 'onscreen': []}
    return got if isinstance(got, dict) else {'inputs': [], 'onscreen': []}


def _open_wizard():
    """(viewport, full boxes, centre boxes) with the wizard's search open.

    The wizard's OWN url is tried first: the production run reached the
    panel that way with no entry-button click at all, and on the version
    probed here the entry button is laid out off-canvas — so the old code
    clicking it was clicking nothing, which is how a page that HAS the
    category reported `no product-type result`. The entry page and its
    buttons stay as the fallback for versions that gate the panel.

    One navigation per url and one measured grow, because the wrapper
    SIGALMRs this call at BU_TIMEOUT seconds and prints NO result when it
    does — the shape of this loop is a budget question, not a style one.
    """
    for url in (_WIZARD_URL, _ENTRY_URL):
        new_tab(url)
        _wait(11, f'loading {url.rsplit("/", 1)[-1] or "generate"}')
        if _gen_width.isdigit():
            _set_viewport(int(_gen_width), max(_viewport()['h'], 1400))
        vp, full, centre = _fit_viewport()
        if full or centre:
            return vp, full, centre
        if _click_entry(_viewport()):
            vp, full, centre = _fit_viewport()
            if full or centre:
                return vp, full, centre
    return None, [], []


t0 = time.time()
vp, full, centre = _open_wizard()
inputs = full or centre
out['viewport'] = [vp['w'], vp['h']] if vp else None
out['reached'] = 'full' if full else ('centre' if centre else None)
out['tried'] = tried
if not inputs:
    drawer = _drawer_state()
    out['drawer'] = drawer
    _fail(
        'the product-type search box was never reachable — measurements: '
        f'{json.dumps(out["tried"])} at {json.dumps(out["viewport"])}. '
        f'The wizard itself reports {json.dumps(drawer.get("inputs")[:2])} '
        'and these controls ARE on screen: '
        f'{json.dumps([c["t"] for c in (drawer.get("onscreen") or [])[:8]])}'
        '. NOTHING has been typed yet, so this says nothing about whether '
        f'{PTYPE!r} exists: the wizard is a CLOSED drawer waiting on an '
        'affordance this script does not know (visibility:hidden, parked '
        f'past the viewport at every size tried, max {MAX_W}x{MAX_H}). '
        'Open it from the screenshot and note WHICH control did it, then '
        'teach the helper that control — do not hand-drive the upload.'
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
_wait(3, 'the keyword search query')
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
_wait(6, 'the product-type result list')
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
        f'{vp["w"]}x{vp["h"]} (its Select is off-screen: {row["btn"]}) — '
        'widen with GENERATOR_WIDTH and re-run; NOTHING was selected.'
    )
# Did the page notice? Generate stays disabled until a product type is
# chosen, so its enabled state — not the fact that a click was sent — is
# what proves the row was selected. Without this the next step would
# click a dead button and report "no .xlsm landed".
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
_wait(2, 'the store checkboxes')
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
    # Bounded by the wrapper's clock, not by a blind 25 tries: this is the
    # last step, so whatever time is left is the wait, and running out of
    # it is reported as what it is instead of the process vanishing.
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
            'Generate clicked but no new .xlsm landed in ' + DL
            + f' before the wrapper budget ({_BUDGET:.0f}s) ran out — '
            'the feed may still be building; re-run, or check that '
            'directory before concluding anything.'
        )
        capture_screenshot()
    print('RESULT ' + json.dumps(out))
