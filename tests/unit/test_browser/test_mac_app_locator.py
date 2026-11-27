"""Unit tests for the shared macOS app locator (all OS, fake system).

The locator's system probes (Spotlight, the process list, ``open``) are
injected as :class:`MacLocatorRunners` fakes and the filesystem is a
real ``tmp_path`` holding real ``.app`` bundles with real
``Info.plist`` files, so validation, ordering and the launch contract
run exactly as in production. Real-macOS behaviour (mdfind, psutil,
open) is exercised live by the vendor lanes' scenarios.
"""

import logging
import plistlib
import subprocess

import pytest

from app.browser import mac_app_locator as mal

pytestmark = pytest.mark.unit

_SPEC = mal.MacAppSpec(
    key='example',
    display_name='Example',
    bundle_ids=('com.example.app',),
    app_names=('Example.app',),
)


def make_app(base, name='Example', bundle_id='com.example.app'):
    """A real .app bundle under ``base``; returns its app_path."""
    app = base / f'{name}.app'
    (app / 'Contents' / 'MacOS').mkdir(parents=True)
    (app / 'Contents' / 'MacOS' / name).write_bytes(b'\xcf\xfa\xed\xfe')
    with open(app / 'Contents' / 'Info.plist', 'wb') as f:
        plistlib.dump(
            {'CFBundleExecutable': name, 'CFBundleIdentifier': bundle_id}, f
        )
    return str(app)


def make_app_without_plist(base, name='Broken'):
    """A directory that looks like a bundle but has no Info.plist."""
    app = base / f'{name}.app'
    (app / 'Contents' / 'MacOS').mkdir(parents=True)
    (app / 'Contents' / 'MacOS' / name).write_bytes(b'x')
    return str(app)


def runners(
    *,
    mdfind=lambda _bid: [],
    processes=lambda: [],
    open_app=lambda _app, _args: (0, ''),
    app_dirs=lambda: [],
):
    return mal.MacLocatorRunners(
        mdfind=mdfind,
        processes=processes,
        open_app=open_app,
        app_dirs=app_dirs,
    )


class TestSavedPath:
    """Hint 1: the caller-saved path."""

    def test_saved_app_bundle(self, tmp_path):
        app = make_app(tmp_path)
        hit = mal.locate(_SPEC, saved_path=app, runners=runners())
        assert hit is not None
        assert hit.app_path == app
        assert hit.exe_path == f'{app}/Contents/MacOS/Example'
        assert hit.bundle_id == 'com.example.app'
        assert hit.hint == 'saved'

    def test_saved_exe_inside_the_bundle(self, tmp_path):
        app = make_app(tmp_path)
        hit = mal.locate(
            _SPEC,
            saved_path=f'{app}/Contents/MacOS/Example',
            runners=runners(),
        )
        assert hit is not None
        assert hit.app_path == app
        assert hit.hint == 'saved'

    def test_saved_dir_containing_the_bundle(self, tmp_path):
        app = make_app(tmp_path / 'installs')
        hit = mal.locate(
            _SPEC,
            saved_path=str(tmp_path / 'installs'),
            runners=runners(),
        )
        assert hit is not None
        assert hit.app_path == app
        assert hit.hint == 'saved'

    def test_saved_path_without_a_valid_bundle_falls_through(
        self, tmp_path, caplog
    ):
        """A saved path that is not a bundle is logged and skipped."""
        mdfind_calls = []

        def mdfind(bid):
            mdfind_calls.append(bid)
            return []

        with caplog.at_level(
            logging.INFO, logger='app.browser.mac_app_locator'
        ):
            hit = mal.locate(
                _SPEC,
                saved_path=str(tmp_path / 'nowhere'),
                runners=runners(mdfind=mdfind),
            )
        assert hit is None
        assert mdfind_calls == ['com.example.app']  # discovery continued
        assert 'not a valid bundle' in caplog.text

    def test_saved_path_to_a_bundle_without_plist_is_rejected(self, tmp_path):
        broken = make_app_without_plist(tmp_path)
        assert mal.locate(_SPEC, saved_path=broken, runners=runners()) is None


class TestProcessHint:
    """Hint 2: a running process inside a named bundle."""

    def test_running_main_process(self, tmp_path):
        app = make_app(tmp_path)
        procs = [
            mal.MacAppProcess(
                pid=11,
                exe=f'{app}/Contents/MacOS/Example',
                args=(f'{app}/Contents/MacOS/Example',),
            ),
            mal.MacAppProcess(pid=12, exe='/usr/bin/other', args=()),
        ]
        hit = mal.locate(_SPEC, runners=runners(processes=lambda: procs))
        assert hit is not None
        assert hit.app_path == app
        assert hit.hint == 'process'
        assert hit.detail == 'pid 11'

    def test_helper_process_inside_the_bundle_counts(self, tmp_path):
        """A helper exe nested under Contents/Frameworks still points at
        the OUTER bundle (its bundle root is the .app, not the helper)."""
        app = make_app(tmp_path)
        helper = (
            f'{app}/Contents/Frameworks/Example Helper.app'
            '/Contents/MacOS/Example Helper'
        )
        procs = [mal.MacAppProcess(pid=21, exe=helper, args=(helper,))]
        hit = mal.locate(_SPEC, runners=runners(processes=lambda: procs))
        assert hit is not None
        assert hit.app_path == app

    def test_other_bundle_name_does_not_match(self, tmp_path):
        make_app(tmp_path, name='Other')
        procs = [
            mal.MacAppProcess(
                pid=31,
                exe=f'{tmp_path}/Other.app/Contents/MacOS/Other',
                args=(),
            )
        ]
        assert (
            mal.locate(_SPEC, runners=runners(processes=lambda: procs)) is None
        )


class TestSpotlightHint:
    """Hint 3: mdfind by bundle id."""

    def test_mdfind_hit(self, tmp_path):
        app = make_app(tmp_path / 'anywhere')
        hit = mal.locate(
            _SPEC,
            runners=runners(mdfind=lambda _bid: [app, '/nope.app']),
        )
        assert hit is not None
        assert hit.app_path == app
        assert hit.hint == 'spotlight'

    def test_mdfind_hit_without_a_valid_bundle_is_skipped(self, tmp_path):
        broken = make_app_without_plist(tmp_path)
        assert (
            mal.locate(_SPEC, runners=runners(mdfind=lambda _bid: [broken]))
            is None
        )

    def test_applications_fallback_after_empty_spotlight(self, tmp_path):
        """Spotlight returns nothing → the Applications dirs are tried."""
        app = make_app(tmp_path / 'Applications')
        hit = mal.locate(
            _SPEC,
            runners=runners(app_dirs=lambda: [str(tmp_path / 'Applications')]),
        )
        assert hit is not None
        assert hit.hint == 'applications'
        assert hit.app_path == app


class TestNotFound:
    def test_nothing_found_returns_none_and_a_message(self):
        assert mal.locate(_SPEC, runners=runners()) is None
        msg = mal.not_found_message(_SPEC)
        assert 'Example' in msg
        assert '/Applications' in msg


class TestLaunch:
    """``launch`` — the open contract, and secrets never leak."""

    def test_launch_ok_calls_open_with_args(self, tmp_path):
        app = make_app(tmp_path)
        seen = []

        def open_app(path, args):
            seen.append((path, args))
            return (0, '')

        located = mal.locate(_SPEC, saved_path=app, runners=runners())
        mal.launch(
            located, ('--headless=true',), runners=runners(open_app=open_app)
        )
        assert seen == [(app, ('--headless=true',))]

    def test_launch_failure_never_leaks_args(self, tmp_path, caplog):
        """A failing open must not put the args in the error or logs
        (a caller may pass an API key on that command line)."""
        app = make_app(tmp_path)

        def open_app(path, args):
            return (1, 'open: some stderr')

        located = mal.locate(_SPEC, saved_path=app, runners=runners())
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(RuntimeError) as ei:
                mal.launch(
                    located,
                    ('--api-key=SECRET123',),
                    runners=runners(open_app=open_app),
                )
        assert located.app_path in str(ei.value)
        assert 'rc=1' in str(ei.value)
        assert 'SECRET123' not in str(ei.value)
        assert 'SECRET123' not in caplog.text

    def test_default_open_app_timeout_never_leaks_args(
        self, tmp_path, caplog, monkeypatch
    ):
        """The REAL runner's timeout text embeds the full command (args
        included); launch must surface none of it. subprocess.run is
        patched to raise a TimeoutExpired whose cmd carries the secret,
        exactly as the real one would."""
        app = make_app(tmp_path)

        def fake_run(cmd, **_kwargs):
            raise subprocess.TimeoutExpired(
                cmd=['open', app, '--args', '--api-key=SECRET123'],
                timeout=30,
            )

        monkeypatch.setattr(mal.subprocess, 'run', fake_run)
        located = mal.locate(_SPEC, saved_path=app, runners=runners())
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(RuntimeError) as ei:
                mal.launch(
                    located,
                    ('--api-key=SECRET123',),
                    runners=mal.MacLocatorRunners(
                        mdfind=lambda _bid: [],
                        processes=lambda: [],
                        open_app=mal._default_open_app,
                        app_dirs=lambda: [],
                    ),
                )
        assert 'timed out' in str(ei.value)
        assert 'SECRET123' not in str(ei.value)
        assert 'SECRET123' not in caplog.text


class TestAppProcesses:
    def test_only_processes_inside_the_bundle(self, tmp_path):
        app = make_app(tmp_path)
        inside = (
            f'{app}/Contents/MacOS/Example',
            f'{app}/Contents/Frameworks/Example Helper.app'
            '/Contents/MacOS/Example Helper',
        )
        procs = [
            mal.MacAppProcess(pid=1, exe=inside[0], args=('a',)),
            mal.MacAppProcess(pid=2, exe=inside[1], args=('b',)),
            mal.MacAppProcess(pid=3, exe='/usr/bin/other', args=()),
            mal.MacAppProcess(pid=4, exe='', args=()),
        ]
        located = mal.locate(_SPEC, saved_path=app, runners=runners())
        got = mal.app_processes(
            located, runners=runners(processes=lambda: procs)
        )
        assert [p.pid for p in got] == [1, 2]
        assert got[0].args == ('a',)
