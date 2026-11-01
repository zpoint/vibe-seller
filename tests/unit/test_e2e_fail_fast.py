"""--maxfail stops an xdist e2e run instead of draining it.

In CI one early failure under ``--dist worksteal --maxfail=1`` left the
failing worker idle while the others ran their whole pre-assigned share;
the drain outlived the 32-minute step timeout and the kill erased the
summary, so the failure that started it was never printed. Each case
runs a small inner suite through real pytest-xdist in a subprocess.
"""

from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

_PLUGIN = Path(__file__).parents[1] / 'e2e' / 'fail_fast.py'

_CONFTEST = """
import fail_fast


def pytest_configure(config):
    fail_fast.register(config)
"""

_SUITE = """
import time

import pytest


@pytest.mark.parametrize('i', range(12))
def test_t(i):
    if i == 0 and {fail}:
        assert False, 'early failure'
    time.sleep(0.5)
"""


def _run(tmp_path, *, fail, extra=()):
    (tmp_path / 'fail_fast.py').write_text(_PLUGIN.read_text())
    (tmp_path / 'conftest.py').write_text(_CONFTEST)
    (tmp_path / 'test_inner.py').write_text(_SUITE.format(fail=fail))
    proc = subprocess.run(
        [
            sys.executable,
            '-m',
            'pytest',
            '-p',
            'no:cacheprovider',
            '--rootdir',
            str(tmp_path),
            '-c',
            str(tmp_path / 'conftest.py'),
            '-n',
            '3',
            '--dist',
            'worksteal',
            '--maxfail=1',
            *extra,
            'test_inner.py',
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.stdout.strip().splitlines()[-1]


def test_unstarted_tests_are_skipped_after_the_first_failure(tmp_path):
    summary = _run(tmp_path, fail=True)
    assert '1 failed' in summary
    # Without the plugin the other workers drain their shares and the
    # summary reads "1 failed, N passed" with nothing skipped.
    assert 'skipped' in summary, summary


def test_a_green_run_is_untouched(tmp_path):
    summary = _run(tmp_path, fail=False)
    assert '12 passed' in summary and 'skipped' not in summary, summary
