"""install.sh adds a reachable apt mirror when a configured one is dead.

Observed on GitHub-hosted runners: their DNS failed for every
``*.ubuntu.com`` host while other domains resolved, so every
``playwright install --with-deps`` (apt) failed for hours. A user whose
default mirror is blocked hits the same. With every source reachable the
installer must change nothing.
"""

from pathlib import Path
import shutil
import stat
import subprocess

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / 'install.sh'

UBUNTU = 'ID=ubuntu\nVERSION_CODENAME=noble\nUBUNTU_CODENAME=noble\n'
DEBIAN = 'ID=debian\nVERSION_CODENAME=bookworm\n'


def _run(tmp_path, reachable, os_release=UBUNTU, args=('--apt-fallback',)):
    bash = shutil.which('bash')
    assert bash
    probe = tmp_path / 'probe.sh'
    allowed = ' '.join(reachable)
    probe.write_text(
        '#!/bin/sh\n'
        f'for h in {allowed}; do [ "$1" = "$h" ] && exit 0; done\n'
        'exit 1\n'
    )
    probe.chmod(probe.stat().st_mode | stat.S_IEXEC)
    osr = tmp_path / 'os-release'
    osr.write_text(os_release)
    env = {
        'PATH': '/usr/bin:/bin',
        'HOME': str(tmp_path),
        'APT_FALLBACK_ROOT': str(tmp_path / 'apt'),
        'APT_FALLBACK_OS_RELEASE': str(osr),
        'APT_FALLBACK_PROBE': str(probe),
        'APT_FALLBACK_SKIP_UPDATE': '1',
    }
    return subprocess.run(
        [bash, str(INSTALL_SH), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture
def apt(tmp_path):
    d = tmp_path / 'apt' / 'sources.list.d'
    d.mkdir(parents=True)
    (tmp_path / 'apt' / 'sources.list').write_text(
        '# comment http://ignored.example.test\n'
        'deb http://archive.ubuntu.com/ubuntu noble main\n'
    )
    return tmp_path / 'apt'


def _fallback(apt):
    return apt / 'sources.list.d' / 'vibe-seller-fallback.list'


def test_all_sources_reachable_writes_nothing(tmp_path, apt):
    r = _run(tmp_path, ['archive.ubuntu.com'])
    assert r.returncode == 0, r.stderr
    assert not _fallback(apt).exists()


def test_a_dead_source_gets_the_first_reachable_fallback(tmp_path, apt):
    r = _run(tmp_path, ['mirrors.edge.kernel.org'])
    assert r.returncode == 0, r.stderr
    text = _fallback(apt).read_text()
    assert 'deb http://mirrors.edge.kernel.org/ubuntu noble main' in text
    assert 'noble-updates' in text and 'noble-security' in text
    assert 'archive.ubuntu.com' in r.stderr  # says which source was dead


def test_the_second_fallback_when_the_first_is_dead_too(tmp_path, apt):
    r = _run(tmp_path, ['mirrors.aliyun.com'])
    assert r.returncode == 0, r.stderr
    assert 'mirrors.aliyun.com/ubuntu noble' in _fallback(apt).read_text()


def test_nothing_reachable_writes_nothing_and_does_not_fail(tmp_path, apt):
    r = _run(tmp_path, [])
    assert r.returncode == 0, r.stderr
    assert not _fallback(apt).exists()
    assert 'no fallback mirror reachable' in r.stderr


def test_debian_gets_debian_and_debian_security(tmp_path, apt):
    (apt / 'sources.list').write_text(
        'deb http://deb.debian.org/debian bookworm main\n'
    )
    r = _run(tmp_path, ['mirrors.edge.kernel.org'], os_release=DEBIAN)
    assert r.returncode == 0, r.stderr
    text = _fallback(apt).read_text()
    assert 'mirrors.edge.kernel.org/debian bookworm main' in text
    assert 'mirrors.edge.kernel.org/debian-security bookworm-security' in text


def test_deb822_and_mirror_file_sources_are_probed(tmp_path, apt):
    """GitHub runners list their mirrors in a mirror+file: indirection;
    newer Ubuntu uses deb822 ``.sources``."""
    (apt / 'sources.list').unlink()
    mirrors = tmp_path / 'apt-mirrors.txt'
    mirrors.write_text('http://azure.archive.ubuntu.com/ubuntu/\tpriority:1\n')
    (apt / 'sources.list.d' / 'ubuntu.sources').write_text(
        f'Types: deb\nURIs: mirror+file:{mirrors}\nSuites: noble\n'
        'Components: main\n\n'
        'Types: deb\nURIs: http://security.ubuntu.com/ubuntu\n'
        'Suites: noble-security\nComponents: main\n'
    )
    # Only the mirror-file host is dead: it must have been found and probed.
    r = _run(tmp_path, ['security.ubuntu.com', 'mirrors.edge.kernel.org'])
    assert r.returncode == 0, r.stderr
    assert _fallback(apt).exists()
    assert 'azure.archive.ubuntu.com' in r.stderr


def test_not_a_debian_family_system_is_left_alone(tmp_path, apt):
    r = _run(tmp_path, [], os_release='ID=fedora\nVERSION_CODENAME=x\n')
    assert r.returncode == 0, r.stderr
    assert not _fallback(apt).exists()


def test_install_sh_removes_the_fallback_when_it_exits():
    """A user's machine is not left with the extra source: a normal run
    registers cleanup on exit, and the Playwright deps step checks the
    sources first."""
    src = INSTALL_SH.read_text()
    assert 'trap _apt_fallback_cleanup EXIT' in src
    pw = src[src.index('_install_playwright_chromium() {') :]
    pw = pw[: pw.index('\n}\n')]
    assert pw.index('_ensure_apt_reachable') < pw.index('"$pw" "${args[@]}"')
