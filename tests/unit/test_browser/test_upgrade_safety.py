"""In-place upgrade safety for the browser-use 0.12 → 0.13 migration.

Covers docs/browser-use-0.13-migration.md §8.4:
  (a) stale wrapper scripts are wiped on boot and regenerated to the
      0.13 shape on the next task launch,
  (c) a browser-use version mismatch is surfaced loudly at boot.
"""

from importlib.metadata import PackageNotFoundError
import logging
from pathlib import Path
from unittest import mock

import pytest

from app.browser import manager
from app.browser.manager import warn_on_browser_use_version_mismatch
from app.browser.wrapper import write_browser_use_wrapper

_wipe_generated_wrappers = manager._wipe_generated_wrappers

pytestmark = pytest.mark.unit


def _write_legacy_wrapper(bin_dir: Path, slug: str) -> Path:
    """Simulate a pre-0.13 (0.12-shaped) auto-generated wrapper left on
    disk by a previous version."""
    d = bin_dir / slug
    d.mkdir(parents=True)
    w = d / 'browser-use'
    w.write_text(
        '#!/usr/bin/env bash\n'
        f'# Auto-generated browser-use wrapper for store: {slug}\n'
        'exec "$REAL_BU" --session "$SESSION" --cdp-url "$WS" "$@"\n'
    )
    return w


class TestWipeStaleWrappers:
    def test_wipes_generated_leaves_user_created(self, tmp_path: Path):
        bin_dir = tmp_path / 'bin'
        legacy = _write_legacy_wrapper(bin_dir, 'acme-store')
        # A user-created wrapper WITHOUT our header must be preserved.
        user_dir = bin_dir / 'my-custom'
        user_dir.mkdir(parents=True)
        user_wrapper = user_dir / 'browser-use'
        user_wrapper.write_text('#!/usr/bin/env bash\necho custom\n')

        with mock.patch('app.browser.manager.BROWSER_USE_BIN_DIR', bin_dir):
            removed = _wipe_generated_wrappers()

        assert removed == 1
        assert not legacy.exists(), 'stale generated wrapper must be wiped'
        assert user_wrapper.exists(), 'user wrapper must be preserved'

    def test_regenerates_013_shape_after_wipe(self, tmp_path: Path):
        """After a wipe, the next task launch regenerates a 0.13-shaped
        wrapper (env injection, not --cdp-url/--session flags)."""
        bin_dir = tmp_path / 'bin'
        legacy = _write_legacy_wrapper(bin_dir, 'acme-store')
        assert '--cdp-url "$WS"' in legacy.read_text()  # old shape

        with mock.patch('app.browser.manager.BROWSER_USE_BIN_DIR', bin_dir):
            _wipe_generated_wrappers()

        # Regenerate via the real generator (write_task_browser_config's
        # per-launch path calls this).
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'acme-store', 'ziniao', 9222, store_id='s1'
            )

        content = legacy.read_text()
        assert 'export BU_NAME="$SESSION"' in content
        assert 'export BU_CDP_WS="ws://' in content
        assert '--cdp-url "$WS"' not in content  # old injection gone

    def test_no_bin_dir_is_noop(self, tmp_path: Path):
        with mock.patch(
            'app.browser.manager.BROWSER_USE_BIN_DIR', tmp_path / 'missing'
        ):
            assert _wipe_generated_wrappers() == 0


class TestVersionAssertion:
    def test_warns_on_old_version(self, caplog):
        with mock.patch('app.browser.manager.version', return_value='0.12.6'):
            with caplog.at_level(logging.ERROR):
                ver = warn_on_browser_use_version_mismatch()
        assert ver == '0.12.6'
        assert any('too old' in r.message for r in caplog.records)

    def test_ok_on_new_version(self, caplog):
        with mock.patch('app.browser.manager.version', return_value='0.13.3'):
            with caplog.at_level(logging.ERROR):
                ver = warn_on_browser_use_version_mismatch()
        assert ver == '0.13.3'
        assert not any('too old' in r.message for r in caplog.records)

    def test_missing_package_is_handled(self, caplog):
        with mock.patch(
            'app.browser.manager.version',
            side_effect=PackageNotFoundError('browser-use'),
        ):
            with caplog.at_level(logging.ERROR):
                ver = warn_on_browser_use_version_mismatch()
        assert ver is None
        assert any('not installed' in r.message for r in caplog.records)
