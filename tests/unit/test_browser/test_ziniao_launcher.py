"""Tests for the generated Ziniao WebDriver launcher .bat.

The bat must launch an install the server's locator found — including a
Chinese-named custom dir, which the old fixed-path list never contained
— and still fall back to that list when nothing is located or the baked
path goes stale. Batch files are parsed in the console code page, so
everything before the ``chcp 65001`` line must be plain ASCII.
"""

import pytest

from app.browser.ziniao_launcher import build_launcher_bat

CUSTOM = r'C:\紫鸟\ziniao\ziniao.exe'
DEFAULT = r'C:\Program Files\ziniao\ziniao.exe'


def text(bat: bytes) -> str:
    return bat.decode('utf-8')


def test_bat_is_utf8_crlf_without_bom():
    bat = build_launcher_bat(CUSTOM)
    assert not bat.startswith(b'\xef\xbb\xbf')
    assert '\r\n' in text(bat)


def test_baked_custom_path_is_used_first():
    body = text(build_launcher_bat(CUSTOM))
    assert f'set "ZINIAO_BAKED={CUSTOM}"' in body
    assert 'if exist "%ZINIAO_BAKED%"' in body
    # The launch goes through the resolved variable only — the custom
    # path appears exactly once, in the set line.
    assert body.count(CUSTOM) == 1
    assert 'start "" "%ZINIAO_EXE%" --run_type=web_driver' in body


def test_ascii_prefix_before_chcp():
    """Every line before chcp 65001 is ASCII (cmd parses the file in
    the console code page until the switch takes effect)."""
    body = text(build_launcher_bat(CUSTOM))
    prefix = body.split('chcp 65001', 1)[0]
    prefix.encode('ascii')
    assert 'set "ZINIAO_BAKED=' not in prefix


def test_fallback_list_survives_a_baked_path():
    """A stale baked path (reinstall to yet another dir) must not brick
    the script: the conventional locations are still searched."""
    body = text(build_launcher_bat(CUSTOM))
    for path in (
        r'C:\Program Files\ziniao\ziniao.exe',
        r'C:\Program Files (x86)\ziniao\ziniao.exe',
        r'D:\ziniao\ziniao.exe',
        r'%LOCALAPPDATA%\ziniao\ziniao.exe',
    ):
        assert f'"{path}"' in body
    assert 'where ziniao.exe' in body


def test_no_baked_path_yields_fallback_only():
    body = text(build_launcher_bat(None))
    assert 'ZINIAO_BAKED' not in body
    assert r'C:\Program Files\ziniao\ziniao.exe' in body
    assert 'start "" "%ZINIAO_EXE%" --run_type=web_driver' in body


def test_blank_path_is_treated_as_none():
    assert build_launcher_bat('') == build_launcher_bat(None)
    assert build_launcher_bat('  ') == build_launcher_bat(None)


@pytest.mark.parametrize(
    'path', [CUSTOM, DEFAULT, r'D:\apps\ziniao\ziniao.exe']
)
def test_essential_steps_present(path):
    body = text(build_launcher_bat(path))
    # The battle-tested steps of the previous static script.
    assert 'Stopping every Ziniao process' in body
    assert 'powershell -NoProfile -ExecutionPolicy Bypass -Command' in body
    assert '--run_type=web_driver --ipc_type=http --port=%PORT%' in body
    assert 'tasklist /FI "IMAGENAME eq ziniao.exe"' in body
    assert 'getBrowserList' in body
    assert 'pause' in body


def test_quoted_path_is_stripped():
    """locate() results may carry quotes from registry values."""
    body = text(build_launcher_bat(f'"{CUSTOM}"'))
    assert f'set "ZINIAO_BAKED={CUSTOM}"' in body
