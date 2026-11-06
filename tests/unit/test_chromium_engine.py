"""The browser engine stays installed across upgrades (chromium_engine).

An upgrade that brings a newer Playwright needs a new Chromium revision,
and the code running the upgrade is the old version. These pin the
pieces that close that gap: the server's boot check, the launch gate,
the source selection, and `vibe-seller upgrade`'s follow-up install.
"""

import asyncio
from pathlib import Path
import subprocess
import sys

import pytest

from app import cli
from app.browser import chromium_engine as ce

MIRROR = ce.MIRROR_HOST

_DRY_RUN = """\
Chrome for Testing 153.0.8010.12 (playwright chromium v1243)
  Install location:    {root}/chromium-1243
  Download url:        {host}/builds/cft/153.0.8010.12/linux64/chrome-linux64.zip

FFmpeg (playwright ffmpeg v1011)
  Install location:    {root}/ffmpeg-1011
  Download url:        {host}/builds/ffmpeg/1011/ffmpeg-linux.zip
  Download fallback 1: https://fallback.test/ffmpeg-linux.zip

FFmpeg (playwright ffmpeg v1011)
  Install location:    {root}/ffmpeg-1011
  Download url:        {host}/builds/ffmpeg/1011/ffmpeg-linux.zip
"""


# -- parsing the real Playwright's answer --------------------------------


@pytest.mark.unit
def test_parse_dry_run_one_piece_per_dir_primary_url_only():
    out = _DRY_RUN.format(root='/r', host='https://h')
    pieces = ce.parse_dry_run(out)
    assert [p.location.name for p in pieces] == [
        'chromium-1243',
        'ffmpeg-1011',
    ]
    assert pieces[1].url == 'https://h/builds/ffmpeg/1011/ffmpeg-linux.zip'


@pytest.mark.unit
def test_parse_dry_run_keeps_windows_drive_colon():
    out = (
        '  Install location:    C:\\Users\\u\\AppData\\Local\\ms-playwright'
        '\\chromium-1243\n'
        '  Download url:        https://h/chrome-win64.zip\n'
    )
    (piece,) = ce.parse_dry_run(out)
    assert str(piece.location).startswith('C:\\Users')
    assert piece.url == 'https://h/chrome-win64.zip'


@pytest.mark.unit
def test_missing_asks_the_real_playwright(tmp_path, monkeypatch):
    """Against the installed Playwright: empty dir → everything missing;
    INSTALLATION_COMPLETE in each → nothing missing. No network."""
    monkeypatch.setenv('PLAYWRIGHT_BROWSERS_PATH', str(tmp_path))
    need = ce.missing()
    assert need, 'an empty browsers dir must report missing pieces'
    assert any(p.name.startswith('chromium-') for p in need)
    for p in need:
        p.mkdir(parents=True)
        (p / 'INSTALLATION_COMPLETE').touch()
    assert ce.missing() == []


# -- source selection (same rules as install.sh) -------------------------


def _fake_sources(monkeypatch, *, speeds, status=None):
    """dry_run/http_status/download_speed fakes keyed by host."""
    status = status or {}

    def dry_run(host):
        h = host or 'https://cdn.playwright.dev'
        return ce.parse_dry_run(_DRY_RUN.format(root='/r', host=h))

    def http_status(url):
        for frag, code in status.items():
            if frag in url:
                return code
        return 200

    def download_speed(url):
        return speeds[MIRROR if 'npmmirror' in url else '']

    monkeypatch.setattr(ce, 'dry_run', dry_run)
    monkeypatch.setattr(ce, 'http_status', http_status)
    monkeypatch.setattr(ce, 'download_speed', download_speed)


@pytest.mark.unit
def test_pick_faster_mirror(monkeypatch):
    _fake_sources(monkeypatch, speeds={'': 1e6, MIRROR: 9e6})
    assert ce.pick_download_host(lambda _m: None) == MIRROR


@pytest.mark.unit
def test_pick_official_when_faster(monkeypatch):
    _fake_sources(monkeypatch, speeds={'': 9e6, MIRROR: 1e6})
    assert ce.pick_download_host(lambda _m: None) == ''


@pytest.mark.unit
def test_never_pick_a_mirror_missing_a_file(monkeypatch):
    _fake_sources(
        monkeypatch,
        speeds={'': 1e6, MIRROR: 9e6},
        status={'npmmirror.com/binaries/playwright/builds/ffmpeg': 404},
    )
    assert ce.pick_download_host(lambda _m: None) == ''


@pytest.mark.unit
def test_pick_mirror_when_official_unreachable(monkeypatch):
    _fake_sources(
        monkeypatch,
        speeds={'': 0.0, MIRROR: 5e5},
        status={'cdn.playwright.dev': 0},
    )
    assert ce.pick_download_host(lambda _m: None) == MIRROR


# -- the boot check and the launch gate ----------------------------------


class _Fakes:
    """Scriptable missing()/install for Engine tests."""

    def __init__(self, monkeypatch, *, missing, install_rc=0, host=''):
        self.missing_now = list(missing)
        self.install_rc = install_rc
        self.installs: list = []
        self.release = asyncio.Event()
        self.release.set()
        monkeypatch.setattr(ce, 'missing', lambda: list(self.missing_now))
        monkeypatch.setattr(ce, 'pick_download_host', lambda *a: host)
        monkeypatch.setattr(ce, '_run_install', self._run_install)

    async def _run_install(self, host):
        self.installs.append(host)
        await self.release.wait()
        rc = (
            self.install_rc(host)
            if callable(self.install_rc)
            else self.install_rc
        )
        if rc == 0:
            self.missing_now = []
        return rc, ''


@pytest.mark.unit
async def test_nothing_missing_means_no_install(monkeypatch):
    fakes = _Fakes(monkeypatch, missing=[])
    engine = ce.Engine()
    await engine.ensure_ready_for_launch()
    assert engine.phase == 'ready'
    assert fakes.installs == []


@pytest.mark.unit
async def test_missing_engine_is_installed_in_the_background(monkeypatch):
    """The upgrade case: boot finds chromium-<new rev> missing, installs
    it; a launch meanwhile fails fast with a clear error, not
    Playwright's 'Executable doesn't exist'; afterwards it launches."""
    fakes = _Fakes(monkeypatch, missing=[Path('/r/chromium-1243')])
    fakes.release.clear()  # hold the download
    engine = ce.Engine()
    task = engine.start()
    with pytest.raises(ce.BrowserEngineNotReady, match='chromium-1243'):
        await asyncio.wait_for(engine.ensure_ready_for_launch(), 5)
    assert engine.phase == 'downloading'
    fakes.release.set()
    assert await task is True
    await engine.ensure_ready_for_launch()  # no raise
    assert fakes.installs == ['']


@pytest.mark.unit
async def test_failed_install_is_retried_on_next_launch(monkeypatch):
    fakes = _Fakes(
        monkeypatch, missing=[Path('/r/chromium-1243')], install_rc=1
    )
    engine = ce.Engine()
    assert await engine.start() is False
    assert engine.phase == 'failed'
    fakes.install_rc = 0
    with pytest.raises(ce.BrowserEngineNotReady):
        await engine.ensure_ready_for_launch()
    assert await engine._task is True
    assert len(fakes.installs) == 2


@pytest.mark.unit
async def test_failed_mirror_install_retries_official(monkeypatch):
    fakes = _Fakes(
        monkeypatch,
        missing=[Path('/r/chromium-1243')],
        host=MIRROR,
        install_rc=lambda host: 1 if host == MIRROR else 0,
    )
    assert await ce.Engine().start() is True
    assert fakes.installs == [MIRROR, '']


@pytest.mark.unit
async def test_user_download_host_is_used_unprobed(monkeypatch):
    monkeypatch.setenv('PLAYWRIGHT_DOWNLOAD_HOST', 'https://mine.test')
    fakes = _Fakes(monkeypatch, missing=[Path('/r/chromium-1243')])

    def no_probe(*_a):
        raise AssertionError('must not probe when the user set a host')

    monkeypatch.setattr(ce, 'pick_download_host', no_probe)
    assert await ce.Engine().start() is True
    assert fakes.installs == [None]  # None = leave the user's env as-is


@pytest.mark.unit
async def test_broken_check_fails_open(monkeypatch):
    def boom():
        raise subprocess.CalledProcessError(1, 'playwright')

    monkeypatch.setattr(ce, 'missing', boom)
    engine = ce.Engine()
    await engine.ensure_ready_for_launch()  # must not block launches
    assert engine.phase == 'ready'


@pytest.mark.unit
async def test_chrome_backend_gates_launch_on_the_engine(monkeypatch, tmp_path):
    """ChromeBackend.start must ask the engine BEFORE starting Playwright."""
    from app.browser import chrome  # noqa: PLC0415

    async def not_ready():
        raise ce.BrowserEngineNotReady('downloading')

    def must_not_launch():
        raise AssertionError('launched Playwright with no engine')

    monkeypatch.setattr(ce, 'ensure_ready_for_launch', not_ready)
    monkeypatch.setattr(chrome, 'async_playwright', must_not_launch)
    monkeypatch.setattr(chrome, 'PROFILES_DIR', tmp_path)
    with pytest.raises(ce.BrowserEngineNotReady):
        await chrome.ChromeBackend().start({'store_slug': 's'})


@pytest.mark.unit
def test_disabled_engine_is_a_no_op(monkeypatch):
    monkeypatch.setenv('VIBE_ENSURE_BROWSER_ENGINE', 'false')
    assert ce.start_background_check() is None


# -- `vibe-seller upgrade` installs the new version's engine -------------


def _record_runs(monkeypatch, rcs):
    calls = []

    def run(cmd, *a, **k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, rcs.pop(0) if rcs else 0)

    monkeypatch.setattr(cli.subprocess, 'run', run)
    return calls


@pytest.mark.unit
def test_upgrade_installs_browser_with_the_new_code(monkeypatch):
    monkeypatch.setattr(cli, '_upgrade_package', lambda a: 0)
    monkeypatch.setattr(cli, '_upgrade_claude_cli', lambda: 0)
    calls = _record_runs(monkeypatch, [0])
    assert cli._cmd_upgrade(None) == 0
    # A fresh, isolated interpreter: the NEW app + Playwright, and not a
    # source checkout that happens to be the cwd.
    assert calls == [[sys.executable, '-I', '-m', 'app.cli', 'install-browser']]


@pytest.mark.unit
def test_upgrade_to_a_build_without_install_browser_falls_back(monkeypatch):
    monkeypatch.setattr(cli, '_upgrade_package', lambda a: 0)
    monkeypatch.setattr(cli, '_upgrade_claude_cli', lambda: 0)
    calls = _record_runs(monkeypatch, [2, 0])
    assert cli._cmd_upgrade(None) == 0
    assert calls[1] == [
        sys.executable,
        '-I',
        '-m',
        'playwright',
        'install',
        'chromium',
    ]


@pytest.mark.unit
def test_failed_upgrade_does_not_install_browser(monkeypatch):
    monkeypatch.setattr(cli, '_upgrade_package', lambda a: 1)
    calls = _record_runs(monkeypatch, [])
    assert cli._cmd_upgrade(None) == 1
    assert calls == []
