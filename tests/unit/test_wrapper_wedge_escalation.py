"""The wrapper must escalate a wedged browser, then admit defeat.

A daemon reload cures a wedge that lives in the daemon. It cannot cure a
browser whose renderer is exhausted or whose site is serving a
maintenance page — the reload reconnects to the same sick browser and the
next call times out identically. Answering every timeout with the same
reload therefore produces an unbounded run of errors that each look
transient, and a caller reasonably keeps paying 120s a round to learn
nothing. Observed live: 20 minutes of reload → navigate → timeout, 11
downloads lost, every round reported as an ordinary error.

These tests pin the escalation as behaviour rather than as wording: they
run the generated wrapper with a stub that always reports the alarm's
exit status, and assert what the wrapper *did* at each step.
"""

import os
from pathlib import Path
import re
import subprocess

import pytest

from app.browser import wrapper as wrapper_mod
from app.browser.wrapper import (
    WEDGE_UNRECOVERABLE_RC,
    write_browser_use_wrapper,
)

pytestmark = pytest.mark.unit

#: Exit status of the wrapper's 120s perl alarm (128 + SIGALRM).
TIMEOUT_RC = 142


@pytest.fixture
def wedged(tmp_path):
    """A generated wrapper whose browser-use always times out.

    Only two things are substituted: the line that execs the real
    browser-use (replaced by a stub returning a status we choose) and the
    runtime dir (so the counter file lands in tmp). Everything else —
    the escalation ladder, the counter arithmetic, the reset — is the
    shipped script.
    """
    write_browser_use_wrapper('acme', 'ziniao', 19222, 7777, 'tok', 'store-123')
    # conftest isolates ``_BIN_DIR`` per test; the generator logs the path
    # rather than returning it.
    generated = Path(wrapper_mod._BIN_DIR) / 'acme' / 'browser-use'

    # Run the recovery ladder *as generated*, but on its own: the lines
    # above it insist on a reachable CDP proxy and a valid API token, and
    # standing those up would test the auto-start arm instead of this one.
    # The text under test is copied verbatim from the shipped wrapper.
    full = generated.read_text()
    marker = '_vs_wedge_file='
    assert marker in full, 'recovery ladder not found in the wrapper'
    ladder = full[full.index(marker) :]

    runtime = tmp_path / 'r'
    runtime.mkdir(parents=True, exist_ok=True)
    # The stub records every recovery lever the wrapper pulls, so a test
    # asserts on actions rather than on log wording.
    log = tmp_path / 'calls.log'
    ladder = re.sub(
        r"perl -e 'alarm shift.*",
        # A subshell, so the status lands in ``$?`` the way the alarm's
        # would instead of exiting the script.
        f'( echo "run" >> {log}; exit "${{STUB_RC:-{TIMEOUT_RC}}}" )',
        ladder,
        count=1,
    )
    ladder = ladder.replace(
        'BU_NAME="$SESSION" "$REAL_BU" --reload >/dev/null 2>&1 || true',
        f'echo "reload" >> {log}',
    )
    # The ladder must not reach for the shared browser at all.
    assert 'force=1' not in ladder, (
        'the wrapper is recycling a browser that concurrent tasks share'
    )
    assert 'reload' in ladder and '--reload' not in ladder

    script = tmp_path / 'ladder.sh'
    script.write_text(
        'set -euo pipefail\n'
        f'export BH_RUNTIME_DIR="{runtime}"\n'
        'SESSION="acme-deadbeef"\n'
        'REAL_BU="/nonexistent"\n'
        'PASSTHROUGH=()\n' + ladder
    )
    script.chmod(0o755)

    def run(stub_rc=TIMEOUT_RC):
        env = {**os.environ, 'STUB_RC': str(stub_rc)}
        for key in ('BU_NAME', 'BU_CDP_WS', 'BU_CDP_URL', 'BU_AUTOSPAWN'):
            env.pop(key, None)
        proc = subprocess.run(
            ['bash', str(script)],
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
        return proc

    def levers():
        if not log.exists():
            return []
        return log.read_text().split()

    return run, levers, log


def test_first_timeout_reloads_the_daemon_only(wedged):
    """One timeout is plausibly the daemon — the cheap lever comes first.

    Recycling the whole browser on the first hiccup would throw away a
    logged-in session (and, for Ziniao, a slow relaunch) over what is
    usually a stuck CDP handshake.
    """
    run, levers, _ = wedged
    proc = run()

    assert proc.returncode == TIMEOUT_RC, proc.stderr
    assert levers() == ['run', 'reload']
    assert 'consecutive: 1' in proc.stderr


def test_second_timeout_never_touches_the_shared_browser(wedged):
    """A reload that did not take must NOT escalate to a browser recycle.

    A store's browser is shared: concurrent tasks and ``--worker N`` slots
    each get their own daemon and mux client on one browser. Recycling it
    from a wrapper would destroy every peer's tabs and login state
    mid-task, and every peer's next call would escalate identically — a
    stop/start storm bounded only by the per-store relaunch budget. One
    tenant does not get to make a store-wide lifecycle call.
    """
    run, levers, _ = wedged
    run()
    proc = run()

    assert 'force-restart' not in levers()
    assert levers() == ['run', 'reload', 'run']
    assert 'recycl' not in proc.stderr.lower()


def test_second_timeout_reports_unrecoverable_and_stops(wedged):
    """The reload is the only lever; when it fails, say so and stop.

    This is the whole point. A distinct status lets a caller tell "retry
    on a fresh daemon" from "stop and record the gap", which is what
    turns 20 minutes of identical errors into one honest report.
    """
    run, levers, _ = wedged
    run()
    proc = run()

    assert proc.returncode == WEDGE_UNRECOVERABLE_RC, proc.stderr
    assert WEDGE_UNRECOVERABLE_RC != TIMEOUT_RC
    # No further lever is pulled — nothing is left to try.
    assert levers()[-1] == 'run'
    assert 'UNRECOVERABLE' in proc.stderr
    assert 'Stop retrying' in proc.stderr
    # The caller is told what to do with the work it could not finish,
    # so a wedge becomes a recorded gap instead of a silent shortfall.
    assert 'gap' in proc.stderr


def test_escalation_keeps_reporting_unrecoverable(wedged):
    """Past the ladder the verdict is stable, not a fresh escalation."""
    run, _, _ = wedged
    for _ in range(2):
        run()
    proc = run()

    assert proc.returncode == WEDGE_UNRECOVERABLE_RC
    assert 'UNRECOVERABLE' in proc.stderr


def test_any_answer_resets_the_budget(wedged):
    """A reachable browser must not inherit an earlier wedge's count.

    Without the reset, a store that wedged hours ago would be declared
    unrecoverable on its next single hiccup — and a caller would be told
    to give up on a browser that is answering fine.
    """
    run, levers, log = wedged
    run()
    assert levers() == ['run', 'reload']

    # A non-timeout status — even a plain error — proves reachability.
    assert run(stub_rc=1).returncode == 1
    log.write_text('')

    proc = run()
    assert proc.returncode == TIMEOUT_RC
    assert 'consecutive: 1' in proc.stderr
    assert levers() == ['run', 'reload'], 'budget did not reset'


def test_counter_is_per_session_not_per_store(wedged, tmp_path):
    """One wedged worker slot must not spend another slot's budget.

    Parallel workers share a store and a browser but get their own
    daemon, so their wedges are independent events.
    """
    run, _, _ = wedged
    run()
    run()

    runtime = tmp_path / 'r'
    names = sorted(p.name for p in runtime.glob('wedge-*.n'))
    assert names == ['wedge-acme-deadbeef.n'], names
    assert names, 'no counter file written'
    assert all(n != 'wedge-.n' for n in names)
    # The session name is in the filename, so -w1 and -w2 cannot collide.
    assert any('acme' in n for n in names), names
