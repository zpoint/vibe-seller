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


def _run_print_path(tmp_path, extra_bin_files=()):
    """Run `install.sh --print-path` with a curated, uv-free PATH.

    Returns (result, fake_home, emitted_path). `extra_bin_files` is a
    list of (relative_path_under_home, contents) executables to seed —
    e.g. ('.local/bin/uv', '') to place a uv the caller's PATH lacks.
    """
    fake_bin = tmp_path / 'bin'
    fake_bin.mkdir()
    for tool in _INFRA_TOOLS:
        src = shutil.which(tool)
        if src:
            (fake_bin / tool).symlink_to(src)
    fake_home = tmp_path / 'home'
    fake_home.mkdir()
    for rel, contents in extra_bin_files:
        dest = fake_home / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(contents or '#!/bin/sh\n')
        dest.chmod(0o755)

    bash = shutil.which('bash')
    assert bash, 'bash is required to run this test'
    env = {'PATH': str(fake_bin), 'HOME': str(fake_home)}
    # uv must NOT be on the caller's PATH — that is the whole point.
    assert shutil.which('uv', path=env['PATH']) is None

    result = subprocess.run(
        [bash, str(INSTALL_SH), '--print-path'],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, fake_home, result.stdout


@pytest.mark.unit
def test_print_path_makes_local_uv_resolvable(tmp_path):
    """`--print-path` must emit a PATH on which a ~/.local/bin uv resolves.

    Regression (izz-mac): uv installs to ~/.local/bin, which a login
    shell may not have on PATH. `install.sh --check-only` passed anyway
    because it bootstraps that dir into its *own* process; that export
    died with the subprocess, so start.sh's `env … uv run …` failed with
    `env: uv: No such file`. The fix makes install.sh the single source
    of truth via `--print-path`, and start.sh adopts it — so the PATH
    the check validated and the PATH the launch uses are identical.
    """
    result, fake_home, emitted = _run_print_path(
        tmp_path, extra_bin_files=[('.local/bin/uv', '#!/bin/sh\n')]
    )

    assert result.returncode == 0, (
        f'--print-path failed: stdout={result.stdout!r} '
        f'stderr={result.stderr!r}'
    )
    emitted_path = emitted.strip()
    assert emitted_path, 'nothing printed'
    # The decisive assertion: uv is now findable on the emitted PATH,
    # resolving to the one under the fake HOME's ~/.local/bin.
    resolved = shutil.which('uv', path=emitted_path)
    assert resolved == str(fake_home / '.local' / 'bin' / 'uv'), (
        f'uv not resolvable on emitted PATH; resolved={resolved!r} '
        f'emitted={emitted_path!r}'
    )


@pytest.mark.unit
def test_print_path_has_no_install_side_effects(tmp_path):
    """`--print-path` is a pure query: one PATH line, no install output.

    It must exit before the clone bootstrap, sudo, and dependency
    install — so it prints exactly the PATH and none of the installer's
    `==>`/`[ok]` chatter (which would corrupt the value start.sh
    captures via command substitution).
    """
    result, _fake_home, emitted = _run_print_path(tmp_path)

    assert result.returncode == 0, (
        f'--print-path failed: stderr={result.stderr!r}'
    )
    combined = result.stdout + result.stderr
    for noise in ('[ok]', '==>', '[error]', 'sudo'):
        assert noise not in combined, (
            f'--print-path leaked installer output ({noise!r}): {combined!r}'
        )
    # A PATH is a single line — no trailing newline chatter beyond it.
    assert emitted.strip().count('\n') == 0, (
        f'--print-path emitted more than one line: {emitted!r}'
    )


@pytest.mark.unit
def test_start_sh_derives_path_from_install_print_path():
    """start.sh must resolve tools via `install.sh --print-path`.

    Static guard: the wiring that closes the check-vs-run PATH gap must
    run before start.sh invokes uv, and must not be silently dropped.
    """
    start_sh = (REPO_ROOT / 'start.sh').read_text()
    assert '--print-path' in start_sh, (
        'start.sh no longer asks install.sh for its canonical PATH'
    )
    bootstrap_at = start_sh.index('--print-path')
    # `uv run python` is the actual server launch; plain `uv run` also
    # appears in the bootstrap comment, so match the launch specifically.
    launch_at = start_sh.index('uv run python')
    assert bootstrap_at < launch_at, (
        'start.sh must set PATH via --print-path *before* it runs uv'
    )
