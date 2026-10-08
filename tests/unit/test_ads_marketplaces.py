"""Writing an authorization's marketplaces back onto a store.

The lists a store carries were typed by a person or guessed by an AI; an
authorized Ads profile is Amazon's own word. So the profile adds — and
nothing is ever taken away.
"""

from __future__ import annotations

import json

import pytest

from app.ads_client import record_marketplaces
from app.models.store import Store

pytestmark = pytest.mark.unit


def _store(platforms='["amazon"]', countries='["US"]', by_platform='{}'):
    return Store(
        name='shop',
        platforms=platforms,
        countries=countries,
        platform_countries=by_platform,
    )


def _lists(store):
    return (
        json.loads(store.platforms),
        json.loads(store.countries),
        json.loads(store.platform_countries),
    )


def test_amazon_and_its_marketplaces_are_added():
    store = _store(platforms='["noon"]', countries='["AE"]')
    assert record_marketplaces(store, ['SA', 'AE'])
    assert _lists(store) == (
        ['noon', 'amazon'],
        ['AE', 'SA'],
        {'amazon': ['AE', 'SA']},
    )


def test_nothing_already_there_is_removed_or_reordered():
    store = _store(
        platforms='["amazon", "noon"]',
        countries='["US", "SA"]',
        by_platform='{"amazon": ["US"], "noon": ["SA"]}',
    )
    record_marketplaces(store, ['SA'])
    assert _lists(store) == (
        ['amazon', 'noon'],
        ['US', 'SA'],
        {'amazon': ['US', 'SA'], 'noon': ['SA']},
    )


def test_a_repeat_changes_nothing():
    store = _store()
    assert record_marketplaces(store, ['SA'])
    snapshot = _lists(store)
    assert record_marketplaces(store, ['SA']) is False
    assert _lists(store) == snapshot


def test_codes_are_upper_cased():
    store = _store()
    record_marketplaces(store, ['ae'])
    assert 'AE' in json.loads(store.countries)


def test_no_marketplaces_means_no_change():
    store = _store()
    assert record_marketplaces(store, []) is False
    assert _lists(store) == (['amazon'], ['US'], {})


@pytest.mark.parametrize('broken', ['', 'not json', '{"a": 1}', 'null'])
def test_a_malformed_list_is_rebuilt_not_fatal(broken):
    store = _store(platforms=broken, countries=broken, by_platform=broken)
    assert record_marketplaces(store, ['SA'])
    platforms, countries, by_platform = _lists(store)
    assert 'amazon' in platforms
    assert 'SA' in countries
    assert by_platform['amazon'] == ['SA']
