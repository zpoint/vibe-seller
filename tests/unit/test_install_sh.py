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


# -- Playwright Chromium download source (official CDN vs mirror) ------
#
# install.sh measures cdn.playwright.dev against npmmirror before
# `playwright install chromium` and uses the faster one. The invariant
# that matters: a mirror is only ever picked when it carries EVERY file
# this Playwright version will fetch — the mirror's path layout has
# changed across Playwright versions (1.58 404s there, 1.63 doesn't),
# and a half-matching mirror would fail mid-install. Fakes stand in for
# `playwright` and `curl` so these run offline and deterministically.

_MIRROR = 'https://cdn.npmmirror.com/binaries/playwright'

_FAKE_PLAYWRIGHT = r"""#!/usr/bin/env bash
echo "${PLAYWRIGHT_DOWNLOAD_HOST:-}|$*" >> "$FAKE_LOG"
if [[ "$*" == *--dry-run* ]]; then
  h=${PLAYWRIGHT_DOWNLOAD_HOST:-https://cdn.playwright.dev}
  echo "  Download url:        $h/builds/cft/1/linux64/chrome-linux64.zip"
  echo "  Download url:        $h/builds/ffmpeg/1/ffmpeg-linux.zip"
  exit 0
fi
if [ -n "${FAKE_INSTALL_FAILS_FOR:-}" ] && \
   [ "${PLAYWRIGHT_DOWNLOAD_HOST:-}" = "$FAKE_INSTALL_FAILS_FOR" ]; then
  exit 1
fi
exit 0
"""

# HEAD (-sIL) prints a status code; a ranged GET prints "code speed".
# FAKE_404 / FAKE_DOWN are URL substrings that 404 / don't answer.
_FAKE_CURL = r"""#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
url="${@: -1}"
code=200
if [ -n "${FAKE_DOWN:-}" ] && [[ "$url" == *"$FAKE_DOWN"* ]]; then
  code=000
elif [ -n "${FAKE_404:-}" ] && [[ "$url" == *"$FAKE_404"* ]]; then
  code=404
fi
for a in "$@"; do
  if [ "$a" = -sIL ]; then printf '%s' "$code"; exit 0; fi
done
if [ "$code" = 000 ]; then printf '000 0'; exit 28; fi
if [[ "$url" == *npmmirror* ]]; then speed=$FAKE_MIRROR_SPEED
else speed=$FAKE_OFFICIAL_SPEED; fi
printf '206 %s.000' "$speed"
"""


def _run_pw_source(tmp_path, call, **env):
    """Source install.sh (without running main) and run `call`.

    Returns (completed process, list of logged fake invocations).
    """
    fake_bin = tmp_path / 'fakebin'
    fake_bin.mkdir(exist_ok=True)
    for name, body in (('playwright', _FAKE_PLAYWRIGHT), ('curl', _FAKE_CURL)):
        p = fake_bin / name
        p.write_text(body)
        p.chmod(0o755)
    log = tmp_path / 'calls.log'
    log.write_text('')
    full_env = {
        'PATH': f'{fake_bin}:/usr/bin:/bin',
        'HOME': str(tmp_path),
        'FAKE_LOG': str(log),
        'PW': str(fake_bin / 'playwright'),
        'VIBE_SELLER_INSTALL_SH_NO_MAIN': '1',
        'FAKE_OFFICIAL_SPEED': '1000000',
        'FAKE_MIRROR_SPEED': '1000000',
        **env,
    }
    result = subprocess.run(
        [
            shutil.which('bash'),
            '-c',
            f'source "{INSTALL_SH}"; OS=macos; {call}',
        ],
        env=full_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result, log.read_text().splitlines()


def _picked(tmp_path, **env):
    result, _ = _run_pw_source(
        tmp_path, 'echo "PICKED=[$(_pick_playwright_host "$PW")]"', **env
    )
    assert result.returncode == 0, result.stderr
    line = [x for x in result.stdout.splitlines() if x.startswith('PICKED=')]
    assert line, result.stdout
    return line[-1][len('PICKED=[') : -1]


@pytest.mark.unit
def test_pw_source_picks_faster_mirror(tmp_path):
    assert (
        _picked(
            tmp_path,
            FAKE_OFFICIAL_SPEED='1000000',
            FAKE_MIRROR_SPEED='9000000',
        )
        == _MIRROR
    )


@pytest.mark.unit
def test_pw_source_keeps_official_when_it_is_faster(tmp_path):
    assert (
        _picked(
            tmp_path,
            FAKE_OFFICIAL_SPEED='9000000',
            FAKE_MIRROR_SPEED='1000000',
        )
        == ''
    )


@pytest.mark.unit
def test_pw_source_never_picks_a_mirror_missing_a_file(tmp_path):
    """A faster mirror that 404s on ANY file must lose to the official CDN."""
    assert (
        _picked(
            tmp_path,
            FAKE_OFFICIAL_SPEED='1000000',
            FAKE_MIRROR_SPEED='9000000',
            FAKE_404='npmmirror.com/binaries/playwright/builds/ffmpeg',
        )
        == ''
    )


@pytest.mark.unit
def test_pw_source_falls_back_to_mirror_when_official_unreachable(tmp_path):
    assert (
        _picked(
            tmp_path,
            FAKE_DOWN='cdn.playwright.dev',
            FAKE_MIRROR_SPEED='500000',
        )
        == _MIRROR
    )


@pytest.mark.unit
def test_pw_source_defaults_to_official_when_nothing_answers(tmp_path):
    # '' lets Playwright use its own CDN fallbacks.
    assert _picked(tmp_path, FAKE_DOWN='https://') == ''


@pytest.mark.unit
def test_pw_install_honors_user_download_host_without_probing(tmp_path):
    result, calls = _run_pw_source(
        tmp_path,
        '_install_playwright_chromium "$PW"',
        PLAYWRIGHT_DOWNLOAD_HOST='https://my-mirror.test',
    )
    assert result.returncode == 0, result.stderr
    assert not [c for c in calls if c.startswith('curl ')], calls
    assert calls == ['https://my-mirror.test|install chromium'], calls


@pytest.mark.unit
def test_pw_install_retries_official_when_mirror_install_fails(tmp_path):
    """The probe may make an install faster, never break a working one."""
    result, calls = _run_pw_source(
        tmp_path,
        '_install_playwright_chromium "$PW"',
        FAKE_OFFICIAL_SPEED='1000000',
        FAKE_MIRROR_SPEED='9000000',
        FAKE_INSTALL_FAILS_FOR=_MIRROR,
    )
    assert result.returncode == 0, result.stderr
    installs = [c for c in calls if '|install chromium' in c]
    assert installs == [
        f'{_MIRROR}|install chromium',
        '|install chromium',
    ], calls


@pytest.mark.unit
def test_every_chromium_install_goes_through_the_source_picker():
    """Both install paths (end-user and --dev) must call the helper; a
    bare `playwright install` would skip the faster-source choice
    silently, and every test above would still pass."""
    text = INSTALL_SH.read_text()
    helper_start = text.index('_install_playwright_chromium() {')
    helper_end = text.index('\n}\n', helper_start)
    outside = text[:helper_start] + text[helper_end:]
    bare = [
        line.strip()
        for line in outside.splitlines()
        if 'playwright"' in line
        and ' install' in line
        and '--dry-run' not in line
        and not line.lstrip().startswith('#')
    ]
    assert not bare, f'bare playwright install outside the helper: {bare}'
    assert outside.count('_install_playwright_chromium "$') == 2, (
        'expected the end-user and --dev paths to call the helper'
    )


# ── Claude Code CLI: installed means it RUNS ─────────────────────────

# A fake npm that lays down what `npm install @anthropic-ai/claude-code`
# does when install scripts are skipped: the package, its install.cjs,
# and a `.bin/claude` stub that answers only once the native binary is
# in place (the marker install.cjs creates).
_FAKE_NPM = """#!/bin/bash
prefix=""
while [ $# -gt 0 ]; do
  [ "$1" = --prefix ] && { prefix=$2; shift; }
  shift
done
pkg="$prefix/node_modules/@anthropic-ai/claude-code"
mkdir -p "$pkg" "$prefix/node_modules/.bin"
echo '// install.cjs' > "$pkg/install.cjs"
cat > "$prefix/node_modules/.bin/claude" <<STUB
#!/bin/sh
[ -f "$pkg/native" ] || { echo "claude native binary not installed" >&2; exit 1; }
echo "9.9.9 (Claude Code)"
STUB
chmod +x "$prefix/node_modules/.bin/claude"
exit 0
"""

# `node install.cjs` puts the native binary in place — unless the test
# says the install step itself is broken.
_FAKE_NODE = """#!/bin/bash
[ "$1" = install.cjs ] || exit 2
[ -n "$FAKE_INSTALL_BROKEN" ] && exit 1
touch native
"""


def _run_install_claude(tmp_path, **env):
    fake_bin = tmp_path / 'fakebin'
    fake_bin.mkdir(exist_ok=True)
    for name, body in (('npm', _FAKE_NPM), ('node', _FAKE_NODE)):
        p = fake_bin / name
        p.write_text(body)
        p.chmod(0o755)
    home = tmp_path / 'vs-home'
    return subprocess.run(
        [
            shutil.which('bash'),
            '-c',
            # install.sh runs under `set -e`; read the status in an `if`.
            f'source "{INSTALL_SH}"; OS=macos; '
            'if install_claude; then echo RC=0; else echo "RC=$?"; fi',
        ],
        env={
            'PATH': f'{fake_bin}:/usr/bin:/bin',
            'HOME': str(tmp_path),
            'VIBE_HOME': str(home),
            'VIBE_SELLER_INSTALL_SH_NO_MAIN': '1',
            **env,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.unit
def test_install_claude_runs_the_install_step_npm_skipped(tmp_path):
    """Newer npm skips the package's postinstall and still exits 0. The
    installer must run that step itself and report the CLI's version."""
    result = _run_install_claude(tmp_path)
    out = result.stdout + result.stderr
    assert 'RC=0' in out, out
    assert 'running it (node install.cjs)' in out, out
    assert '9.9.9 (Claude Code)' in out, out


@pytest.mark.unit
def test_install_claude_never_reports_a_cli_that_does_not_run(tmp_path):
    result = _run_install_claude(tmp_path, FAKE_INSTALL_BROKEN='1')
    out = result.stdout + result.stderr
    assert 'RC=1' in out, out
    assert 'installed but does not run' in out, out
    assert 'claude CLI installed (project-local)' not in out, out
