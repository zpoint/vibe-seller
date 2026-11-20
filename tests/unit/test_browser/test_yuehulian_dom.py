"""Pure DOM parsing for the Yuehulian renderer.

Covers the parser against a fixture with the SAME structure captured from
the real client (el-table newline/tab cells + fixed-column duplicate
"Open" rows), with placeholder store names. Each assertion pairs with a
twist.
"""

import json
from pathlib import Path

import pytest

from app.browser.adapters.yuehulian_dom import (
    CLICK_CLOSE_BROWSER_JS,
    CLICK_NEXT_PAGE_JS,
    CONFIRM_DIALOG_JS,
    NAV_TO_STORE_LIST_JS,
    OPEN_BATCH_MENU_JS,
    click_open_js,
    fill_team_login_js,
    is_logged_in_hash,
    parse_store_list,
    parse_store_list_json,
    row_label_js,
    select_only_row_js,
)

pytestmark = pytest.mark.unit

_FIXTURE = Path(__file__).parent / 'fixtures' / 'yuehulian_store_list.json'


def test_parse_store_list_from_real_structure():
    rows = json.loads(_FIXTURE.read_text())
    stores = parse_store_list(rows)
    assert [s.name for s in stores] == ['store-a', 'store-b']
    assert stores[0].platform == 'MarketplaceX'
    assert stores[0].remote_env == 'RegionA'
    assert stores[1].remote_env == 'RegionB'


def test_parse_store_list_twist_operation_rows_yield_nothing():
    # Fixed-column 'Open' rows (and headers) have no numeric seq → dropped.
    assert parse_store_list(['Open', 'Open in Batches', '']) == []


def test_parse_store_list_json_bad_input_is_empty():
    assert parse_store_list_json('not json') == []
    assert parse_store_list_json('{"a": 1}') == []
    assert parse_store_list_json('[]') == []


def test_login_hash_logged_in():
    assert is_logged_in_hash('#/index') is True


def test_login_hash_twist_logged_out_signals():
    assert is_logged_in_hash('#/login') is False
    assert is_logged_in_hash('') is False
    assert is_logged_in_hash('#/') is False
    assert is_logged_in_hash(None) is False


def test_click_open_js_embeds_name_as_json():
    js = click_open_js('store-b')
    assert json.dumps('store-b') in js
    assert "querySelectorAll('.el-table__row')" in js


def test_click_open_js_scopes_to_the_stores_own_action_row():
    # The click must scope to the matched store's OWN fixed-right action
    # row, not a global list of 'Open' buttons indexed by position — that
    # list misaligns when stores are in mixed open/closed states and clicks
    # the wrong store. Guards against a regression back to the global match.
    js = click_open_js('store-a')
    assert '.el-table__fixed-right .el-table__row' in js
    # It must NOT build a global exact-'Open' button list and index it.
    assert "==='Open';})" not in js
    assert 'opens[idx]' not in js


def test_click_open_js_signals_open_already_open_and_not_found():
    # Three distinct outcomes the client classifier depends on.
    js = click_open_js('store-a')
    assert "'OK:'" in js
    assert "'ALREADYOPEN:'" in js
    assert "'NOTFOUND'" in js
    # Only the exact 'Open' label clicks; any other label is already-open.
    assert "label!=='Open'" in js


def test_click_open_js_twist_injection_is_escaped():
    # A quote in the name must be JSON-escaped, never injected raw.
    js = click_open_js('a"; alert(1); //')
    assert json.dumps('a"; alert(1); //') in js
    assert 'alert(1)' in js  # present, but only inside the JSON string
    assert ')(a";' not in js  # not breaking out of the string literal


def test_nav_to_store_list_clicks_menu_not_hash():
    # The store list is a tags-view tab, NOT identified by the hash (it
    # stays '#/index' under Personal Center), so nav must CLICK the menu
    # item (the #/index route link), never just set location.hash.
    assert '.click()' in NAV_TO_STORE_LIST_JS
    assert '#/index' in NAV_TO_STORE_LIST_JS
    assert 'location.hash =' not in NAV_TO_STORE_LIST_JS


def test_click_next_page_targets_pagination_next():
    assert 'el-pagination' in CLICK_NEXT_PAGE_JS
    assert 'btn-next' in CLICK_NEXT_PAGE_JS
    assert "'END'" in CLICK_NEXT_PAGE_JS and "'OK'" in CLICK_NEXT_PAGE_JS


def test_fill_team_login_js_drives_the_team_tab_form():
    js = fill_team_login_js('acme-team', 'sub-01', 'pw')
    # Selects the 团队登录 tab and matches the three fields by placeholder.
    assert '团队登录' in js
    assert '团队名称' in js and '子账号' in js and '密码' in js
    # Sets v-model via the native setter + input/change (plain .value is
    # ignored by Element-UI), ticks remember, and submits.
    assert 'HTMLInputElement.prototype' in js and "'input'" in js
    assert '记住' in js
    # Distinct outcomes so a UI change is pinpointed.
    for tok in ("'OK'", "'NOTAB'", "'NOFIELDS'", "'NOBTN'"):
        assert tok in js
    # The supplied values are embedded (JSON-encoded).
    assert json.dumps('acme-team') in js and json.dumps('sub-01') in js


def test_fill_team_login_js_twist_injection_is_escaped():
    # A quote in the password must be JSON-escaped, never break the string.
    js = fill_team_login_js('t', 'a', 'p"; alert(1); //')
    assert json.dumps('p"; alert(1); //') in js
    assert ',p"; alert' not in js  # not injected raw into the call


def test_rows_are_matched_by_the_exact_store_name():
    """'test' must never match 'test-2': the row picked is the one with a
    cell reading exactly the name — or a click/close lands on another
    store."""
    for js in (
        click_open_js('test'),
        row_label_js('test'),
        select_only_row_js('test'),
    ):
        assert "(td.innerText||'').trim()===name" in js
        assert '.includes(name)' not in js


def test_select_only_row_refuses_unless_exactly_that_row_is_ticked():
    js = select_only_row_js('store-a')
    assert "'BADSELECT'" in js and "'NOTFOUND'" in js
    assert 'n===1&&on[idx]' in js
    assert json.dumps('store-a') in js


def test_the_close_click_can_only_hit_the_close_browser_item():
    """The same dropdown holds Delete / 解绑环境 — the match must be on the
    close label alone."""
    assert 'close_browser' in CLICK_CLOSE_BROWSER_JS
    for dangerous in ('Delete', '删除', '解绑', 'Re-random'):
        assert dangerous not in CLICK_CLOSE_BROWSER_JS
    assert 'More Batch Operations' in OPEN_BATCH_MENU_JS
    assert "'CONFIRMED'" in CONFIRM_DIALOG_JS
