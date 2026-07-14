"""Regression tests for the `install.sh` bootstrap installer.

These shell out to the real `install.sh` — they pin invariants that
are only expressible at the script level, not in the Python package.
"""

from pathlib import Path
import shutil
import subprocess

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SH = REPO_ROOT / 'install.sh'

# Infra tools install.sh needs *before* the dependency checks (path
# resolution, checkout detection, platform detection). Deliberately
# excludes the DEPS tools (curl/git/uv/node/pnpm/sqlite3/lsof) — and
# crucially npm/node — so the script runs as if on a fresh machine.
_INFRA_TOOLS = ('dirname', 'grep', 'uname', 'sed', 'awk', 'cut', 'cat', 'tr')


@pytest.mark.unit
def test_check_only_survives_missing_npm(tmp_path):
    """`install.sh --check-only` must not abort with 127 when npm is absent.

    Regression: main()'s best-effort PATH bootstrap ran
    `_npm_bin="$(npm config get prefix ...)/bin"` *before* the DEPS loop
    that installs node/npm. On a fresh machine npm doesn't exist, so the
    command substitution exits 127; `2>/dev/null` hid the message but not
    the status, and `set -e` killed the whole installer right after the
    platform check. `--check-only` shares that pre-DEPS block, so it
    reproduces the bug without installing anything.
    """
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    for tool in _INFRA_TOOLS:
        src = shutil.which(tool)
        if src:
            (fake_bin / tool).symlink_to(src)
    # npm/node must NOT be resolvable — assert the curated PATH is clean.
    fake_home = tmp_path / 'home'
    fake_home.mkdir()

    bash = shutil.which('bash')
    assert bash, 'bash is required to run this test'

    env = {'PATH': str(fake_bin), 'HOME': str(fake_home)}
    assert shutil.which('npm', path=env['PATH']) is None
    assert shutil.which('node', path=env['PATH']) is None

    result = subprocess.run(
        [bash, str(INSTALL_SH), '--check-only'],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    combined = result.stdout + result.stderr

    assert result.returncode != 127, (
        'install.sh aborted with 127 — the npm-missing set -e bug. '
        f'stdout={result.stdout!r} stderr={result.stderr!r}'
    )
    # Positive signal that it ran past the crash point through the DEPS
    # loop to the --check-only summary (rather than dying early some
    # other way). With every dep missing it reports them and exits 1.
    assert 'required tool' in combined, (
        'install.sh did not reach the dependency-check summary; '
        f'stdout={result.stdout!r} stderr={result.stderr!r}'
    )
