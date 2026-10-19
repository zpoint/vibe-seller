# ruff: noqa: F821 — browser-harness globals (new_tab, js, cdp, wait_for_load, ...)
"""Find SKUs on noon's My Catalog, in ONE country, in one call.

Three live runs spent 10-25 calls each on this: the page paints seconds
after `wait_for_load()` (an empty read looked like "no results"), the
search box did not filter at all (every term returned the same first
page), and the country is a sidebar switcher
the URL does not show -- so one run read the AE catalogue while
believing it was on SA. Run through the STORE WRAPPER:

  NOON_PROJECT=PRJ123456 NOON_COUNTRY=AE QUERY=SKU-PREFIX \
    browser-use < .claude/skills/noon-shared/scripts/bh_noon_catalog.py

``NOON_COUNTRY`` (SA / AE / EG) switches the sidebar store to that flag
first and proves it; omit it to only read which country is showing.
``QUERY`` is a SKU or SKU prefix; omit it to only switch. The catalogue
is read page by page (20 rows each) until a page comes back empty.

Prints exactly one ``RESULT {json}`` line:
  ok / country / query / rows / other_rows / reason
``rows`` holds every result whose partner SKU contains QUERY (case-
insensitive): ``{psku, noon_sku, code, url}`` -- ``url`` opens that SKU's
detail page (its Content / Offer tabs). ``other_rows`` counts the
fuzzy-search extras it left out. Read-only: it never edits anything.
"""

import json
import os
import time


def _env(name, required=True):
    """A parameter, or one RESULT line saying how to pass it."""
    value = os.environ.get(name, '').strip()
    if required and not value:
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


PROJECT = _env('NOON_PROJECT')
WANT = _env('NOON_COUNTRY', required=False).upper()
QUERY = _env('QUERY', required=False)
CATALOG = (
    f'https://noon-catalog.noon.partners/en/catalog?project={PROJECT}&tab=noon'
)
out = {'ok': False, 'country': None, 'query': QUERY or None, 'rows': []}


def _finish(reason=None):
    if reason:
        out['reason'] = reason
    print('RESULT ' + json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)


def _painted(marker='PSKU', budget=40):
    """Poll until the React page has painted `marker` -- never trust a
    read straight after wait_for_load()."""
    end = time.time() + budget
    while time.time() < end:
        href = js('return location.href') or ''
        if 'login' in href:
            out['signed_out'] = True
            _finish(
                'signed out -- noon redirected My Catalog to login; sign in '
                '(noon-shared SKILL.md §1) and re-run. Nothing was read.'
            )
        if marker in (js('return document.body.innerText') or ''):
            return True
        time.sleep(2)
    return False


_FLAG_JS = (
    "var f=document.querySelector('.ns-side-nav__store-flag');"
    "if(!f)return null;var m=(getComputedStyle(f).backgroundImage||'')"
    '.match(/flags\\/([a-z]{2})\\.svg/i);return m?m[1].toUpperCase():null;'
)


def _country():
    return js(_FLAG_JS)


def _switch(cc):
    """Open the store list, then click the VISIBLE entry whose FLAG is `cc`
    (store names repeat across countries, so the label proves nothing).

    Open first, always: the list's entries are in the DOM while it is
    collapsed, zero-sized, and a click on one lands on nothing -- the
    first cut of this helper did exactly that and stayed on AE.
    """
    js(
        "var f=document.querySelector('.ns-side-nav__store-flag');"
        "if(f){(f.closest('button')||f.closest('a')||"
        "f.closest('[role=button]')||f).click();}"
    )
    time.sleep(2)
    box = js(
        "var fs=document.querySelectorAll('.ns-store-list__item');"
        'for(var i=0;i<fs.length;i++){var g=fs[i].querySelector('
        "'[class*=item-flag]')||fs[i];var u=getComputedStyle(g)"
        f".backgroundImage||'';if(!/\\/{cc.lower()}\\.svg/i.test(u))continue;"
        'var r=fs[i].getBoundingClientRect();if(!r.width||!r.height)continue;'
        'return JSON.stringify({x:Math.round(r.x+r.width/2),'
        'y:Math.round(r.y+r.height/2)});}return null;'
    )
    if not box:
        capture_screenshot()
        _finish(
            f'no visible {cc} store in the sidebar switcher -- read the screenshot'
        )
    p = json.loads(box)
    click_at_xy(p['x'], p['y'])
    time.sleep(4)


new_tab(CATALOG)
wait_for_load()
if not _painted():
    capture_screenshot()
    _finish('My Catalog never rendered -- do not read that as "no SKUs"')
js(
    "var b=Array.from(document.querySelectorAll('button')).find("
    'function(x){return /maybe later/i.test(x.textContent);});'
    'if(b){b.click();}'
)
out['country'] = _country()
if WANT and out['country'] != WANT:
    _switch(WANT)
    new_tab(CATALOG)
    wait_for_load()
    _painted()
    out['country'] = _country()
    if out['country'] != WANT:
        capture_screenshot()
        _finish(
            f'still on {out["country"]} after selecting {WANT} -- read the '
            'screenshot; nothing was read'
        )
if not QUERY:
    out['ok'] = True
    _finish()

# Walk the catalogue's own pages (`&page=N`, 20 rows each) instead of the
# search box: live, the box returned the same unfiltered first page for
# every term -- typed through a focused insert or by hand -- and read as
# "not listed here" for a family that was on page 3.
_ROWS_JS = (
    'var seen={},out=[];'
    'document.querySelectorAll(\'a[href*="/d?code="]\').forEach(function(a){'
    'var m=a.href.match(/catalog\\/([^/?]+)\\/d\\?code=([^&]+)/);'
    'if(!m||seen[m[2]])return;seen[m[2]]=1;'
    'var n=a,t="";for(var k=0;k<8&&n;k++){t=n.innerText||"";'
    'if(/PSKU/.test(t))break;n=n.parentElement;}'
    'var p=(t.match(/PSKU:?\\s*(\\S+)/)||[])[1]||null;'
    'out.push({psku:p,noon_sku:m[1],code:m[2],url:a.href});});'
    'return JSON.stringify(out);'
)
_DEADLINE = time.time() + int(os.environ.get('CATALOG_BUDGET', '95'))
rows, page = [], 0
while time.time() < _DEADLINE:
    page += 1
    new_tab(f'{CATALOG}&page={page}')
    wait_for_load()
    if not _painted(budget=25):
        break  # past the last page: no rows to paint
    got = json.loads(js(_ROWS_JS) or '[]')
    if not got or any(g['code'] in {r['code'] for r in rows} for g in got):
        break  # an empty page, or the site repeating the last one
    rows.extend(got)
else:
    out['partial'] = True
out['pages_read'] = page
q = QUERY.lower()
out['rows'] = [r for r in rows if r.get('psku') and q in r['psku'].lower()]
out['other_rows'] = len(rows) - len(out['rows'])
out['ok'] = True
if not out['rows'] and out.get('partial'):
    out['ok'] = False
    out['reason'] = (
        f'ran out of time after {page} pages without finding {QUERY!r} -- '
        'not proof it is absent; re-run with a larger CATALOG_BUDGET'
    )
elif not out['rows']:
    out['reason'] = (
        f'no partner SKU containing {QUERY!r} on {out["country"]} -- it is '
        'not listed in this country (do not create it here)'
    )
_finish()
