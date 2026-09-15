#!/usr/bin/env python3
"""Pre-flight a filled NIS sheet before uploading it to noon.

Every check here is a failure mode that actually shipped: a NIS import
reported ``Complete`` with ``Total Processed Rows: 4`` and created
nothing, and the agent believed the counter. The whole point of this
script is that noon's own feedback is unreliable in two ways, so the
file has to be judged BEFORE it is uploaded:

1. **A leftover template sample row hides every other row's error.**
   The downloaded template ships a sample row carrying only
   ``family`` / ``product_type`` / ``product_subtype``. If it is still
   in the file, noon's Error File reports ONLY that row's
   ``seller_sku is missing`` and silently drops the ``content_error``
   of every real row, while the counters read ``1 error / N processed``
   — which looks like success. Verified: re-downloading that import's
   Error File 6.5 h later still showed only the sample row.
2. **``Total Processed Rows`` is not "created".** It counts rows that
   passed partner-level validation. The only trustworthy success
   signal is the import's **Report File**: non-empty, one row per SKU,
   with a ``catalog_sku`` filled in. A failed import's Report File is
   4 bytes (UTF-8 BOM + newline).

So: run this, fix what it prints, THEN upload — and verify the Report
File afterwards regardless.

Usage::

    python validate_nis.py FILLED.xlsx [--template BLANK.xlsx]

Exits non-zero if any ERROR is found. WARNINGs are advisory.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

try:
    from openpyxl import load_workbook
except ImportError:  # pragma: no cover - environment issue, not logic
    sys.exit('openpyxl is required: pip install openpyxl')

# Row 9 of ``template_data`` holds the machine field names; rows 1-8 are
# the template's own meta/legend/sample block.
HEADER_ROW = 9
FIRST_DATA_ROW = HEADER_ROW + 1


# The ``valid values`` sheet is UI-facing: a control import that CREATED
# its SKUs disagreed with it on casing, spacing and punctuation for the
# category columns (and used a short unit form where the sheet spells it
# out). So compare NORMALISED -- case/punctuation/whitespace-insensitive
# -- rather than exempting those columns wholesale, which would let a
# genuine typo through unchecked.
def _norm(value) -> str:
    """Casefolded, punctuation-stripped form for tolerant comparison."""
    return re.sub(r'[^a-z0-9]', '', str(value).strip().lower())


# Short unit forms noon accepts where the sheet spells the unit out.
# ``cm`` is proven (the control import that created its SKUs used it);
# the rest follow the same pattern. Spelled out here rather than skipped
# so a near-miss like ``cmm`` still fails.
_VALUE_ALIASES = {
    'cm': 'Centimeter',
    'mm': 'Millimeter',
    'm': 'Meter',
    'in': 'Inch',
}


def _accepts(value, allowed) -> bool:
    """True when ``value`` matches ``allowed`` modulo display form."""
    canonical = {_norm(a) for a in allowed}
    if _norm(value) in canonical:
        return True
    alias = _VALUE_ALIASES.get(str(value).strip().lower())
    return alias is not None and _norm(alias) in canonical


# Warn-only rather than error: a control import that CREATED used the
# spelled-out ``Gram`` while the failing one used ``g``, but noon never
# named this column in any error file — so the evidence that it blocks
# creation is circumstantial. Flag it, don't fail the file on it.
_WARN_ONLY_COLUMNS = frozenset({'shipping_weight_unit'})

# noon rejects an out-of-range ``hs_code`` with
# ``Char Length must be between 6 and 14 - both inclusive``.
_HS_CODE_MIN = 6
_HS_CODE_MAX = 14

# An Arabic rendering this much shorter than its English source is a
# stub, not a translation. Observed: a 150-char English title paired
# with a 40-char Arabic one, and a 382-char description paired with 131.
_STUB_TRANSLATION_RATIO = 0.45

# Per-marketplace fields. A value here for a marketplace the seller does
# not operate in is invented data — and on noon an invented value is
# effectively permanent: a blank cell in a NIS *update* means "no
# change", not "clear", and the Content tab did not persist a clear
# either. So the only reliable time to not have a wrong value is before
# the first upload.
_PER_MARKET_PREFIXES = ('msrp_', 'vat_rate_')


def _sheet(wb, name):
    """Return worksheet ``name``, or None when the workbook lacks it."""
    return wb[name] if name in wb.sheetnames else None


def _headers(ws) -> list[str]:
    """Machine field names from the template's header row."""
    return [
        str(ws.cell(row=HEADER_ROW, column=c).value or '').strip()
        for c in range(1, ws.max_column + 1)
    ]


def _valid_values(wb) -> dict[str, set[str]]:
    """Map column name → its allowed values from the template sheet."""
    ws = _sheet(wb, 'valid values')
    if ws is None:
        return {}
    out: dict[str, set[str]] = {}
    for c in range(1, ws.max_column + 1):
        name = ws.cell(row=1, column=c).value
        if not name:
            continue
        vals = {
            str(ws.cell(row=r, column=c).value).strip()
            for r in range(2, ws.max_row + 1)
            if ws.cell(row=r, column=c).value not in (None, '')
        }
        if vals:
            out[str(name).strip()] = vals
    return out


def _data_rows(ws, hdr) -> list[int]:
    """Row numbers that carry any value at all below the header."""
    return [
        r
        for r in range(FIRST_DATA_ROW, ws.max_row + 1)
        if any(
            ws.cell(row=r, column=c).value not in (None, '')
            for c in range(1, len(hdr) + 1)
        )
    ]


def _get(ws, hdr, row, field):
    """Cell value for ``field`` on ``row``, or None when absent."""
    if field not in hdr:
        return None
    v = ws.cell(row=row, column=hdr.index(field) + 1).value
    return None if v in (None, '') else v


def check_row_identity(ws, hdr, rows) -> list[str]:
    """Every non-empty data row must carry a ``seller_sku``.

    Two shapes of the same defect. A row holding ONLY the category
    columns is the template's own sample row: leaving it in makes noon's
    Error File report just that row and mask every real row's
    ``content_error``. Any OTHER row without a SKU is malformed — and
    because every per-row check below keys off ``seller_sku``, such a
    row would otherwise skip validation entirely and the file would
    still be reported OK.
    """
    errs = []
    category = ('family', 'product_type', 'product_subtype')
    for r in rows:
        if _get(ws, hdr, r, 'seller_sku'):
            continue
        others = [
            h
            for h in hdr
            if h not in category and _get(ws, hdr, r, h) is not None
        ]
        if not others and any(_get(ws, hdr, r, f) for f in category):
            errs.append(
                f'ERROR row {r}: template sample row still present '
                '(category set, seller_sku empty). Delete it — while it '
                "is in the file noon's Error File reports only this row "
                "and hides every real row's content_error."
            )
        else:
            errs.append(
                f'ERROR row {r}: no seller_sku. Every NIS data row needs '
                'one; a row without it is skipped by every other check.'
            )
    return errs


def check_select_values(ws, hdr, rows, valid) -> list[str]:
    """Every select attribute must use a value from ``valid values``."""
    errs = []
    for r in rows:
        if not _get(ws, hdr, r, 'seller_sku'):
            continue
        for field, allowed in valid.items():
            if field not in hdr:
                continue
            if field in _WARN_ONLY_COLUMNS:
                continue
            v = _get(ws, hdr, r, field)
            if v is None:
                continue
            if not _accepts(v, allowed):
                sample = ', '.join(sorted(allowed)[:6])
                errs.append(
                    f'ERROR row {r}: {field}={str(v)!r} is not a valid '
                    f'value. Allowed include: {sample}, …'
                )
    return errs


def check_unit_columns(ws, hdr, rows, valid) -> list[str]:
    """Unit columns: warn only. noon accepted ``cm`` on a control import
    but the sheet lists ``Centimeter``, so the sheet is advisory here —
    except that a control import that CREATED used ``Gram``, so a bare
    ``g`` is worth flagging rather than silently trusting."""
    warns = []
    for r in rows:
        if not _get(ws, hdr, r, 'seller_sku'):
            continue
        for field in sorted(_WARN_ONLY_COLUMNS):
            allowed = valid.get(field)
            v = _get(ws, hdr, r, field)
            if not allowed or v is None:
                continue
            if not _accepts(v, allowed):
                errs = ', '.join(sorted(allowed))
                warns.append(
                    f'WARNING row {r}: {field}={str(v)!r} is not in the '
                    f'template list ({errs}). A control import that '
                    'created SKUs used the spelled-out form.'
                )
    return warns


def check_market_scope(ws, hdr, rows, markets) -> tuple[list[str], list[str]]:
    """Per-marketplace fields must stay blank outside the task's scope.

    ``markets`` is the set of country codes the task actually covers
    (``--markets AE``). Anything the user did not ask for is left blank:
    a skill that fills a field "while it is there" is inventing data,
    and one answer ("100 AED") fanned across ``msrp_ae`` / ``msrp_sa``
    / ``msrp_eg`` prices two marketplaces nobody priced — one of which
    the seller does not sell in.
    """
    errors: list[str] = []
    warnings: list[str] = []
    fields = [
        h
        for h in hdr
        if h.startswith(_PER_MARKET_PREFIXES) and len(h.rsplit('_', 1)) == 2
    ]
    for r in rows:
        if not _get(ws, hdr, r, 'seller_sku'):
            continue
        seen: dict[str, list[str]] = {}
        for field in fields:
            v = _get(ws, hdr, r, field)
            if v is None:
                continue
            cc = field.rsplit('_', 1)[1].upper()
            if markets and cc not in markets:
                errors.append(
                    f'ERROR row {r}: {field}={str(v)!r} but the task '
                    f'covers {sorted(markets)}. Leave per-marketplace '
                    'fields blank outside the requested scope — noon '
                    'cannot clear them afterwards.'
                )
            if field.startswith('msrp_'):
                seen.setdefault(str(v).strip(), []).append(cc)
        for value, ccs in seen.items():
            if len(ccs) > 1:
                warnings.append(
                    f'WARNING row {r}: msrp {value} is repeated across '
                    f'{sorted(ccs)}. A price given for one marketplace '
                    'is not a price for the others (different currency).'
                )
    return errors, warnings


def check_hs_code(ws, hdr, rows) -> list[str]:
    """``hs_code`` must be 6-14 characters when present."""
    errs = []
    for r in rows:
        v = _get(ws, hdr, r, 'hs_code')
        if v is None:
            continue
        n = len(str(v).strip())
        if not _HS_CODE_MIN <= n <= _HS_CODE_MAX:
            errs.append(
                f'ERROR row {r}: hs_code={str(v)!r} is {n} chars; noon '
                f'requires {_HS_CODE_MIN}-{_HS_CODE_MAX}. Leave it blank '
                'rather than guessing a customs code.'
            )
    return errs


def check_locale_parity(ws, hdr, rows) -> tuple[list[str], list[str]]:
    """Every filled ``*_en`` needs its ``*_ar`` counterpart.

    The bilingual template pairs each localised field. Filling only the
    English half produces a listing that is live but half-translated —
    shipped once with all four Arabic feature bullets empty, which no
    gate caught because the import reported no errors.
    """
    errs: list[str] = []
    warns: list[str] = []
    pairs = [h[:-3] for h in hdr if h.endswith('_en') and h[:-3] + '_ar' in hdr]
    for r in rows:
        if not _get(ws, hdr, r, 'seller_sku'):
            continue
        for base in pairs:
            en = _get(ws, hdr, r, base + '_en')
            ar = _get(ws, hdr, r, base + '_ar')
            if en is None:
                continue
            if ar is None:
                errs.append(
                    f'ERROR row {r}: {base}_en is filled but {base}_ar '
                    'is empty. Fill both halves of the bilingual pair.'
                )
                continue
            en_len, ar_len = len(str(en)), len(str(ar))
            if en_len and ar_len / en_len < _STUB_TRANSLATION_RATIO:
                warns.append(
                    f'WARNING row {r}: {base}_ar is {ar_len} chars vs '
                    f'{en_len} for {base}_en — likely a stub, not a full '
                    'translation.'
                )
    return errs, warns


def validate(path: Path, template: Path | None = None, markets=None):
    """Return ``(errors, warnings)`` for a filled NIS workbook."""
    wb = load_workbook(path)
    ws = _sheet(wb, 'template_data') or wb.active
    hdr = _headers(ws)
    if 'seller_sku' not in hdr:
        return (
            [
                f'ERROR: {path.name} has no seller_sku column on row '
                f'{HEADER_ROW} — is this a NIS template?'
            ],
            [],
        )
    # A filled sheet keeps the template's own ``valid values``; fall back
    # to a separately supplied blank template when it was stripped.
    valid = _valid_values(wb)
    if not valid and template is not None:
        valid = _valid_values(load_workbook(template))

    rows = _data_rows(ws, hdr)
    if not rows:
        # Fail closed: an empty sheet creates nothing, which is the very
        # outcome this pre-flight exists to catch. Reporting OK here
        # would green-light it.
        return (
            [
                f'ERROR: {path.name} has no data rows below row '
                f'{HEADER_ROW} — nothing would be created.'
            ],
            [],
        )
    errors = check_row_identity(ws, hdr, rows)
    errors += check_select_values(ws, hdr, rows, valid)
    errors += check_hs_code(ws, hdr, rows)
    parity_errs, parity_warns = check_locale_parity(ws, hdr, rows)
    errors += parity_errs
    market_errs, market_warns = check_market_scope(ws, hdr, rows, markets)
    errors += market_errs
    warnings = (
        check_unit_columns(ws, hdr, rows, valid) + parity_warns + market_warns
    )
    return errors, warnings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('xlsx', type=Path, help='the filled NIS sheet')
    ap.add_argument(
        '--template',
        type=Path,
        default=None,
        help='blank template, if the filled sheet lost "valid values"',
    )
    ap.add_argument(
        '--markets',
        default='',
        help=(
            'comma-separated marketplaces the task covers, e.g. "AE". '
            'Per-marketplace fields outside this set are errors.'
        ),
    )
    args = ap.parse_args(argv)

    markets = {m.strip().upper() for m in args.markets.split(',') if m.strip()}
    errors, warnings = validate(args.xlsx, args.template, markets)
    for w in warnings:
        print(w)
    for e in errors:
        print(e)
    if errors:
        print(f'\n{len(errors)} error(s) — do NOT upload yet.')
        return 1
    print(
        f'\nOK: no blocking problems ({len(warnings)} warning(s)). '
        'After upload, confirm the import Report File is non-empty and '
        'every row has a catalog_sku — the counters are not proof.'
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
