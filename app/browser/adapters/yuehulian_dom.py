"""Pure DOM logic for the Yuehulian renderer (no I/O — unit-testable).

The client's store list is a Vue/Element-UI page in the Electron
renderer. We read and drive it through the Node inspector via
``webContents.executeJavaScript`` (UIA is a dead end — the renderer ships
with accessibility off). This module holds the JS we inject and the pure
parsers for what it returns, so the parsing is covered in CI against a
fixture captured from the real client; the inspector I/O lives in the
Windows layer.

Verified live (2026-11-18): the el-table renders fixed-column duplicate
rows, so a 2-store list yields 4 ``.el-table__row`` nodes — two data rows
(``<seq>\\n<name>\\n<platform>\\n<marketplace>\\n...``) and two
operation-only rows (just ``Open``). Data rows start with a numeric seq;
that is how we separate them.
"""

import json

from app.browser.inject.client import StoreRef

# Returns each .el-table__row's innerText (newline-joined cell text).
# Data rows start with the numeric sequence column; fixed-column
# operation rows are just "Open" and get filtered out by the parser.
SCRAPE_STORES_JS = (
    "Array.from(document.querySelectorAll('.el-table__row'))"
    ".map(function(r){return (r.innerText||'').trim();})"
)

# Route hash of the logged-in main view (verified '#/index'); the login
# screen is a different route. Read through the renderer.
LOGIN_HASH_JS = 'location.hash'

# Show the store-list VIEW by CLICKING its sidebar menu item. A logged-in
# client can sit on another view (Personal Center, a store env, ...) where
# the el-table is not mounted, so a scrape finds zero rows and wrongly reads
# as "no stores" / StoreNotFound. The hash is NOT reliable here: the app is
# a tags-view (tab) shell whose hash stays '#/index' even while Personal
# Center is the active tab (verified live) — so setting the hash does
# nothing; the menu click switches the tab and mounts the list. Target the
# store-list route link (href='#/index') first — language-independent — and
# fall back to the menu item by its known labels. Returns 'OK' / 'NOTFOUND'.
NAV_TO_STORE_LIST_JS = (
    '(function(){'
    'var link=document.querySelector(\'.el-menu a[href="#/index"]\')'
    '||document.querySelector(\'a[href="#/index"]\');'
    "if(link){link.click();return 'OK';}"
    "var labels=['店铺列表','Shop List','Store List'];"
    "var it=Array.from(document.querySelectorAll('li.el-menu-item')).find("
    "function(e){return labels.indexOf((e.textContent||'').trim())>=0;});"
    "if(it){it.click();return 'OK';}"
    "return 'NOTFOUND';"
    '})()'
)


# The data row of the store NAMED exactly ``name`` (one of its cells reads
# exactly the name — never a substring: 'test' must not match 'test-2'),
# among the data rows (first line = numeric seq). Leaves ``rows`` and
# ``idx`` (−1 when absent) in scope; the operation rows of the
# fixed-right table line up with ``rows`` by index.
_ROW_IDX_JS = (
    "var rows=Array.from(document.querySelectorAll('.el-table__row'))"
    ".filter(function(r){var h=(r.innerText||'').trim().split('\\n')[0]"
    '.trim();return /^[0-9]+$/.test(h);});'
    'var idx=rows.findIndex(function(r){'
    "return Array.from(r.querySelectorAll('td')).some(function(td){"
    "return (td.innerText||'').trim()===name;});});"
)

# The store row's primary action button (first one with text): 'Open'
# when the env is closed, 'Opened' when the client thinks it is open.
_ACTION_BTN_JS = (
    'var actionRows=Array.from(document.querySelectorAll('
    "'.el-table__fixed-right .el-table__row'));"
    'var cell=idx<0?null:actionRows[idx];'
    "var btn=cell?Array.from(cell.querySelectorAll('button')).find("
    "function(b){return (b.innerText||'').trim().length>0;}):null;"
)


# A data row's selection box.
_BOX_JS = "function box(r){return r.querySelector('.el-checkbox__input');}"


def click_open_js(store_name: str) -> str:
    """JS that clicks the Open action on the row matching ``store_name``.

    Element-UI renders the action column as a parallel
    ``.el-table__fixed-right`` table — one row per data row, in the same
    order. We scope to the matched store's OWN action row and click its
    primary button (the first with non-empty text; the others are empty
    icon buttons). That button reads ``'Open'`` when the store is closed
    and ``'Opened'`` when it is already open.

    Matching per row — not a global ``=== 'Open'`` list indexed by
    position — stays correct when stores are in mixed states (one open,
    one closed). The global list drops the already-open store's button, so
    the Nth entry no longer lines up with the Nth data row and the click
    lands on the wrong store. That case is real: two concurrent tasks
    open two stores, so the second one always sees the first as 'Opened'.
    The row is the one with a cell reading exactly the name.

    Returns ``'OK:<idx>'`` (clicked), ``'ALREADYOPEN:<label>'`` (the store
    is already open, so no fresh debug-port kernel will spawn), or
    ``'NOTFOUND'``.
    """
    name = json.dumps(store_name)
    return (
        '(function(name){'
        + _ROW_IDX_JS
        + "if(idx<0)return 'NOTFOUND';"
        + _ACTION_BTN_JS
        + "if(!btn)return 'NOTFOUND';"
        "var label=(btn.innerText||'').trim();"
        "if(label!=='Open')return 'ALREADYOPEN:'+label;"
        "btn.click();return 'OK:'+idx;"
        '})(' + name + ')'
    )


def row_label_js(store_name: str) -> str:
    """JS returning the store row's action label ('Open' / 'Opened' / …),
    or ``'NOTFOUND'``."""
    return (
        '(function(name){'
        + _ROW_IDX_JS
        + _ACTION_BTN_JS
        + "return btn?(btn.innerText||'').trim():'NOTFOUND';"
        '})(' + json.dumps(store_name) + ')'
    )


def select_only_row_js(store_name: str) -> str:
    """JS ticking the selection box of ``store_name``'s row ONLY: every
    other ticked row is unticked. ``'CLICKED:<idx>'`` / ``'NOTFOUND'``.

    Element-UI re-renders the boxes asynchronously, so the result must be
    read back with :func:`selected_only_js` afterwards — a read in the
    same tick still sees the old state.
    """
    return (
        '(function(name){'
        + _ROW_IDX_JS
        + "if(idx<0)return 'NOTFOUND';"
        + _BOX_JS
        + 'rows.forEach(function(r,i){var b=box(r);if(!b)return;'
        "var on=b.classList.contains('is-checked');"
        'if(on!==(i===idx))b.click();});'
        "return 'CLICKED:'+idx;"
        '})(' + json.dumps(store_name) + ')'
    )


def selected_only_js(store_name: str) -> str:
    """JS reading the selection back: ``'OK:<idx>'`` only when exactly
    ``store_name``'s row is ticked, else ``'BADSELECT'`` / ``'NOTFOUND'``.
    A batch action must never reach another store's env."""
    return (
        '(function(name){'
        + _ROW_IDX_JS
        + "if(idx<0)return 'NOTFOUND';"
        + _BOX_JS
        + 'var on=rows.map(function(r){var b=box(r);'
        "return !!(b&&b.classList.contains('is-checked'));});"
        'var n=on.filter(Boolean).length;'
        "return (n===1&&on[idx])?'OK:'+idx:'BADSELECT';"
        '})(' + json.dumps(store_name) + ')'
    )


# Untick every selected row (leave the list as we found it).
CLEAR_SELECTION_JS = (
    '(function(){'
    "Array.from(document.querySelectorAll('.el-table__row "
    ".el-checkbox__input.is-checked')).forEach(function(b){b.click();});"
    "return 'OK';})()"
)

# Open the toolbar's "More Batch Operations" dropdown (it holds the
# client's own close-browser action). 'OK' / 'NOTRIGGER'.
OPEN_BATCH_MENU_JS = (
    '(function(){'
    "var t=Array.from(document.querySelectorAll('button.el-dropdown-selfdefine'))"
    '.find(function(b){return /More Batch Operations|更多批量操作/.test('
    "(b.innerText||'').trim());});"
    "if(!t)return 'NOTRIGGER';"
    "t.dispatchEvent(new MouseEvent('mouseenter',{bubbles:true}));"
    "t.click();return 'OK';})()"
)

# Click the visible "close browser" item of that dropdown — and nothing
# else: the same menu holds Delete / 解绑环境, so the match is on the close
# label only (the client ships it as the raw key 'brower.close_browser').
# 'CLICKED' / 'NOITEM:<labels>'.
CLICK_CLOSE_BROWSER_JS = (
    '(function(){'
    "var items=Array.from(document.querySelectorAll('.el-dropdown-menu__item'))"
    '.filter(function(i){return i.offsetParent;});'
    'var it=items.find(function(i){return '
    "/close_browser|关闭浏览器|close browser/i.test((i.innerText||'').trim());});"
    "if(!it)return 'NOITEM:'+items.map(function(i){"
    "return (i.innerText||'').trim();}).join('|');"
    "it.click();return 'CLICKED';})()"
)

# Confirm the client's "close the browser window?" dialog.
# 'CONFIRMED' / 'NODIALOG' / 'NOBTN'.
CONFIRM_DIALOG_JS = (
    '(function(){'
    "var box=Array.from(document.querySelectorAll('.el-message-box'))"
    '.find(function(b){return b.offsetParent;});'
    "if(!box)return 'NODIALOG';"
    "var b=Array.from(box.querySelectorAll('button')).find(function(x){"
    "return /^(Confirm|确定|确认|OK)$/.test((x.innerText||'').trim());});"
    "if(!b)return 'NOBTN';b.click();return 'CONFIRMED';})()"
)


# Click the store-list pagination's Next button when it is enabled. The
# el-table shows one page at a time (the user runs 5/page with >5 stores),
# so enumerate must page through to list every store and open must page to
# find a store on page 2/3. Returns 'OK' (advanced), 'END' (last page) or
# 'NONEXT' (no pager — single page).
CLICK_NEXT_PAGE_JS = (
    '(function(){'
    "var b=document.querySelector('.el-pagination button.btn-next')"
    "||document.querySelector('.el-pagination .btn-next');"
    "if(!b)return 'NONEXT';"
    "if(b.disabled||b.classList.contains('is-disabled'))return 'END';"
    "b.click();return 'OK';"
    '})()'
)


def fill_team_login_js(team: str, account: str, password: str) -> str:
    """JS that drives the client's 团队登录 (team login) form: select the tab,
    fill team name / sub-account / password, tick remember-password, submit.

    The fields are Element-UI inputs bound by v-model, so a plain
    ``el.value = x`` is ignored — set the value through the native setter and
    dispatch ``input``/``change`` so Vue's model updates. Fields are matched
    by placeholder substring (团队名称 / 子账号 / 密码) so a label tweak doesn't
    break it; the tab and submit button by their text. Returns 'OK', or
    'NOTAB' / 'NOFIELDS' / 'NOBTN' naming what was missing (so a UI change is
    pinpointed). The secret only lives in this one eval — never logged.
    """
    return (
        '(function(team,account,password){'
        'function setv(el,v){var s=Object.getOwnPropertyDescriptor('
        "window.HTMLInputElement.prototype,'value').set;s.call(el,v);"
        "el.dispatchEvent(new Event('input',{bubbles:true}));"
        "el.dispatchEvent(new Event('change',{bubbles:true}));}"
        'var tab=Array.from(document.querySelectorAll('
        "'.el-tabs__item,[role=tab],[class*=tab]')).find(function(e){"
        "return (e.textContent||'').trim()==='团队登录';});"
        "if(!tab)return 'NOTAB';tab.click();"
        "function f(ph){return Array.from(document.querySelectorAll('input'))"
        '.find(function(i){'
        "return (i.getAttribute('placeholder')||'').indexOf(ph)>=0;});}"
        "var t=f('团队名称'),a=f('子账号'),p=f('密码');"
        "if(!t||!a||!p)return 'NOFIELDS';"
        'setv(t,team);setv(a,account);setv(p,password);'
        "var rem=Array.from(document.querySelectorAll('.el-checkbox'))"
        ".find(function(c){return (c.textContent||'').indexOf('记住')>=0;});"
        "if(rem){var ri=rem.querySelector('input');"
        'if(ri&&!ri.checked)rem.click();}'
        "var btn=Array.from(document.querySelectorAll('button')).find("
        "function(b){return (b.textContent||'').indexOf('登录')>=0;});"
        "if(!btn)return 'NOBTN';btn.click();return 'OK';"
        '})('
        + json.dumps(team)
        + ','
        + json.dumps(account)
        + ','
        + json.dumps(password)
        + ')'
    )


def is_logged_in_hash(route_hash: str | None) -> bool:
    """True when the route hash is the logged-in app, not the login page.

    Conservative: the logged-in main view is ``#/index`` (verified). Any
    explicit login route reads as logged out; an empty/bare hash is
    treated as not-yet-ready → logged out. The exact logged-out route is
    confirmed by the logout e2e (E6/E7).
    """
    h = (route_hash or '').strip().lower()
    if not h or h in ('#', '#/'):
        return False
    return not (h.startswith('#/login') or 'login' in h or 'signin' in h)


def parse_store_list(rows: list[str]) -> list[StoreRef]:
    """Parse ``SCRAPE_STORES_JS`` output into store refs.

    Keeps only data rows (numeric seq first), drops the fixed-column
    operation duplicates. Field order (verified): seq, name, platform,
    marketplace, …, remote-env, created-date.
    """
    stores: list[StoreRef] = []
    for row in rows:
        toks = [t.strip() for t in (row or '').split('\n') if t.strip()]
        if len(toks) < 2 or not toks[0].isdigit():
            continue
        name = toks[1]
        platform = toks[2] if len(toks) > 2 else ''
        marketplace = toks[3] if len(toks) > 3 else ''
        stores.append(
            StoreRef(
                name=name,
                platform=platform,
                remote_env=marketplace,
            )
        )
    return stores


def parse_store_list_json(raw: str) -> list[StoreRef]:
    """Parse a JSON array of row strings (what the inspector returns)."""
    try:
        rows = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(rows, list):
        return []
    return parse_store_list([str(r) for r in rows])
