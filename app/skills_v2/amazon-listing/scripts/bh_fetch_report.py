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
import re
import shutil
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
BATCH = _env('BATCH_ID')
DL = os.path.expanduser(_env('DOWNLOADS_DIR'))
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
# The sign-in page sets ue_mid too, so a lapsed session read as "batch id
# not found -- switch marketplace": the right marketplace, the wrong
# advice, and an agent hand-driving the status page. Bulk pages demand a
# sign-in within the last few minutes, so this lapses during any wait.
_href = js('return location.href') or ''
if '/ap/signin' in _href or '/ap/mfa' in _href:
    print(
        'RESULT '
        + json.dumps({
            **out,
            'signed_out': True,
            'reason': (
                f'signed out -- {HOST} redirected the upload-status page to '
                'sign-in. Run amazon-shared/scripts/bh_signin.py, then '
                're-run this helper; the batch was not looked at.'
            ),
        })
    )
    raise SystemExit(0)
# The batch's OWN row: the cells of the table row holding the batch id.
# A fixed window of leaf texts around the id spilled into the neighbouring
# row -- an agent read a September batch's filename as part of its own
# upload and stopped to question which file it had sent. The window is
# kept only as the fallback for a layout with no row element. Control
# labels are left out: a queued row carries Cancel buttons, and their
# label read back as the batch's status -- "queued | cancel | cancel".
row = js(
    'function* w(r){for(const e of r.querySelectorAll("*")){yield e;'
    'if(e.shadowRoot) yield* w(e.shadowRoot);}}'
    'function ctl(c,r){for(let n=c;n&&n!==r;'
    'n=n.parentElement||(n.getRootNode&&n.getRootNode().host)){'
    'if(["BUTTON","KAT-BUTTON","A","KAT-LINK"].includes(n.tagName)||'
    '(n.getAttribute&&n.getAttribute("role")==="button"))return true;}'
    'return false;}'
    'for(const e of w(document)){'
    f'if(e.children.length===0&&(e.textContent||"").trim()==="{BATCH}"){{'
    'let r=e;for(let i=0;i<14&&r;i++){if(/ROW|TR/.test(r.tagName||""))'
    'break;r=r.parentElement||(r.getRootNode&&r.getRootNode().host);}'
    'if(r&&/ROW|TR/.test(r.tagName||"")){let cells=[];'
    'for(const c of w(r)){if(c.children.length===0&&!ctl(c,r)){'
    'const t=(c.textContent||"").trim();if(t)cells.push(t);}}'
    'return cells.join(" | ");}break;}}'
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
                'to the marketplace it was UPLOADED to. Switch with '
                'amazon-shared/scripts/bh_switch_marketplace.py and re-run.'
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
        # A finished batch -- a FAILED one included (0/N) -- shows its
        # processed count as x/y; a pending one shows N/A there. Read by
        # structure, so the answer is the same in every console language.
        # Without it, every honest "still queued" told the agent the row
        # might be stale, and it went to inspect the status page by hand.
        out['pending'] = not any(
            re.fullmatch(r'\d+\s*/\s*\d+', c.strip())
            for c in str(row).split('|')
        )
        if out['pending']:
            out['reason'] = (
                'the batch is still pending (no processed count yet). This '
                'is normal: Amazon can take 30+ minutes, longer when other '
                'batches for the same SKUs are ahead of it. Wait 5 minutes '
                'and re-run this helper; there is nothing to inspect by '
                'hand. A wait that long usually signs the session out -- '
                'if this helper then reports signed_out, run '
                'amazon-shared/scripts/bh_signin.py first.'
            )
        else:
            out['reason'] = (
                'the batch finished but its row has no download control. '
                'The row reads: '
                + str(row)
                + ' -- re-run this helper once (it hard-reloads); if the '
                'control is still missing, take a screenshot of the row.'
            )
        print('RESULT ' + json.dumps(out))
