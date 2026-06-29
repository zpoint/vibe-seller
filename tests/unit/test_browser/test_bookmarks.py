"""Tests for read_bookmarks() across the two profile locations the
project actually writes to.

These tests exist because a previous refactor deleted the
``~/.vibe-seller/browser_profiles/`` lookup based on a stale
"Legacy profile" comment in the source — even though the Chrome
backend (``app/browser/chrome.py``) and stores router still actively
write there. Nothing in CI caught it.

Coverage:
- bookmark file in the browser-use config dir → returned
- bookmark file in vibe-seller browser_profiles dir → returned
- both present → first match wins (browser-use takes precedence)
- neither present → empty list
"""

import json
from pathlib import Path

import pytest

from app.browser import bookmarks as bookmarks_mod
from app.browser.bookmarks import read_bookmarks

pytestmark = pytest.mark.unit


def _write_bookmarks(profile_root: Path, slug: str, items: list[dict]) -> None:
    """Drop a Chrome-format Bookmarks JSON into <root>/<slug>/Default/."""
    target = profile_root / slug / 'Default' / 'Bookmarks'
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        'roots': {
            'bookmark_bar': {
                'children': [
                    {
                        'type': 'url',
                        'name': item['name'],
                        'url': item['url'],
                    }
                    for item in items
                ],
            },
        },
    }
    target.write_text(json.dumps(payload))


@pytest.fixture
def profile_dirs(tmp_path, monkeypatch):
    """Redirect both bookmark-source paths into tmp dirs."""
    bu_root = tmp_path / 'browseruse-profiles'
    vibe_root = tmp_path / 'vibe-browser-profiles'
    monkeypatch.setattr(bookmarks_mod, '_BROWSER_USE_PROFILES_DIR', bu_root)
    monkeypatch.setattr(bookmarks_mod, '_VIBE_PROFILES_DIR', vibe_root)
    return bu_root, vibe_root


def test_returns_browseruse_profile_bookmarks(profile_dirs):
    bu_root, _ = profile_dirs
    _write_bookmarks(
        bu_root,
        'demo-alpha',
        [{'name': 'Seller Central', 'url': 'https://sellercentral.amazon.com'}],
    )

    result = read_bookmarks('demo-alpha')

    assert result == [
        {'name': 'Seller Central', 'url': 'https://sellercentral.amazon.com'}
    ]


def test_returns_vibe_seller_profile_bookmarks(profile_dirs):
    """Regression: this is the Chrome backend's persistent profile
    location. Deleting it silently broke Chrome-store bookmarks.
    """
    _, vibe_root = profile_dirs
    _write_bookmarks(
        vibe_root,
        'demo-beta',
        [{'name': 'Noon Seller Lab', 'url': 'https://sellerlab.noon.com'}],
    )

    result = read_bookmarks('demo-beta')

    assert result == [
        {'name': 'Noon Seller Lab', 'url': 'https://sellerlab.noon.com'}
    ]


def test_browseruse_takes_precedence_when_both_exist(profile_dirs):
    bu_root, vibe_root = profile_dirs
    _write_bookmarks(
        bu_root,
        'demo-alpha',
        [{'name': 'browser-use one', 'url': 'https://example.com/bu'}],
    )
    _write_bookmarks(
        vibe_root,
        'demo-alpha',
        [{'name': 'vibe-seller one', 'url': 'https://example.com/vs'}],
    )

    result = read_bookmarks('demo-alpha')

    assert result == [
        {'name': 'browser-use one', 'url': 'https://example.com/bu'}
    ]


def test_returns_empty_when_no_profile_exists(profile_dirs):
    assert read_bookmarks('store-with-no-profile-at-all') == []
