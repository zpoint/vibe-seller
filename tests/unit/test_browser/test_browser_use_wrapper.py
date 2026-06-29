"""Tests for browser-use CLI wrapper isolation.

Generates wrapper scripts to a tmp dir and verifies:
- Default session is the store slug
- Allowed sessions pass through
- Wrong sessions are blocked (exit 1)
- --cdp-url flag is blocked
- --mcp flag is blocked
- Both Ziniao and Chrome wrappers inject --cdp-url
- Concurrent stores get separate isolated wrappers
"""

import os
from pathlib import Path
import re
import subprocess
from unittest import mock

import pytest

from app.browser.manager import (
    store_slug,
    write_browser_use_wrapper,
)


def _generate_wrapper(
    tmp_path: Path,
    store_name: str = 'test-store',
    backend: str = 'ziniao',
    proxy_port: int | None = 9222,
    store_id: str = 'store-1',
) -> Path:
    """Helper: generate a wrapper and patch REAL_BU to echo."""
    bin_dir = tmp_path / 'bin'
    with (
        mock.patch('app.browser.wrapper._BIN_DIR', bin_dir),
        mock.patch(
            'app.browser.wrapper.shutil.which',
            return_value='/usr/local/bin/browser-use',
        ),
    ):
        write_browser_use_wrapper(
            store_name,
            backend,
            proxy_port,
            store_id=store_id,
        )

    slug = store_slug(store_name)
    wrapper = bin_dir / slug / 'browser-use'

    # Replace REAL_BU with echo so the wrapper prints args
    # instead of executing the real binary. Use regex because
    # the generator picks the sibling `browser-use` next to
    # `sys.executable` first (real path on dev clones) and
    # only falls back to `shutil.which` if that's missing —
    # so the mocked /usr/local/bin path isn't guaranteed.
    content = wrapper.read_text()
    content = re.sub(r'REAL_BU="[^"]*"', 'REAL_BU="echo"', content)
    # Replace curl with /usr/bin/true so auto-start checks
    # pass instantly without hitting real endpoints.
    content = content.replace('curl ', '/usr/bin/true ')
    wrapper.write_text(content)
    return wrapper


def _run_wrapper(wrapper: Path, *args: str) -> subprocess.CompletedProcess:
    """Run the wrapper script with the given arguments."""
    return subprocess.run(
        [str(wrapper), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.unit
class TestWrapperDefaultSession:
    """Default session should be the store slug."""

    def test_default_session_is_store(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(wrapper, 'state')
        assert result.returncode == 0
        assert '--session test-store' in result.stdout


@pytest.mark.unit
class TestWrapperSessionValidation:
    """Session allow/deny logic."""

    def test_explicit_store_session_allowed(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(wrapper, '--session', 'test-store', 'state')
        assert result.returncode == 0

    def test_aux_session_allowed(self, tmp_path: Path):
        wrapper = _generate_wrapper(
            tmp_path,
            store_name='test-store',
            backend='chrome',
            proxy_port=9222,
        )
        result = _run_wrapper(wrapper, '--session', 'test-store-aux', 'state')
        assert result.returncode == 0

    def test_wrong_session_blocked(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(wrapper, '--session', 'other-store', 'state')
        assert result.returncode == 1
        assert 'not allowed' in result.stderr

    def test_per_task_session_allowed(self, tmp_path: Path):
        """Per-task sessions with 8-hex suffix are allowed."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        # Valid per-task session format: {slug}-{8hex}
        result = _run_wrapper(
            wrapper, '--session', 'test-store-a1b2c3d4', 'state'
        )
        assert result.returncode == 0

    def test_per_task_session_uppercase_hex_allowed(self, tmp_path: Path):
        """Per-task sessions accept uppercase hex characters."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(
            wrapper, '--session', 'test-store-A1B2C3D4', 'state'
        )
        assert result.returncode == 0

    def test_per_task_session_wrong_length_blocked(self, tmp_path: Path):
        """Per-task sessions must have exactly 8 hex chars."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        # Too short (7 chars)
        result = _run_wrapper(
            wrapper, '--session', 'test-store-a1b2c3d', 'state'
        )
        assert result.returncode == 1
        assert 'not allowed' in result.stderr
        # Too long (9 chars)
        result = _run_wrapper(
            wrapper, '--session', 'test-store-a1b2c3d4e', 'state'
        )
        assert result.returncode == 1
        assert 'not allowed' in result.stderr

    def test_per_task_session_invalid_chars_blocked(self, tmp_path: Path):
        """Per-task sessions must be hex characters only."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        # Invalid characters (g, h, i)
        result = _run_wrapper(
            wrapper, '--session', 'test-store-a1b2c3gh', 'state'
        )
        assert result.returncode == 1
        assert 'not allowed' in result.stderr


@pytest.mark.unit
class TestWrapperFlagBlocking:
    """Dangerous flags are rejected."""

    def test_cdp_url_blocked(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(wrapper, '--cdp-url', 'http://evil', 'state')
        assert result.returncode == 1
        assert '--cdp-url' in result.stderr

    def test_mcp_flag_blocked(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(wrapper, '--mcp')
        assert result.returncode == 1
        assert '--mcp' in result.stderr

    def test_headed_flag_blocked(self, tmp_path: Path):
        """--headed is managed by the wrapper and blocked."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        result = _run_wrapper(wrapper, '--headed', 'state')
        assert result.returncode == 1
        assert '--headed' in result.stderr

    def test_session_override_blocked_in_agent(self, tmp_path: Path):
        """--session override is blocked when VIBE_TASK_ID is set."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        env = {'VIBE_TASK_ID': 'a1b2c3d4-0000-0000-0000-000000000000'}
        result = subprocess.run(
            [str(wrapper), '--session', 'test-store', 'state'],
            capture_output=True,
            text=True,
            timeout=10,
            env={**subprocess.os.environ, **env},
        )
        assert result.returncode == 1
        assert 'auto-assigned' in result.stderr

    def test_session_aux_allowed_in_agent(self, tmp_path: Path):
        """--session {slug}-aux is allowed even in agent mode."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        env = {'VIBE_TASK_ID': 'a1b2c3d4-0000-0000-0000-000000000000'}
        result = subprocess.run(
            [str(wrapper), '--session', 'test-store-aux', 'state'],
            capture_output=True,
            text=True,
            timeout=10,
            env={**subprocess.os.environ, **env},
        )
        assert result.returncode == 0

    def test_session_override_allowed_without_task(self, tmp_path: Path):
        """--session override is allowed when VIBE_TASK_ID is NOT set."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        # Unset VIBE_TASK_ID if present
        env = {
            k: v
            for k, v in subprocess.os.environ.items()
            if k != 'VIBE_TASK_ID'
        }
        result = subprocess.run(
            [str(wrapper), '--session', 'test-store', 'state'],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0


@pytest.mark.unit
class TestWrapperCdpInjection:
    """Ziniao vs Chrome CDP injection."""

    def test_ziniao_wrapper_has_cdp_url(self, tmp_path: Path):
        bin_dir = tmp_path / 'bin'
        with (
            mock.patch('app.browser.wrapper._BIN_DIR', bin_dir),
            mock.patch(
                'app.browser.wrapper.shutil.which',
                return_value='/usr/local/bin/browser-use',
            ),
        ):
            write_browser_use_wrapper(
                'test-store', 'ziniao', 9222, store_id='s1'
            )

        content = (bin_dir / 'test-store' / 'browser-use').read_text()
        assert '--cdp-url' in content
        assert 'CDP_ARGS' in content

    def test_ziniao_wrapper_autostart_polls(self, tmp_path: Path):
        """Autostart uses --max-time 90 and a 1s poll loop, not a crude
        fixed sleep, for CDP readiness."""
        bin_dir = tmp_path / 'bin'
        with (
            mock.patch('app.browser.wrapper._BIN_DIR', bin_dir),
            mock.patch(
                'app.browser.wrapper.shutil.which',
                return_value='/usr/local/bin/browser-use',
            ),
        ):
            write_browser_use_wrapper(
                'test-store', 'ziniao', 9222, store_id='s1'
            )

        content = (bin_dir / 'test-store' / 'browser-use').read_text()
        assert '--max-time 90' in content
        assert 'while [' in content
        # Readiness poll is a 1s loop (not a crude one-shot sleep).
        assert 'sleep 1' in content

    def test_chrome_wrapper_has_cdp_url(self, tmp_path: Path):
        """Chrome wrapper now has CDP_ARGS injection (same as Ziniao).

        Both backends use CDPMuxProxy, so both get --cdp-url injected.
        """
        bin_dir = tmp_path / 'bin'
        with (
            mock.patch('app.browser.wrapper._BIN_DIR', bin_dir),
            mock.patch(
                'app.browser.wrapper.shutil.which',
                return_value='/usr/local/bin/browser-use',
            ),
        ):
            write_browser_use_wrapper('storec', 'chrome', 9222, store_id='s2')

        content = (bin_dir / 'storec' / 'browser-use').read_text()
        # CDP_ARGS block is present in Chrome wrappers (unified with Ziniao)
        assert 'CDP_ARGS' in content
        assert '--cdp-url' in content


@pytest.mark.unit
class TestWrapperAutoStartFailure:
    """Wrapper exits with error when CDP auto-start fails."""

    def test_api_failure_exits_with_error(self, tmp_path: Path):
        """Non-2xx API response causes wrapper to exit 1."""
        wrapper = _generate_wrapper(tmp_path, store_name='test-store')
        # Replace /usr/bin/true (always succeeds) with a
        # script that fails the CDP check and API call.
        content = wrapper.read_text()
        content = content.replace(
            '/usr/bin/true ',
            '/usr/bin/false ',
        )
        wrapper.write_text(content)
        result = _run_wrapper(wrapper, 'state')
        assert result.returncode == 1
        assert 'ERROR' in result.stderr


@pytest.mark.unit
class TestWrapperConcurrentStores:
    """Multiple stores get separate isolated wrappers."""

    def test_concurrent_stores_isolated(self, tmp_path: Path):
        wrapper_a = _generate_wrapper(
            tmp_path,
            store_name='store-a',
            proxy_port=9222,
            store_id='store-a',
        )
        wrapper_b = _generate_wrapper(
            tmp_path,
            store_name='store-b',
            proxy_port=9223,
            store_id='sb',
        )

        # Separate directories
        assert wrapper_a.parent != wrapper_b.parent
        assert wrapper_a.parent.name == 'store-a'
        assert wrapper_b.parent.name == 'store-b'

        # store-a wrapper only allows store-a sessions
        result = _run_wrapper(wrapper_a, '--session', 'store-b', 'state')
        assert result.returncode == 1

        # store-b wrapper only allows store-b sessions
        result = _run_wrapper(wrapper_b, '--session', 'store-a', 'state')
        assert result.returncode == 1

        # Each allows its own session
        result = _run_wrapper(wrapper_a, '--session', 'store-a', 'state')
        assert result.returncode == 0
        result = _run_wrapper(wrapper_b, '--session', 'store-b', 'state')
        assert result.returncode == 0


@pytest.mark.unit
class TestWrapperUrlValidation:
    """Surface shell-mangled URLs at the wrapper boundary.

    Background: when an agent issues
    ``browser-use open https://x.com/page?a=1&b=2`` from an
    unquoted shell context, zsh treats ``?`` as a glob and ``&``
    as a background operator. The URL gets chopped before the
    wrapper sees it, so ``browser-use open`` runs with no URL (or
    a fragment) and quietly stays on the previous page. The
    wrapper used to be silent about this; now it exits non-zero
    with a clear error so the failure is visible on the FIRST
    call instead of being papered over by retries.
    """

    def test_open_with_valid_url_passes_through(self, tmp_path: Path):
        wrapper = _generate_wrapper(tmp_path)
        result = _run_wrapper(wrapper, 'open', 'https://example.com/')
        assert result.returncode == 0, (
            f'http(s) URL must pass: stderr={result.stderr!r}'
        )

    def test_open_with_about_blank_passes_through(self, tmp_path: Path):
        """browser-use uses about:blank for session recovery — must
        pass the URL-shape guard. Regression guard against a too-tight
        ``http(s)://`` allowlist."""
        wrapper = _generate_wrapper(tmp_path)
        result = _run_wrapper(wrapper, 'open', 'about:blank')
        assert result.returncode == 0, (
            f'about:blank must pass: stderr={result.stderr!r}'
        )

    def test_open_with_file_url_passes_through(self, tmp_path: Path):
        """file:// is a valid local-artifact navigation."""
        wrapper = _generate_wrapper(tmp_path)
        result = _run_wrapper(wrapper, 'open', 'file:///tmp/report.html')
        assert result.returncode == 0, (
            f'file:// must pass: stderr={result.stderr!r}'
        )

    def test_open_with_no_url_is_loud(self, tmp_path: Path):
        """Simulates zsh nomatch swallowing the URL entirely."""
        wrapper = _generate_wrapper(tmp_path)
        result = _run_wrapper(wrapper, 'open')
        assert result.returncode == 2, (
            f'missing URL must exit 2, got {result.returncode}'
        )
        assert 'browser-use open' in result.stderr
        assert 'http' in result.stderr.lower()
        # Mentions the actual fix (quoting) so the agent can self-correct
        assert "'" in result.stderr  # the quoted-URL example

    def test_open_with_non_url_fragment_is_loud(self, tmp_path: Path):
        """Simulates zsh chopping a URL on '?' or '&'."""
        wrapper = _generate_wrapper(tmp_path)
        # After `?` was treated as a glob, the leftover was a stray
        # arg like 'foo=bar' — not an http(s) URL.
        result = _run_wrapper(wrapper, 'open', 'foo=bar')
        assert result.returncode == 2
        assert 'expects an http' in result.stderr
        assert 'foo=bar' in result.stderr

    def test_non_open_subcommands_dont_require_url(self, tmp_path: Path):
        """`state`, `click`, etc. don't take a URL — must still work."""
        wrapper = _generate_wrapper(tmp_path)
        for argv in (
            ('state',),
            ('click', '5'),
            ('get', 'text', '3'),
        ):
            result = _run_wrapper(wrapper, *argv)
            assert result.returncode == 0, (
                f'{argv} should not trip URL check: stderr={result.stderr!r}'
            )


def _generate_raw_wrapper(
    tmp_path: Path,
    real_bu: str,
    store_name: str = 'test-store',
    backend: str = 'ziniao',
    store_id: str = 'store-1',
) -> Path:
    """Generate a wrapper with REAL_BU pointed at a custom stub.

    Unlike ``_generate_wrapper`` this does NOT stub out ``curl`` —
    the self-heal tests intercept ``curl`` via a fake on PATH so they
    can observe the ``/vibe/reset-tabs`` recovery call.
    """
    bin_dir = tmp_path / 'bin'
    with (
        mock.patch('app.browser.wrapper._BIN_DIR', bin_dir),
        mock.patch(
            'app.browser.wrapper.shutil.which',
            return_value='/usr/local/bin/browser-use',
        ),
    ):
        write_browser_use_wrapper(store_name, backend, 9222, store_id=store_id)
    wrapper = bin_dir / store_slug(store_name) / 'browser-use'
    content = wrapper.read_text()
    content = re.sub(r'REAL_BU="[^"]*"', f'REAL_BU="{real_bu}"', content)
    wrapper.write_text(content)
    return wrapper


@pytest.mark.unit
class TestWrapperSelfHealOpen:
    """`open`/`navigate` must be a true one-shot for proxy sessions.

    A wedged Ziniao tab makes the daemon's BrowserStartEvent hang and
    every naive retry fails identically (the wedged-tab failure). The
    wrapper now recycles the daemon + asks the proxy to drop the wedged
    tab, then retries once — so the agent sees a single successful open.
    """

    def _fake_path_env(self, tmp_path: Path, reset_marker: Path) -> dict:
        """Build a PATH with a fake `curl` that records reset-tabs hits
        and a fake `pkill` that is a no-op, so the test never touches
        real processes or endpoints."""
        fakebin = tmp_path / 'fakebin'
        fakebin.mkdir(exist_ok=True)
        curl = fakebin / 'curl'
        curl.write_text(
            '#!/usr/bin/env bash\n'
            'for a in "$@"; do\n'
            '  case "$a" in *reset-tabs*) echo hit >> '
            f'"{reset_marker}";; esac\n'
            'done\n'
            'exit 0\n'
        )
        curl.chmod(0o755)
        pkill = fakebin / 'pkill'
        pkill.write_text('#!/usr/bin/env bash\nexit 0\n')
        pkill.chmod(0o755)
        return {
            **os.environ,
            'PATH': f'{fakebin}:{os.environ.get("PATH", "")}',
        }

    def _wedge_then_ok_stub(self, tmp_path: Path, counter: Path) -> str:
        """A fake browser-use: first call emits a BrowserStartEvent
        timeout and exits 1; second call succeeds."""
        stub = tmp_path / 'fake_bu.sh'
        stub.write_text(
            '#!/usr/bin/env bash\n'
            f'n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n+1))\n'
            f'echo "$n" > "{counter}"\n'
            'if [ "$n" -eq 1 ]; then\n'
            '  echo "Error: Event handler ...on_BrowserStartEvent#42 '
            'timed out after 30.0s and interrupted" >&2\n'
            '  exit 1\n'
            'fi\n'
            'echo "navigated-ok"\n'
            'exit 0\n'
        )
        stub.chmod(0o755)
        return str(stub)

    def test_open_recovers_and_retries_once(self, tmp_path: Path):
        counter = tmp_path / 'n'
        reset_marker = tmp_path / 'reset'
        stub = self._wedge_then_ok_stub(tmp_path, counter)
        wrapper = _generate_raw_wrapper(tmp_path, real_bu=stub)
        env = self._fake_path_env(tmp_path, reset_marker)

        result = subprocess.run(
            [str(wrapper), 'open', 'https://advertising.amazon.com/cm'],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        # Final result is the successful retry.
        assert result.returncode == 0, (
            f'self-heal should succeed: rc={result.returncode} '
            f'stdout={result.stdout!r} stderr={result.stderr!r}'
        )
        assert 'navigated-ok' in result.stdout
        # Exactly two attempts: the wedged one + one retry.
        assert counter.read_text().strip() == '2'
        # The proxy reset-tabs recovery was invoked between attempts.
        assert reset_marker.exists(), 'reset-tabs recovery was not called'

    def test_open_succeeds_first_try_no_recovery(self, tmp_path: Path):
        """Healthy open must NOT trigger the kill/reset path."""
        counter = tmp_path / 'n'
        reset_marker = tmp_path / 'reset'
        stub = tmp_path / 'ok_bu.sh'
        stub.write_text(
            '#!/usr/bin/env bash\n'
            f'n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n+1))\n'
            f'echo "$n" > "{counter}"\n'
            'echo ok\nexit 0\n'
        )
        stub.chmod(0o755)
        wrapper = _generate_raw_wrapper(tmp_path, real_bu=str(stub))
        env = self._fake_path_env(tmp_path, reset_marker)
        result = subprocess.run(
            [str(wrapper), 'open', 'https://example.com/'],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        assert result.returncode == 0
        assert counter.read_text().strip() == '1', 'should not retry'
        assert not reset_marker.exists(), 'must not reset a healthy tab'

    def test_kill_is_scoped_to_session_not_unscoped(self, tmp_path: Path):
        """The recovery kill must be scoped to this task's session token,
        never an unscoped pkill that could hit sibling tasks."""
        wrapper = _generate_raw_wrapper(tmp_path, real_bu='echo')
        content = wrapper.read_text()
        assert 'pkill -9 -f "browser_use.skill_cli.daemon.*$SESSION"' in content
        # No unscoped daemon kill.
        assert 'pkill -9 -f "browser_use.skill_cli.daemon"' not in content
        # The tab reset is scoped to OUR client id so the proxy never
        # closes a sibling task's tabs.
        assert '/vibe/reset-tabs?client=${CLIENT_ID:-}' in content

    def test_aux_session_does_not_self_heal(self, tmp_path: Path):
        """`-aux` is Chrome-direct (no proxy) — it must fall through to a
        plain exec with no daemon-kill/reset recovery."""
        wrapper = _generate_raw_wrapper(tmp_path, real_bu='echo')
        content = wrapper.read_text()
        assert '[ "$SESSION" != "test-store-aux" ]' in content

    # --- read/eval self-heal (the eval/state hang the agent fought) ---

    def test_state_recovers_retries_without_resetting_tab(self, tmp_path: Path):
        """`state` is a pure read: recover the wedged daemon and retry,
        but DON'T reset the tab (the agent wants the current page)."""
        counter = tmp_path / 'n'
        reset_marker = tmp_path / 'reset'
        stub = self._wedge_then_ok_stub(tmp_path, counter)
        wrapper = _generate_raw_wrapper(tmp_path, real_bu=stub)
        env = self._fake_path_env(tmp_path, reset_marker)
        result = subprocess.run(
            [str(wrapper), 'state'],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        assert result.returncode == 0
        assert counter.read_text().strip() == '2', 'read should retry once'
        assert not reset_marker.exists(), 'read must NOT reset the tab'

    def test_eval_retries_only_on_connect_failure(self, tmp_path: Path):
        """`eval` may run side-effecting JS, so it only retries when the
        failure proves the daemon never connected (JS never ran)."""
        counter = tmp_path / 'n'
        reset_marker = tmp_path / 'reset'
        # _wedge_then_ok_stub emits a BrowserStartEvent timeout = connfail.
        stub = self._wedge_then_ok_stub(tmp_path, counter)
        wrapper = _generate_raw_wrapper(tmp_path, real_bu=stub)
        env = self._fake_path_env(tmp_path, reset_marker)
        result = subprocess.run(
            [str(wrapper), 'eval', 'document.title'],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        assert result.returncode == 0
        assert counter.read_text().strip() == '2', 'connfail eval retries'
        assert not reset_marker.exists(), 'eval must NOT reset the tab'

    def test_eval_does_not_retry_on_generic_error(self, tmp_path: Path):
        """A non-connect eval error (JS may have run + mutated the DOM)
        must NOT be retried — double-apply risk."""
        counter = tmp_path / 'n'
        reset_marker = tmp_path / 'reset'
        stub = tmp_path / 'err_bu.sh'
        stub.write_text(
            '#!/usr/bin/env bash\n'
            f'n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n+1))\n'
            f'echo "$n" > "{counter}"\n'
            'echo "Error: ReferenceError foo is not defined" >&2\n'
            'exit 1\n'
        )
        stub.chmod(0o755)
        wrapper = _generate_raw_wrapper(tmp_path, real_bu=str(stub))
        env = self._fake_path_env(tmp_path, reset_marker)
        result = subprocess.run(
            [str(wrapper), 'eval', 'foo()'],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        assert result.returncode == 1
        assert counter.read_text().strip() == '1', 'generic eval err: no retry'
        assert not reset_marker.exists()

    def test_mutating_command_is_plain_exec(self, tmp_path: Path):
        """`click`/`type`/etc. are mutating — never auto-retried (would
        double-apply). They run exactly once even on failure."""
        counter = tmp_path / 'n'
        reset_marker = tmp_path / 'reset'
        stub = self._wedge_then_ok_stub(tmp_path, counter)
        wrapper = _generate_raw_wrapper(tmp_path, real_bu=stub)
        env = self._fake_path_env(tmp_path, reset_marker)
        result = subprocess.run(
            [str(wrapper), 'click', '5'],
            capture_output=True,
            text=True,
            timeout=20,
            env=env,
        )
        assert result.returncode == 1, 'first (wedged) attempt is returned'
        assert counter.read_text().strip() == '1', 'mutating cmd: no retry'
        assert not reset_marker.exists()
