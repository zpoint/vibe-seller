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

from app.browser import wrapper as wrapper_mod
from app.browser.manager import warn_on_browser_use_version_mismatch
from app.browser.wrapper import (
    WRAPPER_FORMAT_MARKER,
    WRAPPER_FORMAT_VERSION,
    write_browser_use_wrapper,
)

_wipe_generated_wrappers = wrapper_mod.wipe_generated_wrappers

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

        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
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

        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
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
            'app.browser.wrapper._BIN_DIR', tmp_path / 'missing'
        ):
            assert _wipe_generated_wrappers() == 0


class TestWipeOrphanedWrappers:
    """A wrapper must not outlive the store it was generated for.

    Version-only reaping kept current-format wrappers forever, including
    ones for deleted stores. Those hold a FROZEN proxy_port, and ports
    are re-allocated from the base every boot — so the number eventually
    lands on a live store. The orphan then finds the port already up,
    skips the start API (its dead store_id never 404s) and points
    BU_CDP_WS at another store's browser: wrong account, wrong downloads
    dir. Same cross-store hole wrapper v3 closed for aux sessions.
    """

    @staticmethod
    def _write_current(bin_dir: Path, slug: str, store_id: str) -> Path:
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(slug, 'ziniao', 9227, store_id=store_id)
        return bin_dir / slug / 'browser-use'

    def test_orphan_reaped_live_store_kept(self, tmp_path: Path):
        bin_dir = tmp_path / 'bin'
        live = self._write_current(bin_dir, 'live-store', 'store-live')
        orphan = self._write_current(bin_dir, 'dead-store', 'store-dead')
        assert f'{WRAPPER_FORMAT_MARKER} {WRAPPER_FORMAT_VERSION}' in (
            orphan.read_text()
        )  # current format — version alone would never reap it

        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            removed = _wipe_generated_wrappers({'store-live'})

        assert removed == 1
        assert live.exists(), 'live store wrapper must survive a restart'
        assert not orphan.exists(), 'deleted store wrapper must be reaped'
        assert not orphan.parent.exists(), 'empty dir should go too'

    def test_no_live_ids_means_version_only(self, tmp_path: Path):
        """Omitting the arg keeps the old behaviour — callers without a
        DB must never trigger a mass orphan-reap."""
        bin_dir = tmp_path / 'bin'
        w = self._write_current(bin_dir, 'some-store', 'store-x')
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            assert _wipe_generated_wrappers() == 0
        assert w.exists()

    def test_empty_live_set_never_reaps(self, tmp_path: Path):
        """No stores loaded reads as 'wrong/unloaded DB', not 'delete
        everything'. Guard against a caller handing us an empty set
        while a real task is using those wrappers."""
        bin_dir = tmp_path / 'bin'
        w = self._write_current(bin_dir, 'live-store', 'store-live')
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            assert _wipe_generated_wrappers(set()) == 0
        assert w.exists()

    def test_unattributable_wrapper_is_kept(self, tmp_path: Path):
        """No parseable store id (e.g. the store-less `_web` wrapper) →
        fail safe and keep it, even with an empty live set."""
        bin_dir = tmp_path / 'bin'
        d = bin_dir / '_web'
        d.mkdir(parents=True)
        w = d / 'browser-use'
        w.write_text(
            '#!/usr/bin/env bash\n'
            '# Auto-generated browser-use wrapper for store: _web\n'
            f'# {WRAPPER_FORMAT_MARKER} {WRAPPER_FORMAT_VERSION}\n'
            'curl http://127.0.0.1:7777/api/browser/web/start\n'
        )
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            assert _wipe_generated_wrappers(set()) == 0
        assert w.exists()


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


def _write_versioned_wrapper(bin_dir: Path, slug: str, version) -> Path:
    """Auto-generated wrapper carrying (or missing) a format-version tag.
    version=None → unmarked (pre-versioning, treated as 0)."""
    d = bin_dir / slug
    d.mkdir(parents=True, exist_ok=True)
    w = d / 'browser-use'
    tag = '' if version is None else f'# {WRAPPER_FORMAT_MARKER} {version}\n'
    w.write_text(
        '#!/usr/bin/env bash\n'
        f'# Auto-generated browser-use wrapper for store: {slug}\n'
        f'{tag}exec "$REAL_BU" "$@"\n'
    )
    return w


class TestVersionAwareWipe:
    """Failure point 1 (mid-restart): boot must delete only OUTDATED
    wrappers — never the current version's own (else there's a
    wrapper-less window) and never a newer version's (rollback safety)."""

    def test_deletes_lower_keeps_current_and_future(self, tmp_path: Path):
        bin_dir = tmp_path / 'bin'
        older = _write_versioned_wrapper(
            bin_dir, 's-old', WRAPPER_FORMAT_VERSION - 1
        )
        unmarked = _write_versioned_wrapper(bin_dir, 's-unmarked', None)
        current = _write_versioned_wrapper(
            bin_dir, 's-cur', WRAPPER_FORMAT_VERSION
        )
        future = _write_versioned_wrapper(
            bin_dir, 's-future', WRAPPER_FORMAT_VERSION + 1
        )
        ud = bin_dir / 's-user'
        ud.mkdir(parents=True)
        user = ud / 'browser-use'
        user.write_text('#!/usr/bin/env bash\necho hi\n')

        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            removed = _wipe_generated_wrappers()

        assert not older.exists(), 'older-version wrapper must be wiped'
        assert not unmarked.exists(), 'unmarked (pre-versioning) wiped'
        assert current.exists(), 'current wrapper must survive (no gap)'
        assert future.exists(), 'newer wrapper untouched (rollback safety)'
        assert user.exists(), 'user wrapper untouched'
        assert removed == 2

    def test_freshly_generated_wrapper_survives_boot(self, tmp_path: Path):
        """A wrapper written by the CURRENT generator carries the current
        format version and must NOT be wiped on the next boot."""
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper('acme', 'ziniao', 9222, store_id='s1')
        w = bin_dir / 'acme' / 'browser-use'
        assert (
            f'{WRAPPER_FORMAT_MARKER} {WRAPPER_FORMAT_VERSION}' in w.read_text()
        )
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            removed = _wipe_generated_wrappers()
        assert removed == 0
        assert w.exists(), 'current generated wrapper must survive boot'
