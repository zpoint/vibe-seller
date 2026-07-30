"""The per-campaign TSVs have a FIXED header, and the server checks it.

These files are the machine-readable half of an audit: the review console
renders from them, an execution task acts on them, and the next audit
diffs against them. ``output-spec.md`` said to write them and said what
they were for — but never said what the columns were, so every run
invented a header.

Measured on one store's files: **22 different targeting headers and 12
search-term headers**, including ``target/match/entity/…``,
``targeting_group/status/…``, ``row_id/status/match_type/…``, English
title-case, Chinese, and two files delimited with PIPES rather than tabs
(read as a TSV those are one column wide and every figure is lost).
Currency was folded into column names — ``spend_SAR``, ``Spend (AED)``,
plain ``spend`` — so a reader looking for spend finds a different column
per market, or none.

The cost was not hypothetical: the agent's own summary script mis-summed
the layer and it had to recompute, because nothing could rely on a column
being where it was last time.

So the schema moves out of prose and into code. This is deliberately a
HEADER check, not a value check — the values are already covered by the
reconciliation, rollup and export cross-checks. What was missing was any
guarantee that a consumer can FIND a column.
"""

from __future__ import annotations

import csv

# Exact columns, in order. Two contracts, one per layer.
TARGETING_COLUMNS = (
    'ad_group',
    'target',
    'match_type',
    'state',
    'bid',
    'currency',
    'clicks',
    'spend',
    'orders',
    'sales',
    'acos',
    'roas',
    'suggestion',
    # ── what was actually DONE to this row, and when ──────────────
    # The TSV is the change record. It is one file per campaign under a
    # git-backed workspace, so its own history is the audit trail — no
    # second ledger to keep in sync with it, and `git log` on the file
    # answers "when did we last touch this target?".
    #
    # Written by the EXECUTION pass (after a human approves decisions in
    # the review console), never by the audit that merely suggests. Blank
    # means untouched. `previous_bid` holds the value replaced, so the
    # pair (previous_bid, bid) is the old->new of the last applied move.
    'applied_action',
    'applied_at',
    'previous_bid',
)
SEARCHTERM_COLUMNS = (
    'ad_group',
    'search_term',
    'source_target',
    'match_type',
    'currency',
    'clicks',
    'spend',
    'orders',
    'sales',
    'roas',
    'suggestion',
    'applied_action',
    'applied_at',
    'previous_bid',
)

# A header that folds the currency into the name. Called out separately
# because it is the most common variant and the least obvious: the file
# looks fine until a reader asks for `spend` in another marketplace.
_CURRENCY_IN_NAME = ('spend_', 'sales_', 'revenue_', 'spend (', 'sales (')


def _read_header(path) -> tuple[list[str], str] | None:
    """``(columns, delimiter)`` of the first line, or None if unreadable."""
    try:
        with open(path, encoding='utf-8', newline='') as fh:
            first = fh.readline()
    except OSError:
        return None
    if not first.strip():
        return None
    line = first.rstrip('\r\n')
    # Sniffing is deliberate rather than assuming tabs: a pipe-delimited
    # file must be REPORTED as such, not silently parsed as one column.
    delim = '\t'
    if '\t' not in line and '|' in line:
        delim = '|'
    elif '\t' not in line and ',' in line:
        delim = ','
    cols = next(csv.reader([line], delimiter=delim), [])
    return [c.strip().lstrip('﻿') for c in cols], delim


def check_tsv_header(path, layer: str) -> str | None:
    """None when the header is right, else a one-line reason.

    ``layer`` is ``'targeting'`` or ``'searchterm'``. An unreadable or
    absent file returns None — its absence is already a gap raised by the
    drill-block cross-check, and reporting it twice would double-count.
    """
    want = TARGETING_COLUMNS if layer == 'targeting' else SEARCHTERM_COLUMNS
    got = _read_header(path)
    if got is None:
        return None
    cols, delim = got
    if delim != '\t':
        return (
            f'用了 {"竖线" if delim == "|" else "逗号"}分隔，不是制表符'
            '——按 TSV 读会变成一列，所有数字都丢了'
        )
    lowered = [c.lower() for c in cols]
    if lowered == [c.lower() for c in want]:
        return None
    missing = [c for c in want if c.lower() not in lowered]
    baked = [c for c in cols if c.lower().startswith(_CURRENCY_IN_NAME)]
    bits = []
    if baked:
        bits.append(
            f'币种写进了列名（{"、".join(baked[:3])}）——currency 要单独一列，'
            '否则换个市场读的人就找不到 spend'
        )
    if missing:
        bits.append(f'缺列：{"、".join(missing[:6])}')
    if not bits:
        bits.append(f'列序不对，应为 {" / ".join(want)}')
    return '；'.join(bits)
