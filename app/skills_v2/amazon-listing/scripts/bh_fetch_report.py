# ruff: noqa: F821 — browser-harness globals (new_tab, js, cdp, ...)
"""Fetch ONE batch's Processing Summary from Check Upload Status.

Finds the row for BATCH_ID (shadow-DOM aware), reports its live status,
and — when a Download Processing Summary button exists for it — clicks
it and waits for the report to land in the store downloads dir. Run
through the STORE WRAPPER:

  SC_HOST=sellercentral.amazon.ae BATCH_ID=100000000001 \
  DOWNLOADS_DIR=~/.vibe-seller/downloads/<slug> \
  browser-use < .claude/skills/amazon-listing/scripts/bh_fetch_report.py

Prints exactly one ``RESULT {json}`` line:
  ok / row (status text) / report (downloaded path) / marketplace / reason

``marketplace`` is the live ``ue_mid`` — the marketplace the SESSION is
really on, which is what the status page lists batches for. The
subdomain does not decide it, so a batch "not found" on a `.ae` URL is
usually an SA session, not a missing batch.

The download button's label is LOCALISED (a ZH session renders
下载处理一览, not "Download Processing Summary"), so it is matched by a
multilingual pattern scoped to the batch's own row — never by the
English text alone.

Then run ``listing_bulk.py parse-feedback <report> --batch-id <id>`` —
that writes the verdict the completion gate checks.
"""

import glob
import json
import os
import time

HOST = os.environ['SC_HOST']
BATCH = os.environ['BATCH_ID']
DL = os.path.expanduser(os.environ['DOWNLOADS_DIR'])
out = {
    'ok': False,
    'batch_id': BATCH,
    'row': None,
    'report': None,
    'marketplace': None,
}


def _newest_xlsm(after_ts):
    cands = [
        p
        for p in glob.glob(os.path.join(DL, '*.xlsm'))
        if os.path.getmtime(p) > after_ts
    ]
    return max(cands, key=os.path.getmtime) if cands else None


new_tab(f'https://{HOST}/listing/status')
time.sleep(15)
out['marketplace'] = js(
    "return (typeof ue_mid!=='undefined' && ue_mid) ? String(ue_mid) : null"
)
row = js(
    'function* w(r){for(const e of r.querySelectorAll("*")){yield e;'
    'if(e.shadowRoot) yield* w(e.shadowRoot);}}'
    'let seq=[];'
    'for(const e of w(document)){if(e.children.length===0){'
    'const t=(e.textContent||"").trim(); if(t) seq.push(t);}}'
    f'let i=seq.findIndex(s=>s.includes("{BATCH}"));'
    'return i>=0? seq.slice(Math.max(0,i-2), i+4).join(" | ") : null;'
)
out['row'] = row
if not row:
    capture_screenshot()
    print(
        'RESULT '
        + json.dumps({
            **out,
            'reason': (
                'batch id not found on this page — this session is on '
                f'marketplace {out["marketplace"]}; the batch belongs '
                'to the marketplace it was UPLOADED to. Switch the '
                'account switcher to that marketplace and re-run.'
            ),
        })
    )
else:
    t0 = time.time()
    clicked = js(
        'function* w(r){for(const e of r.querySelectorAll("*")){yield e;'
        'if(e.shadowRoot) yield* w(e.shadowRoot);}}'
        'let anchor=null;'
        'for(const e of w(document)){const t=(e.textContent||"").trim();'
        f'if(t==="{BATCH}"){{anchor=e;break;}}}}'
        'if(!anchor) return "no anchor";'
        'let row=anchor;'
        'for(let i=0;i<14&&row;i++){const tg=row.tagName||"";'
        'if(/ROW|TR/.test(tg))break;'
        'row=row.parentElement||(row.getRootNode&&row.getRootNode().host);}'
        'const scope=row||document;'
        'for(const b of w(scope)){'
        'const bt=(b.innerText||(b.getAttribute&&b.getAttribute("label"))'
        '||"").trim();'
        # The label is localised: EN "Download Processing Summary",
        # ZH 下载处理一览, AR تنزيل. Scoped to this batch's own row, a
        # bare download verb is unambiguous — the row's only other
        # button is "fix products" / 修复商品.
        'if(/download|下载|تنزيل/i.test(bt)){'
        'const r=b.getBoundingClientRect();'
        'if(r.width) return JSON.stringify('
        '{x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)});}}'
        'return "no dl button";'
    )
    if clicked and clicked.startswith('{'):
        # Trusted click — the kat-button ignores untrusted JS .click().
        b = json.loads(clicked)
        click_at_xy(b['x'], b['y'])
        report = None
        for _ in range(20):
            time.sleep(1)
            report = _newest_xlsm(t0)
            if report:
                break
        out['report'] = report
        out['ok'] = bool(report)
        if not report:
            out['reason'] = 'download clicked but no new .xlsm appeared'
        print('RESULT ' + json.dumps(out))
    else:
        out['reason'] = (
            'no Download Processing Summary button for this batch yet '
            '(still processing?) — row: ' + str(row)
        )
        print('RESULT ' + json.dumps(out))
