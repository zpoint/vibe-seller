# ruff: noqa: F821 — browser-harness globals (new_tab, js, cdp, ...)
"""Stage + submit a listing flat-file on ONE marketplace's upload page.

Deterministic fast path for the whole upload dance (the file-chooser
intercept, the introspect wait, the Submit click, the batch id) so the
agent never re-derives it. Run through the STORE WRAPPER with env
parameters, from the task workspace:

  UPLOAD_FILE=/abs/path/file.txt SC_HOST=sellercentral.amazon.ae \
  MARKER_DIR="$PWD" browser-use < .claude/skills/amazon-listing/scripts/bh_upload_flatfile.py

Prints exactly one ``RESULT {json}`` line:
  ok / staged / detected / region_error / batch_id / reason

On success it writes ``UPLOAD_BATCH_<id>.json`` into MARKER_DIR (pass
your task workspace) — the completion gate then requires a parse-feedback
verdict for that batch before the task may finish. On failure it takes a
screenshot and reports the reason; explore from there, don't blind-retry.
"""

import json
import os
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
    'host': HOST,
    'file': F,
}


def _finish(reason=None):
    if reason:
        out['reason'] = reason
    print('RESULT ' + json.dumps(out))


new_tab(f'https://{HOST}/product-search/bulk')
time.sleep(12)
cdp('Page.enable')
cdp('Page.setInterceptFileChooserDialog', enabled=True)
drain_events()
box = js(
    "var u=document.querySelector('kat-file-upload');"
    'if(!u) return null;'
    "var b=u.shadowRoot.querySelector('#select-file')"
    "||u.shadowRoot.querySelector('button');"
    'var r=b.getBoundingClientRect();'
    'return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};'
)
if not box:
    cdp('Page.setInterceptFileChooserDialog', enabled=False)
    capture_screenshot()
    _finish('upload widget (kat-file-upload) not found on the page')
else:
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
    else:
        cdp('DOM.setFileInputFiles', backendNodeId=bnid, files=[F])
        cdp('Page.setInterceptFileChooserDialog', enabled=False)
        time.sleep(10)  # introspect-feed runs
        state = js(
            'var t=document.body.innerText;'
            'return {detected:/automatically detected/i.test(t),'
            'region:/different region|MARKETPLACES_DIFFERENT/i.test(t),'
            'notup:/file not uploaded/i.test(t)};'
        )
        out['staged'] = not state['notup']
        out['detected'] = bool(state['detected'])
        out['region_error'] = bool(state['region'])
        if state['region']:
            _finish(
                'template region-stamp mismatch: regenerate the template '
                'with THIS marketplace ticked (bh_download_template)'
            )
        elif not state['detected']:
            capture_screenshot()
            _finish(
                'file staged but type not detected — read the screenshot '
                'for the widget error before retrying'
            )
        else:
            ref = None
            for _ in range(2):  # some flows need a second Submit click
                sb = js(
                    'var els=[...document.querySelectorAll('
                    "'kat-button,button')];"
                    'var b=els.find(function(e){return /submit products/i'
                    ".test(e.innerText||e.getAttribute('label')||'');});"
                    'if(!b) return null;'
                    'var r=b.getBoundingClientRect();'
                    'return {x:Math.round(r.x+r.width/2),'
                    'y:Math.round(r.y+r.height/2)};'
                )
                if sb:
                    click_at_xy(sb['x'], sb['y'])
                    time.sleep(8)
                ref = js(
                    'return (location.href.match(/reference_id=(\\d+)/)'
                    '||[])[1]||null'
                )
                if ref:
                    break
            out['batch_id'] = ref
            out['ok'] = bool(ref)
            if ref:
                marker = os.path.join(MARKER_DIR, f'UPLOAD_BATCH_{ref}.json')
                with open(marker, 'w') as fh:
                    json.dump({'batch_id': ref, 'host': HOST, 'file': F}, fh)
                _finish()
            else:
                capture_screenshot()
                _finish('submit clicked but no reference_id in the URL')
