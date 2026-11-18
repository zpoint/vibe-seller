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

    Returns ``'OK:<idx>'`` (clicked), ``'ALREADYOPEN:<label>'`` (the store
    is already open, so no fresh debug-port kernel will spawn), or
    ``'NOTFOUND'``.
    """
    name = json.dumps(store_name)
    return (
        '(function(name){'
        "var rows=Array.from(document.querySelectorAll('.el-table__row'))"
        ".filter(function(r){var h=(r.innerText||'').trim().split('\\n')[0]"
        '.trim();return /^[0-9]+$/.test(h);});'
        'var idx=rows.findIndex(function(r){'
        "return (r.innerText||'').includes(name);});"
        "if(idx<0)return 'NOTFOUND';"
        'var actionRows=Array.from(document.querySelectorAll('
        "'.el-table__fixed-right .el-table__row'));"
        "var cell=actionRows[idx];if(!cell)return 'NOTFOUND';"
        "var btn=Array.from(cell.querySelectorAll('button')).find("
        "function(b){return (b.innerText||'').trim().length>0;});"
        "if(!btn)return 'NOTFOUND';"
        "var label=(btn.innerText||'').trim();"
        "if(label!=='Open')return 'ALREADYOPEN:'+label;"
        "btn.click();return 'OK:'+idx;"
        '})(' + name + ')'
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
