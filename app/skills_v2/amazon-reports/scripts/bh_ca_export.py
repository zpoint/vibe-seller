# ruff: noqa: F821 — browser-harness globals (goto_url, js, cdp, ...)
"""Export one Custom Analytics table tile, optionally for a custom range.

references/custom-analytics.md, made deterministic. Hand-driving a tile
costs many calls and fails silently in three ways seen live: a tile in a
background tab never requests its data (it stays a grey placeholder), a
date typed into the range picker without the popover's own Apply changes
the label but not the data, and a page left with a filter applied blocks
the next navigation on a "leave this page?" dialog. Run through the STORE
WRAPPER:

  SC_HOST=sellercentral.amazon.sa browser-use < .claude/skills/amazon-reports/scripts/bh_ca_export.py

Parameters (environment, on the same line as browser-use):
  SC_HOST          required -- the Seller Central host of the marketplace
  CA_DASHBOARD     dashboard slug (default detail-page-sales-traffic-template)
  CA_TILE          0-based index of the tile on the dashboard (default 0)
  CA_START/CA_END  optional custom range, YYYY/MM/DD in every UI language,
                   at most 92 days; give both or neither
  CA_EXPORT        1 (default) clicks Export; 0 only reads the tile
  CA_DOWNLOAD_DIR  optional -- the store's downloads dir; when set, waits
                   for metric-data.xlsx to land there and reports its path
                   ($VIBE_STORE_DOWNLOADS; if that is empty on an older
                   Vibe Seller, use the downloads folder the store
                   context names)

Prints exactly one ``RESULT {json}`` line:
  ok          -- true when the tile loaded (and the export landed, if
                 CA_DOWNLOAD_DIR was given)
  marketplace -- ue_mid of the page; compare it with the target
  tile        -- the tile's title;  period -- the range the tile shows
  totals      -- the tile's Total row as text; empty -- no data rows
  exported    -- Export was clicked;  file -- the landed file, if checked
  needs       -- when not ok: 'signin', 'no_tile', 'chart_tile', 'range'
                 or 'download';  reason -- what to do next
"""

import json
import os
import time


def _env(name, default=None):
    value = os.environ.get(name, '').strip()
    if value:
        return value
    if default is not None:
        return default
    print(
        'RESULT '
        + json.dumps({
            'ok': False,
            'reason': f'{name} is not set -- put it on the same line as '
            'browser-use (NAME=value browser-use < script) or export it',
        })
    )
    raise SystemExit(0)


HOST = _env('SC_HOST')
DASHBOARD = _env('CA_DASHBOARD', 'detail-page-sales-traffic-template')
TILE = int(_env('CA_TILE', '0'))
START = os.environ.get('CA_START', '').strip()
END = os.environ.get('CA_END', '').strip()
EXPORT = _env('CA_EXPORT', '1') != '0'
DL_DIR = os.path.expanduser(os.environ.get('CA_DOWNLOAD_DIR', '').strip())
# The wrapper kills a call at 120s; leave room for the RESULT line.
_DEADLINE = time.time() + int(os.environ.get('CA_BUDGET', '100'))
out = {'ok': False, 'host': HOST, 'dashboard': DASHBOARD, 'tile_index': TILE}


def _finish(needs=None, reason=None, ok=False):
    out['ok'] = ok
    if needs:
        out['needs'] = needs
    if reason:
        out['reason'] = reason
    print('RESULT ' + json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)


if bool(START) != bool(END):
    _finish('range', 'give both CA_START and CA_END (YYYY/MM/DD) or neither')

# The tile element: the smallest ancestor of its "Actions for <title>"
# menu button that holds no other tile's menu. The aria-label is English
# in every UI language seen (zh-CN, en-AE).
_TILE = (
    '(function(){var bs=document.querySelectorAll('
    '\'button[aria-label^="Actions for "]\');var b=bs[__I__];if(!b)return null;'
    'var c=b;while(c.parentElement&&c.parentElement.querySelectorAll('
    '\'button[aria-label^="Actions for "]\').length<2)c=c.parentElement;'
    'return c;})()'
).replace('__I__', str(TILE))


# Button labels the helper must press by text, in the UI languages seen.
_APPLY = '/^(应用|Apply|Aplicar)$/.test(b.innerText.trim())'
_TOUR = '/^(跳过演示|关闭|Close|Skip tour)$/.test(b.innerText.trim())'


def _click(expr):
    """A real click at the element's centre; False when it is absent.

    The time-period controls ignore synthetic .click() calls on some
    builds, and a coordinate click is what a person does.
    """
    box = js(
        'var e=' + expr + ';if(!e)return null;e.scrollIntoView({block:'
        '"center"});var r=e.getBoundingClientRect();'
        'return [r.x+r.width/2,r.y+r.height/2];'
    )
    if not box:
        return False
    click_at_xy(box[0], box[1])
    return True


def _state():
    return js(
        'var c=' + _TILE + ';if(!c)return null;'
        'var tp=c.querySelector("[data-testid=time-period-row]");'
        'var tot=[...c.querySelectorAll(".ag-floating-top .ag-row")]'
        '.map(function(r){return r.innerText;}).join(" ");'
        'var t=c.querySelector(\'button[aria-label^="Actions for "]\');'
        'return {tile:t.getAttribute("aria-label").slice(12),'
        'period:tp?tp.innerText.replace(/\\s+/g," ").trim():null,'
        'totals:tot.replace(/\\s+/g," ").trim(),'
        'rows:c.querySelectorAll(".ag-center-cols-container .ag-row").length,'
        'export:!!c.querySelector("[data-testid=btn-export]"),'
        'chart:!!c.querySelector("[data-testid=caw-metric-trend-tile]")};'
    )


# 1. Foreground first: a hidden tab renders the tiles but never asks for
#    their data. Clear a leave-page dialog a previous filter left behind,
#    or the navigation below times out on it.
cdp('Page.bringToFront')
try:
    cdp('Page.handleJavaScriptDialog', accept=True)
    out['cleared_dialog'] = True
except Exception:  # noqa: BLE001 - "No dialog is showing" is the norm
    pass
url = f'https://{HOST}/custom-analytics/dashboard/{DASHBOARD}'
try:
    goto_url(url)
except Exception:  # noqa: BLE001 - a dialog raised by this navigation
    # Accepting lets the blocked navigation carry on to `url`; a second
    # goto_url fired straight away collides with it and times out too.
    cdp('Page.handleJavaScriptDialog', accept=True)
    out['cleared_dialog'] = True
    time.sleep(3)
    if DASHBOARD not in js('return location.href'):
        goto_url(url)
wait_for_load()
time.sleep(3)
for _ in range(4):  # first-visit onboarding tours sit over the tiles
    js(
        'var b=[...document.querySelectorAll("button")].find(b=>'
        + _TOUR
        + ');if(b)b.click();'
    )
    time.sleep(0.4)
_href = js('return location.href')
if '/ap/signin' in _href or '/ap/mfa' in _href:
    out['url'] = _href
    _finish(
        'signin',
        'signed out -- run amazon-shared/scripts/bh_signin.py, '
        'then re-run this helper',
    )
out['marketplace'] = js('return typeof ue_mid!=="undefined"?ue_mid:null')

# 2. Wait for the tile; tiles below the fold load once scrolled into view.
st = None
while time.time() < _DEADLINE - 50:
    js('var c=' + _TILE + ';if(c)c.scrollIntoView({block:"start"});')
    st = _state()
    if st and (st['chart'] or (st['export'] and (st['totals'] or st['rows']))):
        break
    time.sleep(2)
if not st:
    _finish('no_tile', f'the dashboard has no tile #{TILE}')
out.update({k: st[k] for k in ('tile', 'period', 'totals')})
if st['chart']:
    _finish('chart_tile', 'chart tiles have no Export -- pick a table tile')
out['empty'] = not st['rows']

# 3. Custom range: popover -> range dialog -> type both dates -> the
#    dialog's Apply -> the popover's Apply. Skipping the last step changes
#    the label but not the data.
if START:
    _click(_TILE + '.querySelector("[data-testid=btn-tp-menu]")')
    time.sleep(2)
    _click(
        '[...document.querySelectorAll("[data-testid=custom-date-range-'
        'picker] button")].find(b=>b.offsetParent)'
    )
    time.sleep(1.5)
    _DLG = (
        '[...document.querySelectorAll("[role=dialog]")]'
        '.filter(x=>x.offsetParent&&x.querySelector('
        '"input[placeholder=\\"YYYY/MM/DD\\"]")).pop()'
    )
    for i, value in enumerate((START, END)):
        _click(_DLG + f'.querySelectorAll("input")[{i}]')
        js(_DLG + f'.querySelectorAll("input")[{i}].select()')
        press_key('Backspace')
        type_text(value)
        time.sleep(0.4)
    _click(
        '[...' + _DLG + '.querySelectorAll("button")].find(b=>' + _APPLY + ')'
    )
    time.sleep(1.5)
    if js('return !!' + _DLG):
        msg = js('return ' + _DLG + '.innerText.replace(/\\s+/g," ")')
        _finish(
            'range',
            'the range dialog refused the dates (custom ranges '
            'are at most 92 days): ' + msg[-120:],
        )
    before = _state()['totals']
    # The popover's Apply: the last visible Apply on the page (the range
    # dialog, which has its own, is closed by now).
    _click(
        '[...document.querySelectorAll("button")].filter(b=>b.offsetParent'
        '&&' + _APPLY + ').pop()'
    )
    want = f'{START} — {END}'
    while time.time() < _DEADLINE - 25:
        time.sleep(2)
        st = _state()
        if want in (st['period'] or '') and st['totals'] != before:
            break
    out.update({k: st[k] for k in ('period', 'totals')})
    out['empty'] = not st['rows']
    if want not in (st['period'] or ''):
        _finish('range', f'the tile still shows {st["period"]!r}')

# 4. Export. The file is always named metric-data.xlsx.
if EXPORT:
    t0 = time.time()
    if not _click(_TILE + '.querySelector("[data-testid=btn-export]")'):
        _finish('no_tile', 'the tile has no Export button')
    out['exported'] = True
    if DL_DIR:
        path = os.path.join(DL_DIR, 'metric-data.xlsx')
        while time.time() < _DEADLINE:
            if os.path.exists(path) and os.path.getmtime(path) >= t0 - 1:
                out['file'] = path
                out['bytes'] = os.path.getsize(path)
                _finish(ok=True)
            time.sleep(1)
        _finish('download', f'no fresh metric-data.xlsx in {DL_DIR}')
_finish(ok=True)
