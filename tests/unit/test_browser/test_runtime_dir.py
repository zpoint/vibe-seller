"""Daemon socket paths stay bindable for every supported VIBE_HOME."""

import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile

import pytest

from app.store_slugs import MAX_SLUG

pytestmark = pytest.mark.unit


def _configured_runtime_dir(repo: Path, home: Path) -> Path:
    """Resolve BH_RUNTIME_DIR in a fresh process, as a server launch does."""
    env = {
        **os.environ,
        'VIBE_HOME': str(home),
        'JWT_SECRET': 'unit-test-only',
        'VIBE_SELLER_TELEMETRY': '0',
        'VIBE_EXTERNAL_SYNC': 'off',
        'VIBE_GITHUB_MAIN_SYNC': 'off',
    }
    code = (
        'import sys; sys.path.insert(0, sys.argv[1]); '
        'from app.config import BH_RUNTIME_DIR; print(BH_RUNTIME_DIR)'
    )
    done = subprocess.run(
        [sys.executable, '-I', '-c', code, str(repo)],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(done.stdout.strip())


@pytest.mark.skipif(os.name != 'posix', reason='AF_UNIX is a POSIX contract')
def test_long_home_supports_the_longest_browser_session(tmp_path):
    """A long VIBE_HOME must not push a 64-char BU_NAME past sun_path."""
    repo = Path(__file__).resolve().parents[3]
    home = tmp_path / ('vibe-home-' + 'x' * 80)
    runtime = _configured_runtime_dir(repo, home)
    session = f'{"s" * MAX_SLUG}-deadbeef-w9'
    sock = runtime / f'bu-{session}.sock'
    resolved_sock = Path(sock.resolve())
    try:
        assert runtime != home / 'r'
        assert stat.S_IMODE(runtime.stat().st_mode) == 0o700
        # macOS sun_path holds 104 bytes including the terminating NUL,
        # and its /tmp resolves to /private/tmp.
        mac_sock = Path('/private/tmp') / runtime.name / sock.name
        assert len(os.fsencode(mac_sock)) <= 103, mac_sock
        assert len(os.fsencode(resolved_sock)) <= 103, resolved_sock
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.bind(str(sock))
    finally:
        sock.unlink(missing_ok=True)
        try:
            runtime.rmdir()
        except OSError:
            pass


def test_short_home_keeps_its_existing_daemon_directory():
    """An upgrade must not move daemon state that already has a safe path."""
    repo = Path(__file__).resolve().parents[3]
    with tempfile.TemporaryDirectory(prefix='vs-safe-') as raw_home:
        home = Path(raw_home)
        assert _configured_runtime_dir(repo, home) == home / 'r'
