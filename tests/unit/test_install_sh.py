"""Regression tests for the `install.sh` bootstrap installer.

These shell out to the real `install.sh` — they pin invariants that
are only expressible at the script level, not in the Python package.
"""

import os
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
    # resolving to the one under the fake HOME's ~/.local/bin. Resolved
    # over the sandbox's own dirs: install.sh also puts the HOST's
    # Homebrew bin first on purpose, and a host with a brew uv there
    # (any Mac developer box) would shadow the one this test planted.
    sandbox_path = os.pathsep.join(
        d for d in emitted_path.split(os.pathsep) if d.startswith(str(tmp_path))
    )
    resolved = shutil.which('uv', path=sandbox_path)
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


# ── Shared harness for the functions below ───────────────────────────


def _run_sourced(tmp_path, call, fakes=(), env=None, os_type=None):
    """Source install.sh without main(), then run `call` in bash.

    `fakes` is (name, script body) pairs put first on PATH. Returns the
    completed process; stdout+stderr are in .stdout/.stderr.
    """
    fake_bin = tmp_path / 'fakebin'
    fake_bin.mkdir(exist_ok=True)
    for name, body in fakes:
        p = fake_bin / name
        p.write_text(body)
        p.chmod(0o755)
    home = tmp_path / 'home'
    home.mkdir(exist_ok=True)
    prefix = f'OSTYPE={os_type}; ' if os_type else ''
    return subprocess.run(
        [shutil.which('bash'), '-c', f'{prefix}source "{INSTALL_SH}"; {call}'],
        env={
            'PATH': f'{fake_bin}:/usr/bin:/bin',
            'HOME': str(home),
            'VIBE_SELLER_INSTALL_SH_NO_MAIN': '1',
            **(env or {}),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )


# ── System proxy: used when it answers, ignored when it doesn't ──────
#
# Browsers follow the macOS "system proxy"; curl/uv/npm/git/Playwright
# read only *_proxy env vars. A user whose proxy client is on reached
# GitHub in the browser while install.sh stalled on its first download.

_SCUTIL_HTTP_AND_SOCKS = """#!/bin/sh
cat <<EOF
<dictionary> {
  HTTPEnable : 1
  HTTPPort : 7890
  HTTPProxy : 127.0.0.1
  HTTPSEnable : 1
  HTTPSPort : 7890
  HTTPSProxy : 127.0.0.1
  SOCKSEnable : 1
  SOCKSPort : 7891
  SOCKSProxy : 127.0.0.1
}
EOF
"""

_SCUTIL_SOCKS_ONLY = """#!/bin/sh
cat <<EOF
<dictionary> {
  HTTPEnable : 0
  SOCKSEnable : 1
  SOCKSPort : 1080
  SOCKSProxy : 10.0.0.2
}
EOF
"""

_SCUTIL_NONE = """#!/bin/sh
cat <<EOF
<dictionary> {
  HTTPEnable : 0
  HTTPSEnable : 0
  SOCKSEnable : 0
}
EOF
"""

# Answers through a proxy only when FAKE_PROXY_ALIVE=1; logs each call.
_FAKE_PROXY_CURL = """#!/bin/bash
echo "curl $*" >> "$FAKE_LOG"
if [ "$FAKE_PROXY_ALIVE" = 1 ]; then printf 200; exit 0; fi
printf 000; exit 7
"""

_PRINT_PROXY_ENV = (
    'echo "https_proxy=${https_proxy:-} HTTPS_PROXY=${HTTPS_PROXY:-} '
    'http_proxy=${http_proxy:-} all_proxy=${all_proxy:-} '
    'no_proxy=${no_proxy:-}"'
)


def _proxy_run(tmp_path, scutil, alive, **env):
    log = tmp_path / 'calls.log'
    log.write_text('')
    result = _run_sourced(
        tmp_path,
        f'setup_proxy; {_PRINT_PROXY_ENV}',
        fakes=(('scutil', scutil), ('curl', _FAKE_PROXY_CURL)),
        env={
            'FAKE_LOG': str(log),
            'FAKE_PROXY_ALIVE': '1' if alive else '0',
            **env,
        },
    )
    assert result.returncode == 0, result.stderr
    return result, log.read_text()


@pytest.mark.unit
def test_system_proxy_prefers_http_over_socks(tmp_path):
    result = _run_sourced(
        tmp_path,
        'echo "P=[$(_system_proxy)]"',
        fakes=(('scutil', _SCUTIL_HTTP_AND_SOCKS),),
    )
    assert 'P=[http://127.0.0.1:7890]' in result.stdout, result


@pytest.mark.unit
def test_system_proxy_socks_only(tmp_path):
    result = _run_sourced(
        tmp_path,
        'echo "P=[$(_system_proxy)]"',
        fakes=(('scutil', _SCUTIL_SOCKS_ONLY),),
    )
    assert 'P=[socks5h://10.0.0.2:1080]' in result.stdout, result


@pytest.mark.unit
def test_system_proxy_none_when_disabled(tmp_path):
    result = _run_sourced(
        tmp_path,
        'echo "P=[$(_system_proxy)]"',
        fakes=(('scutil', _SCUTIL_NONE),),
    )
    assert 'P=[]' in result.stdout, result


@pytest.mark.unit
def test_live_system_proxy_is_exported_for_every_downloader(tmp_path):
    result, calls = _proxy_run(tmp_path, _SCUTIL_HTTP_AND_SOCKS, alive=True)
    p = 'http://127.0.0.1:7890'
    assert f'https_proxy={p} HTTPS_PROXY={p}' in result.stdout, result
    assert f'http_proxy={p} all_proxy={p}' in result.stdout, result
    # Local servers (vibe-seller's own health checks) never go through it.
    assert 'no_proxy=localhost,127.0.0.1,::1' in result.stdout, result
    assert 'Using your system proxy' in result.stdout, result
    # It was probed through the proxy before being trusted.
    assert f'-x {p}' in calls, calls


@pytest.mark.unit
def test_dead_system_proxy_falls_back_to_direct_network(tmp_path):
    result, _ = _proxy_run(tmp_path, _SCUTIL_HTTP_AND_SOCKS, alive=False)
    assert 'https_proxy= HTTPS_PROXY= http_proxy= all_proxy=' in (
        result.stdout
    ), result
    assert 'not answering' in result.stderr, result


@pytest.mark.unit
def test_no_system_proxy_changes_nothing(tmp_path):
    result, calls = _proxy_run(tmp_path, _SCUTIL_NONE, alive=True)
    assert 'https_proxy= HTTPS_PROXY=' in result.stdout, result
    assert calls == '', 'no proxy configured, nothing to probe'


@pytest.mark.unit
def test_user_set_proxy_env_wins_unprobed(tmp_path):
    result, calls = _proxy_run(
        tmp_path,
        _SCUTIL_HTTP_AND_SOCKS,
        alive=True,
        https_proxy='http://corp-proxy:3128',
    )
    assert 'https_proxy=http://corp-proxy:3128' in result.stdout, result
    assert calls == '', calls


@pytest.mark.unit
def test_proxy_detection_can_be_switched_off(tmp_path):
    result, calls = _proxy_run(
        tmp_path, _SCUTIL_HTTP_AND_SOCKS, alive=True, VIBE_SELLER_PROXY='off'
    )
    assert 'https_proxy= HTTPS_PROXY=' in result.stdout, result
    assert calls == '', calls


@pytest.mark.unit
def test_print_path_never_probes_a_proxy(tmp_path):
    """--print-path is captured by start.sh; a probe would slow every
    start and its chatter would corrupt the captured PATH."""
    log = tmp_path / 'calls.log'
    log.write_text('')
    fake_bin = tmp_path / 'fakebin'
    fake_bin.mkdir()
    for name, body in (
        ('scutil', _SCUTIL_HTTP_AND_SOCKS),
        ('curl', _FAKE_PROXY_CURL),
    ):
        (fake_bin / name).write_text(body)
        (fake_bin / name).chmod(0o755)
    home = tmp_path / 'home'
    home.mkdir()
    result = subprocess.run(
        [shutil.which('bash'), str(INSTALL_SH), '--print-path'],
        env={
            'PATH': f'{fake_bin}:/usr/bin:/bin',
            'HOME': str(home),
            'FAKE_LOG': str(log),
            'FAKE_PROXY_ALIVE': '1',
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert log.read_text() == ''
    assert 'proxy' not in result.stdout + result.stderr


# ── Oldest supported macOS: refuse before downloading anything ───────


def _sw_vers(version):
    return f'#!/bin/sh\necho {version}\n'


def _os_check(tmp_path, version, os_type='darwin20.0'):
    return _run_sourced(
        tmp_path,
        'check_os_version; echo PASSED',
        fakes=(('sw_vers', _sw_vers(version)),),
        os_type=os_type,
    )


@pytest.mark.unit
@pytest.mark.parametrize('version', ['11.1', '12.7.6'])
def test_macos_older_than_13_is_refused(tmp_path, version):
    result = _os_check(tmp_path, version)
    assert result.returncode == 1, result
    assert 'PASSED' not in result.stdout
    assert f'macOS {version} is not supported' in result.stderr
    assert 'Software Update' in result.stderr


@pytest.mark.unit
@pytest.mark.parametrize('version', ['13.0', '14.6.1', '15.2', '26.1'])
def test_macos_13_and_newer_pass(tmp_path, version):
    result = _os_check(tmp_path, version)
    assert result.returncode == 0, result.stderr
    assert 'PASSED' in result.stdout


@pytest.mark.unit
def test_os_check_ignores_linux(tmp_path):
    result = _os_check(tmp_path, '11.1', os_type='linux-gnu')
    assert result.returncode == 0, result.stderr
    assert 'PASSED' in result.stdout


@pytest.mark.unit
def test_os_check_runs_before_any_download():
    """The check must sit ahead of the proxy probe, the --dev clone and
    every installer — the point is to stop before ~400 MB of downloads.
    """
    text = INSTALL_SH.read_text()
    call = text.index('\n    check_os_version\n')
    for later in (
        '\n    setup_proxy\n',
        'git clone "$REPO_URL"',
        '\nmain() {',
    ):
        assert call < text.index(later), later


# ── macOS without Homebrew: Node from the official tarball ───────────

# Serves SHASUMS256.txt and the tarball; FAKE_DOWN_HOST never answers.
_FAKE_NODE_CURL = """#!/bin/bash
echo "curl $*" >> "$FAKE_LOG"
out=""
url=""
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out=$2; shift ;;
    http*) url=$1 ;;
  esac
  shift
done
if [ -n "$FAKE_DOWN_HOST" ] && [[ "$url" == *"$FAKE_DOWN_HOST"* ]]; then
  exit 7
fi
case "$url" in
  */SHASUMS256.txt)
    echo "abc  node-v22.9.9-darwin-x64.tar.gz"
    echo "def  node-v22.9.9-darwin-arm64.tar.gz" ;;
  *.tar.gz) cp "$FAKE_TARBALL" "$out" ;;
  *) exit 22 ;;
esac
"""


def _node_tarball(tmp_path):
    root = tmp_path / 'tarsrc' / 'node-v22.9.9-darwin-x64' / 'bin'
    root.mkdir(parents=True)
    (root / 'node').write_text('#!/bin/sh\necho v22.9.9\n')
    (root / 'node').chmod(0o755)
    (root / 'npm').write_text('#!/bin/sh\necho 10.0.0\n')
    (root / 'npm').chmod(0o755)
    tarball = tmp_path / 'node.tar.gz'
    subprocess.run(
        ['tar', '-czf', str(tarball), '-C', str(tmp_path / 'tarsrc'), '.'],
        check=True,
    )
    return tarball


def _install_node_no_brew(tmp_path, **env):
    log = tmp_path / 'calls.log'
    log.write_text('')
    result = _run_sourced(
        tmp_path,
        'OS=macos; ARCH=x86_64; install_node; echo "NODE=$(node -v)"',
        fakes=(('curl', _FAKE_NODE_CURL),),
        env={
            'FAKE_LOG': str(log),
            'FAKE_TARBALL': str(_node_tarball(tmp_path)),
            **env,
        },
    )
    return result, log.read_text()


@pytest.mark.unit
def test_macos_without_homebrew_installs_node_tarball(tmp_path):
    result, calls = _install_node_no_brew(tmp_path)
    assert result.returncode == 0, result.stderr
    assert 'NODE=v22.9.9' in result.stdout, result
    home = tmp_path / 'home'
    assert (home / '.local' / 'bin' / 'node').resolve() == (
        home / '.local/node/node-v22.9.9-darwin-x64/bin/node'
    ).resolve()
    assert 'nodejs.org/dist/latest-v22.x' in calls, calls
    assert 'Homebrew' not in result.stderr


@pytest.mark.unit
def test_node_tarball_falls_back_to_mirror(tmp_path):
    result, calls = _install_node_no_brew(tmp_path, FAKE_DOWN_HOST='nodejs.org')
    assert result.returncode == 0, result.stderr
    assert 'NODE=v22.9.9' in result.stdout, result
    assert 'npmmirror.com/mirrors/node/latest-v22.x/node-v22.9.9' in calls


# ── A claude that is present but does not run is not "found" ─────────


@pytest.mark.unit
def test_check_claude_rejects_a_cli_that_does_not_run(tmp_path):
    """Re-running install.sh after Claude Code failed to start (macOS 11:
    dyld abort) printed "[ok] claude CLI found" and hid the failure."""
    vs_home = tmp_path / 'vs'
    bin_dir = vs_home / 'node_modules' / '.bin'
    bin_dir.mkdir(parents=True)
    claude = bin_dir / 'claude'
    claude.write_text(
        '#!/bin/sh\necho "dyld: Symbol not found" >&2\nexit 134\n'
    )
    claude.chmod(0o755)
    result = _run_sourced(
        tmp_path,
        'if check_claude; then echo FOUND; else echo MISSING; fi',
        env={'VIBE_HOME': str(vs_home)},
    )
    assert 'MISSING' in result.stdout, result
    assert 'claude CLI found' not in result.stdout


@pytest.mark.unit
def test_check_claude_accepts_a_cli_that_runs(tmp_path):
    vs_home = tmp_path / 'vs'
    bin_dir = vs_home / 'node_modules' / '.bin'
    bin_dir.mkdir(parents=True)
    claude = bin_dir / 'claude'
    claude.write_text('#!/bin/sh\necho "2.1.0 (Claude Code)"\n')
    claude.chmod(0o755)
    result = _run_sourced(
        tmp_path,
        'if check_claude; then echo FOUND; else echo MISSING; fi',
        env={'VIBE_HOME': str(vs_home)},
    )
    assert 'FOUND' in result.stdout, result


# ── Root commands keep the proxy ─────────────────────────────────────

# sudo resets the environment; this one shows what reached the command.
_FAKE_SUDO = '#!/bin/sh\necho "SUDO $*"\n'


@pytest.mark.unit
def test_sudo_carries_the_proxy_to_root_commands(tmp_path):
    """sudo's env_reset dropped *_proxy, so behind a proxy apt went
    direct and failed (seen in the blocked-network install test)."""
    result = _run_sourced(
        tmp_path,
        'is_root() { return 1; }; _sudo apt-get update -qq',
        fakes=(('sudo', _FAKE_SUDO),),
        env={'https_proxy': 'http://p:7890', 'no_proxy': 'localhost'},
    )
    assert result.returncode == 0, result.stderr
    assert (
        'SUDO env https_proxy=http://p:7890 no_proxy=localhost '
        'apt-get update -qq'
    ) in result.stdout, result


@pytest.mark.unit
def test_sudo_without_a_proxy_is_plain_sudo(tmp_path):
    result = _run_sourced(
        tmp_path,
        'is_root() { return 1; }; _sudo apt-get update -qq',
        fakes=(('sudo', _FAKE_SUDO),),
    )
    assert 'SUDO env apt-get update -qq' in result.stdout, result


@pytest.mark.unit
def test_no_bare_sudo_package_installs():
    """Every root command goes through _sudo, or it loses the proxy."""
    code = [
        line.strip()
        for line in INSTALL_SH.read_text().splitlines()
        if not line.lstrip().startswith('#')
    ]
    # Allowed: the credential check (sudo -n / -v) and _sudo itself.
    bare = [
        line
        for line in code
        if line.startswith('sudo ')
        and not line.startswith(('sudo -n', 'sudo -v', 'sudo env '))
    ]
    assert not bare, bare
    # Playwright's --with-deps calls sudo itself and loses the proxy.
    assert not [line for line in code if '--with-deps' in line]
