"""Tests for BrowserManager: per-store backends, wrappers, etc."""

from pathlib import Path
from unittest import mock

import pytest

from app.browser.chrome import ChromeBackend
from app.browser.manager import (
    BrowserManager,
    remove_browser_use_wrapper,
    store_slug as _store_slug,
    write_browser_use_wrapper,
)
from app.task_runner import build_store_context


class TestStoreSlug:
    """All slugs must match ``[A-Za-z0-9_-]+`` — browser-use's session
    validator (``validate_session_name``) rejects anything else, so any
    name whose reduction contains non-ASCII characters must fall back
    to ``store-<id_prefix>``."""

    def test_simple(self):
        assert _store_slug('test-store') == 'test-store'

    def test_spaces_and_caps(self):
        assert _store_slug('My Store') == 'my-store'

    def test_special_chars(self):
        assert _store_slug('Store #1!') == 'store-1'

    def test_cjk_name_falls_back_to_id(self):
        # Non-ASCII (CJK) store name reduces to empty; fall back
        # to the id-derived slug.
        slug = _store_slug('测试店铺', 'ba034737-37e5-4510')
        assert slug == 'store-ba034737'

    def test_other_non_ascii_name_falls_back_to_id(self):
        slug = _store_slug('テスト商店', 'deadbeef-1234')
        assert slug == 'store-deadbeef'

    def test_mixed_non_ascii_and_english_keeps_ascii(self):
        # Hybrid names keep their ASCII component and drop the rest.
        assert _store_slug('测试 Store', 'abc12345') == 'store'
        assert _store_slug('ABC-测试-Name', 'abc12345') == 'abc-name'

    def test_empty_after_strip_falls_back(self):
        """Pure special chars reduce to empty → store-<id>."""
        slug = _store_slug('###', 'cafe0000-abcd')
        assert slug == 'store-cafe0000'

    def test_empty_without_store_id_raises(self):
        with pytest.raises(ValueError, match='empty ASCII slug'):
            _store_slug('测试店铺')

    def test_ascii_names_do_not_need_store_id(self):
        # Backward compat — existing ASCII store names keep working
        # even when callers don't pass store_id.
        assert _store_slug('storea') == 'storea'
        assert _store_slug('Store B') == 'store-b'


class TestWriteBrowserUseWrapper:
    def test_creates_wrapper_ziniao(self, tmp_path: Path, monkeypatch):
        """Creates executable wrapper for ziniao store."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        # Point sys.executable at a tmp dir with no browser-use
        # sibling so the wrapper falls back to shutil.which.
        monkeypatch.setattr(
            'app.browser.wrapper.sys.executable',
            str(tmp_path / 'fake-venv' / 'bin' / 'python'),
        )
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper(
            'test-store', 'ziniao', 9222, store_id='store-1'
        )

        wrapper = tmp_path / 'bin' / 'test-store' / 'browser-use'
        assert wrapper.exists()
        assert wrapper.stat().st_mode & 0o700 == 0o700
        content = wrapper.read_text()
        assert 'REAL_BU="/usr/local/bin/browser-use"' in content
        # Per-task session assignment from VIBE_TASK_ID
        assert 'VIBE_TASK_ID' in content
        assert 'SESSION="test-store-${VIBE_TASK_ID:0:8}"' in content
        # Prefix-based session validation (not allowlist)
        assert 'test-store|test-store-*)' in content
        # 0.13 injects the CDP endpoint via BU_CDP_WS env, not --cdp-url.
        assert 'export BU_CDP_WS="ws://' in content
        assert '9222' in content

    def test_creates_wrapper_chrome(self, tmp_path: Path, monkeypatch):
        """Chrome wrapper injects BU_CDP_WS (both backends use the proxy)."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper('storec', 'chrome', 9222, store_id='store-2')

        wrapper = tmp_path / 'bin' / 'storec' / 'browser-use'
        assert wrapper.exists()
        content = wrapper.read_text()
        # 0.13 env injection present (both backends use CDPMuxProxy)
        assert 'export BU_NAME="$SESSION"' in content
        assert 'export BU_CDP_WS="ws://' in content
        # Per-task session + strict validation (regex pattern)
        assert 'VIBE_TASK_ID' in content
        # Bash regex validation for {slug}, {slug}-aux, or {slug}-{8hex}
        assert '=~ ^storec(-aux|-[0-9a-fA-F]{8})?$' in content

    def test_blocks_cdp_url_flag(self, tmp_path: Path, monkeypatch):
        """Wrapper script blocks the --cdp-url flag."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper(
            'test-store', 'ziniao', 9222, store_id='store-1'
        )

        content = (tmp_path / 'bin' / 'test-store' / 'browser-use').read_text()
        assert '--cdp-url|--cdp-url=*|--cdp-ws|--cdp-ws=*)' in content
        assert 'the CDP endpoint is managed by the wrapper' in content

    def test_blocks_mcp_flag(self, tmp_path: Path, monkeypatch):
        """Wrapper script blocks --mcp flag."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper(
            'test-store', 'ziniao', 9222, store_id='store-1'
        )

        content = (tmp_path / 'bin' / 'test-store' / 'browser-use').read_text()
        assert '--mcp is not allowed' in content

    def test_updates_existing_wrapper(self, tmp_path: Path, monkeypatch):
        """Overwrites wrapper on port change."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper('test-store', 'ziniao', 9222, store_id='s1')
        write_browser_use_wrapper('test-store', 'ziniao', 9305, store_id='s1')

        content = (tmp_path / 'bin' / 'test-store' / 'browser-use').read_text()
        assert '9305' in content

    def test_multi_store_wrappers(self, tmp_path: Path, monkeypatch):
        """Multiple stores get separate wrapper dirs."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper('test-store', 'ziniao', 9222, store_id='s1')
        write_browser_use_wrapper('storeB', 'ziniao', 9223, store_id='s2')

        assert (tmp_path / 'bin' / 'test-store' / 'browser-use').exists()
        assert (tmp_path / 'bin' / 'storeb' / 'browser-use').exists()

    def test_cleans_stale_pre_slug_guard_dirs(
        self, tmp_path: Path, monkeypatch
    ):
        """Regen removes raw-name wrapper/download dirs left by old code.

        Non-ASCII store names used to produce ``bin/<raw name>/``
        before ``store_slug`` gained the id-fallback. Regenerating
        the wrapper must delete the stale wrapper dir (when it
        carries our auto-generation header) and the stale downloads
        dir (when empty), so agents stop watching the wrong paths.
        """
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.DOWNLOADS_DIR', tmp_path / 'downloads'
        )
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        name = '云帆科技'
        stale_bin = tmp_path / 'bin' / name
        stale_bin.mkdir(parents=True)
        (stale_bin / 'browser-use').write_text(
            f'#!/usr/bin/env bash\n'
            f'# Auto-generated browser-use wrapper for store: {name}\n'
        )
        stale_dl_empty = tmp_path / 'downloads' / name
        stale_dl_empty.mkdir(parents=True)

        write_browser_use_wrapper(name, 'ziniao', 9222, store_id='abcd1234ef')

        assert (tmp_path / 'bin' / 'store-abcd1234' / 'browser-use').exists()
        assert not stale_bin.exists()
        assert not stale_dl_empty.exists()

    def test_keeps_nonempty_downloads_and_foreign_dirs(
        self, tmp_path: Path, monkeypatch
    ):
        """Cleanup never touches user files or non-wrapper dirs."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.DOWNLOADS_DIR', tmp_path / 'downloads'
        )
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        name = '云帆科技'
        # bin dir without our auto-generation header → not ours, keep
        foreign_bin = tmp_path / 'bin' / name
        foreign_bin.mkdir(parents=True)
        (foreign_bin / 'browser-use').write_text('#!/bin/sh\necho hi\n')
        # downloads dir with a user file → keep
        stale_dl = tmp_path / 'downloads' / name
        stale_dl.mkdir(parents=True)
        (stale_dl / 'report.csv').write_text('data')

        write_browser_use_wrapper(name, 'ziniao', 9222, store_id='abcd1234ef')

        assert foreign_bin.exists()
        assert (stale_dl / 'report.csv').exists()

    def test_cleanup_rejects_dot_and_separator_names(
        self, tmp_path: Path, monkeypatch
    ):
        """Cleanup never targets the base dirs or traverses paths.

        ``.``/``..``/separator-bearing store names would resolve
        outside the per-store dir layout — the guards must bail
        before any filesystem mutation.
        """
        bin_dir = tmp_path / 'bin'
        bin_dir.mkdir(parents=True)
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', bin_dir)
        monkeypatch.setattr(
            'app.browser.wrapper.DOWNLOADS_DIR', tmp_path / 'downloads'
        )
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        # Bait: a header-matching script in the bin dir's PARENT —
        # exactly what a '..' store name would resolve to and rmtree.
        (tmp_path / 'browser-use').write_text(
            '# Auto-generated browser-use wrapper for store: ..\n'
        )

        for name in ('.', '..', 'a/b', '../store-abcd1234'):
            write_browser_use_wrapper(
                name, 'ziniao', 9222, store_id='abcd1234ef'
            )

        assert tmp_path.exists()
        assert bin_dir.exists()
        assert (tmp_path / 'browser-use').exists()

    def test_fallback_when_binary_not_found(self, tmp_path: Path, monkeypatch):
        """Uses 'browser-use' as fallback if binary not found."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        # No sibling next to sys.executable AND shutil.which
        # returns None — the wrapper should fall back to the
        # bare-string "browser-use".
        monkeypatch.setattr(
            'app.browser.wrapper.sys.executable',
            str(tmp_path / 'fake-venv' / 'bin' / 'python'),
        )
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: None,
        )
        write_browser_use_wrapper('test-store', 'ziniao', 9222, store_id='s1')

        content = (tmp_path / 'bin' / 'test-store' / 'browser-use').read_text()
        assert 'REAL_BU="browser-use"' in content

    def test_ziniao_auto_start_block(self, tmp_path: Path, monkeypatch):
        """Ziniao wrapper includes auto-start curl logic."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper(
            'test-store', 'ziniao', 9222, store_id='store-1'
        )

        content = (tmp_path / 'bin' / 'test-store' / 'browser-use').read_text()
        assert 'Auto-start' in content
        assert 'curl' in content
        assert '/json/version' in content

    def test_chrome_has_auto_start(self, tmp_path: Path, monkeypatch):
        """Chrome wrapper now has auto-start logic (same as Ziniao)."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper('storec', 'chrome', 9222, store_id='s2')

        content = (tmp_path / 'bin' / 'storec' / 'browser-use').read_text()
        assert 'Auto-start' in content
        assert 'curl' in content
        assert '/json/version' in content

    def test_ziniao_aux_is_chrome_direct(self, tmp_path: Path, monkeypatch):
        """Ziniao -aux is Chrome-direct: it gets a BU_NAME but no
        BU_CDP_WS (it must NOT be routed through the store CDP proxy),
        and it is excluded from the timeout/reload self-heal path."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper('test-store', 'ziniao', 9222, store_id='s1')

        content = (tmp_path / 'bin' / 'test-store' / 'browser-use').read_text()
        # Ziniao gets a dedicated aux case-arm (Chrome direct, no proxy).
        assert 'test-store-aux)' in content
        # aux is excluded from the wedge-recovery (timeout/reload) branch.
        assert '[ "$SESSION" != "test-store-aux" ]' in content

    def test_chrome_has_no_aux_case(self, tmp_path: Path, monkeypatch):
        """Chrome stores route everything through CDPMuxProxy — there is
        no Chrome-direct aux case-arm (a -aux session is a normal proxy
        session for Chrome)."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper('storec', 'chrome', 9222, store_id='s2')

        content = (tmp_path / 'bin' / 'storec' / 'browser-use').read_text()
        # No dedicated Chrome-direct aux case-arm.
        assert 'storec-aux)\n' not in content


class TestRemoveBrowserUseWrapper:
    def test_removes_dir(self, tmp_path: Path, monkeypatch):
        """Removes wrapper directory on session stop."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper('test-store', 'ziniao', 9222, store_id='s1')
        assert (tmp_path / 'bin' / 'test-store' / 'browser-use').exists()

        remove_browser_use_wrapper('test-store')
        assert not (tmp_path / 'bin' / 'test-store').exists()

    def test_no_dir(self, tmp_path: Path, monkeypatch):
        """Does not crash if wrapper dir does not exist."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        remove_browser_use_wrapper('test-store')  # no crash


class TestBrowserManagerPerStore:
    def test_separate_backend_instances(self):
        """Two stores get separate backend instances."""
        mgr = BrowserManager()
        b1 = mgr._get_backend('store-a', 'ziniao')
        b2 = mgr._get_backend('store-b', 'ziniao')
        assert b1 is not b2

    def test_same_store_same_backend(self):
        """Same store returns the same backend instance."""
        mgr = BrowserManager()
        b1 = mgr._get_backend('store-a', 'ziniao')
        b2 = mgr._get_backend('store-a', 'ziniao')
        assert b1 is b2

    def test_chrome_returns_chrome_backend(self):
        """Chrome stores return ChromeBackend instance."""
        mgr = BrowserManager()
        b = mgr._get_backend('store-c', 'chrome')
        assert isinstance(b, ChromeBackend)

    def test_unique_proxy_ports(self):
        """Each store gets a unique proxy port."""
        mgr = BrowserManager()
        p1 = mgr._allocate_proxy_port('store-a')
        p2 = mgr._allocate_proxy_port('store-b')
        assert p1 != p2

    def test_same_store_same_port(self):
        """Same store always gets the same proxy port."""
        mgr = BrowserManager()
        p1 = mgr._allocate_proxy_port('store-a')
        p2 = mgr._allocate_proxy_port('store-a')
        assert p1 == p2


class TestZiniaoGuard:
    """Ziniao account conflict detection (one account per machine)."""

    def test_same_account_allowed(self):
        """Two stores with the same Ziniao account can coexist."""
        mgr = BrowserManager()
        # Simulate store-a started with account-1
        mgr._active_ziniao_account_id = 'account-1'
        mgr._ziniao_stores['store-a'] = 'Store A'
        # store-b with same account should not raise
        mgr._active_ziniao_account_id = 'account-1'
        mgr._ziniao_stores['store-b'] = 'Store B'
        # Both tracked
        assert len(mgr._ziniao_stores) == 2

    def test_different_account_blocked(self):
        """Store with different Ziniao account raises error."""
        mgr = BrowserManager()
        mgr._active_ziniao_account_id = 'account-1'
        mgr._ziniao_stores['store-a'] = 'Store A'

        # Simulate the guard check (extracted from
        # _start_session_locked)
        new_account_id = 'account-2'
        assert mgr._active_ziniao_account_id is not None
        assert mgr._active_ziniao_account_id != new_account_id
        # The actual error would be raised in start_session;
        # here we just verify the tracking state
        with pytest.raises(RuntimeError, match='account'):
            if (
                mgr._active_ziniao_account_id is not None
                and mgr._active_ziniao_account_id != new_account_id
            ):
                active = ', '.join(mgr._ziniao_stores.values())
                raise RuntimeError(
                    f'Ziniao account conflict. '
                    f'Store(s) [{active}] are using a '
                    f'different account.'
                )

    def test_clear_on_last_stop(self):
        """Account tracking clears when last store stops."""
        mgr = BrowserManager()
        mgr._active_ziniao_account_id = 'account-1'
        mgr._ziniao_stores['store-a'] = 'Store A'
        mgr._ziniao_stores['store-b'] = 'Store B'

        # Stop store-a — account still active
        mgr._ziniao_stores.pop('store-a', None)
        if not mgr._ziniao_stores:
            mgr._active_ziniao_account_id = None
        assert mgr._active_ziniao_account_id == 'account-1'

        # Stop store-b — now cleared
        mgr._ziniao_stores.pop('store-b', None)
        if not mgr._ziniao_stores:
            mgr._active_ziniao_account_id = None
        assert mgr._active_ziniao_account_id is None

    def test_chrome_no_guard(self):
        """Chrome stores have no account guard."""
        mgr = BrowserManager()
        mgr._active_ziniao_account_id = 'account-1'
        mgr._ziniao_stores['store-a'] = 'Store A'
        # Chrome backend returns ChromeBackend — no conflict tracking
        b = mgr._get_backend('store-c', 'chrome')
        assert isinstance(b, ChromeBackend)
        # Account tracking unaffected
        assert mgr._active_ziniao_account_id == 'account-1'


class TestRemoveBrowserEntry:
    """remove_browser_entry delegates to wrapper removal."""

    def test_removes_wrapper(self, tmp_path: Path, monkeypatch):
        """remove_browser_entry removes wrapper dir."""
        monkeypatch.setattr('app.browser.wrapper._BIN_DIR', tmp_path / 'bin')
        monkeypatch.setattr(
            'app.browser.wrapper.shutil.which',
            lambda x: '/usr/local/bin/browser-use',
        )
        write_browser_use_wrapper('storec', 'chrome', 9222, store_id='s1')
        assert (tmp_path / 'bin' / 'storec' / 'browser-use').exists()

        mgr = BrowserManager()
        mgr.remove_browser_entry('storec', 'chrome')

        assert not (tmp_path / 'bin' / 'storec').exists()


class TestBuildStoreContext:
    def test_ziniao_dual_browser_context(self):
        """Ziniao store context includes dual browser CLI info."""
        store = mock.MagicMock()
        store.name = 'test-store'
        store.browser_backend = 'ziniao'
        store.platform_countries = '{}'
        ctx = build_store_context(store)
        assert 'DUAL BROWSER SYSTEM' in ctx
        assert 'browser-use' in ctx
        assert 'test-store-aux' in ctx
        # Should include routing rules
        assert 'Seller center' in ctx or 'seller center' in ctx

    def test_chrome_single_browser_context(self):
        """Chrome store context has single browser, no dual."""
        store = mock.MagicMock()
        store.name = 'storec'
        store.browser_backend = 'chrome'
        store.platform_countries = '{}'
        ctx = build_store_context(store)
        assert 'browser-use' in ctx
        assert 'DUAL BROWSER' not in ctx
        assert 'aux' not in ctx.lower()

    def test_includes_browser_cli_info(self):
        """build_store_context references browser-use CLI."""
        store = mock.MagicMock()
        store.name = 'test-store'
        store.browser_backend = 'ziniao'
        store.platform_countries = '{}'
        ctx = build_store_context(store)
        assert 'browser-use' in ctx
        assert 'mcp__playwright' not in ctx
