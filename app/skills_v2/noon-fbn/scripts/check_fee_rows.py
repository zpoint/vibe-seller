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

Zero rows means different things per report, and getting that wrong in
either direction costs something. Three classes, not two:

``monthly_storage`` — the **witness**
    Billed against ALL held stock, so any inventory at all produces rows.
    It is therefore the only report whose emptiness proves the service
    month is not posted, and the only one that can vouch for the others.

``longterm_storage`` / ``nonsaleable_storage`` — **conditional** accruals
    Long-term bills only stock aged past the threshold; non-saleable only
    damaged or expired units. A small, fast-turning store genuinely has
    zero of both. So zero here is real *if the witness has rows*, and
    means "not posted" only if the witness is empty too.

``rtv_removal`` — an **event** report
    Removals are discrete occurrences. Zero is always a legitimate
    answer, and the file is still worth keeping as proof the question
    was asked.

That middle class is why a blanket "storage accrues, so empty means
unpublished" rule is wrong. Measured live: one project's June had 17
monthly-storage rows — so June was published — alongside zero long-term
and zero non-saleable, while a larger project the same month had 1 and
162 rows. The blanket rule called the small project's published month
missing, which sends a run back to re-fetch files that are already
correct.

Usage::

    python check_fee_rows.py <dir> [--service-month YYYY-MM] [--json]

Exit status is 1 when anything is unposted or undecidable, so a caller
can gate on it; ``--json`` prints a machine-readable summary instead.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import sys

#: Removals are discrete occurrences — zero is always a legal answer.
EVENT_STEMS = ('rtv_removal',)

#: Billed against ALL held stock, so any inventory at all produces rows.
#: This is the only report whose emptiness proves the month is unpublished,
#: which makes it the publication **witness** for its country.
WITNESS_STEM = 'monthly_storage'

#: Also accruals, but CONDITIONAL ones: long-term storage bills only stock
#: aged past the threshold, non-saleable only damaged/expired units. A
#: small, fast-turning store genuinely has zero of both — measured live,
#: one project's June had 17 monthly-storage rows (so June was published)
#: alongside zero long-term and zero non-saleable, while a larger project
#: the same month had 1 and 162. Treating these as unconditional reported
#: a published month as missing, which is the expensive direction: it
#: sends a run back to re-fetch a file that is already correct.
CONDITIONAL_STEMS = ('longterm_storage', 'nonsaleable_storage')


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


def month_of(name: str) -> str | None:
    """The ``YYYY-MM`` service month in a filename, if it carries one.

    ``monthly_storage_sa_2026-06.csv`` -> ``'2026-06'``. Returns ``None``
    when the name has no month token; such a file can neither be
    certified by a witness nor act as one, because there is no month to
    match it against.
    """
    m = re.search(r'(\d{4})-(0[1-9]|1[0-2])(?!\d)', name)
    return f'{m.group(1)}-{m.group(2)}' if m else None


def classify(name: str) -> str:
    """``'witness'``, ``'conditional'``, ``'event'``, or ``''``."""
    stem = name.lower()
    if any(stem.startswith(s) for s in EVENT_STEMS):
        return 'event'
    if stem.startswith(WITNESS_STEM):
        return 'witness'
    if any(stem.startswith(s) for s in CONDITIONAL_STEMS):
        return 'conditional'
    return ''


def country_of(name: str) -> str:
    """``monthly_storage_sa_2026-06.csv`` → ``sa``.

    Publication is per country, so a witness only vouches for its own.
    """
    parts = name.rsplit('.', 1)[0].split('_')
    for p in reversed(parts):
        if len(p) == 2 and p.isalpha():
            return p.lower()
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

    scanned = []
    for path in sorted(root.glob('*.csv')):
        kind = classify(path.name)
        if not kind:
            continue
        scanned.append({
            'file': path.name,
            'kind': kind,
            'country': country_of(path.name),
            'month': month_of(path.name),
            'rows': data_rows(path),
        })

    # Per country AND service month, does the witness prove publication?
    # Only monthly storage can: it bills all held stock, so rows there
    # mean the month exists. Without a witness we cannot tell a genuine
    # zero from an unpublished month, and we say so rather than guessing.
    #
    # Keyed by (country, month), never by country alone. A folder can hold
    # more than one service month — the monthly run stages the month it is
    # finalising beside the one it is provisionally computing — and a
    # witness only ever vouches for its own month. Keyed by country, a
    # populated June witness would certify an empty July conditional as a
    # genuine zero, which is the exact misreading this script exists to
    # prevent.
    def key(entry):
        return (entry['country'], entry['month'])

    published = {
        key(e) for e in scanned if e['kind'] == 'witness' and e['rows'] > 0
    }
    witnessed = {key(e) for e in scanned if e['kind'] == 'witness'}

    findings = []
    for e in scanned:
        if e['rows'] > 0 or e['kind'] == 'event':
            verdict = 'ok'
        elif e['kind'] == 'witness':
            # The witness itself is empty: nothing held stock, which for
            # an active store means the month is not posted.
            verdict = 'not_published'
        elif key(e) in published:
            # This month's own witness says the month exists, so the
            # zero is real.
            verdict = 'genuinely_zero'
        elif key(e) in witnessed:
            verdict = 'not_published'
        else:
            verdict = 'unknown'
        findings.append({**e, 'verdict': verdict})

    unpublished = [f for f in findings if f['verdict'] == 'not_published']
    unknown = [f for f in findings if f['verdict'] == 'unknown']

    NOTES = {
        'ok': '',
        'genuinely_zero': '  (zero is real — monthly storage has rows, '
        'so the month IS published)',
        'not_published': '  (month not posted yet)',
        'unknown': '  (no monthly_storage for this country to compare '
        'against — cannot tell)',
    }

    if args.as_json:
        print(
            json.dumps(
                {
                    'checked': findings,
                    'not_published': [f['file'] for f in unpublished],
                    'unknown': [f['file'] for f in unknown],
                },
                indent=2,
            )
        )
    else:
        if not findings:
            print(f'No FBN fee CSVs found in {root}')
        for f in findings:
            mark = '✗' if f['verdict'] in ('not_published', 'unknown') else '✓'
            note = NOTES[f['verdict']]
            if f['verdict'] == 'ok' and f['kind'] == 'event' and not f['rows']:
                note = '  (zero is legal — removals are events)'
            print(f'{mark} {f["file"]}: {f["rows"]} data rows{note}')

        month = args.service_month or 'this service month'
        if unpublished:
            print(
                f'\n{len(unpublished)} report(s) for {month} are not posted '
                'yet. Monthly storage bills ALL held stock, so its emptiness '
                'is what proves the month is missing — NOT that the fee was '
                'zero.\nDo not retry: a second generation returns a '
                'byte-identical empty file. Report these as pending and let '
                'the next run pick them up.'
            )
        if unknown:
            print(
                f'\n{len(unknown)} report(s) for {month} are empty with no '
                'monthly_storage for their country to compare against. '
                "Download that country's Monthly Storage Charge report and "
                're-run: with rows there, these zeros are real; without, the '
                'month is not posted.'
            )

    return 1 if (unpublished or unknown) else 0


if __name__ == '__main__':
    sys.exit(main())
