"""The wrapper's typed browser-start failure behaviour, executed.

The generated wrapper runs for real against a stub curl that answers
the start API with typed 500 bodies, pinning the C2 contract at the
layer the agent actually reads:

- a TERMINAL code prints the typed line (code + hint + "report this
  to the user") and exits 1 — never a bare ``HTTP 500``;
- a TRANSIENT code is retried exactly ONCE, then reported typed;
- a body with NO code (a backend outside the inject taxonomy, e.g.
  Ziniao) prints exactly the pre-C2 output — Ziniao is not
  reclassified;
- the start POST names the task it serves (``X-Vibe-Task``) so the
  server can end that task.
"""

import json
import os
from pathlib import Path
import re
import subprocess
from unittest import mock

import pytest

from app.browser import wrapper_start as wrapper_start_mod
from app.browser.manager import write_browser_use_wrapper

pytestmark = pytest.mark.unit

_CURL_STUB = '''#!/usr/bin/env python3
"""curl stand-in: records argv, answers /browser/start from a queue.

A queued ``''`` means "connection refused" (exit 7, no output); any
other entry is ``<body>\\n<http code>`` — printed exactly like
``curl -s -w '\\n%{http_code}'`` (body, then the code, exit 0: an HTTP
error is not a curl error). A 2xx start marks the store's CDP proxy
healthy, so the post-start health checks pass from then on.
"""
import json
import os
import sys

argv = sys.argv[1:]
log = os.environ.get('CURL_LOG')
if log:
    with open(log, 'a', encoding='utf-8') as fh:
        fh.write(' '.join(argv) + '\\n')
url = ' '.join(argv)
if 'browser/start' in url:
    with open(os.environ['CURL_STATE'], encoding='utf-8') as fh:
        state = json.load(fh)
    i = state['i']
    state['i'] = i + 1
    resp = state['responses'][i] if i < len(state['responses']) else ''
    if resp == '':
        sys.exit(7)
    body, code = resp.rsplit('\\n', 1)
    if 200 <= int(code) < 300:
        state['healthy'] = True
    with open(os.environ['CURL_STATE'], 'w', encoding='utf-8') as fh:
        json.dump(state, fh)
    # What ``curl -s -w '\\n%{http_code}'`` writes: body, newline, code.
    print(body + '\\n' + code)
    sys.exit(0)
if '/json/version' in url:
    # Healthy only after a successful start (2xx) answered earlier.
    with open(os.environ['CURL_STATE'], encoding='utf-8') as fh:
        state = json.load(fh)
    sys.exit(0 if state.get('healthy') else 1)
sys.exit(0)
'''


def _stub_real_bu(tmp_path: Path) -> str:
    stub = tmp_path / 'fake_bu.sh'
    stub.write_text('#!/usr/bin/env bash\necho WRAP_EXEC_OK\n')
    stub.chmod(0o755)
    return str(stub)


def _generate(
    tmp_path: Path,
    responses: list[str],
) -> tuple[Path, dict]:
    """Wrapper + its env; curl stubbed to ``responses`` per start POST.

    The pre-start health probe fails (so the auto-start arm runs) and
    turns healthy once a 2xx start response has been answered — the
    stub tracks that in its state file.
    """
    bin_dir = tmp_path / 'bin'
    with (
        mock.patch('app.browser.wrapper._BIN_DIR', bin_dir),
        mock.patch.object(
            wrapper_start_mod, 'START_TRANSIENT_RETRY_SLEEP_S', '0.01'
        ),
    ):
        write_browser_use_wrapper(
            'test-store',
            'yuehulian',
            9222,
            store_id='s1',
            slug='test-store',
        )
    wrapper = bin_dir / 'test-store' / 'browser-use'
    content = wrapper.read_text()
    content = re.sub(
        r'REAL_BU="[^"]*"', f'REAL_BU="{_stub_real_bu(tmp_path)}"', content
    )
    stub = tmp_path / 'curl-stub'
    stub.write_text(_CURL_STUB)
    stub.chmod(0o755)
    content = content.replace('curl ', f'"{stub}" ')
    wrapper.write_text(content)
    state = tmp_path / 'curl-state.json'
    state.write_text(
        json.dumps({'i': 0, 'responses': responses, 'healthy': False})
    )
    log = tmp_path / 'curl-calls.log'
    env = {k: v for k, v in os.environ.items() if k != 'VIBE_TASK_ID'}
    env['VIBE_TASK_ID'] = '0123456789abcdef'
    env['CURL_STATE'] = str(state)
    env['CURL_LOG'] = str(log)
    return wrapper, env


def _run(wrapper: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(wrapper)],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
        input='',
    )


def _typed_500(code: str, hint: str = 'Switch the kernel to Chrome.') -> str:
    body = json.dumps({
        'detail': {
            'code': code,
            'message': f'{code} happened',
            'hint': hint,
        }
    })
    return f'{body}\n500'


def _start_calls(log: Path) -> list[str]:
    if not log.exists():
        return []
    return [line for line in log.read_text().splitlines() if '/start' in line]


class TestTerminalStartFailure:
    def test_typed_line_hint_and_report_instruction(self, tmp_path):
        wrapper, env = _generate(tmp_path, [_typed_500('UNSUPPORTED_KERNEL')])
        result = _run(wrapper, env)
        assert result.returncode == 1
        # The typed line: the CODE, never a bare HTTP 500.
        assert 'ERROR: [UNSUPPORTED_KERNEL]' in result.stderr
        assert 'HTTP 500' not in result.stderr
        # The plain-language hint from the taxonomy.
        assert 'Switch the kernel to Chrome.' in result.stderr
        # And the stop instruction (incident 97db66d2).
        assert 'Report this' in result.stderr
        assert 'do NOT' in result.stderr

    def test_start_post_names_the_task(self, tmp_path):
        wrapper, env = _generate(tmp_path, [_typed_500('LOGIN_REQUIRED')])
        result = _run(wrapper, env)
        assert result.returncode == 1
        calls = _start_calls(Path(env['CURL_LOG']))
        assert len(calls) == 1
        assert 'X-Vibe-Task: 0123456789abcdef' in calls[0]

    @pytest.mark.parametrize(
        'code',
        [
            'STORE_UNAVAILABLE',
            'STORE_NOT_FOUND',
            'PLAN_REQUIRED',
            'API_NOT_ENABLED',
            'ACCOUNT_MISMATCH',
            'LOGIN_REQUIRED',
        ],
    )
    def test_every_terminal_code_prints_typed(self, tmp_path, code):
        wrapper, env = _generate(tmp_path, [_typed_500(code)])
        result = _run(wrapper, env)
        assert result.returncode == 1, code
        assert f'ERROR: [{code}]' in result.stderr, code


class TestTransientStartFailure:
    def test_retried_exactly_once_then_typed(self, tmp_path):
        wrapper, env = _generate(
            tmp_path,
            [
                _typed_500('CLIENT_NOT_RUNNING', 'Start the client.'),
                _typed_500('CLIENT_NOT_RUNNING', 'Start the client.'),
            ],
        )
        result = _run(wrapper, env)
        assert result.returncode == 1
        # Exactly two POSTs: the failed start and the ONE retry.
        assert len(_start_calls(Path(env['CURL_LOG']))) == 2
        assert 'retrying once' in result.stderr
        assert 'ERROR: [CLIENT_NOT_RUNNING]' in result.stderr
        assert 'Start the client.' in result.stderr
        # D-C2-3: even the second transient failure — which does NOT
        # fail the task server-side — tells the agent to report to the
        # user rather than investigate.
        assert 'Report this' in result.stderr

    def test_transient_then_success_reaches_browser_use(self, tmp_path):
        wrapper, env = _generate(
            tmp_path,
            [_typed_500('CDP_UNREACHABLE'), '{"ok": true}\n200'],
        )
        result = _run(wrapper, env)
        # The retry succeeded, the CDP health check passed and the
        # wrapper reached the real browser-use stub.
        assert result.returncode == 0, result.stderr
        assert 'WRAP_EXEC_OK' in result.stdout
        assert len(_start_calls(Path(env['CURL_LOG']))) == 2


class TestUntypedFailureIsUnchanged:
    """No taxonomy code in the body → exactly the pre-C2 output.

    Ziniao (and any backend outside the inject engine) must not be
    reclassified by the wrapper.
    """

    def test_plain_body_prints_the_old_error(self, tmp_path):
        wrapper, env = _generate(
            tmp_path, ['Ziniao could not be launched\n500']
        )
        result = _run(wrapper, env)
        assert result.returncode == 1
        assert 'ERROR: browser start API failed (HTTP 500)' in result.stderr
        assert 'Ziniao could not be launched' in result.stderr
        # No typed line, no report instruction.
        assert 'Report this' not in result.stderr

    def test_refused_connection_prints_the_old_error(self, tmp_path):
        wrapper, env = _generate(tmp_path, [''])
        result = _run(wrapper, env)
        assert result.returncode == 1
        assert 'browser start API failed (HTTP unavailable)' in (result.stderr)
