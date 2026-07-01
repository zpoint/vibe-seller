"""Tests for app.update_check — the web UI's update-available popup.

Network calls go through httpx.MockTransport, so these run offline
and on all platforms.
"""

import httpx
import pytest

import app.update_check as uc

pytestmark = pytest.mark.unit


class TestIsDevVersion:
    def test_dev_release_is_dev(self):
        assert uc.is_dev_version('0.1.0.dev3+gabc123')

    def test_local_segment_fallback_is_dev(self):
        assert uc.is_dev_version('0.0.0+dev')

    def test_plain_release_is_not_dev(self):
        assert not uc.is_dev_version('0.6.0')

    def test_unparseable_is_dev(self):
        assert uc.is_dev_version('not-a-version')


class TestIsNewer:
    def test_higher_beats_lower(self):
        assert uc.is_newer('0.0.2', '0.0.1')

    def test_lower_is_not_newer(self):
        assert not uc.is_newer('0.0.1', '0.0.2')

    def test_equal_is_not_newer(self):
        assert not uc.is_newer('0.0.1', '0.0.1')

    def test_unparseable_is_not_newer(self):
        assert not uc.is_newer('not-a-version', '0.0.1')


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestFetchLatestPypiVersion:
    @pytest.mark.asyncio
    async def test_parses_version_from_pypi_json(self):
        async def handler(request):
            assert request.url.path == '/pypi/vibe-seller/json'
            return httpx.Response(200, json={'info': {'version': '0.7.0'}})

        async with _client(handler) as client:
            assert await uc.fetch_latest_pypi_version(client) == '0.7.0'

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        async def handler(request):
            return httpx.Response(500)

        async with _client(handler) as client:
            assert await uc.fetch_latest_pypi_version(client) is None

    @pytest.mark.asyncio
    async def test_malformed_json_returns_none(self):
        async def handler(request):
            return httpx.Response(200, json={'unexpected': 'shape'})

        async with _client(handler) as client:
            assert await uc.fetch_latest_pypi_version(client) is None


_RELEASES = [
    {
        'tag_name': 'v0.7.0',
        'name': 'v0.7.0',
        'body': 'Adds thing.',
        'html_url': 'https://x/0.7.0',
        'published_at': '2026-06-01T00:00:00Z',
    },
    {
        'tag_name': 'v0.6.5',
        'name': 'v0.6.5',
        'body': 'Fixes thing.',
        'html_url': 'https://x/0.6.5',
        'published_at': '2026-05-01T00:00:00Z',
    },
    {
        'tag_name': 'v0.6.0',
        'name': 'v0.6.0',
        'body': 'Old.',
        'html_url': 'https://x/0.6.0',
        'published_at': '2026-04-01T00:00:00Z',
    },
]


class TestFetchReleaseNotes:
    @pytest.mark.asyncio
    async def test_only_returns_releases_newer_than_current(self):
        async def handler(request):
            return httpx.Response(200, json=_RELEASES)

        async with _client(handler) as client:
            notes = await uc.fetch_release_notes(client, '0.6.0')
        assert [n['version'] for n in notes] == ['0.7.0', '0.6.5']

    @pytest.mark.asyncio
    async def test_caps_at_max_releases(self, monkeypatch):
        monkeypatch.setattr(uc, '_MAX_RELEASES', 1)

        async def handler(request):
            return httpx.Response(200, json=_RELEASES)

        async with _client(handler) as client:
            notes = await uc.fetch_release_notes(client, '0.0.0')
        assert [n['version'] for n in notes] == ['0.7.0']

    @pytest.mark.asyncio
    async def test_http_error_returns_empty_list(self):
        async def handler(request):
            return httpx.Response(500)

        async with _client(handler) as client:
            assert await uc.fetch_release_notes(client, '0.6.0') == []
