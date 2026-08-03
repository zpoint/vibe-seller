#!/usr/bin/env python3
"""Tell a not-yet-published fee report from a genuinely-zero one.

An FBN finance report for a service month the marketplace has not posted
yet still reaches ``Complete`` on the Reports page and still downloads
successfully. What arrives is a valid CSV holding its header row and no
data — a few hundred bytes, so it passes every "the file exists and is
non-empty" check. One such file shipped as a delivered monthly report
inside a run that was marked complete and independently reviewed as ok.

The list page hints at it: the *Nr. of Rows* column shows a number for a
populated report and an ellipsis for an empty one. That hint is easy to
miss and impossible to assert on, so check the file instead.

Zero rows means different things per report, and that is the whole
reason this script exists rather than a blanket rule:

``monthly_storage`` / ``longterm_storage`` / ``nonsaleable_storage``
    Storage **accrues** against stock held all month. If the store held
    inventory, a zero-row month has not been published yet.

``rtv_removal``
    Removals are discrete **events**. A month with no removals is a
    legitimate zero, and the file is still worth keeping as proof the
    question was asked.

Usage::

    python check_fee_rows.py <dir> [--service-month YYYY-MM] [--json]

Exit status is 1 when any accrual report is empty, so a caller can gate
on it; ``--json`` prints a machine-readable summary instead of prose.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

#: Filename stem → whether zero rows is a legal answer.
EVENT_STEMS = ('rtv_removal',)
ACCRUAL_STEMS = (
    'monthly_storage',
    'longterm_storage',
    'nonsaleable_storage',
)


def data_rows(path: Path) -> int:
    """Data rows in a CSV, header excluded.

    Parsed rather than line-counted: a quoted field may contain a
    newline, and a product title that wraps would otherwise make an empty
    file look populated.
    """
    try:
        with path.open('r', encoding='utf-8-sig', newline='') as fh:
            return max(sum(1 for _ in csv.reader(fh)) - 1, 0)
    except (OSError, UnicodeDecodeError, csv.Error):
        return 0


def classify(name: str) -> str:
    """``'accrual'``, ``'event'``, or ``''`` for a file we don't judge."""
    stem = name.lower()
    if any(stem.startswith(s) for s in EVENT_STEMS):
        return 'event'
    if any(stem.startswith(s) for s in ACCRUAL_STEMS):
        return 'accrual'
    return ''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('directory', help='folder holding the downloaded CSVs')
    ap.add_argument(
        '--service-month',
        help='YYYY-MM these reports are for; only used in the message',
    )
    ap.add_argument('--json', action='store_true', dest='as_json')
    args = ap.parse_args()

    root = Path(args.directory).expanduser()
    if not root.is_dir():
        print(f'not a directory: {root}', file=sys.stderr)
        return 2

    findings = []
    for path in sorted(root.glob('*.csv')):
        kind = classify(path.name)
        if not kind:
            continue
        rows = data_rows(path)
        findings.append({
            'file': path.name,
            'kind': kind,
            'rows': rows,
            # An empty accrual report is the actionable state; an
            # empty event report is a fact about the month.
            'verdict': (
                'not_published' if kind == 'accrual' and rows == 0 else 'ok'
            ),
        })

    unpublished = [f for f in findings if f['verdict'] == 'not_published']

    if args.as_json:
        print(
            json.dumps(
                {
                    'checked': findings,
                    'not_published': [f['file'] for f in unpublished],
                },
                indent=2,
            )
        )
    else:
        if not findings:
            print(f'No FBN fee CSVs found in {root}')
        for f in findings:
            mark = '✗' if f['verdict'] == 'not_published' else '✓'
            note = '' if f['kind'] == 'accrual' else '  (zero is legal)'
            print(f'{mark} {f["file"]}: {f["rows"]} data rows{note}')
        if unpublished:
            month = args.service_month or 'this service month'
            print(
                f'\n{len(unpublished)} storage report(s) for {month} came '
                'back empty. Storage accrues against held stock, so an '
                'empty file means the marketplace has not posted the '
                'service month yet — NOT that the fee was zero.\n'
                'Do not retry: a second generation returns a '
                'byte-identical empty file. Report these as pending and '
                'let the next run pick them up.'
            )

    return 1 if unpublished else 0


if __name__ == '__main__':
    sys.exit(main())
