"""The mock CLI's store-browser resolver must run the WRAPPER, not a
PATH-resolved real browser-use.

The CI failure this pins: a server started under ``uv run`` carries
the venv's bin on PATH, so the mock's ``shutil.which('browser-use')``
found the REAL CLI — the scenario bypassed the store wrapper (and its
start contract), the task completed, and the tier-1 e2e asserted on a
flow that never ran. The wrapper under ``$VIBE_HOME/bin/`` wins;
``which`` is only the fallback when no wrapper exists.
"""

import os
from pathlib import Path
import stat
import sys
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'e2e'))
import mock_cli  # noqa: E402

pytestmark = pytest.mark.unit


def _make_wrapper(home: Path, name: str, mtime: float) -> Path:
    d = home / 'bin' / name
    d.mkdir(parents=True)
    w = d / 'browser-use'
    w.write_text('#!/bin/sh\ntrue\n')
    w.chmod(stat.S_IRWXU)
    os.utime(w, (mtime, mtime))
    return w


class TestResolveBrowserUse:
    def test_wrapper_wins_over_a_path_resolved_real_cli(self, tmp_path):
        """The CI condition: both a PATH binary and a wrapper exist —
        the WRAPPER must win (the negative control on the old code:
        it returned the PATH binary and the e2e bypassed the wrapper).
        """
        home = tmp_path / 'home'
        wrapper = _make_wrapper(home, 'store-a', 2000.0)
        real = tmp_path / 'bin' / 'browser-use'
        real.parent.mkdir()
        real.write_text('#!/bin/sh\ntrue\n')
        real.chmod(stat.S_IRWXU)
        with (
            mock.patch.dict(
                os.environ,
                {
                    'VIBE_HOME': str(home),
                    'PATH': f'{real.parent}:{os.environ["PATH"]}',
                },
            ),
        ):
            assert mock_cli._resolve_browser_use() == str(wrapper)

    def test_newest_wrapper_wins(self, tmp_path):
        home = tmp_path / 'home'
        old = _make_wrapper(home, 'store-old', 1000.0)
        new = _make_wrapper(home, 'store-new', 2000.0)
        with mock.patch.dict(os.environ, {'VIBE_HOME': str(home)}):
            assert mock_cli._resolve_browser_use() == str(new)
            assert str(old) != mock_cli._resolve_browser_use()

    def test_path_fallback_when_no_wrapper(self, tmp_path):
        real = tmp_path / 'bin' / 'browser-use'
        real.parent.mkdir()
        real.write_text('#!/bin/sh\ntrue\n')
        real.chmod(stat.S_IRWXU)
        with (
            mock.patch.dict(
                os.environ,
                {
                    'VIBE_HOME': str(tmp_path / 'empty-home'),
                    'PATH': f'{real.parent}:{os.environ["PATH"]}',
                },
            ),
        ):
            assert mock_cli._resolve_browser_use() == str(real)

    def test_default_home_when_vibe_home_unset(self, tmp_path):
        """The CI condition (the second failure this pinned): the mock
        server is started WITHOUT ``VIBE_HOME`` — the default
        configuration — so the server serves ``~/.vibe-seller`` while
        the mock child sees no ``VIBE_HOME`` and a PATH binary. The
        resolver must resolve the home the way the server does
        (default ``~/.vibe-seller``) and run THAT wrapper, not the
        PATH binary.
        """
        home = tmp_path / 'home-dir'
        wrapper = _make_wrapper(home / '.vibe-seller', 'store-a', 2000.0)
        real = tmp_path / 'bin' / 'browser-use'
        real.parent.mkdir()
        real.write_text('#!/bin/sh\ntrue\n')
        real.chmod(stat.S_IRWXU)
        env = {
            'VIBE_HOME': '',
            'HOME': str(home),
            'PATH': f'{real.parent}:{os.environ["PATH"]}',
        }
        with mock.patch.dict(os.environ, env):
            assert mock_cli._resolve_browser_use() == str(wrapper)

    def test_raises_when_nothing_exists(self, tmp_path):
        """Neither a wrapper nor a PATH binary: the resolver must SAY so
        instead of running something else.

        PATH is CONTROLLED here. The CI runner has the real
        browser-use on PATH (the venv's bin, because the server runs
        under ``uv run``) — a PATH that leaks into this test finds it
        and turns the raise into a silent fallback, which is the same
        environment difference that broke the e2e.
        """
        empty_bin = tmp_path / 'empty-bin'
        empty_bin.mkdir()
        with mock.patch.dict(
            os.environ,
            {
                'VIBE_HOME': str(tmp_path / 'empty-home'),
                'PATH': str(empty_bin),
            },
        ):
            with pytest.raises(RuntimeError, match='wrapper'):
                mock_cli._resolve_browser_use()
