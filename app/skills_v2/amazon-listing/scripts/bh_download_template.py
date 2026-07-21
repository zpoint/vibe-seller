# ruff: noqa: F821 — browser-harness globals (new_tab, js, cdp, ...)
"""Generate + download ONE marketplace's product-spreadsheet template.

The generated template is REGION-STAMPED by the store checkboxes (they
default to the account's home marketplace), so this drives the whole
generator flow with the TARGET store ticked and the other leaf stores
unticked. Run through the STORE WRAPPER:

  SC_HOST=sellercentral.amazon.ae PRODUCT_TYPE=socks \
  STORE_LABEL=Amazon.ae DOWNLOADS_DIR=~/.vibe-seller/downloads/<slug> \
  MARKER_DIR="$PWD" \
  browser-use < .claude/skills/amazon-listing/scripts/bh_download_template.py

Prints exactly one ``RESULT {json}`` line:
  ok / template (downloaded path) / picked (product type) /
  stores (label -> ticked, read back AFTER ticking) / reason

The store tick is VERIFIED, never assumed: kat-checkbox often ignores a
JS ``.click()`` (shadow DOM), and a template generated WITHOUT the
target store ticked is region-stamped for the wrong marketplace — the
whole upload then succeeds on the wrong storefront. After ticking, the
checkbox states are read back; an unticked target is retried with a
trusted coordinate click; if the target STILL isn't ticked, the result
is ``ok: false`` with the observed states. Other leaves left ticked are
fine — a bundled multi-marketplace template works (``fill`` routes the
offer by marketplace), so we never fight the other checkboxes.

On success it also writes ``UPLOAD_PENDING.json`` into MARKER_DIR (pass
your task workspace): the completion gate then refuses to end the turn
until the batch you upload has a parse-feedback verdict (or you remove
the marker because no upload happened).

On any miss it screenshots and reports the step that failed — explore
from the screenshot rather than blind-retrying.
"""

import glob
import json
import os
import time

HOST = os.environ['SC_HOST']
PTYPE = os.environ['PRODUCT_TYPE']
STORE = os.environ['STORE_LABEL']
DL = os.path.expanduser(os.environ['DOWNLOADS_DIR'])
MARKER_DIR = os.environ.get('MARKER_DIR', '.')
out = {'ok': False, 'template': None, 'picked': None, 'stores': None}

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
        'y:Math.round(r.y+r.height/2)});}'
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


def _click_text(pattern):
    """Trusted-click the first element whose text matches *pattern*."""
    box = js(
        _WALK + 'for(const e of w(document)){'
        'const t=(e.innerText||(e.getAttribute&&e.getAttribute("label"))'
        '||"").trim();'
        f'if(/{pattern}/i.test(t) && t.length<60){{'
        'const r=e.getBoundingClientRect();'
        'if(r.width) return JSON.stringify('
        '{x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)});}}'
        'return null;'
    )
    if not box:
        return False
    b = json.loads(box)
    click_at_xy(b['x'], b['y'])
    return True


def _fail(reason):
    capture_screenshot()
    out['reason'] = reason
    print('RESULT ' + json.dumps(out))


t0 = time.time()
new_tab(f'https://{HOST}/product-search/bulk/generate')
time.sleep(13)
# The seller-central UI language follows the SESSION (ZH sessions are
# common) — every text match below carries the Chinese variant too.
_DPS = '^(download product spreadsheet|下载商品电子表格)$'
_entered = _click_text(_DPS)
if not _entered:
    # Some layouts gate the generator behind the Download Blank
    # Template entry — take it once, then retry.
    _click_text('^(download blank template|下载空白模板)$')
    time.sleep(5)
    _entered = _click_text(_DPS)
if not _entered:
    _fail(
        'Download Product Spreadsheet button not found (tried EN+ZH '
        'and the Download Blank Template entry)'
    )
else:
    time.sleep(7)
    # Product-type search wants TRUSTED keystrokes (a JS value set does
    # not stick on the kat predictive input).
    sb = js(
        _WALK + 'for(const e of w(document)){'
        'const tag=(e.tagName||"").toLowerCase();'
        'const ph=(e.placeholder||(e.getAttribute&&'
        'e.getAttribute("placeholder"))||"");'
        'if(tag==="input" && /product keyword|商品关键|关键词/i.test(ph)){'
        'const r=e.getBoundingClientRect();'
        'return JSON.stringify({x:Math.round(r.x+r.width/2),'
        'y:Math.round(r.y+r.height/2)});}}'
        'return null;'
    )
    if not sb:
        _fail('product-type search input not found')
    else:
        b = json.loads(sb)
        click_at_xy(b['x'], b['y'])
        time.sleep(1)
        cdp('Input.insertText', text=PTYPE)
        for t in ('keyDown', 'keyUp'):
            cdp(
                'Input.dispatchKeyEvent',
                type=t,
                key='Enter',
                code='Enter',
                windowsVirtualKeyCode=13,
            )
        time.sleep(6)
        # First result's Select button (topmost).
        sel = js(
            _WALK + 'let best=null, label=null, prev=null;'
            'for(const e of w(document)){'
            'const t=(e.innerText||(e.getAttribute&&'
            'e.getAttribute("label"))||"").trim();'
            'if(/^(select|选择)$/i.test(t)){const r=e.getBoundingClientRect();'
            'if(r.width && (!best||r.y<best.y)){'
            'best={x:Math.round(r.x+r.width/2),'
            'y:Math.round(r.y+r.height/2)}; label=prev;}}'
            'if(t && t.length<40 && !/^(select|选择)$/i.test(t)) prev=t;}'
            'return best?JSON.stringify({box:best,label:label}):null;'
        )
        if not sel:
            _fail(f'no product-type result for {PTYPE!r}')
        else:
            info = json.loads(sel)
            out['picked'] = info.get('label')
            click_at_xy(info['box']['x'], info['box']['y'])
            time.sleep(4)
            # Extend the viewport BEFORE ticking: the store checkboxes
            # and the Generate button sit low in the modal, and both the
            # coordinate readback and the trusted clicks need on-screen
            # coordinates.
            cdp(
                'Emulation.setDeviceMetricsOverride',
                width=1920,
                height=1600,
                deviceScaleFactor=1,
                mobile=False,
            )
            time.sleep(1)
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
            for _attempt in range(2):
                if not needs_click:
                    break
                click_at_xy(needs_click['x'], needs_click['y'])
                time.sleep(1.5)
                states, tgt, needs_click = _target_unticked()
            out['stores'] = {s['label']: bool(s['on']) for s in states}
            if not states:
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
                _fail('Generate Spreadsheet button not found')
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
                out['template'] = template
                out['ok'] = bool(template)
                if not template:
                    out['reason'] = (
                        'Generate clicked but no new .xlsm landed in ' + DL
                    )
                    capture_screenshot()
                else:
                    # Arm the upload gate: a downloaded template means an
                    # upload is intended this turn; the turn can't end
                    # until the uploaded batch has a verdict (or the
                    # agent removes this marker because no upload
                    # happened). See stop_gates/listing_upload_gate.
                    marker = os.path.join(MARKER_DIR, 'UPLOAD_PENDING.json')
                    with open(marker, 'w') as fh:
                        json.dump(
                            {
                                'template': template,
                                'store': STORE,
                                'host': HOST,
                            },
                            fh,
                        )
                print('RESULT ' + json.dumps(out))
