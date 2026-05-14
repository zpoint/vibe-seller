"""Read browser bookmarks from store profiles."""

import json
import logging
import os
from pathlib import Path
import platform

from app.config import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

# Two Chrome profile locations exist depending on which backend wrote
# them:
#   1. browser-use per-session profile (auxiliary Chrome from a Ziniao
#      store): ~/.config/browseruse/profiles/{slug}/
#   2. Chrome backend's persistent profile (Chrome-backed stores +
#      Ziniao aux sessions managed by stores.py):
#      ~/.vibe-seller/browser_profiles/{slug}/
#
# Check both — the right one wins by existence.
_BROWSER_USE_PROFILES_DIR = (
    Path(
        os.environ.get(
            'BROWSER_USE_CONFIG_DIR',
            Path(
                os.environ.get(
                    'XDG_CONFIG_HOME',
                    Path.home() / '.config',
                )
            )
            / 'browseruse',
        )
    )
    / 'profiles'
)
_VIBE_PROFILES_DIR = VIBE_SELLER_DIR / 'browser_profiles'


def read_bookmarks(store_slug: str) -> list[dict]:
    """Read Chrome bookmarks for a store. Returns ``[{name, url}]`` or
    an empty list if no Bookmarks file is found in either profile
    location."""
    for root in (_BROWSER_USE_PROFILES_DIR, _VIBE_PROFILES_DIR):
        path = root / store_slug / 'Default' / 'Bookmarks'
        if path.exists():
            return _parse_bookmarks_file(path, store_slug)
    return []


def read_ziniao_bookmarks(browser_oauth: str) -> list[dict]:
    """Read bookmarks from a Ziniao browser profile.

    NOTE: Ziniao encrypts its Bookmarks files on disk (not
    standard Chrome JSON), so this function always returns [].
    The ``browser_oauth`` token also doesn't match the
    directory naming pattern (``chrome_{numericId}_...``),
    so even locating the correct profile directory fails.

    Kept as a no-op for API compatibility
    (``stores.py`` calls it).

    Returns empty list.
    """
    logger.debug(
        'Ziniao bookmarks are encrypted; skipping filesystem read',
    )
    return []


def _ziniao_userdata_dir() -> Path | None:
    """Return Ziniao's local userdata directory for this OS."""
    system = platform.system()
    if system == 'Darwin':
        return (
            Path.home()
            / 'Library'
            / 'Application Support'
            / 'ziniaobrowser'
            / 'userdata'
        )
    # TODO: Windows support
    # Likely: Path.home() / 'AppData' / 'Roaming' / 'ziniaobrowser'
    #         / 'userdata'
    logger.debug('Ziniao bookmarks not supported on %s', system)
    return None


def _parse_bookmarks_file(bookmarks_file: Path, label: str) -> list[dict]:
    """Parse a Chrome-format Bookmarks JSON file."""
    if not bookmarks_file.exists():
        return []

    try:
        data = json.loads(bookmarks_file.read_text())
    except (json.JSONDecodeError, OSError) as e:
        logger.warning('Failed to read bookmarks for %s: %s', label, e)
        return []

    results: list[dict] = []

    def _walk(node: dict) -> None:
        if node.get('type') == 'url':
            results.append({
                'name': node.get('name', ''),
                'url': node.get('url', ''),
            })
        for child in node.get('children', []):
            _walk(child)

    for root in data.get('roots', {}).values():
        if isinstance(root, dict):
            _walk(root)

    return results
