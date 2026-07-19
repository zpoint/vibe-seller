# ruff: noqa: F821 — browser-harness globals (new_tab, js, cdp, ...)
"""Generate + download ONE marketplace's product-spreadsheet template.

The generated template is REGION-STAMPED by the store checkboxes (they
default to the account's home marketplace), so this drives the whole
generator flow with the TARGET store ticked and the other leaf stores
unticked. Run through the STORE WRAPPER:

  SC_HOST=sellercentral.amazon.ae PRODUCT_TYPE=socks \
  STORE_LABEL=Amazon.ae DOWNLOADS_DIR=~/.vibe-seller/downloads/<slug> \
  browser-use < .claude/skills/amazon-listing/scripts/bh_download_template.py

Prints exactly one ``RESULT {json}`` line:
  ok / template (downloaded path) / picked (product type) / reason

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
out = {'ok': False, 'template': None, 'picked': None}

_WALK = (
    'function* w(r){for(const e of r.querySelectorAll("*")){yield e;'
    'if(e.shadowRoot) yield* w(e.shadowRoot);}}'
)


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
if not _click_text('^download product spreadsheet$'):
    _fail('Download Product Spreadsheet button not found')
else:
    time.sleep(7)
    # Product-type search wants TRUSTED keystrokes (a JS value set does
    # not stick on the kat predictive input).
    sb = js(
        _WALK + 'for(const e of w(document)){'
        'const tag=(e.tagName||"").toLowerCase();'
        'const ph=(e.placeholder||(e.getAttribute&&'
        'e.getAttribute("placeholder"))||"");'
        'if(tag==="input" && /product keyword/i.test(ph)){'
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
            'if(/^select$/i.test(t)){const r=e.getBoundingClientRect();'
            'if(r.width && (!best||r.y<best.y)){'
            'best={x:Math.round(r.x+r.width/2),'
            'y:Math.round(r.y+r.height/2)}; label=prev;}}'
            'if(t && t.length<40) prev=t;}'
            'return best?JSON.stringify({box:best,label:label}):null;'
        )
        if not sel:
            _fail(f'no product-type result for {PTYPE!r}')
        else:
            info = json.loads(sel)
            out['picked'] = info.get('label')
            click_at_xy(info['box']['x'], info['box']['y'])
            time.sleep(4)
            # Store checkboxes: tick the TARGET leaf, untick other
            # Amazon.* leaves (never touch region parents like Europe —
            # unticking a parent clears all its children).
            js(
                _WALK + 'for(const e of w(document)){'
                'if((e.tagName||"").toLowerCase()!=="kat-checkbox")'
                'continue;'
                'const lbl=(e.getAttribute("label")||e.textContent||"")'
                '.trim();'
                'if(!/^Amazon\\./i.test(lbl)) continue;'
                'const on=e.hasAttribute("checked")&&'
                'e.getAttribute("checked")!=="false";'
                f'const want=(lbl.toLowerCase()==="{STORE.lower()}");'
                'if(want!==on) e.click();}'
                'return "stores set";'
            )
            time.sleep(2)
            # Generate sits low in the modal — extend the viewport so
            # the trusted click lands.
            cdp(
                'Emulation.setDeviceMetricsOverride',
                width=1920,
                height=1600,
                deviceScaleFactor=1,
                mobile=False,
            )
            time.sleep(1)
            if not _click_text('^generate spreadsheet$'):
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
                print('RESULT ' + json.dumps(out))
