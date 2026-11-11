"""The Chinese installer wizard is fetched from a pinned Inno Setup tag.

Fetching from upstream ``main`` broke the v0.3.1 release build: Inno
Setup 7 moved ChineseSimplified.isl, the URL 404'd, and the installer
silently fell back to an English-only wizard.
"""

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.unit

BUILD_PS1 = (
    Path(__file__).resolve().parents[2] / 'installer' / 'windows' / 'build.ps1'
)


def test_isl_url_is_pinned_to_an_inno_6_tag():
    m = re.search(
        r"'https://raw\.githubusercontent\.com/jrsoftware/issrc/([^/]+)/"
        r"[^']*ChineseSimplified\.isl'",
        BUILD_PS1.read_text(),
    )
    assert m, 'ChineseSimplified.isl URL not found in build.ps1'
    # The compiler is Inno Setup 6; a 7.x file may use messages it lacks.
    assert re.fullmatch(r'is-6_\d+_\d+', m.group(1)), m.group(1)


ISS = BUILD_PS1.with_name('vibe-seller.iss')


def test_failed_fetch_fails_the_build():
    src = BUILD_PS1.read_text()
    fn = src[src.index('function Get-ChineseLang {') :]
    fn = fn[: fn.index('\n}\n')]
    assert 'throw' in fn
    assert 'Write-Warning' not in fn


def test_iss_requires_the_chinese_wizard():
    """No FileExists guard: compiling without the file must fail."""
    iss = ISS.read_text()
    assert 'Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"' in iss
    assert 'ChineseSimplified.isl")' not in iss  # no #if FileExists(...)
