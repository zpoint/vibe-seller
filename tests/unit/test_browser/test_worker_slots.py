"""Parallel worker slots: `browser-use --worker N`.

A subagent runs in its parent's process and inherits its environment,
``VIBE_TASK_ID`` included. The wrapper derives BOTH the browser-use
daemon name and the CDP mux client id from that one variable, so a
subagent driving the browser at the same time as its parent lands on the
same daemon — and ``browser_harness`` serves each socket connection in
its own asyncio task with no lock, so the two interleave on one tab and
silently return each other's pages.

``--worker N`` is the way out that keeps the wrapper owning the
environment: the caller supplies a validated slot NUMBER and the wrapper
derives a distinct daemon and a distinct mux client from it. These tests
pin the properties that make that safe:

  * a slot changes BOTH identifiers, and changes them together;
  * distinct slots never collide, with each other or with the main
    session;
  * the agent cannot turn the flag into an arbitrary session id;
  * a worker daemon stays attributable to its task, so the reaper kills
    it when the task ends.
"""

import os
from pathlib import Path
import re
import subprocess
from unittest import mock

import pytest

from app.browser import wrapper as wrapper_mod
from app.browser.cdp_mux_types import short_cid
from app.browser.daemon_reaper import task_prefix_for_bu_name
from app.browser.manager import write_browser_use_wrapper
from app.browser.worker_slots import (
    max_worker_slots,
    worker_session_regex_fragment,
)

_TASK_ID = 'a1b2c3d4-0000-0000-0000-000000000000'
_TASK8 = 'a1b2c3d4'


def _stub_bu(tmp_path: Path) -> Path:
    """A browser-use stand-in that prints the BU_* env the wrapper set."""
    stub = tmp_path / 'fake_bu.sh'
    stub.write_text(
        '#!/usr/bin/env bash\ncat >/dev/null\nenv | grep -E "^BU_" | sort\n'
    )
    stub.chmod(0o755)
    return stub


def _wrapper(tmp_path: Path, slots: int | None = None) -> Path:
    """Generate a per-store wrapper wired to offline stubs.

    ``slots`` overrides ``VIBE_BROWSER_WORKER_SLOTS`` at GENERATION time
    — the bound is baked into the script, so this is also the only way
    to test the disabled build.
    """
    bin_dir = tmp_path / 'bin'
    env = {'VIBE_BROWSER_WORKER_SLOTS': str(slots)} if slots is not None else {}
    with (
        mock.patch.object(wrapper_mod, '_BIN_DIR', bin_dir),
        mock.patch.dict(os.environ, env),
    ):
        write_browser_use_wrapper(
            'test-store', 'chrome', 9222, store_id='store-1'
        )
    path = bin_dir / 'test-store' / 'browser-use'
    text = path.read_text()
    text = re.sub(r'REAL_BU="[^"]*"', f'REAL_BU="{_stub_bu(tmp_path)}"', text)
    text = text.replace('curl ', '/usr/bin/true ')
    path.write_text(text)
    return path


def _run(wrapper: Path, *args: str, task: bool = True):
    env = {k: v for k, v in os.environ.items() if k != 'VIBE_TASK_ID'}
    env.pop('BU_NAME', None)
    env.pop('BU_CDP_WS', None)
    if task:
        env['VIBE_TASK_ID'] = _TASK_ID
    return subprocess.run(
        [str(wrapper), *args],
        capture_output=True,
        text=True,
        timeout=30,
        input='',
        env=env,
    )


def _env(result) -> dict[str, str]:
    """BU_* env the wrapper injected, as a dict."""
    return dict(
        line.split('=', 1)
        for line in result.stdout.splitlines()
        if line.startswith('BU_')
    )


@pytest.mark.unit
class TestWorkerSlotIdentity:
    """A slot must move the daemon and the mux client TOGETHER."""

    def test_slot_changes_both_daemon_and_client(self, tmp_path: Path):
        wrapper = _wrapper(tmp_path)
        main = _env(_run(wrapper))
        w2 = _env(_run(wrapper, '--worker', '2'))

        assert main['BU_NAME'] == f'test-store-{_TASK8}'
        assert w2['BU_NAME'] == f'test-store-{_TASK8}-w2'
        # Distinct BU_NAME alone is not enough: two daemons pointed at
        # ONE mux client id would still share that client's tabs, and
        # would clobber each other's registration in the mux.
        assert main['BU_CDP_WS'].endswith(f'/client-{_TASK_ID}')
        assert w2['BU_CDP_WS'].endswith(f'/client-{_TASK_ID}-w2')

    def test_distinct_slots_never_collide(self, tmp_path: Path):
        wrapper = _wrapper(tmp_path)
        seen = [_env(_run(wrapper))]
        seen += [
            _env(_run(wrapper, '--worker', str(n)))
            for n in range(1, max_worker_slots() + 1)
        ]
        names = [e['BU_NAME'] for e in seen]
        endpoints = [e['BU_CDP_WS'] for e in seen]
        assert len(set(names)) == len(names), names
        assert len(set(endpoints)) == len(endpoints), endpoints

    def test_equals_form_is_equivalent(self, tmp_path: Path):
        wrapper = _wrapper(tmp_path)
        assert _env(_run(wrapper, '--worker=3')) == _env(
            _run(wrapper, '--worker', '3')
        )

    def test_slot_rides_the_same_store_browser(self, tmp_path: Path):
        """A worker is a second TAB, not a second browser.

        The whole point is a logged-in page without a second login: the
        endpoint must stay on this store's own proxy port, differing
        only in the client id.
        """
        wrapper = _wrapper(tmp_path)
        main = _env(_run(wrapper))['BU_CDP_WS']
        w1 = _env(_run(wrapper, '--worker', '1'))['BU_CDP_WS']
        assert main.rsplit('/client-', 1)[0] == w1.rsplit('/client-', 1)[0]
        assert 'ws://127.0.0.1:9222' in w1


@pytest.mark.unit
class TestWorkerSlotValidation:
    """The agent supplies a number; the wrapper decides everything else."""

    @pytest.mark.parametrize(
        'bad',
        [
            '0',  # slots are 1-based
            '4',  # above the generated bound
            '99',
            'abc',
            '',
            '1x',
            '08',  # leading zero: `[ 08 -lt 1 ]` is invalid octal
            '-1',
            '1 2',
        ],
    )
    def test_bad_slot_rejected(self, tmp_path: Path, bad: str):
        wrapper = _wrapper(tmp_path, slots=3)
        result = _run(wrapper, '--worker', bad)
        assert result.returncode == 1, result.stdout
        assert 'ERROR: --worker' in result.stderr

    def test_slot_cannot_smuggle_a_session_name(self, tmp_path: Path):
        """`--worker` takes a NUMBER — never a session id.

        This is the property that keeps "agent invents an arbitrary id"
        unrepresentable, which is what made the old rotate-VIBE_TASK_ID
        workaround harmful.
        """
        wrapper = _wrapper(tmp_path, slots=3)
        for attempt in ('other-store', 'test-store-aux', '../evil', '1;id'):
            result = _run(wrapper, '--worker', attempt)
            assert result.returncode == 1, attempt
            assert 'ERROR: --worker' in result.stderr

    def test_worker_and_session_are_mutually_exclusive(self, tmp_path: Path):
        """A slot is a slot on the MAIN browser, never grafted onto aux."""
        wrapper = _wrapper(tmp_path)
        result = _run(wrapper, '--session', 'test-store-aux', '--worker', '1')
        assert result.returncode == 1
        assert 'mutually exclusive' in result.stderr
        # ... in either order.
        result = _run(wrapper, '--worker', '1', '--session', 'test-store-aux')
        assert result.returncode == 1
        assert 'mutually exclusive' in result.stderr

    def test_aux_and_main_still_work_unchanged(self, tmp_path: Path):
        """The slot feature must not narrow what already worked."""
        wrapper = _wrapper(tmp_path)
        assert _run(wrapper).returncode == 0
        aux = _run(wrapper, '--session', 'test-store-aux')
        assert aux.returncode == 0, aux.stderr
        assert _env(aux)['BU_NAME'] == 'test-store-aux'
        assert _env(aux)['BU_CDP_WS'].endswith('/client-aux')

    def test_slots_can_be_disabled(self, tmp_path: Path):
        """`VIBE_BROWSER_WORKER_SLOTS=0` removes the capability outright.

        Not merely "range 1..0": the flag must fail loudly, and the
        session allowlist must fall back to the pre-slot shape so a
        `-w1` name cannot slip through some other path.
        """
        wrapper = _wrapper(tmp_path, slots=0)
        result = _run(wrapper, '--worker', '1')
        assert result.returncode == 1
        assert 'disabled' in result.stderr
        assert '-w' not in wrapper.read_text().split('=~')[1].split(']]')[0]


@pytest.mark.unit
class TestWorkerSessionAllowlist:
    """The generated regex is the last line of defence behind the flag."""

    @pytest.mark.parametrize(
        ('slots', 'fragment'),
        [(0, ''), (1, '-w1'), (3, '-w[1-3]'), (9, '-w[1-9]')],
    )
    def test_fragment_shape(self, slots: int, fragment: str):
        assert worker_session_regex_fragment(slots) == fragment

    def test_double_digit_slots_use_alternation(self):
        """`-w[1-12]` would be the character class 1,-,2 plus a stray 1."""
        frag = worker_session_regex_fragment(12)
        assert frag.startswith('-w(1|2|')
        assert frag.endswith('|12)')

    def test_allowlist_still_rejects_a_foreign_session(self, tmp_path: Path):
        wrapper = _wrapper(tmp_path)
        result = _run(wrapper, '--session', 'other-store-w1', task=False)
        assert result.returncode == 1
        assert 'not allowed' in result.stderr

    def test_allowlist_rejects_an_out_of_range_slot_name(self, tmp_path: Path):
        """Even reached directly by name, not via the flag."""
        wrapper = _wrapper(tmp_path, slots=3)
        result = _run(wrapper, '--session', 'test-store-w9', task=False)
        assert result.returncode == 1
        assert 'not allowed' in result.stderr

    def test_worker_tail_is_optional_not_mandatory(self, tmp_path: Path):
        """Regression guard for `-w[1-3]?` (the `?` binds to the class).

        Written ungrouped, the tail makes `-w` REQUIRED and every plain
        session name — including the default one every task uses — stops
        validating.
        """
        wrapper = _wrapper(tmp_path)
        assert _run(wrapper, task=False).returncode == 0
        assert (
            _run(wrapper, '--session', 'test-store', task=False).returncode == 0
        )
        assert (
            _run(wrapper, '--session', f'test-store-{_TASK8}', task=False)
        ).returncode == 0


@pytest.mark.unit
class TestWorkerDaemonIsReapable:
    """A worker daemon is spawned BY a task and must die WITH it."""

    def test_pidfile_regex_attributes_a_worker_to_its_task(self):
        for bu_name in (
            f'test-store-{_TASK8}',
            f'test-store-{_TASK8}-w1',
            f'test-store-{_TASK8}-w12',
        ):
            assert task_prefix_for_bu_name(bu_name) == _TASK8, bu_name

    def test_non_task_sessions_stay_unattributed(self):
        """Manual/aux daemons have no owning task and must not be reaped."""
        for bu_name in ('test-store', 'test-store-aux', 'test-store-w1'):
            assert task_prefix_for_bu_name(bu_name) is None, bu_name

    def test_the_name_the_wrapper_writes_is_the_name_the_reaper_reads(
        self, tmp_path: Path
    ):
        """Pin the two ends together, not each against a literal.

        The reaper keys off `bu-<BU_NAME>.pid`, so a naming change in
        the wrapper that the reaper's regex doesn't follow leaks a
        daemon (and a mux client) per parallel subagent, forever.
        """
        wrapper = _wrapper(tmp_path)
        bu_name = _env(_run(wrapper, '--worker', '2'))['BU_NAME']
        assert task_prefix_for_bu_name(bu_name) == _TASK8, bu_name


@pytest.mark.unit
class TestSlotsStayVisibleInLogs:
    """A slot you cannot see in the log is a slot you cannot debug.

    Mux ownership lines truncate the client id. That was unambiguous
    only while every client WAS a distinct task uuid; slots append the
    suffix, so head-truncation collapses a parent and all of its workers
    into one string — exactly when telling them apart is the point.
    """

    def test_slot_survives_truncation(self):
        assert short_cid(f'{_TASK_ID}-w2') == f'{_TASK8}-w2'
        assert short_cid(f'{_TASK_ID}-w13') == f'{_TASK8}-w13'

    def test_main_client_is_unchanged(self):
        """No churn for the common case — still the plain head."""
        assert short_cid(_TASK_ID) == _TASK8
        assert short_cid(_TASK_ID, 16) == _TASK_ID[:16]

    def test_a_task_and_its_slots_are_all_distinct(self):
        """The property the raw `[:8]` truncation lost."""
        rendered = {
            short_cid(c)
            for c in (
                _TASK_ID,
                f'{_TASK_ID}-w1',
                f'{_TASK_ID}-w2',
                f'{_TASK_ID}-w3',
            )
        }
        assert len(rendered) == 4, rendered

    def test_non_slot_suffixes_are_not_mistaken_for_one(self):
        # `-aux` and arbitrary tails must not be treated as slots.
        assert short_cid('some-store-aux') == 'some-sto'
        assert short_cid('deadbeef-cafe-wX') == 'deadbeef'

    def test_short_ids_are_left_alone(self):
        assert short_cid('aux') == 'aux'
        assert short_cid('abc-w2') == 'abc-w2'
