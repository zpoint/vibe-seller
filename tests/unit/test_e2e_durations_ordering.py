"""The e2e tier is collected longest-first, and that ordering is what runs.

``tests/e2e/conftest.py::pytest_collection_modifyitems`` reorders the
tier by ``tests/e2e/durations.json``, and CI dispatches with
``--dist load --maxschedchunk 1``: the controller sends from the HEAD of
the collection to whichever worker is free (xdist keeps at least two
tests pending per worker — ``scheduler/load.py`` "keep at least 2 tests
pending even if --maxschedchunk=1"), so the order tests are collected in
is the order they start in.

A measured CI run is why that matters: 4880s of work over 6 workers took
1344s of wall time, because the two ~400s tests started at 910s and 943s
and four workers then sat idle for the last ~600s. Ordered longest-first
the same durations finish in ~814s — the floor (work / workers).

What is proven here is the MECHANISM, on a generated suite of sleeps —
never the real tier, which would spend provider credits to learn that a
sort worked. The strong assertion is deterministic (the order the
collected items end up in, read back from the run itself); the runtime
assertions use a slack of seconds because worker startup is seconds, and
they compare POSITIONS (does the longest test start in the first batch,
or last) rather than timings, so a loaded runner cannot flip them.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]

#: name -> seconds, ASCENDING in collection order (test_a.py holds the
#: three shortest, test_c.py the three longest). Left unordered, the run
#: starts with the short ones — the shape of the CI run this replaces.
SLEEPS = {
    'test_a01': 0.2,
    'test_a02': 0.3,
    'test_a03': 0.4,
    'test_b01': 0.5,
    'test_b02': 0.6,
    'test_b03': 0.7,
    'test_c01': 0.9,
    'test_c02': 1.2,
    'test_c03': 1.5,
}
LONGEST = 'test_c03'
WORKERS = 3

_CONFTEST = '''"""Reuse the tier's own ordering hook — not a copy of it."""

from tests.e2e.conftest import pytest_collection_modifyitems  # noqa: F401


def pytest_configure(config):
    config.addinivalue_line('markers', 'e2e: tier marker (synthetic)')
'''

#: Written LAST (``trylast``) so what it records is the order every
#: other hook — including the one under test — has already produced.
_ORDER_PLUGIN = '''"""Record the final collection order, as the runner sees it."""

import os
from pathlib import Path
import pytest


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(items):
    Path(os.environ['R8_COLLECTED']).write_text(
        '\\n'.join(item.nodeid for item in items), encoding='utf-8'
    )
'''

_TEST_FILE = '''"""Synthetic suite: sleeps of measured lengths, no server, no LLM."""

import os
import time

import pytest

pytestmark = pytest.mark.e2e

_STARTS = os.environ.get('R8_STARTS')


def _started(name: str) -> None:
    if _STARTS:
        with open(_STARTS, 'a', encoding='utf-8') as handle:
            worker = os.environ.get('PYTEST_XDIST_WORKER', 'main')
            handle.write(
                f'{{time.monotonic():.4f}} {{worker}} {{name}}' + chr(10)
            )


{tests}
'''


def _suite(tmp_path: Path) -> Path:
    """A generated 3-file suite, collection order = shortest test first."""
    root = tmp_path / 'suite'
    root.mkdir()
    (root / 'conftest.py').write_text(_CONFTEST, encoding='utf-8')
    (root / 'r8_order_plugin.py').write_text(_ORDER_PLUGIN, encoding='utf-8')
    by_letter: dict[str, list[str]] = {'a': [], 'b': [], 'c': []}
    for name, seconds in SLEEPS.items():
        letter = re.match(r'test_([abc])\d\d$', name).group(1)
        by_letter[letter].append(
            f'def {name}() -> None:\n'
            f'    _started("{name}")\n'
            f'    time.sleep({seconds})\n'
        )
    for letter, body in by_letter.items():
        (root / f'test_{letter}.py').write_text(
            _TEST_FILE.format(tests='\n\n'.join(body)), encoding='utf-8'
        )
    return root


def _pytest(
    root: Path, args: list[str], env: dict[str, str]
) -> subprocess.CompletedProcess:
    """Run pytest on the suite, from the repo root.

    cwd is the repo so the suite's conftest can ``import
    tests.e2e.conftest`` (``python -m pytest`` puts cwd first on
    sys.path); the suite itself stays OUTSIDE the repo, so no repo
    conftest, ini or collection touches it.
    """
    return subprocess.run(
        [
            sys.executable,
            '-m',
            'pytest',
            '-q',
            '-p',
            'no:cacheprovider',
            '-p',
            'r8_order_plugin',
            *args,
            str(root),
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _env(tmp_path: Path, durations: Path | None, root: Path) -> dict[str, str]:
    env = {
        **os.environ,
        'PYTHONPATH': f'{root}{os.pathsep}{os.environ.get("PYTHONPATH", "")}',
        'R8_COLLECTED': str(tmp_path / 'collected.txt'),
        'R8_STARTS': str(tmp_path / 'starts.txt'),
        'PYTHONDONTWRITEBYTECODE': '1',
    }
    if durations is not None:
        env['E2E_DURATIONS_FILE'] = str(durations)
    else:
        env.pop('E2E_DURATIONS_FILE', None)
    for name in ('collected.txt', 'starts.txt'):
        (tmp_path / name).write_text('', encoding='utf-8')
    return env


def _collected_order(tmp_path: Path) -> list[str]:
    text = (tmp_path / 'collected.txt').read_text(encoding='utf-8')
    names = [line.split('::')[1] for line in text.splitlines() if line]
    assert len(names) == len(SLEEPS), names
    return names


def _durations_file(tmp_path: Path, root: Path, env: dict[str, str]) -> Path:
    """durations.json keyed by the ids THIS runner reports for the suite.

    Node ids are relative to the child's rootdir, which is not
    predictable across platforms — so they are read back from a
    collection run rather than assumed.
    """
    assert _pytest(root, ['--collect-only', '-q'], env).returncode == 0
    path = tmp_path / 'durations.json'
    ids = [
        line
        for line in (tmp_path / 'collected.txt')
        .read_text(encoding='utf-8')
        .splitlines()
        if line
    ]
    assert len(ids) == len(SLEEPS), ids
    (tmp_path / 'collected.txt').write_text('', encoding='utf-8')
    path.write_text(
        json.dumps({node: SLEEPS[node.split('::')[1]] for node in ids}),
        encoding='utf-8',
    )
    return path


def _first_on_each_worker(tmp_path: Path) -> set[str]:
    """The test each worker ran FIRST.

    ``--dist load`` hands each worker its tests in collection order, so
    whichever worker received the longest test runs it before anything
    else -- however slowly the workers booted. Nothing here compares
    clocks across processes or against a bound.
    """
    first: dict[str, tuple[float, str]] = {}
    for line in (
        (tmp_path / 'starts.txt').read_text(encoding='utf-8').splitlines()
    ):
        if not line.strip():
            continue
        stamp, worker, name = line.split()
        if worker not in first or float(stamp) < first[worker][0]:
            first[worker] = (float(stamp), name)
    return {name for _, name in first.values()}


def _run_ordered(root: Path, env: dict[str, str]) -> None:
    """The suite the way CI runs the tier."""
    done = _pytest(
        root,
        ['-n', str(WORKERS), '--dist', 'load', '--maxschedchunk', '1'],
        env,
    )
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-2000:]


def test_measured_durations_order_the_collection_longest_first(tmp_path):
    """The hook is the whole ordering: durations decide the collection.

    Deterministic — nothing here depends on how fast anything ran.
    """
    root = _suite(tmp_path)
    plain = _env(tmp_path, None, root)
    durations = _durations_file(tmp_path, root, plain)

    ordered_env = _env(tmp_path, durations, root)
    assert _pytest(root, ['--collect-only', '-q'], ordered_env).returncode == 0
    assert _collected_order(tmp_path) == [
        'test_c03',
        'test_c02',
        'test_c01',
        'test_b03',
        'test_b02',
        'test_b01',
        'test_a03',
        'test_a02',
        'test_a01',
    ]


def test_the_longest_test_is_the_first_its_worker_runs(tmp_path):
    """Under ``load`` the ordering reaches the workers, not just the log.

    The worker that receives the 1.5s test runs it before anything else.
    Asserted per worker, not on the global start order or the wall time:
    those depend on which worker booted first and on machine load (a wall
    bound here failed on a busy machine), this does not.
    """
    root = _suite(tmp_path)
    durations = _durations_file(tmp_path, root, _env(tmp_path, None, root))
    _run_ordered(root, _env(tmp_path, durations, root))

    assert LONGEST in _first_on_each_worker(tmp_path)


def test_no_durations_file_means_no_reordering(tmp_path):
    """NEGATIVE CONTROL: no measurements, collection order runs.

    Same suite, same scheduler, no durations file. The 1.5s test -- the
    one that leads when the ordering is on -- is collected last, so no
    worker runs it first. If this ever looks like longest-first, the test
    above is proving nothing.
    """
    root = _suite(tmp_path)
    env = _env(tmp_path, None, root)
    _run_ordered(root, env)

    collected = _collected_order(tmp_path)
    assert collected[0] == 'test_a01', collected
    assert LONGEST not in _first_on_each_worker(tmp_path)
