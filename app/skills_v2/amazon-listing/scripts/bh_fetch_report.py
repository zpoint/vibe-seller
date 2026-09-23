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

The download control is found by STRUCTURE, scoped to the batch's own
row: a real anchor's href first, then an icon/type attribute (Amazon's
own API tokens, identical in every language). Seller Central renders in
whatever language the session is set to, so the visible label is the
last resort, not the first — and when it is used it carries the
non-Latin variants.

Then run ``listing_bulk.py parse-feedback <report> --batch-id <id>`` —
that writes the verdict the completion gate checks.
"""

import glob
import json
import os
import shutil
import time

HOST = os.environ['SC_HOST']
BATCH = os.environ['BATCH_ID']
DL = os.path.expanduser(os.environ['DOWNLOADS_DIR'])
# Page settle after the hard reload; bump on a slow account.
_LOAD_WAIT = int(os.environ.get('REPORT_LOAD_WAIT', '15'))
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


# Cache-bust AND hard-reload. The status page is an SPA: navigating to
# the same URL can restore the view it already had, and a batch row then
# keeps whatever it said when the tab was first opened. Observed live —
# a batch that had FAILED (0/4, "action required", report ready) kept
# reading back as N/A / in-progress on every poll for two hours, because
# the DOM was never re-fetched. Same URL from a fresh tab showed the
# real row immediately.
new_tab(f'https://{HOST}/listing/status?_={int(time.time())}')
time.sleep(6)
try:
    cdp('Page.reload', ignoreCache=True)
except Exception:  # noqa: BLE001 — older harnesses may not expose it
    pass
time.sleep(_LOAD_WAIT)
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
        # Structure first, wording last. Seller Central renders in
        # whatever language the SESSION is set to, so the visible label
        # ("Download Processing Summary" / 下载处理一览 / تنزيل…) is the
        # least reliable handle there is. Within this batch's own row,
        # prefer signals that are the same in every language: a real
        # anchor (its href IS the report), then an icon/type attribute —
        # those values are Amazon's own API tokens, not user-facing copy.
        # The wording match stays only as the last net, with the
        # non-Latin variants, so a layout without either signal still
        # works instead of silently reporting "no button".
        'let fallback=null;'
        'for(const b of w(scope)){'
        'const tag=(b.tagName||"");'
        'const href=(b.getAttribute&&b.getAttribute("href"))||"";'
        'if(tag==="A" && href) return JSON.stringify({href:href});'
        'const apiToken=((b.getAttribute&&('
        'b.getAttribute("icon")||b.getAttribute("data-icon")||'
        'b.getAttribute("download")))||"")+"";'
        'const r=b.getBoundingClientRect();'
        'if(!r.width) continue;'
        'const box={x:Math.round(r.x+r.width/2),'
        'y:Math.round(r.y+r.height/2)};'
        'if(/download/i.test(apiToken)) return JSON.stringify(box);'
        'const bt=(b.innerText||(b.getAttribute&&'
        'b.getAttribute("label"))||"").trim();'
        'if(!fallback && /download|下载|تنزيل/i.test(bt)) fallback=box;}'
        'if(fallback) return JSON.stringify(fallback);'
        'return "no dl button";'
    )
    if clicked and clicked.startswith('{'):
        b = json.loads(clicked)
        if b.get('href'):
            # A real anchor: the href IS the report, no clicking needed.
            new_tab(b['href'])
        else:
            # Trusted click — a kat-button ignores untrusted JS .click().
            click_at_xy(b['x'], b['y'])
        report = None
        for _ in range(20):
            time.sleep(1)
            report = _newest_xlsm(t0)
            if report:
                break
        if report:
            # The report carries NO batch id, and Amazon names it after the
            # UPLOAD file -- so every batch of `create-sa.txt` downloads as
            # the same `create-sa-processing-summary.xlsm`, overwriting the
            # last. Parsing that file "for batch N" once wrote a verdict of
            # six errors onto a batch that had gone through 4/4 clean. The
            # one moment the report is provably batch N's is right here.
            stem, ext = os.path.splitext(report)
            tagged = f'{stem}__batch{BATCH}{ext}'
            shutil.copy2(report, tagged)
            report = tagged
        out['report'] = report
        out['ok'] = bool(report)
        if not report:
            out['reason'] = 'download clicked but no new .xlsm appeared'
        print('RESULT ' + json.dumps(out))
    else:
        out['reason'] = (
            "no download control in this batch's row. The row reads: "
            + str(row)
            + " — read the row's OWN status cell rather than assuming "
            'the feed is still queued. If the row is byte-identical to '
            'your previous poll, you are looking at a stale render, not '
            'at progress: a finished batch (including a FAILED one, '
            'which shows 0/N) always offers the report. Re-run this '
            'helper (it hard-reloads) before waiting any longer.'
        )
        print('RESULT ' + json.dumps(out))
