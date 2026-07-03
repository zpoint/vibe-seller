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

from app.browser.manager import store_slug, write_browser_use_wrapper
from app.browser.web_wrapper import write_web_browser_use_wrapper
from app.config import WEB_BROWSER_SLUG


def _stub_real_bu(tmp_path: Path) -> str:
    """A fake browser-use that prints its args + the BU_/BH_ env the
    wrapper injected — so tests can assert the 0.13 env contract."""
    stub = tmp_path / 'fake_bu.sh'
    stub.write_text(
        '#!/usr/bin/env bash\n'
        'echo "ARGS: $*"\n'
        'env | grep -E "^(BU_|BH_)" | sort\n'
    )
    stub.chmod(0o755)
    return str(stub)


def _generate_wrapper(
    tmp_path: Path,
    store_name: str = 'test-store',
    backend: str = 'ziniao',
    proxy_port: int | None = 9222,
    store_id: str = 'store-1',
) -> Path:
    """Generate a wrapper with REAL_BU pointed at the env-printing stub
    and curl stubbed to /usr/bin/true so auto-start passes offline."""
    bin_dir = tmp_path / 'bin'
    with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
        write_browser_use_wrapper(
            store_name, backend, proxy_port, store_id=store_id
        )
    slug = store_slug(store_name)
    wrapper = bin_dir / slug / 'browser-use'
    content = wrapper.read_text()
    content = re.sub(
        r'REAL_BU="[^"]*"', f'REAL_BU="{_stub_real_bu(tmp_path)}"', content
    )
    content = content.replace('curl ', '/usr/bin/true ')
    wrapper.write_text(content)
    return wrapper


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
        assert result.returncode == 0

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
                'test-store', 'ziniao', 9222, store_id='s1'
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
            write_browser_use_wrapper('storec', 'chrome', 9222, store_id='s2')
        content = (bin_dir / 'storec' / 'browser-use').read_text()
        assert 'export BU_NAME="$SESSION"' in content
        assert 'export BU_CDP_WS="ws://' in content

    def test_ziniao_aux_has_no_cdp_ws(self, tmp_path: Path):
        """Ziniao -aux is Chrome-direct: BU_NAME set, but no BU_CDP_WS."""
        wrapper = _generate_wrapper(
            tmp_path, store_name='test-store', backend='ziniao'
        )
        result = _run_wrapper(
            wrapper, '--session', 'test-store-aux', env=_env_without_task()
        )
        assert result.returncode == 0, result.stderr
        assert 'BU_NAME=test-store-aux' in result.stdout
        assert 'BU_CDP_WS=' not in result.stdout

    def test_autostart_polls(self, tmp_path: Path):
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'test-store', 'ziniao', 9222, store_id='s1'
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
                'test-store', 'ziniao', 9222, store_id='s1'
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
        # On a 142 (SIGALRM) timeout, reload this session's daemon.
        assert '_vs_rc" -eq 142' in content
        assert 'BU_NAME="$SESSION" "$REAL_BU" --reload' in content

    def test_aux_session_does_not_self_heal(self, tmp_path: Path):
        """-aux (Chrome-direct) falls through to a plain exec."""
        bin_dir = tmp_path / 'bin'
        with mock.patch('app.browser.wrapper._BIN_DIR', bin_dir):
            write_browser_use_wrapper(
                'test-store', 'ziniao', 9222, store_id='s1'
            )
        content = (bin_dir / 'test-store' / 'browser-use').read_text()
        assert '[ "$SESSION" != "test-store-aux" ]' in content


@pytest.mark.unit
class TestWrapperAutoStartFailure:
    """Wrapper exits with error when CDP auto-start fails."""

    def test_api_failure_exits_with_error(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        content = wrapper.read_text()
        content = content.replace('/usr/bin/true ', '/usr/bin/false ')
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


def _generate_web_wrapper(tmp_path: Path) -> Path:
    """Generate the store-less orchestrator web wrapper, REAL_BU/curl
    stubbed so it runs offline."""
    bin_dir = tmp_path / 'bin'
    with mock.patch('app.browser.web_wrapper._BIN_DIR', bin_dir):
        write_web_browser_use_wrapper(9222, api_token='t0ken')
    wrapper = bin_dir / WEB_BROWSER_SLUG / 'browser-use'
    content = wrapper.read_text()
    content = re.sub(
        r'REAL_BU="[^"]*"', f'REAL_BU="{_stub_real_bu(tmp_path)}"', content
    )
    content = content.replace('curl ', '/usr/bin/true ')
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
