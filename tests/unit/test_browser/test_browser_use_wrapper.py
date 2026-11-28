"""Tests for the browser-use 0.13 CLI wrapper isolation.

Generates wrapper scripts to a tmp dir and verifies the 0.13 contract:
- Default session → ``BU_NAME=<slug>`` env (no ``--session`` flag)
- Allowed sessions pass, wrong sessions blocked (exit 1)
- The CDP endpoint is injected via ``BU_CDP_WS`` env (not ``--cdp-url``)
- ``--cdp-url``/``--cdp-ws``/``--mcp``/``--connect``/``--profile`` blocked
- Agent-supplied ``BU_NAME``/``BU_CDP_WS`` env is rejected
- ``BH_RUNTIME_DIR`` daemon-state dir is exported (shared, named files)
- Wedge recovery bounds each call + reloads the daemon on timeout
- Concurrent stores get separate isolated wrappers
"""

import os
from pathlib import Path
import re
import subprocess
from unittest import mock

import pytest

from app.browser.manager import write_browser_use_wrapper
from app.browser.web_wrapper import write_web_browser_use_wrapper
from app.browser.wrapper import atomic_write_script
from app.config import DOWNLOADS_DIR, WEB_BROWSER_SLUG


def _stub_real_bu(tmp_path: Path) -> str:
    """A fake browser-use that prints its args + the BU_/BH_ env the
    wrapper injected — so tests can assert the 0.13 env contract."""
    stub = tmp_path / 'fake_bu.sh'
    stub.write_text(
        '#!/usr/bin/env bash\n'
        'echo "ARGS: $*"\n'
        'env | grep -E "^(BU_|BH_|VIBE_STORE)" | sort\n'
    )
    stub.chmod(0o755)
    return str(stub)


def _generate_wrapper(
    tmp_path: Path,
    store_name: str = 'test-store',
    backend: str = 'ziniao',
    proxy_port: int | None = 9222,
    store_id: str = 'store-1',
    slug: str | None = None,
) -> Path:
    """Generate a wrapper with REAL_BU pointed at the env-printing stub
    and curl stubbed to the ownership-aware stand-in, so auto-start
    passes offline (the probe must see THIS instance's id)."""
    bin_dir = tmp_path / 'bin'
    with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
        write_browser_use_wrapper(
            store_name,
            backend,
            proxy_port,
            store_id=store_id,
            slug=slug or store_name,
            instance_id='test-instance',
        )
    wrapper = bin_dir / (slug or store_name) / 'browser-use'
    content = wrapper.read_text()
    content = re.sub(
        r'REAL_BU="[^"]*"', f'REAL_BU="{_stub_real_bu(tmp_path)}"', content
    )
    content = content.replace('curl ', f'"{_stub_curl(tmp_path)}" ')
    wrapper.write_text(content)
    return wrapper


def _stub_curl(tmp_path: Path) -> Path:
    """Offline curl stand-in: answers the ownership probe (/vibe/instance
    with the wrapper's baked test-instance id), the store start API
    (typed proxy_port back), and the aux/start API with a fixed ws."""
    stub = tmp_path / 'curl-stub'
    if not stub.exists():
        stub.write_text(
            '#!/usr/bin/env bash\n'
            'for a in "$@"; do\n'
            '  case "$a" in\n'
            '    *vibe/instance*)\n'
            '    echo \'{"instance": "test-instance"}\'; exit 0;;\n'
            '    *browser/start*)\n'
            '    echo \'{"ok":true,"proxy_port":9222,'
            '"instance_id":"test-instance"}\'; exit 0;;\n'
            '    *browser/aux/start*)\n'
            '    echo \'{"ok":true,"proxy_port":9450,'
            '"ws":"ws://127.0.0.1:9450/client-aux"}\'; exit 0;;\n'
            '  esac\n'
            'done\n'
            'exit 0\n'
        )
        stub.chmod(0o755)
    return stub


def _run_wrapper(
    wrapper: Path, *args: str, env: dict | None = None
) -> subprocess.CompletedProcess:
    """Run the wrapper script with the given arguments."""
    return subprocess.run(
        [str(wrapper), *args],
        capture_output=True,
        text=True,
        timeout=15,
        env=env,
    )


def _env_without_task() -> dict:
    return {k: v for k, v in os.environ.items() if k != 'VIBE_TASK_ID'}


@pytest.mark.unit
class TestWrapperDefaultSession:
    """Default session should be the store slug, injected as BU_NAME."""

    def test_default_session_is_store(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(wrapper, env=_env_without_task())
        assert result.returncode == 0, result.stderr
        assert 'BU_NAME=test-store' in result.stdout

    def test_cdp_ws_injected(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(wrapper, env=_env_without_task())
        assert 'BU_CDP_WS=ws://' in result.stdout
        assert 'client-' in result.stdout


@pytest.mark.unit
class TestWrapperSessionValidation:
    """Session allow/deny logic (BU_NAME derived from --session shim)."""

    def test_explicit_store_session_allowed(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(
            wrapper, '--session', 'test-store', env=_env_without_task()
        )
        assert result.returncode == 0

    def test_aux_session_allowed(self, tmp_path: Path):
        wrapper = _generate_wrapper(
            tmp_path, store_name='test-store', backend='chrome'
        )
        result = _run_wrapper(
            wrapper, '--session', 'test-store-aux', env=_env_without_task()
        )
        assert result.returncode == 0

    def test_wrong_session_blocked(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(
            wrapper, '--session', 'other-store', env=_env_without_task()
        )
        assert result.returncode == 1
        assert 'not allowed' in result.stderr

    def test_per_task_session_allowed(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(
            wrapper, '--session', 'test-store-a1b2c3d4', env=_env_without_task()
        )
        assert result.returncode == 0
        assert 'BU_NAME=test-store-a1b2c3d4' in result.stdout

    def test_per_task_session_uppercase_hex_allowed(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(
            wrapper, '--session', 'test-store-A1B2C3D4', env=_env_without_task()
        )
        assert result.returncode == 0

    def test_per_task_session_wrong_length_blocked(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        for bad in ('test-store-a1b2c3d', 'test-store-a1b2c3d4e'):
            result = _run_wrapper(
                wrapper, '--session', bad, env=_env_without_task()
            )
            assert result.returncode == 1
            assert 'not allowed' in result.stderr

    def test_per_task_session_invalid_chars_blocked(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(
            wrapper, '--session', 'test-store-a1b2c3gh', env=_env_without_task()
        )
        assert result.returncode == 1
        assert 'not allowed' in result.stderr


@pytest.mark.unit
class TestWrapperFlagBlocking:
    """Dangerous flags and agent env overrides are rejected."""

    def test_cdp_url_blocked(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(
            wrapper, '--cdp-url', 'http://evil', env=_env_without_task()
        )
        assert result.returncode == 1
        assert 'managed by the wrapper' in result.stderr

    def test_cdp_ws_blocked(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(
            wrapper, '--cdp-ws', 'ws://evil', env=_env_without_task()
        )
        assert result.returncode == 1
        assert 'managed by the wrapper' in result.stderr

    def test_mcp_flag_blocked(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(wrapper, '--mcp', env=_env_without_task())
        assert result.returncode == 1
        assert '--mcp' in result.stderr

    def test_connect_flag_blocked(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(wrapper, '--connect', env=_env_without_task())
        assert result.returncode == 1
        assert '--connect' in result.stderr

    def test_profile_flag_blocked(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(
            wrapper, '--profile', 'Default', env=_env_without_task()
        )
        assert result.returncode == 1
        assert '--profile' in result.stderr

    def test_agent_bu_name_env_rejected(self, tmp_path: Path):
        """An agent that presets BU_NAME (to hijack the session) is
        rejected — the wrapper owns that env var."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        env = {**_env_without_task(), 'BU_NAME': 'other-store'}
        result = _run_wrapper(wrapper, env=env)
        assert result.returncode == 1
        assert 'BU_NAME is managed by the wrapper' in result.stderr

    def test_agent_bu_cdp_ws_env_rejected(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        env = {**_env_without_task(), 'BU_CDP_WS': 'ws://evil'}
        result = _run_wrapper(wrapper, env=env)
        assert result.returncode == 1
        assert 'BU_CDP_WS is managed by the wrapper' in result.stderr

    def test_session_override_blocked_in_agent(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        env = {
            **os.environ,
            'VIBE_TASK_ID': 'a1b2c3d4-0000-0000-0000-000000000000',
        }
        result = _run_wrapper(wrapper, '--session', 'test-store', env=env)
        assert result.returncode == 1
        assert 'auto-assigned' in result.stderr

    def test_session_aux_allowed_in_agent(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        env = {
            **os.environ,
            'VIBE_TASK_ID': 'a1b2c3d4-0000-0000-0000-000000000000',
        }
        result = _run_wrapper(wrapper, '--session', 'test-store-aux', env=env)
        assert result.returncode == 0, result.stderr
        # v4: ziniao aux attaches to the ws the aux/start API returned.
        assert 'BU_CDP_WS=ws://127.0.0.1:9450/client-aux' in result.stdout

    def test_session_override_allowed_without_task(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(
            wrapper, '--session', 'test-store', env=_env_without_task()
        )
        assert result.returncode == 0


@pytest.mark.unit
class TestWrapperEnvInjection:
    """0.13 injects connection identity via env, not flags."""

    def test_ziniao_wrapper_injects_bu_env(self, tmp_path: Path):
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'test-store', 'ziniao', 9222, store_id='s1', slug='test-store'
            )
        content = (bin_dir / 'test-store' / 'browser-use').read_text()
        assert 'export BU_NAME="$SESSION"' in content
        assert 'export BU_CDP_WS="ws://' in content
        assert 'export BH_RUNTIME_DIR=' in content
        assert 'BH_RUNTIME_DIR_SHARED=1' in content
        # No 0.12-style flag INJECTION into the exec line (only the
        # blocking case-arm may still mention --cdp-url).
        assert 'CDP_ARGS' not in content
        assert '"--cdp-url" "ws://' not in content

    def test_chrome_wrapper_injects_bu_env(self, tmp_path: Path):
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'storec', 'chrome', 9222, store_id='s2', slug='storec'
            )
        content = (bin_dir / 'storec' / 'browser-use').read_text()
        assert 'export BU_NAME="$SESSION"' in content
        assert 'export BU_CDP_WS="ws://' in content

    def test_wrapper_exports_resolved_downloads_dir(self, tmp_path: Path):
        """VIBE_STORE_DOWNLOADS is baked in RESOLVED for this server's
        VIBE_HOME — the one path a doc must never spell. A hardcoded
        default-home path sent a live agent into another server's home
        on a multi-home machine."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        content = wrapper.read_text()
        expected = str(DOWNLOADS_DIR / 'test-store')
        assert f'export VIBE_STORE_DOWNLOADS="{expected}"' in content
        # And it actually reaches the exec'd browser-use process:
        result = _run_wrapper(wrapper, env=_env_without_task())
        assert result.returncode == 0, result.stderr
        assert f'VIBE_STORE_DOWNLOADS={expected}' in result.stdout

    def test_wrapper_downloads_dir_overrides_inherited_value(
        self, tmp_path: Path
    ):
        """An inherited (e.g. agent-guessed) VIBE_STORE_DOWNLOADS is
        overwritten by the wrapper's authoritative value, not honored."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        env = _env_without_task()
        env['VIBE_STORE_DOWNLOADS'] = '/home/guessed/downloads'
        result = _run_wrapper(wrapper, env=env)
        assert result.returncode == 0, result.stderr
        expected = str(DOWNLOADS_DIR / 'test-store')
        assert f'VIBE_STORE_DOWNLOADS={expected}' in result.stdout
        assert '/home/guessed' not in result.stdout

    def test_ziniao_aux_gets_own_proxy_client(self, tmp_path: Path):
        """Ziniao -aux gets an EXPLICIT endpoint on this store's proxy.

        The old "Chrome direct" exemption exported no BU_CDP_WS, so the
        daemon fell back to ambient Chrome discovery and attached to a
        DIFFERENT store's browser (wrong Amazon account, wrong
        downloads dir) — observed live with two Ziniao stores. Every
        allowed session must carry an explicit endpoint; aux uses the
        stable client-aux id so it never shares a task client's tabs.
        """
        wrapper = _generate_wrapper(
            tmp_path, store_name='test-store', backend='ziniao'
        )
        result = _run_wrapper(
            wrapper, '--session', 'test-store-aux', env=_env_without_task()
        )
        assert result.returncode == 0, result.stderr
        assert 'BU_NAME=test-store-aux' in result.stdout
        # v4: the DEDICATED login-less aux browser's proxy (returned by
        # the lazy aux/start API) — NOT the main (Ziniao) proxy.
        assert 'BU_CDP_WS=ws://127.0.0.1:9450/client-aux' in result.stdout
        assert 'BU_CDP_WS=ws://127.0.0.1:9222' not in result.stdout

    def test_autostart_polls(self, tmp_path: Path):
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'test-store', 'ziniao', 9222, store_id='s1', slug='test-store'
            )
        content = (bin_dir / 'test-store' / 'browser-use').read_text()
        assert '--max-time 90' in content
        assert 'while [' in content
        assert 'sleep 1' in content


@pytest.mark.unit
class TestWrapperWedgeRecovery:
    """Each call is timeout-bounded; a wedge reloads the daemon."""

    def test_wrapper_bounds_and_reloads(self, tmp_path: Path):
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'test-store', 'ziniao', 9222, store_id='s1', slug='test-store'
            )
        content = (bin_dir / 'test-store' / 'browser-use').read_text()
        # Hard timeout via perl alarm (macOS has no GNU timeout).
        # MUST use the explicit-program form `exec {$ARGV[0]} @ARGV`:
        # a bare `exec @ARGV` with an empty PASSTHROUGH (the primary
        # heredoc usage) is a single-element list, which makes perl
        # fall back to `/bin/sh -c`. On Windows the backslash $REAL_BU
        # path is then mangled by sh ("command not found"). The block
        # form always uses execvp, never the shell.
        assert "perl -e 'alarm shift; exec {$ARGV[0]} @ARGV' 120" in content
        assert 'exec @ARGV' not in content  # never the shell-fallback form
        # A 142 (SIGALRM) timeout is what triggers recovery; any other
        # status returns straight to the caller, which is why the branch
        # reads as an early return rather than an `-eq` guard. Assert the
        # conditional itself — `'142' in content` would also pass on a
        # stray mention in a comment. What recovery then does is pinned in
        # test_wrapper_wedge_escalation.py.
        assert '_vs_rc" -ne 142' in content
        assert 'BU_NAME="$SESSION" "$REAL_BU" --reload' in content

    def test_aux_session_also_self_heals(self, tmp_path: Path):
        """-aux runs the same bounded self-heal path as every session.

        It used to fall through to a plain exec as part of the removed
        Chrome-direct exemption; on the store's own proxy there is no
        reason to exempt aux from wedge recovery.
        """
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'test-store', 'ziniao', 9222, store_id='s1', slug='test-store'
            )
        content = (bin_dir / 'test-store' / 'browser-use').read_text()
        assert '[ "$SESSION" != "test-store-aux" ]' not in content
        assert 'BU_NAME="$SESSION" "$REAL_BU" --reload' in content

    def test_perl_exec_reaches_real_bu_with_metachar_path(self, tmp_path: Path):
        """Executable regression guard: the timeout trampoline must reach
        ``$REAL_BU`` even when its path holds a shell metacharacter AND
        PASSTHROUGH is empty — the bare-heredoc usage ``browser-use
        <<'PY' … PY`` that produces a single-element ``@ARGV``.

        A bare ``exec @ARGV`` on a single element makes perl fall back to
        ``/bin/sh -c``; the shell then mangles the path. On Windows the
        backslash ``browser-use.EXE`` path became "command not found" and
        browser-use was unusable via the documented heredoc interface.
        A SPACE in the path triggers the exact same sh-fallback on POSIX,
        so this reproduces the bug on the fast Linux unit CI (where the
        agent-e2e that missed it does not run, and forks have no LLM key).
        The explicit-program form ``exec {$ARGV[0]} @ARGV`` always uses
        execvp and never consults the shell.
        """
        # Stub browser-use at a path containing a space (a shell
        # metacharacter), so a shell-fallback exec would fail to find it.
        stub_dir = tmp_path / 'dir with space'
        stub_dir.mkdir(parents=True)
        stub = stub_dir / 'browser-use'
        stub.write_text(
            '#!/usr/bin/env bash\ncat >/dev/null\necho WRAP_EXEC_OK\n'
        )
        stub.chmod(0o755)

        # Real generated wrapper; point REAL_BU at the space-path stub and
        # stub curl to answer the ownership probe, so the offline
        # auto-start check passes and the exec is reached.
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'test-store',
                'ziniao',
                9222,
                store_id='s1',
                slug='test-store',
                instance_id='test-instance',
            )
        wrapper = bin_dir / 'test-store' / 'browser-use'
        content = wrapper.read_text()
        content = re.sub(r'REAL_BU="[^"]*"', f'REAL_BU="{stub}"', content)
        # The stubbed curl must answer the ownership probe, or the
        # wrapper takes the auto-start path and never reaches the exec.
        content = content.replace('curl ', f'"{_stub_curl(tmp_path)}" ')
        wrapper.write_text(content)

        # No CLI args → empty PASSTHROUGH (the trigger); code on stdin.
        result = subprocess.run(
            [str(wrapper)],
            input='print("hi")\n',
            capture_output=True,
            text=True,
            timeout=30,
            env=_env_without_task(),
        )
        assert 'WRAP_EXEC_OK' in result.stdout, (
            'perl exec never reached $REAL_BU with a metachar path + empty '
            'PASSTHROUGH — the bare `exec @ARGV` shell-fallback regressed. '
            f'stdout={result.stdout!r} stderr={result.stderr!r}'
        )


@pytest.mark.unit
@pytest.mark.unit
class TestWrapperOwnershipProbe:
    """The wrapper's liveness probe is an OWNERSHIP probe (wrapper v9).

    On main the probe was a bare ``/json/version`` liveness curl, which
    a FOREIGN listener happily answers: with two servers on one
    machine, server B's wrapper found server A's proxy "alive",
    skipped the start API, and attached the agent straight onto server
    A's browser. Here the stubbed curl dispatches by URL so the full
    bash path runs: probe → foreign → start API → follow the typed
    proxy_port → probe ours → attach.
    """

    @staticmethod
    def _dispatching_curl(tmp_path: Path) -> Path:
        """curl stand-in that answers by URL, like a real machine with
        a foreign mux on the baked port and our mux on the fresh one."""
        stub = tmp_path / 'curl-foreign'
        stub.write_text(
            '#!/usr/bin/env bash\n'
            '_body=""\n'
            'for a in "$@"; do\n'
            '  case "$a" in\n'
            '    *"/vibe/instance"*)\n'
            '      case "$a" in\n'
            '        *":9222/"*)\n'
            '          _body=\'{"instance": "another-server"}\';;\n'
            '        *)\n'
            '          _body=\'{"instance": "test-instance"}\';;\n'
            '      esac;;\n'
            '    *browser/start*)\n'
            '      _body=\'{"ok":true,"proxy_port":9225,'
            '"instance_id":"test-instance"}\';;\n'
            '  esac\n'
            'done\n'
            'printf \'%s\' "$_body"\n'
            "# Emulate `-w '\\n%{http_code}'` on the start-API call.\n"
            'for a in "$@"; do\n'
            '  if [ "$a" = "-w" ]; then printf \'\\n200\'; fi\n'
            'done\n'
            'exit 0\n'
        )
        stub.chmod(0o755)
        return stub

    def _foreign_wrapper(self, tmp_path: Path) -> Path:
        """A wrapper baked with the (foreign-held) port 9222."""
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'test-store',
                'chrome',
                9222,
                store_id='store-1',
                slug='test-store',
                instance_id='test-instance',
            )
        wrapper = bin_dir / 'test-store' / 'browser-use'
        content = wrapper.read_text()
        content = re.sub(
            r'REAL_BU="[^"]*"',
            f'REAL_BU="{_stub_real_bu(tmp_path)}"',
            content,
        )
        content = content.replace(
            'curl ', f'"{self._dispatching_curl(tmp_path)}" '
        )
        wrapper.write_text(content)
        return wrapper

    def test_foreign_listener_is_never_attached(self, tmp_path: Path):
        """The baked port answers with ANOTHER server's instance id →
        the wrapper calls the start API, follows its typed proxy_port,
        and attaches THERE — never to the foreign listener."""
        wrapper = self._foreign_wrapper(tmp_path)
        result = _run_wrapper(wrapper, env=_env_without_task())
        assert result.returncode == 0, result.stderr
        ws = [
            line
            for line in result.stdout.splitlines()
            if line.startswith('BU_CDP_WS=')
        ]
        assert len(ws) == 1
        assert ws[0].startswith('BU_CDP_WS=ws://127.0.0.1:9225/client-'), (
            result.stdout
        )

    def test_a_dead_browser_behind_our_mux_still_calls_start(
        self, tmp_path: Path
    ):
        """The probe's second dimension: the mux answers /vibe/instance
        with OUR id but /json/version 502s (browser dead). The wrapper
        must treat it as down and go through the start API, never
        attach to a proxy whose browser is gone."""
        # Port-aware: on the baked port 9222 the mux answers the
        # handshake but 502s /json/version (dead browser); on the
        # start API's fresh port 9226 both answer, so the post-start
        # poll passes.
        stub = tmp_path / 'curl-dead-upstream'
        stub.write_text(
            '#!/usr/bin/env bash\n'
            '_body=""\n'
            '_rc=0\n'
            'for a in "$@"; do\n'
            '  case "$a" in\n'
            '    *":9222/vibe/instance"*)\n'
            '      _body=\'{"instance": "test-instance"}\';;\n'
            '    *":9226/vibe/instance"*)\n'
            '      _body=\'{"instance": "test-instance"}\';;\n'
            '    *":9222/json/version"*)\n'
            '      _rc=22;;  # curl -f on a 502\n'
            '    *browser/start*)\n'
            '      _body=\'{"ok":true,"proxy_port":9226,'
            '"instance_id":"test-instance"}\';;\n'
            '  esac\n'
            'done\n'
            '[ "$_rc" -eq 0 ] && printf \'%s\' "$_body"\n'
            'for a in "$@"; do\n'
            '  if [ "$a" = "-w" ]; then printf \'\\n200\'; fi\n'
            'done\n'
            'exit $_rc\n'
        )
        stub.chmod(0o755)

        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'test-store',
                'chrome',
                9222,
                store_id='store-1',
                slug='test-store',
                instance_id='test-instance',
            )
        wrapper = bin_dir / 'test-store' / 'browser-use'
        content = wrapper.read_text()
        content = re.sub(
            r'REAL_BU="[^"]*"',
            f'REAL_BU="{_stub_real_bu(tmp_path)}"',
            content,
        )
        content = content.replace('curl ', f'"{stub}" ')
        wrapper.write_text(content)

        result = _run_wrapper(wrapper, env=_env_without_task())
        assert result.returncode == 0, result.stderr
        ws = [
            line
            for line in result.stdout.splitlines()
            if line.startswith('BU_CDP_WS=')
        ]
        assert len(ws) == 1
        assert ws[0].startswith('BU_CDP_WS=ws://127.0.0.1:9226/client-'), (
            result.stdout
        )

    def test_baked_probe_only_fires_when_the_listener_is_ours(
        self, tmp_path: Path
    ):
        """Sanity on the negative direction: when the baked port answers
        with OUR id, no start API call happens (the wrapper execs on
        the first probe) — the single-instance fast path is intact."""
        wrapper = self._foreign_wrapper(tmp_path)
        # Flip the FOREIGN answer to ours on the baked port — in the
        # dispatching stub, which is what answers the probe.
        stub = tmp_path / 'curl-foreign'
        stub.write_text(
            stub.read_text().replace(
                '{"instance": "another-server"}',
                '{"instance": "test-instance"}',
            )
        )
        result = _run_wrapper(wrapper, env=_env_without_task())
        assert result.returncode == 0, result.stderr
        ws = [
            line
            for line in result.stdout.splitlines()
            if line.startswith('BU_CDP_WS=')
        ]
        assert len(ws) == 1
        assert ws[0].startswith('BU_CDP_WS=ws://127.0.0.1:9222/client-'), (
            result.stdout
        )


@pytest.mark.unit
class TestWrapperAutoStartFailure:
    """Wrapper exits with error when CDP auto-start fails."""

    def test_api_failure_exits_with_error(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        content = wrapper.read_text()
        # Break the (stubbed) curl so both the health check and the
        # start API fail — the wrapper must error out, not fall through.
        content = content.replace(str(_stub_curl(tmp_path)), '/usr/bin/false')
        wrapper.write_text(content)
        result = _run_wrapper(wrapper, env=_env_without_task())
        assert result.returncode == 1
        assert 'ERROR' in result.stderr


@pytest.mark.unit
class TestWrapperConcurrentStores:
    """Multiple stores get separate isolated wrappers."""

    def test_concurrent_stores_isolated(self, tmp_path: Path):
        wrapper_a = _generate_wrapper(
            tmp_path, store_name='store-a', proxy_port=9222, store_id='store-a'
        )
        wrapper_b = _generate_wrapper(
            tmp_path, store_name='store-b', proxy_port=9223, store_id='sb'
        )
        assert wrapper_a.parent != wrapper_b.parent
        assert wrapper_a.parent.name == 'store-a'
        assert wrapper_b.parent.name == 'store-b'
        env = _env_without_task()
        assert (
            _run_wrapper(wrapper_a, '--session', 'store-b', env=env).returncode
            == 1
        )
        assert (
            _run_wrapper(wrapper_b, '--session', 'store-a', env=env).returncode
            == 1
        )
        assert (
            _run_wrapper(wrapper_a, '--session', 'store-a', env=env).returncode
            == 0
        )
        assert (
            _run_wrapper(wrapper_b, '--session', 'store-b', env=env).returncode
            == 0
        )


@pytest.mark.unit
class TestWrapperLeavesPageTitleAlone:
    """browser_harness prefixes every tab it drives with a horse emoji by
    rewriting ``document.title`` — a change the page can observe, and one
    no person's browser makes. Every wrapper turns it off, whatever the
    caller's env says."""

    @pytest.mark.parametrize('backend', ['chrome', 'ziniao'])
    def test_store_wrapper_turns_tab_marker_off(
        self, tmp_path: Path, backend: str
    ):
        wrapper = _generate_wrapper(tmp_path, backend=backend)
        env = {**_env_without_task(), 'BH_TAB_MARKER': '1'}
        result = _run_wrapper(wrapper, env=env)
        assert result.returncode == 0, result.stderr
        assert 'BH_TAB_MARKER=0' in result.stdout.splitlines()

    def test_web_wrapper_turns_tab_marker_off(self, tmp_path: Path):
        wrapper = _generate_web_wrapper(tmp_path)
        env = {**_env_without_task(), 'BH_TAB_MARKER': '1'}
        result = _run_wrapper(wrapper, env=env)
        assert result.returncode == 0, result.stderr
        assert 'BH_TAB_MARKER=0' in result.stdout.splitlines()


def _generate_web_wrapper(tmp_path: Path) -> Path:
    """Generate the store-less orchestrator web wrapper, REAL_BU/curl
    stubbed so it runs offline (the ownership probe must see this
    instance's id, like the store wrapper's helper)."""
    bin_dir = tmp_path / 'bin'
    with mock.patch('app.browser.web_wrapper._BIN_DIR', bin_dir):
        write_web_browser_use_wrapper(
            9222, api_token='t0ken', instance_id='test-instance'
        )
    wrapper = bin_dir / WEB_BROWSER_SLUG / 'browser-use'
    content = wrapper.read_text()
    content = re.sub(
        r'REAL_BU="[^"]*"', f'REAL_BU="{_stub_real_bu(tmp_path)}"', content
    )
    content = content.replace('curl ', f'"{_stub_curl(tmp_path)}" ')
    wrapper.write_text(content)
    return wrapper


@pytest.mark.unit
class TestWebWrapper:
    """The store-less orchestrator ``web`` browser wrapper (0.13)."""

    def test_default_session_is_web(self, tmp_path: Path):
        wrapper = _generate_web_wrapper(tmp_path)
        result = _run_wrapper(wrapper, env=_env_without_task())
        assert result.returncode == 0, result.stderr
        assert 'BU_NAME=web' in result.stdout

    def test_per_task_session_allowed(self, tmp_path: Path):
        wrapper = _generate_web_wrapper(tmp_path)
        result = _run_wrapper(
            wrapper, '--session', 'web-a1b2c3d4', env=_env_without_task()
        )
        assert result.returncode == 0, result.stderr
        assert 'BU_NAME=web-a1b2c3d4' in result.stdout

    def test_store_session_blocked(self, tmp_path: Path):
        wrapper = _generate_web_wrapper(tmp_path)
        result = _run_wrapper(
            wrapper, '--session', 'mystore', env=_env_without_task()
        )
        assert result.returncode == 1
        assert 'not allowed' in result.stderr

    def test_cdp_ws_injected(self, tmp_path: Path):
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.web_wrapper._BIN_DIR', bin_dir):
            write_web_browser_use_wrapper(9222, api_token='t0ken')
        content = (bin_dir / WEB_BROWSER_SLUG / 'browser-use').read_text()
        assert 'export BU_NAME="$SESSION"' in content
        assert 'export BU_CDP_WS="ws://' in content
        # Auto-start hits the store-less web route, never /api/stores/.
        assert '/api/browser/web/start' in content
        assert '/api/stores/' not in content

    def test_cdp_url_flag_blocked(self, tmp_path: Path):
        wrapper = _generate_web_wrapper(tmp_path)
        result = _run_wrapper(
            wrapper, '--cdp-url', 'http://evil', env=_env_without_task()
        )
        assert result.returncode == 1
        assert 'managed by the wrapper' in result.stderr

    def test_agent_bu_env_rejected(self, tmp_path: Path):
        wrapper = _generate_web_wrapper(tmp_path)
        env = {**_env_without_task(), 'BU_CDP_WS': 'ws://evil'}
        result = _run_wrapper(wrapper, env=env)
        assert result.returncode == 1
        assert 'managed by the wrapper' in result.stderr


@pytest.mark.unit
class TestWrapperTmpDirPerSession:
    """capture_screenshot() writes a fixed shot.png into BH_TMP_DIR; a dir
    shared by every session handed one task another store's picture."""

    def test_tmp_dir_carries_the_task_session(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        env = {
            **os.environ,
            'VIBE_TASK_ID': 'a1b2c3d4-0000-0000-0000-000000000000',
        }
        result = _run_wrapper(wrapper, env=env)
        assert result.returncode == 0, result.stderr
        assert re.search(
            r'^BH_TMP_DIR=.*/test-store-a1b2c3d4$', result.stdout, re.M
        ), result.stdout

    def test_aux_session_gets_its_own_tmp_dir(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(
            wrapper, '--session', 'test-store-aux', env=_env_without_task()
        )
        assert result.returncode == 0, result.stderr
        assert re.search(
            r'^BH_TMP_DIR=.*/test-store-aux$', result.stdout, re.M
        ), result.stdout


@pytest.mark.unit
class TestAntidetectAuxWrapper:
    """The aux session must be a DEDICATED login-less Chromium (its own
    /browser/aux/start) for EVERY anti-detect backend — not just ziniao.
    Regression: a yuehulian store's aux fell through to the main
    (login-required) /browser/start and failed with 'Log into the client'
    because the aux special-case was gated on backend == 'ziniao'."""

    def test_yuehulian_aux_uses_dedicated_login_less_start(
        self, tmp_path: Path
    ):
        wrapper = _generate_wrapper(tmp_path, backend='yuehulian')
        content = wrapper.read_text()
        assert 'browser/aux/start' in content

    def test_ziniao_aux_uses_dedicated_login_less_start(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, backend='ziniao')
        assert 'browser/aux/start' in wrapper.read_text()

    def test_chrome_aux_rides_main_proxy_not_aux_start(self, tmp_path: Path):
        # Twist / negative control: a chrome store's aux is a client on the
        # main proxy, so it must NOT call the dedicated aux-start API.
        wrapper = _generate_wrapper(tmp_path, backend='chrome')
        assert 'browser/aux/start' not in wrapper.read_text()

    def test_wrapper_has_no_dead_interpreter_dependency(self, tmp_path: Path):
        """Regression: the wrapper must never call python3. On native
        Windows `python3` resolves to the non-functional WindowsApps
        execution-alias stub (returns nothing), which left the aux ws
        parse empty and failed every aux start for anti-detect backends
        (ziniao AND yuehulian). Covers the aux parse (anti-detect) and the
        CLIENT_ID uuid fallback (all backends)."""
        for backend in ('chrome', 'ziniao', 'yuehulian'):
            content = _generate_wrapper(tmp_path, backend=backend).read_text()
            assert 'python3' not in content, (
                f'{backend} wrapper still depends on python3 '
                '(a dead stub on native Windows)'
            )

    def test_antidetect_aux_parses_ws_with_sed(self, tmp_path: Path):
        """The anti-detect aux arm extracts the ws endpoint with sed
        (bundled in git-bash, present on Linux/Mac), not python3."""
        for backend in ('ziniao', 'yuehulian'):
            content = _generate_wrapper(tmp_path, backend=backend).read_text()
            assert 'sed -n' in content
            assert 'aux browser start failed' in content


@pytest.mark.unit
class TestAtomicWriteScript:
    """atomic_write_script's Windows-lock contract (found live by a real
    task on native Windows: a cold store's first browser-use call 500s
    when the server's same-content wrapper rewrite hits WinError 5)."""

    def test_identical_content_is_not_rewritten(self, tmp_path: Path):
        target = tmp_path / 'browser-use'
        atomic_write_script(target, 'CONTENT-1\n')
        first_mtime = target.stat().st_mtime_ns
        atomic_write_script(target, 'CONTENT-1\n')
        assert target.read_text() == 'CONTENT-1\n'
        assert target.stat().st_mtime_ns == first_mtime

    def test_changed_content_is_swapped(self, tmp_path: Path):
        target = tmp_path / 'browser-use'
        atomic_write_script(target, 'CONTENT-1\n')
        atomic_write_script(target, 'CONTENT-2\n')
        assert target.read_text() == 'CONTENT-2\n'

    def test_a_locked_target_keeps_the_old_file_instead_of_raising(
        self, tmp_path: Path, monkeypatch
    ):
        """Windows: os.replace over an executing script raises
        PermissionError. The writer must keep the old copy (the running
        wrapper follows the start API's typed port at runtime) instead
        of failing the browser start with a 500."""
        target = tmp_path / 'browser-use'
        atomic_write_script(target, 'OLD\n')

        def _locked_replace(src, dst, *a, **kw):
            raise PermissionError(5, 'Access is denied', dst)

        monkeypatch.setattr('app.browser.wrapper.os.replace', _locked_replace)
        atomic_write_script(target, 'NEW\n')  # must not raise
        # The OLD copy stays; no temp litter.
        assert target.read_text() == 'OLD\n'
        assert list(tmp_path.glob('*.tmp')) == []
