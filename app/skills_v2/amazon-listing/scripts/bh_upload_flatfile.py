# ruff: noqa: F821 — browser-harness globals (new_tab, js, cdp, ...)
"""Stage + submit a listing flat-file on ONE marketplace's upload page.

Deterministic fast path for the whole upload dance (the marketplace
check, the file-chooser intercept, the introspect wait, the Submit
click, the batch id) so the agent never re-derives it. Run through the
STORE WRAPPER with env parameters, from the task workspace:

  UPLOAD_FILE=/abs/path/file.txt SC_HOST=sellercentral.amazon.ae \
  MARKER_DIR="$PWD" browser-use < .claude/skills/amazon-listing/scripts/bh_upload_flatfile.py

Prints exactly one ``RESULT {json}`` line:
  ok / staged / detected / region_error / batch_id / marketplace /
  submit_label / reason

Two things it refuses to do, because both fail SILENTLY by hand:

* **Upload onto the wrong marketplace.** The file's own ``settings=``
  blob carries ``primaryMarketplaceId``; the live page carries
  ``ue_mid``. A ``.ae`` URL happily renders under an SA session — an
  AE-stamped file then lands in SA's upload history and the agent
  verifies "AE" off a page that was never AE. Both sides are
  machine-readable ids, so the mismatch is a hard stop before staging.
* **Guess at page text.** Seller Central runs in whatever language the
  SESSION is set to — one account can render English, Chinese or Arabic
  on the same URL — so matching any fixed wording reports "not detected"
  on a page that is perfectly fine, and the agent then abandons this
  helper and hand-drives. Nothing here reads a word: readiness IS the
  Submit button flipping disabled -> enabled (Amazon keeps it disabled
  until introspect-feed accepts the file), and the button is identified
  by a key compared only against itself between two snapshots. The
  rendered label is reported, never matched.

On success it writes ``UPLOAD_BATCH_<id>.json`` into MARKER_DIR (pass
your task workspace) — the completion gate then requires a parse-feedback
verdict for that batch before the task may finish. On failure it takes a
screenshot and reports the reason; explore from there, don't blind-retry.
"""

import json
import os
import re
import time

F = os.environ['UPLOAD_FILE']
HOST = os.environ['SC_HOST']
MARKER_DIR = os.environ.get('MARKER_DIR', '.')
out = {
    'ok': False,
    'staged': False,
    'detected': False,
    'region_error': False,
    'batch_id': None,
    'marketplace': None,
    'file_marketplace': None,
    'submit_label': None,
    'host': HOST,
    'file': F,
}


def _finish(reason=None):
    if reason:
        out['reason'] = reason
    print('RESULT ' + json.dumps(out))
    raise SystemExit(0)


# Waits are env-tunable — a heavy account / slow session needs longer
# than the mac defaults (verified live: 12s/10s left the widget not yet
# interactive and the type-detection not yet run; 15s/12s worked).
_LOAD_WAIT = int(os.environ.get('UPLOAD_LOAD_WAIT', '15'))
_INTROSPECT_WAIT = int(os.environ.get('UPLOAD_INTROSPECT_WAIT', '12'))

# The upload file's marketplace stamp, straight out of its settings blob.
_STAMP_RE = re.compile(r'primaryMarketplaceId=amzn1\.mp\.o\.(A[0-9A-Z]{8,})')
try:
    with open(F, encoding='utf-8', errors='replace') as fh:
        m = _STAMP_RE.search(fh.readline())
    out['file_marketplace'] = m.group(1) if m else None
except OSError as exc:
    _finish(f'cannot read UPLOAD_FILE: {exc}')

# Every button-ish control, with a stable identity that carries NO
# language. `key` is only ever compared against ITSELF across the two
# snapshots — never against a word — so this works on a console in any
# language. The rendered label is carried for the RESULT line only.
_BUTTONS_JS = (
    'function key(e){'
    "var l=(e.getAttribute&&e.getAttribute('label'))||'';"
    'if(l.trim()) return "L:"+l.trim();'
    'var p=[],n=e;'
    'while(n&&n.nodeType===1&&p.length<8){'
    'var par=n.parentNode;'
    'p.unshift(n.tagName+":"+(par?[].indexOf.call(par.children,n):0));'
    'n=par||(n.getRootNode&&n.getRootNode().host);}'
    'return "P:"+p.join("/");}'
    'var out=[];'
    "var sel='kat-button,button,[role=\\'button\\'],input[type=\\'submit\\']';"
    'document.querySelectorAll(sel).forEach(function(b){'
    'var r=b.getBoundingClientRect();'
    "var d=b.hasAttribute('disabled')||b.disabled===true"
    "||b.getAttribute('aria-disabled')==='true';"
    'out.push({key:key(b),'
    "label:(((b.getAttribute&&b.getAttribute('label'))||b.innerText||'')"
    ').trim().slice(0,40),'
    'disabled:!!d,w:Math.round(r.width),h:Math.round(r.height),'
    'x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});});'
    'return JSON.stringify(out);'
)


def _buttons():
    try:
        return json.loads(js(_BUTTONS_JS) or '[]')
    except (TypeError, ValueError):
        return []


def _pick_submit(before, after):
    """The control Amazon enabled once it accepted the file.

    Purely structural: the upload page keeps Submit disabled until the
    introspect-feed succeeds, so the button that flips disabled -> enabled
    IS the submit. No text is matched, in any language. When several flip,
    the lowest one on the page wins -- Submit sits under the widget.
    """
    was = {b['key'] for b in before if b['disabled']}
    flipped = [
        b for b in after if b['w'] and b['key'] in was and not b['disabled']
    ]
    return max(flipped, key=lambda b: b['y']) if flipped else None


new_tab(f'https://{HOST}/product-search/bulk')
time.sleep(_LOAD_WAIT)

# Marketplace check BEFORE anything is staged: ue_mid is the marketplace
# the SESSION is really on, in every language, and is what decides where
# the feed lands — not the subdomain in the URL.
out['marketplace'] = js(
    "return (typeof ue_mid!=='undefined' && ue_mid) ? String(ue_mid) : null"
)
# Fail CLOSED: without BOTH ids the helper cannot prove where the feed
# lands, and "probably the right marketplace" is what put an AE file in
# SA's upload history. No proof, no upload.
if not out['file_marketplace']:
    capture_screenshot()
    _finish(
        'cannot read primaryMarketplaceId from the upload file, so the '
        'marketplace it targets is unknown. Fill the upload file with '
        'listing_bulk.py from a freshly downloaded template (the stamp '
        'lives in its settings blob) instead of hand-rolling it.'
    )
if not out['marketplace']:
    capture_screenshot()
    _finish(
        'cannot read ue_mid from this page, so the marketplace this '
        'session is really on is unknown (the URL host does NOT decide '
        'it). Confirm the page is a logged-in seller-central page and '
        're-run; do not upload blind.'
    )
if out['marketplace'] != out['file_marketplace']:
    out['region_error'] = True
    capture_screenshot()
    _finish(
        'MARKETPLACE MISMATCH — this file is stamped for '
        f'{out["file_marketplace"]} but the live session is on '
        f'{out["marketplace"]} (URL host {HOST} does NOT decide this). '
        'Uploading here would list on the wrong storefront. Either '
        "switch the session's marketplace in the account switcher and "
        're-run, or regenerate the template with the intended store '
        'ticked (bh_download_template) and fill that one.'
    )

# A short viewport leaves the file button AND the Submit button below
# the fold, so coordinate clicks land on empty space (observed live —
# the upload "succeeded" per setFileInputFiles yet nothing attached, and
# Submit stayed disabled). Force a tall viewport so every control the
# coordinate clicks below target is actually on-screen.
cdp(
    'Emulation.setDeviceMetricsOverride',
    width=1920,
    height=3000,
    deviceScaleFactor=1,
    mobile=False,
)
time.sleep(1)
cdp('Page.enable')
cdp('Page.setInterceptFileChooserDialog', enabled=True)
drain_events()
before = _buttons()
box = js(
    "var u=document.querySelector('kat-file-upload');"
    'if(!u) return null;'
    'u.scrollIntoView({block:"center"});'
    "var b=u.shadowRoot.querySelector('#select-file')"
    "||u.shadowRoot.querySelector('button');"
    'var r=b.getBoundingClientRect();'
    'return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};'
)
if not box:
    cdp('Page.setInterceptFileChooserDialog', enabled=False)
    capture_screenshot()
    _finish('upload widget (kat-file-upload) not found on the page')

# Trusted click opens the (suppressed) chooser; Chrome hands us the
# REAL input's backendNodeId. Setting the visible input is a no-op
# (it is a decoy) — this is the only reliable staging path.
click_at_xy(box['x'], box['y'])
bnid = None
for _ in range(12):
    time.sleep(0.5)
    for e in drain_events():
        if 'fileChooserOpened' in str(e.get('method', '')):
            bnid = e['params']['backendNodeId']
    if bnid:
        break
if not bnid:
    cdp('Page.setInterceptFileChooserDialog', enabled=False)
    capture_screenshot()
    _finish('file chooser never opened (trusted click missed?)')

cdp('DOM.setFileInputFiles', backendNodeId=bnid, files=[F])
cdp('Page.setInterceptFileChooserDialog', enabled=False)

# Readiness is STRUCTURAL: Amazon's introspect-feed enables Submit when
# it has accepted the file. Poll instead of sleeping a fixed span.
submit = None
deadline = time.time() + max(_INTROSPECT_WAIT, 4)
while time.time() < deadline:
    time.sleep(2)
    submit = _pick_submit(before, _buttons())
    if submit and not submit['disabled']:
        break
    submit = None
out['staged'] = True
out['detected'] = bool(submit)
if submit:
    # Reported so a human can see WHICH control was clicked,
    # in whatever language the console renders. Never matched.
    out['submit_label'] = submit.get('label') or submit.get('key')

if not submit:
    # Surface Amazon's OWN message (any language) instead of a bare
    # "not detected" — the alert names the real problem.
    alerts = js(
        'var out=[];'
        "document.querySelectorAll('kat-alert').forEach(function(a){"
        "out.push(((a.getAttribute('header')||'')+' '+"
        "(a.innerText||'')).trim().slice(0,200));});"
        'return JSON.stringify(out);'
    )
    try:
        out['alerts'] = json.loads(alerts or '[]')
    except (TypeError, ValueError):
        out['alerts'] = []
    capture_screenshot()
    _finish(
        'file staged but Submit never enabled within '
        f'{_INTROSPECT_WAIT}s — read out["alerts"] and the screenshot '
        "for Amazon's own message, and bump UPLOAD_INTROSPECT_WAIT "
        'before concluding the file is bad'
    )

ref = None
for _ in range(2):  # some flows need a second Submit click
    click_at_xy(submit['x'], submit['y'])
    time.sleep(8)
    ref = js('return (location.href.match(/reference_id=(\\d+)/)||[])[1]||null')
    if ref:
        break
    submit = _pick_submit(before, _buttons()) or submit
out['batch_id'] = ref
out['ok'] = bool(ref)
if ref:
    marker = os.path.join(MARKER_DIR, f'UPLOAD_BATCH_{ref}.json')
    with open(marker, 'w') as fh:
        json.dump(
            {
                'batch_id': ref,
                'host': HOST,
                'file': F,
                'marketplace': out['marketplace'],
            },
            fh,
        )
    _finish()
capture_screenshot()
_finish('submit clicked but no reference_id in the URL')
