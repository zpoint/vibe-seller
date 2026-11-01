"""Make ``--maxfail`` stop an xdist run instead of draining it.

pytest-xdist honours ``--maxfail`` by sending every worker "shutdown",
and a worker queues that marker BEHIND the tests it already holds
(``xdist/remote.py``: ``torun.put(Marker.SHUTDOWN)``). Under
``--dist worksteal`` each worker is handed an even share of the whole
suite up front and stealing stops once shutdown starts, so one early
failure produced the worst of both: the failing worker dropped its
share and sat idle, the others ran theirs to the end, and the run took
as long as a green one. In CI that drain outlived the step timeout,
and the kill erased pytest's summary — the failure that started it was
never printed.

Here the controller drops a flag file at the ``--maxfail``-th failure
and every worker skips each test it has not started yet. Tests already
in flight finish, so a failed run ends no later than a green one would
have — sooner, in practice — and the summary always gets written.
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

_KEY = 'e2e_abort_flag'


class _Controller:
    """xdist controller side: raise the flag, hand its path to workers."""

    def __init__(self, maxfail: int):
        self.maxfail = maxfail
        self.failures = 0
        self.flag = Path(tempfile.mkdtemp(prefix='e2e-abort-')) / 'abort'

    def pytest_configure_node(self, node):
        node.workerinput[_KEY] = str(self.flag)

    def pytest_runtest_logreport(self, report):
        # A rerun attempt reports outcome 'rerun', not 'failed', so only
        # the attempt that really fails counts — as with --maxfail itself.
        if report.failed:
            self.failures += 1
            if self.failures >= self.maxfail:
                self.flag.touch()


class _Worker:
    """xdist worker side: skip what has not started once the flag is up."""

    def __init__(self, flag: str):
        self.flag = Path(flag)

    @pytest.hookimpl(tryfirst=True)
    def pytest_runtest_setup(self, item):
        if self.flag.exists():
            pytest.skip('not run: an earlier test failed (--maxfail)')


def register(config: pytest.Config) -> None:
    """Install the controller or worker half; no-op without xdist."""
    workerinput = getattr(config, 'workerinput', None)
    if workerinput is not None:
        if _KEY in workerinput:
            config.pluginmanager.register(_Worker(workerinput[_KEY]))
        return
    maxfail = config.getoption('maxfail') or 0
    if maxfail > 0 and config.pluginmanager.hasplugin('xdist'):
        config.pluginmanager.register(_Controller(maxfail))
