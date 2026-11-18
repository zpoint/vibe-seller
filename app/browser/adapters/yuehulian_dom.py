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
    """JS that clicks the Open button on the row matching ``store_name``.

    Rows are filtered to data rows (numeric seq first line); the Nth data
    row's Open button is the Nth button whose text is exactly 'Open'
    (verified: "Open in Batches" is excluded, order matches rows).
    Returns ``'OK:<idx>'`` or ``'NOTFOUND'``.
    """
    name = json.dumps(store_name)
    return (
        '(function(name){'
        "var rows=Array.from(document.querySelectorAll('.el-table__row'))"
        ".filter(function(r){var h=(r.innerText||'').trim().split('\\n')[0]"
        '.trim();return /^[0-9]+$/.test(h);});'
        'var idx=rows.findIndex(function(r){'
        "return (r.innerText||'').includes(name);});"
        "var opens=Array.from(document.querySelectorAll('button'))"
        ".filter(function(b){return (b.innerText||'').trim()==='Open';});"
        "if(idx<0||!opens[idx])return 'NOTFOUND';"
        "opens[idx].click();return 'OK:'+idx;"
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
