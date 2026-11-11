#!/usr/bin/env python3
"""Regenerate ``tests/e2e/durations.json`` from a CI e2e job log.

The e2e tier is dominated by wall-clock: one pytest-xdist run, six
workers, and the suite's finish time is the SLOWEST worker, not the sum.
Measured on a green run (6 workers, ``--dist worksteal``): span 1344s
while the work was 4880s — four workers sat idle for the last ~600s,
because the two ~400s tests only STARTED at 910s and 943s. The same
durations scheduled longest-first finish in ~814s, which is the
theoretical floor (work / 6). That log is PR #230's ``e2e-test`` job —
run 42896988559, job 130112844242 — the source recorded in the file.
So the order the tests are COLLECTED IN is what the run costs, and this
file is the measurement that order is built from
(``tests/e2e/conftest.py::pytest_collection_modifyitems``).

Usage::

    gh api repos/zpoint/vibe-seller/actions/jobs/<JOB_ID>/logs > /tmp/e2e.log
    .venv/bin/python scripts/e2e_durations.py /tmp/e2e.log

``<JOB_ID>`` is the ``e2e-test`` job of a GREEN run (find it with
``gh api repos/zpoint/vibe-seller/actions/runs/<run>/jobs``). A run where
the step timed out or the provider 529'd says more about that failure
than about the tests, so take a green one.

Output: ``{nodeid: seconds}``, pytest-split's shape — a flat map of the
full node id to the measured wall time of its last attempt. Keys that
start with ``"_"`` are provenance, never test ids, and the reader skips
them.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re

START_RE = re.compile(r'^(\S+Z) (tests/e2e/\S+)\s*$', re.M)
DONE_RE = re.compile(
    r'^(\S+Z) \[(gw\d+)\] \[\s*\d+%\] (PASSED|FAILED|ERROR|SKIPPED|XFAIL)'
    r' (tests/e2e/\S+)',
    re.M,
)
OUT_DEFAULT = Path('tests/e2e/durations.json')


def _ts(text: str) -> datetime:
    """GitHub's timestamp ('…Z', 7-digit fraction) as an aware datetime."""
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def durations_from_log(text: str) -> dict[str, float]:
    """Seconds per e2e node id, from one CI job log.

    A line-by-line pytest ``-v`` log has no duration column, so each
    test's span is its start line to its result line. A rerun
    (``--reruns 1``) re-prints both, so the LAST start/result pair wins:
    it is the attempt that ended the test, and the first attempt's
    timeout would otherwise be recorded as the test's cost. Skipped
    tests are left out — they measured nothing, and an unknown test gets
    the reader's default anyway.
    """
    started: dict[str, datetime] = {}
    finished: dict[str, datetime] = {}
    for match in START_RE.finditer(text):
        started.setdefault(match.group(2), _ts(match.group(1)))
        # A later start for the same id is the rerun: keep the newest.
        started[match.group(2)] = _ts(match.group(1))
    for match in DONE_RE.finditer(text):
        state, node = match.group(3), match.group(4)
        if state == 'SKIPPED':
            finished.pop(node, None)
            continue
        finished[node] = _ts(match.group(1))
    out: dict[str, float] = {}
    for node, done in finished.items():
        begin = started.get(node)
        if begin is None or done < begin:
            continue
        out[node] = round((done - begin).total_seconds(), 1)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('log', type=Path, help='a CI e2e job log')
    parser.add_argument(
        '--out', type=Path, default=OUT_DEFAULT, help=f'default: {OUT_DEFAULT}'
    )
    parser.add_argument(
        '--source',
        default='',
        help='where the log came from, recorded under "_source"',
    )
    args = parser.parse_args()

    text = args.log.read_text(encoding='utf-8', errors='replace')
    found = durations_from_log(text)
    if not found:
        print(f'no e2e results found in {args.log}', flush=True)
        return 1
    payload: dict[str, object] = {'_source': args.source or 'unrecorded'}
    payload.update(dict(sorted(found.items())))
    args.out.write_text(
        json.dumps(payload, indent=1, sort_keys=True) + '\n', encoding='utf-8'
    )
    longest = sorted(found.items(), key=lambda kv: -kv[1])[:5]
    print(f'{args.out}: {len(found)} tests, {sum(found.values()):.0f}s of work')
    for node, seconds in longest:
        print(f'  {seconds:6.1f}s  {node}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
