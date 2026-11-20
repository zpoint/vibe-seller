"""The live question-park e2e's fake back office serves what it claims."""

import httpx
import pytest

from tests.e2e.fake_seller_shipping import FakeSellerShipping

pytestmark = pytest.mark.unit


@pytest.fixture
def fake():
    f = FakeSellerShipping().start()
    yield f
    f.stop()


def test_shipping_page_offers_two_options(fake):
    r = httpx.get(f'{fake.base_url}/orders/ORD-1001/shipping')
    assert r.status_code == 200
    assert 'Select Standard' in r.text
    assert 'Select Express' in r.text


def test_select_returns_the_options_code_and_is_logged(fake):
    r = httpx.post(
        f'{fake.base_url}/orders/ORD-1001/shipping/select',
        data={'option': 'express'},
    )
    assert fake.code_for('express') in r.text
    assert [s['body'] for s in fake.selections()] == ['option=express']


def test_status_page_shows_its_code(fake):
    r = httpx.get(f'{fake.base_url}/status')
    assert fake.status_code in r.text


def test_reloading_the_confirmation_url_is_not_a_selection(fake):
    httpx.post(
        f'{fake.base_url}/orders/ORD-1001/shipping/select',
        data={'option': 'express'},
    )
    httpx.get(f'{fake.base_url}/orders/ORD-1001/shipping/select')
    assert [s['body'] for s in fake.selections()] == ['option=express']
