"""``REAL_BU`` must be absolute, or no wrapper gets written at all.

The agent's PATH puts the wrapper dir FIRST so that bare ``browser-use``
reaches the wrapper. A wrapper that forwards to the bare name therefore
re-execs itself: on Windows, where the console shim is
``browser-use.exe`` with no extensionless sibling, the old
``shutil.which`` fallthrough produced exactly that — recursion until the
guard rejected the inherited ``BU_NAME``.

Resolution has two legal outcomes, an absolute path or a raise. These
tests pin both, plus the wrapper-tree self-reference that would
reintroduce the recursion by another route.
"""

from pathlib import Path
import re
from unittest import mock

import pytest

from app.browser import bu_binary
from app.browser.bu_binary import BrowserUseNotFound, resolve_browser_use
from app.browser.web_wrapper import write_web_browser_use_wrapper
from app.browser.wrapper import write_browser_use_wrapper

pytestmark = pytest.mark.unit


def _shim(dir_path: Path, name: str) -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / name
    p.write_text('#!/usr/bin/env bash\nexit 0\n')
    p.chmod(0o755)
    return p


class TestResolveBrowserUse:
    def test_prefers_the_sibling_of_the_daemon_interpreter(
        self, tmp_path, monkeypatch
    ):
        # uv tool install mode: the tool venv bin is NOT on PATH, so the
        # sibling lookup is the only thing that finds it.
        sibling = _shim(tmp_path / 'venv-bin', 'browser-use')
        elsewhere = _shim(tmp_path / 'other', 'browser-use')
        monkeypatch.setattr(
            bu_binary.shutil, 'which', lambda _n: str(elsewhere)
        )
        assert resolve_browser_use(tmp_path / 'venv-bin') == str(sibling)

    def test_windows_finds_the_exe_with_no_bare_sibling(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(bu_binary, 'IS_WINDOWS', True)
        monkeypatch.setattr(bu_binary.shutil, 'which', lambda _n: None)
        exe = _shim(tmp_path / 'Scripts', 'browser-use.exe')
        assert resolve_browser_use(tmp_path / 'Scripts') == str(exe)

    def test_posix_does_not_pick_up_a_windows_shim(self, tmp_path, monkeypatch):
        monkeypatch.setattr(bu_binary, 'IS_WINDOWS', False)
        monkeypatch.setattr(bu_binary.shutil, 'which', lambda _n: None)
        _shim(tmp_path / 'bin', 'browser-use.exe')
        with pytest.raises(BrowserUseNotFound):
            resolve_browser_use(tmp_path / 'bin')

    def test_path_lookup_is_the_fallback(self, tmp_path, monkeypatch):
        found = _shim(tmp_path / 'on-path', 'browser-use')
        monkeypatch.setattr(bu_binary.shutil, 'which', lambda _n: str(found))
        assert resolve_browser_use(tmp_path / 'empty') == str(found)

    def test_raises_instead_of_returning_a_bare_name(
        self, tmp_path, monkeypatch
    ):
        """The whole point: no bare-name fallback exists to be written."""
        monkeypatch.setattr(bu_binary.shutil, 'which', lambda _n: None)
        with pytest.raises(BrowserUseNotFound):
            resolve_browser_use(tmp_path / 'nothing-here')

    def test_rejects_a_binary_inside_the_wrapper_tree(
        self, tmp_path, monkeypatch
    ):
        """A wrapper resolving to the wrapper tree IS the recursion."""
        wrapper_root = tmp_path / 'vibe-bin'
        inside = _shim(wrapper_root / 'store-1', 'browser-use')
        monkeypatch.setattr(bu_binary, 'BROWSER_USE_BIN_DIR', wrapper_root)
        monkeypatch.setattr(bu_binary.shutil, 'which', lambda _n: str(inside))
        with pytest.raises(BrowserUseNotFound, match='wrapper tree'):
            resolve_browser_use(tmp_path / 'empty')


class TestAbsoluteContract:
    """The returned path goes into a script that runs from anywhere."""

    def test_relative_which_result_is_made_absolute(
        self, tmp_path, monkeypatch
    ):
        _shim(tmp_path / 'rel', 'browser-use')
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            bu_binary.shutil, 'which', lambda _n: 'rel/browser-use'
        )
        got = resolve_browser_use(tmp_path / 'empty')
        assert Path(got).is_absolute()
        assert got == str(tmp_path / 'rel' / 'browser-use')

    def test_relative_daemon_bin_is_made_absolute(self, tmp_path, monkeypatch):
        _shim(tmp_path / 'venv-bin', 'browser-use')
        monkeypatch.chdir(tmp_path)
        got = resolve_browser_use(Path('venv-bin'))
        assert Path(got).is_absolute()

    def test_symlink_into_the_wrapper_tree_is_rejected(
        self, tmp_path, monkeypatch
    ):
        """A lexical containment check is dodgeable by a symlink.

        The PATH entry sits outside the wrapper tree, but its target is a
        generated wrapper inside it — so the new wrapper would exec the
        old one. Same recursion, one indirection away.
        """
        wrapper_root = tmp_path / 'vibe-bin'
        real_wrapper = _shim(wrapper_root / 'store-1', 'browser-use')
        link_dir = tmp_path / 'outside'
        link_dir.mkdir()
        link = link_dir / 'browser-use'
        link.symlink_to(real_wrapper)
        monkeypatch.setattr(bu_binary, 'BROWSER_USE_BIN_DIR', wrapper_root)
        monkeypatch.setattr(bu_binary.shutil, 'which', lambda _n: str(link))
        with pytest.raises(BrowserUseNotFound, match='wrapper tree'):
            resolve_browser_use(tmp_path / 'empty')


class TestGeneratedWrappers:
    """End to end: what lands in the generated script's REAL_BU."""

    @staticmethod
    def _real_bu_of(text: str) -> str:
        m = re.search(r'REAL_BU="([^"]*)"', text)
        assert m, 'wrapper has no REAL_BU line'
        return m.group(1)

    def test_store_wrapper_embeds_an_absolute_path(self, tmp_path, monkeypatch):
        exe = _shim(tmp_path / 'Scripts', 'browser-use.exe')
        monkeypatch.setattr(bu_binary, 'IS_WINDOWS', True)
        monkeypatch.setattr(bu_binary.shutil, 'which', lambda _n: None)
        monkeypatch.setattr(
            bu_binary.sys, 'executable', str(tmp_path / 'Scripts' / 'python')
        )
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'test-store', 'chrome', 9222, store_id='store-1'
            )
        wrapper = next(bin_dir.glob('*/browser-use'))
        real_bu = self._real_bu_of(wrapper.read_text())
        assert real_bu == str(exe)
        assert Path(real_bu).is_absolute()
        assert real_bu != 'browser-use'

    def test_web_wrapper_embeds_an_absolute_path(self, tmp_path, monkeypatch):
        exe = _shim(tmp_path / 'Scripts', 'browser-use.exe')
        monkeypatch.setattr(bu_binary, 'IS_WINDOWS', True)
        monkeypatch.setattr(bu_binary.shutil, 'which', lambda _n: None)
        monkeypatch.setattr(
            bu_binary.sys, 'executable', str(tmp_path / 'Scripts' / 'python')
        )
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.web_wrapper._BIN_DIR', bin_dir):
            write_web_browser_use_wrapper(9333)
        wrapper = next(bin_dir.glob('*/browser-use'))
        assert self._real_bu_of(wrapper.read_text()) == str(exe)

    def test_no_wrapper_is_written_when_resolution_fails(
        self, tmp_path, monkeypatch
    ):
        """Better a loud failure than a wrapper that execs itself.

        ``bin/_guard/browser-use`` still sits on the agent PATH, so a
        missing wrapper fails loudly instead of reaching the real binary
        and attaching to the user's own Chrome.
        """
        monkeypatch.setattr(bu_binary.shutil, 'which', lambda _n: None)
        monkeypatch.setattr(
            bu_binary.sys, 'executable', str(tmp_path / 'empty' / 'python')
        )
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            with pytest.raises(BrowserUseNotFound):
                write_browser_use_wrapper(
                    'test-store', 'chrome', 9222, store_id='store-1'
                )
        assert not list(bin_dir.glob('*/browser-use'))
