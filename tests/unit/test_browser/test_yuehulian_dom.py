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
    click_open_js,
    is_logged_in_hash,
    parse_store_list,
    parse_store_list_json,
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
