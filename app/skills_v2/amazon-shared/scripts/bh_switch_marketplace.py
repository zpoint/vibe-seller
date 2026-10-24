# ruff: noqa: F821 — browser-harness globals (new_tab, js, wait_for_load, ...)
"""Switch the Seller Central session to ONE marketplace, in one call.

§4a of SKILL.md, made deterministic. On a unified account the session is
pinned to one marketplace whatever the URL subdomain says, so moving from
SA to AE means the account-switcher page -- and an agent driving it by hand
spent ~45 calls there: it found the target row, clicked it (the row ticks),
and never found the confirm button, because that is a `kat-button` custom
element and it was looking for `button` / `a` / `[role=button]`. Run
through the STORE WRAPPER:

  SC_MARKETPLACE=AE SC_HOST=sellercentral.amazon.ae browser-use < .claude/skills/amazon-shared/scripts/bh_switch_marketplace.py

``SC_MARKETPLACE`` is the target country code or raw marketplace id; it
defaults to the one ``SC_HOST``'s domain names.

Nothing on the page is matched by a fixed word. The target row is found by
the country's name AS THIS SESSION'S LANGUAGE WRITES IT -- the browser's
own ``Intl.DisplayNames`` for the page's ``lang`` -- and the confirm button
by its ``data-test="confirm-selection"``. Rows carry no marketplace id, so
the localized name is the only handle the page offers.

Prints exactly one ``RESULT {json}`` line:
  ok / marketplace (the session's ue_mid afterwards) / target / reason
"""

import json
import os
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
# Inline because this script is fed to browser-use on stdin and cannot
# import its siblings (same tables as amazon-listing/bh_upload_flatfile.py).
_HOST_MARKETPLACES = {
    'com': 'ATVPDKIKX0DER',
    'ca': 'A2EUQ1WTGCTBG2',
    'com.mx': 'A1AM78C64UM0Y8',
    'com.br': 'A2Q3Y263D00KWC',
    'co.uk': 'A1F83G8C2ARO7P',
    'de': 'A1PA6795UKMFR9',
    'fr': 'A13V1IB3VIYZZH',
    'it': 'APJ6JRA9NG5V4',
    'es': 'A1RKKUPIHCS9HS',
    'nl': 'A1805IZSGTT6HS',
    'se': 'A2NODRKZP88ZB9',
    'pl': 'A1C3SOZRARQ6R3',
    'com.be': 'AMEN7PMS3EDWL',
    'com.tr': 'A33AVAJ2PDY3EV',
    'ie': 'A28R8C7NBKEWEA',
    'ae': 'A2VIGQ35RCS4UG',
    'sa': 'A17E79C6D8DWNP',
    'eg': 'ARBP9OOSHTCHU',
    'in': 'A21TJRUUN4KGV',
    'co.jp': 'A1VC38T7YXB528',
    'com.au': 'A39IBJ37TRP1C6',
    'sg': 'A19VAU5U5O7RUS',
}
_COUNTRY_HOSTS = {
    'US': 'com',
    'CA': 'ca',
    'MX': 'com.mx',
    'BR': 'com.br',
    'GB': 'co.uk',
    'DE': 'de',
    'FR': 'fr',
    'IT': 'it',
    'ES': 'es',
    'NL': 'nl',
    'SE': 'se',
    'PL': 'pl',
    'BE': 'com.be',
    'TR': 'com.tr',
    'IE': 'ie',
    'AE': 'ae',
    'SA': 'sa',
    'EG': 'eg',
    'IN': 'in',
    'JP': 'co.jp',
    'AU': 'com.au',
    'SG': 'sg',
}
_ID_COUNTRY = {
    _HOST_MARKETPLACES[tld]: cc for cc, tld in _COUNTRY_HOSTS.items()
}

out = {'ok': False, 'marketplace': None, 'target': None, 'host': HOST}


def _finish(reason=None):
    if reason:
        out['reason'] = reason
    print('RESULT ' + json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)


def _target():
    """(country code, marketplace id) the caller asked for."""
    want = (os.environ.get('SC_MARKETPLACE') or '').strip().upper()
    if want == 'UK':
        want = 'GB'
    if want in _COUNTRY_HOSTS:
        return want, _HOST_MARKETPLACES[_COUNTRY_HOSTS[want]]
    if want in _ID_COUNTRY:
        return _ID_COUNTRY[want], want
    tail = HOST.split('amazon.', 1)[-1].strip('/').lower()
    mid = _HOST_MARKETPLACES.get(tail)
    return (_ID_COUNTRY.get(mid), mid) if mid else (None, None)


def _ue_mid():
    return js('return (typeof ue_mid!=="undefined" && ue_mid) || null')


cc, mid = _target()
out['target'] = mid
if not mid:
    _finish(
        f'cannot tell which marketplace {HOST!r} means -- pass '
        'SC_MARKETPLACE=<CC|marketplace id>'
    )


def _session_marketplace():
    """The marketplace the SESSION is on, read off an ordinary page.

    Not off the switcher page: there ue_mid follows the URL host, so on
    `sellercentral.amazon.ae/account-switcher/...` it reads AE while every
    other page still renders SA. Read from there, the first cut of this
    helper reported a switch it had never made.
    """
    new_tab(f'https://{HOST}/home')
    wait_for_load()
    time.sleep(4)
    href = js('return location.href') or ''
    if '/ap/signin' in href or '/ap/mfa' in href:
        out['signed_out'] = True
        _finish(
            'signed out -- run amazon-shared/scripts/bh_signin.py, then '
            're-run this helper; nothing was switched.'
        )
    return _ue_mid()


out['marketplace'] = _session_marketplace()
if out['marketplace'] == mid:
    out['ok'] = True
    _finish()

new_tab(f'https://{HOST}/account-switcher/default/merchantMarketplace')
wait_for_load()
time.sleep(4)

# The row for the target country, by its name in the page's own language.
# Collapsed accounts show only their active marketplace, so expand any
# account row first (§4a step 2) and look again.
_ROW_JS = (
    'var name;try{name=new Intl.DisplayNames([document.documentElement.lang'
    f'||"en"],{{type:"region"}}).of("{cc}");}}catch(e){{return "{{}}";}}'
    'var rows=Array.from(document.querySelectorAll('
    '"button.full-page-account-switcher-account-details"));'
    'for(var i=0;i<rows.length;i++){var b=rows[i];'
    'var lab=b.querySelector(".full-page-account-switcher-account-label");'
    'var t=((lab||b).textContent||"").trim();'
    'if(t.indexOf(name)!==0)continue;'
    'var rest=t.slice(name.length).trim();'
    'var cur=!!(lab&&lab.getAttribute("data-test")==="current-account");'
    'var r=b.getBoundingClientRect();'
    'return JSON.stringify({name:name,label:t,extra:rest,current:cur,'
    'x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)});}'
    'return JSON.stringify({name:name});'
)
_EXPAND_JS = (
    'var n=0;document.querySelectorAll('
    '"kat-icon[name=arrow_drop_down]").forEach(function(i){'
    'var b=i.closest("button");if(b){b.click();n++;}});return n;'
)


def _row():
    try:
        return json.loads(js(_ROW_JS) or '{}')
    except (TypeError, ValueError):
        return {}


row = _row()
if 'label' not in row:
    js(_EXPAND_JS)
    time.sleep(2)
    row = _row()
if 'label' not in row:
    capture_screenshot()
    _finish(
        f'no row for {row.get("name") or cc} on the switcher page -- this '
        'account does not hold that marketplace (or the page changed); '
        'read the screenshot.'
    )
if row.get('extra') and not row.get('current'):
    # A parenthesised marker that is not the "current" one: pending /
    # not registered. Selecting it would start a registration, not a switch.
    _finish(
        f'the {row["label"]!r} row is not an active marketplace on this '
        'account -- refusing to select it.'
    )

js(
    'var rows=document.querySelectorAll('
    '"button.full-page-account-switcher-account-details");'
    f'for(var i=0;i<rows.length;i++){{if((rows[i].textContent||"").trim()'
    f'.indexOf({json.dumps(row["name"])})===0){{rows[i].click();break;}}}}'
)
time.sleep(1)
box = js(
    'var k=document.querySelector("kat-button[data-test=confirm-selection]");'
    'if(!k)return null;var r=k.getBoundingClientRect();'
    'return JSON.stringify({x:Math.round(r.x+r.width/2),'
    'y:Math.round(r.y+r.height/2)});'
)
if not box:
    capture_screenshot()
    _finish('no confirm-selection button after selecting the row')
box = json.loads(box)
click_at_xy(box['x'], box['y'])
time.sleep(6)
wait_for_load()

# The label is the truth (§4a step 4): read the marketplace back on a fresh
# ordinary page rather than trusting the click.
out['marketplace'] = _session_marketplace()
out['ok'] = out['marketplace'] == mid
if not out['ok']:
    capture_screenshot()
    _finish(
        f'the session is still on {out["marketplace"]} after confirming '
        f'{row["label"]}; read the screenshot.'
    )
_finish()
