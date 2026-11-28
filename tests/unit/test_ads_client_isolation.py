"""Ads-service selection is safe without reloading module state."""

from __future__ import annotations

import pytest

from app import ads_client

pytestmark = pytest.mark.unit


@pytest.mark.parametrize('demo', ['1', 'true', 'TRUE', 'yes'])
def test_demo_defaults_to_the_local_service(demo):
    assert ads_client.resolve_service_url({'VIBE_DEMO_MODE': demo}) == (
        ads_client.DEMO_SERVICE_URL
    )


def test_production_needs_no_environment():
    assert ads_client.resolve_service_url({}) == (
        ads_client.PRODUCTION_SERVICE_URL
    )


def test_an_explicit_test_service_still_wins_in_demo_mode():
    override = 'http://127.0.0.1:18081/ads-api'
    assert (
        ads_client.resolve_service_url({
            'VIBE_DEMO_MODE': '1',
            'VIBE_ADS_SERVICE_URL': override,
        })
        == override
    )


def test_production_keeps_the_existing_explicit_override():
    override = 'https://ads.example/ads-api'
    assert (
        ads_client.resolve_service_url({'VIBE_ADS_SERVICE_URL': override})
        == override
    )


@pytest.mark.parametrize(
    'production_url',
    [
        ads_client.PRODUCTION_SERVICE_URL,
        ads_client.PRODUCTION_SERVICE_URL.upper(),
        ads_client.PRODUCTION_SERVICE_URL + '/another-path',
    ],
)
def test_demo_refuses_the_production_service(production_url):
    with pytest.raises(ValueError, match='cannot use the production'):
        ads_client.resolve_service_url({
            'VIBE_DEMO_MODE': '1',
            'VIBE_ADS_SERVICE_URL': production_url,
        })


def test_demo_allows_a_non_production_test_host():
    override = 'https://ads.example/ads-api'
    assert (
        ads_client.resolve_service_url({
            'VIBE_DEMO_MODE': 'true',
            'VIBE_ADS_SERVICE_URL': override,
        })
        == override
    )
