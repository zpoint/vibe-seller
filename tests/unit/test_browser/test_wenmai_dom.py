"""Pure DOM logic for the wenmai adapter: login-form JS + hash parsing."""

import pytest

from app.browser.adapters.wenmai_dom import (
    CLICK_OPEN_STORE_JS,
    LOGIN_HASH_JS,
    fill_enterprise_login_js,
    is_logged_in_hash,
)

pytestmark = pytest.mark.unit


def test_is_logged_in_hash_login_page_is_logged_out():
    assert is_logged_in_hash('#/login') is False


def test_is_logged_in_hash_any_other_route_is_logged_in():
    # Verified live: a successful login leaves #/login (it went to
    # #/guide); the app proper is any non-login hash.
    assert is_logged_in_hash('#/guide') is True
    assert is_logged_in_hash('#/index') is True


def test_is_logged_in_hash_negative_control_empty_is_logged_out():
    # A window still loading is not logged in (conservative default).
    assert is_logged_in_hash('') is False
    assert is_logged_in_hash(None) is False


def test_fill_enterprise_login_js_finds_fields_by_placeholder():
    js = fill_enterprise_login_js('acme', 'user-1', 'pw-1')
    for needle in (
        '#tab-enterprise',
        '企业名称',
        '企业用户名',
        '企业密码',
        'HTMLInputElement',
    ):
        assert needle in js
    # The credentials ride inside the one eval, JSON-quoted.
    assert 'acme' in js and 'user-1' in js and 'pw-1' in js


def test_fill_enterprise_login_js_negative_control_no_phone_tab():
    # The phone tab has an image captcha — the JS must NOT reference it.
    js = fill_enterprise_login_js('a', 'b', 'c')
    for needle in ('手机', 'phone', '验证码', 'captcha'):
        assert needle not in js


def test_click_open_store_js_clicks_the_primary_button():
    assert 'button.btn-primary' in CLICK_OPEN_STORE_JS
    assert 'NOBTN' in CLICK_OPEN_STORE_JS  # a missing button is named


def test_login_hash_js_is_just_the_hash():
    assert LOGIN_HASH_JS == 'location.hash'
