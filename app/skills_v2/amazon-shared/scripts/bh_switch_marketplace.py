# ruff: noqa: F821 — browser-harness globals (new_tab, js, wait_for_load, ...)
"""Switch the Seller Central session to ONE marketplace, in one call.

§4a-bis of SKILL.md, made deterministic. On a unified account the
marketplace context is PER DOMAIN: each sellercentral subdomain renders
the marketplace last selected on it, so a session can be pinned the
"wrong" way (observed live: `.sa` serving AE while `.ae` served SA).
Read and verify on the TARGET marketplace's own domain -- reading the
source host can never see a switch it did make, which is how the first
cuts of this helper reported ok:false on successful switches.

  SC_MARKETPLACE=AE SC_HOST=sellercentral.amazon.ae browser-use < .claude/skills/amazon-shared/scripts/bh_switch_marketplace.py

``SC_MARKETPLACE`` is the target country code or raw marketplace id; it
defaults to the one ``SC_HOST``'s domain names. ``SC_HOST`` may also be
a full origin (``http://127.0.0.1:PORT``) -- used verbatim as the base
URL (integration tests, proxies).

Nothing on the page is matched by a fixed word. The target row is found
by the country's name AS THIS SESSION'S LANGUAGE WRITES IT -- the
browser's own ``Intl.DisplayNames`` for the page's ``lang`` -- and the
confirm button by its ``data-test="confirm-selection"``. Rows carry no
marketplace id, so the localized name is the only handle the page
offers. One row-identification pass drives both finding and clicking:
the chosen row's INDEX is clicked, never a second text match.

Amazon ships several front-end generations and accounts disagree on
which click method the switcher accepts: live evidence 2026-11-11 --
every store in this fleet ticks rows with a JS click and confirms only
through the kat-button's SHADOW-ROOT inner button (a coordinate click
on it does nothing), while SKILL.md's older note records accounts
where only real coordinate clicks work. So no method is assumed: every
click is act -> VERIFY the observable state (row has the -selected
class; the page left the switcher) -> fall back to the other method,
and the RESULT names the method that worked.

Prints exactly one ``RESULT {json}`` line:
  ok / marketplace (ue_mid on the target domain afterwards) / target /
  host / row_method / confirm_method / reason (when not ok)
On failure: ok:false + reason + a screenshot (shot.png in the session's
BH_TMP_DIR) -- never a claimed success.

The flow runs only when the browser-harness globals are present (the
code is exec'd by browser-use); the pure helpers above it import
cleanly for unit tests.
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


def resolve_target(host, want):
    """(country code, marketplace id) for a SC_MARKETPLACE + SC_HOST pair.

    ``want`` is a country code or a raw marketplace id (already upper);
    empty means "whatever the host's domain names". Pure -- unit-tested.
    """
    if want == 'UK':
        want = 'GB'
    if want in _COUNTRY_HOSTS:
        return want, _HOST_MARKETPLACES[_COUNTRY_HOSTS[want]]
    if want in _ID_COUNTRY:
        return _ID_COUNTRY[want], want
    tail = host.split('amazon.', 1)[-1].strip('/').lower()
    mid = _HOST_MARKETPLACES.get(tail)
    return (_ID_COUNTRY.get(mid), mid) if mid else (None, None)


def target_base(host, cc):
    """The origin whose /home renders the session state we act on.

    A SC_HOST carrying a scheme is used verbatim (local test servers,
    proxies). Otherwise the marketplace context is per-domain, so the
    TARGET marketplace's own sellercentral domain is the only host on
    which reading and verifying is meaningful -- not the host the
    caller happened to pass. Pure -- unit-tested.
    """
    if '://' in host:
        return host.rstrip('/')
    if cc and cc in _COUNTRY_HOSTS:
        return 'https://sellercentral.amazon.' + _COUNTRY_HOSTS[cc]
    return 'https://' + host


def pick_row(rows, name):
    """Choose the row to select, from descriptors, by localized name.

    ``rows``: dicts with label / current / hasExpander (from ONE page
    pass; the picked row's index is what gets clicked).
    ``name``: the target country's name in the page's own language.
    A row whose label carries anything else after the country name is
    not an active marketplace on this account -- e.g. the localized
    "(pending registration)" suffix -- and selecting it would START A
    REGISTRATION, so it is refused. The check is on the suffix being
    non-empty, never on its words (the page may be any language).
    Returns ``(row_or_None, why)`` where why is one of
    ``pick``           -- row is selectable, select it
    ``none``           -- no row names this country (expand and retry,
                          or the account does not hold it)
    ``current-only``   -- the only match IS the current marketplace
    ``pending``        -- every match carries a non-current marker
    Pure -- unit-tested.
    """
    cands = [
        r
        for r in rows
        if not r.get('hasExpander') and (r.get('label') or '').startswith(name)
    ]
    if not cands:
        return None, 'none'
    open_rows = [
        r
        for r in cands
        if not r.get('current') and not (r['label'] or '')[len(name) :].strip()
    ]
    if open_rows:
        return open_rows[0], 'pick'
    if any(r.get('current') for r in cands):
        return next(r for r in cands if r.get('current')), 'current-only'
    return cands[0], 'pending'


def _main():
    host = _env('SC_HOST')
    out = {
        'ok': False,
        'marketplace': None,
        'target': None,
        'host': host,
        'row_method': None,
        'confirm_method': None,
    }

    def _finish(reason=None):
        if reason:
            out['reason'] = reason
        print('RESULT ' + json.dumps(out, ensure_ascii=False))
        raise SystemExit(0)

    cc, mid = resolve_target(
        host, (os.environ.get('SC_MARKETPLACE') or '').strip().upper()
    )
    out['target'] = mid
    if not mid:
        _finish(
            f'cannot tell which marketplace {host!r} means -- pass '
            'SC_MARKETPLACE=<CC|marketplace id>'
        )
    base = target_base(host, cc)

    tab = [None]

    def _goto(url):
        """Navigate the helper's one tab (open it on first use)."""
        if tab[0] is None:
            tab[0] = new_tab(url)
        else:
            goto_url(url)
        wait_for_load()

    def _poll(expr, tries=30, delay=0.4):
        """Poll a js() expression until truthy (condition, not sleep)."""
        for _ in range(tries):
            try:
                v = js(expr)
            except Exception:
                v = None
            if v:
                return v
            time.sleep(delay)
        return None

    def _signed_out():
        out['signed_out'] = True
        _finish(
            'signed out -- run amazon-shared/scripts/bh_signin.py, '
            'then re-run this helper; nothing was switched.'
        )

    def _session_marketplace(sso_settle=60):
        """The marketplace the SESSION renders on BASE, an ordinary page.

        Not off the switcher page: there ue_mid follows the URL host
        (the /ap/signin bounce page defines it too), so the gate is
        the URL leaving /ap/, not ue_mid appearing.
        """
        _goto(base + '/home')
        href = js('return location.href') or ''
        if '/ap/' in href:
            # A LIVE session can bounce through the unified signin
            # page while SSO re-authenticates -- observed 2026-11-11:
            # the first /home load after the browser idled sat on
            # /ap/signin with an ssoResponse= param for >10 s, then
            # settled signed-in. The param says "wait it out"; a
            # page that stays on /ap/ is honestly signed out.
            tries = sso_settle if 'ssoResponse' in href else 25
            settled = _poll(
                'return (location.href||"").indexOf("/ap/")<0 &&'
                'document.readyState==="complete";',
                tries=tries,
            )
            if not settled:
                _signed_out()
        mid_now = _poll(
            'return (typeof ue_mid!=="undefined" && ue_mid) || null',
            tries=25,
        )
        href = js('return location.href') or ''
        if '/ap/signin' in href or '/ap/mfa' in href:
            _signed_out()
        return mid_now

    out['marketplace'] = _session_marketplace()
    if out['marketplace'] == mid:
        out['ok'] = True
        _finish()

    # --- the switcher page ----------------------------------------
    # Rows load asynchronously under the account row (a spinner is
    # visible while they arrive); the page is ready when the row count
    # is stable and no spinner is on screen.
    rows_present = (
        'var n=document.querySelectorAll('
        '"button.full-page-account-switcher-account-details").length;'
        'var sp=Array.prototype.slice.call(document.querySelectorAll('
        '"[class*=spinner],[class*=loading],kat-spinner"));'
        'var busy=sp.some(function(e){var r=e.getBoundingClientRect();'
        'return r.width>0&&r.height>0;});return n>0&&!busy?n:0;'
    )

    def _wait_rows(tries=40):
        """Rows arrived (no spinner) and the count held for two polls."""
        prev = -1
        for _ in range(tries):
            try:
                n = js(rows_present) or 0
            except Exception:
                n = 0
            if n > 0 and n == prev:
                return n
            prev = n
            time.sleep(0.4)
        return prev

    def _expand_groups():
        """JS-click account rows that are not expanded yet (one round).

        Collapsed account groups hide their country rows; the expander
        icon lives inside the account row's own button. Already-
        expanded rows are left alone -- clicking them would COLLAPSE
        the group.
        """
        return js(
            'var n=0;document.querySelectorAll('
            '"button.full-page-account-switcher-account-details")'
            '.forEach(function(b){var ic=b.querySelector('
            '"kat-icon.full-page-account-switcher-account-expander-icon");'
            'if(ic&&b.getAttribute("aria-expanded")!=="true"'
            '&&ic.className.indexOf("expander-icon-expanded")<0)'
            '{b.click();n++;}});return n;'
        )

    def _rows():
        """Every row on the page, in ONE identification pass.

        The SAME descriptors decide the pick and get clicked -- by
        index -- so finding and clicking can never disagree (the old
        helper matched the label for finding and the whole button text
        for clicking, which can pick different rows).
        """
        return json.loads(
            js(
                'var rows=Array.prototype.slice.call('
                'document.querySelectorAll('
                '"button.full-page-account-switcher-account-details"));'
                'return JSON.stringify(rows.map(function(b,i){'
                'var lab=b.querySelector('
                '".full-page-account-switcher-account-label");'
                'var t=((lab||b).textContent||"")'
                '.replace(/\\s+/g," ").trim();'
                'var r=b.getBoundingClientRect();'
                'return {i:i,label:t,'
                'current:!!(lab&&lab.getAttribute("data-test")'
                '==="current-account"),'
                'selected:b.className.indexOf("account-selected")>=0,'
                'hasExpander:!!b.querySelector('
                '"kat-icon.full-page-account-switcher-account-'
                'expander-icon"),'
                'x:Math.round(r.x+r.width/2),'
                'y:Math.round(r.y+r.height/2)};}));'
            )
            or '[]'
        )

    def _name_of():
        """The target country's name as this page's language writes it."""
        return js(
            'try{return new Intl.DisplayNames('
            '[document.documentElement.lang||"en"],{type:"region"})'
            f'.of("{cc}");}}catch(e){{return null;}}'
        )

    _goto(base + '/account-switcher/default/merchantMarketplace')
    _wait_rows()

    name = _name_of()
    if not name:
        capture_screenshot()
        _finish(
            f'Intl.DisplayNames gave no name for {cc!r} in this page language'
        )
    descriptors = _rows()
    row, why = pick_row(descriptors, name)
    if why == 'none':
        # A collapsed account group hides its country rows until
        # expanded -- expand and look once more (one round).
        _expand_groups()
        _wait_rows(tries=30)
        descriptors = _rows()
        row, why = pick_row(descriptors, name)
    if why in ('none', 'pending'):
        capture_screenshot()
        _finish(
            f'no selectable {name!r} row on the switcher page -- this '
            'account does not hold that marketplace, or the row is '
            'not an active one (pending registration); read the '
            'screenshot.'
        )
    if (
        len([d for d in descriptors if (d.get('label') or '').startswith(name)])
        > 1
    ):
        out['caveat'] = (
            'several rows match '
            + repr(name)
            + ' -- selected the first non-current one'
        )

    # --- select the row: act, verify, fall back ---------------------
    idx = row['i']
    row_at = (
        'var b=document.querySelectorAll('
        '"button.full-page-account-switcher-account-details")['
        + str(idx)
        + '];'
    )
    selected_expr = row_at + (
        'return !!b&&b.className.indexOf("account-selected")>=0;'
    )
    for method in ('js-click', 'coordinate-click'):
        if method == 'js-click':
            js(row_at + 'if(b)b.click();')
        else:
            js(row_at + 'if(b)b.scrollIntoView({block:"center"});')
            box = json.loads(
                js(
                    row_at + 'if(!b)return null;'
                    'var r=b.getBoundingClientRect();'
                    'return JSON.stringify({x:Math.round(r.x+r.width/2),'
                    'y:Math.round(r.y+r.height/2)});'
                )
                or 'null'
            )
            if box:
                click_at_xy(box['x'], box['y'])
        if _poll(selected_expr, tries=8):
            out['row_method'] = method
            break
    if not out['row_method']:
        capture_screenshot()
        _finish(
            f'clicking the {row["label"]!r} row never marked it '
            'selected (tried JS click then a coordinate click); read '
            'the screenshot.'
        )
    if why == 'current-only':
        out['caveat'] = (
            "the only matching row is the switcher's current row "
            'while /home renders '
            + str(out['marketplace'])
            + ' -- confirming it anyway'
        )

    # --- confirm: act, verify, fall back ----------------------------
    # The confirm is a kat-button custom element; on some accounts it
    # responds only to a JS click on the inner <button> in its SHADOW
    # ROOT, on others only to a real coordinate click.
    confirmed_expr = (
        'return (location.href||"").indexOf("account-switcher")<0||'
        '!(document.querySelectorAll('
        '"button.full-page-account-switcher-account-details").length>0);'
    )

    def _confirm_attempt(method):
        if method == 'shadow-js':
            js(
                'var k=document.querySelector('
                '"kat-button[data-test=confirm-selection]");'
                'if(k&&k.shadowRoot){var b=k.shadowRoot.querySelector('
                '"button");if(b){b.click();return 1;}}return 0;'
            )
        elif method == 'host-js':
            js(
                'var k=document.querySelector('
                '"kat-button[data-test=confirm-selection]");'
                'if(k){k.click();return 1;}return 0;'
            )
        else:
            box = json.loads(
                js(
                    'var k=document.querySelector('
                    '"kat-button[data-test=confirm-selection]");'
                    'if(!k)return null;'
                    'k.scrollIntoView({block:"center"});'
                    'var r=k.getBoundingClientRect();'
                    'return JSON.stringify({x:Math.round(r.x+r.width/2),'
                    'y:Math.round(r.y+r.height/2)});'
                )
                or 'null'
            )
            if not box:
                return False
            click_at_xy(box['x'], box['y'])
        return bool(_poll(confirmed_expr, tries=12))

    for method in ('shadow-js', 'host-js', 'coordinate-click'):
        if _confirm_attempt(method):
            out['confirm_method'] = method
            break
    if not out['confirm_method']:
        capture_screenshot()
        _finish(
            'no confirm click moved the page off the switcher (tried '
            'the shadow-root JS click, the host JS click, and a '
            'coordinate click); read the screenshot.'
        )

    # --- verify on an ordinary page ---------------------------------
    # Shorter bounce budget: the session is warm now and the wrapper
    # alarms at 120 s -- a still-bouncing page past this is reported
    # honestly, not waited out forever.
    out['marketplace'] = _session_marketplace(sso_settle=25)
    out['ok'] = out['marketplace'] == mid
    if not out['ok']:
        capture_screenshot()
        _finish(
            f'the session is still on {out["marketplace"]} after '
            f'confirming {row["label"]}; read the screenshot.'
        )
    _finish()


# The code is exec()'d inside browser_harness's globals (run.py), where
# the harness helpers exist; a plain `import` (unit tests) defines the
# pure functions above and runs nothing.
if callable(globals().get('js')) and callable(globals().get('new_tab')):
    _main()
