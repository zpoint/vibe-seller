"""Validation and verdict arithmetic for listing flat files.

Split out of `listing_schema` (which describes a template's SHAPE) to
keep both under the repo line cap. Everything here decides whether a
value can land and whether a batch did: the enum gate `fill` runs
before writing, the id-tail rule for `label (id)` valid values, and the
successful-vs-processed check `parse-feedback` runs on a report.
"""

import re

from listing_schema import base_attr

ENUM_HINT = (
    " -- not in the template's valid-value list. That list is NOT a "
    'reliable picture of what Amazon enforces: it rejected an off-list '
    'fulfilment code and a boolean written `False` (90244), and accepted '
    'an off-list `style` and `special_size_type` with no warning at all. '
    'Check the accepted spec for this category first; if Amazon then '
    'rejects the value, the report names the fix.'
)


def enum_violations(fields, valid):
    """EVERY (field, value, allowed) outside a non-empty valid set.

    All of them, not the first: returning one let an override on the
    first field hide the rest entirely -- an off-list `style` was never
    even reported behind a browse-node override.
    """
    out, rest = [], dict(fields)
    while True:
        hit = enum_violation(rest, valid)
        if not hit:
            return out
        out.append(hit)
        rest.pop(hit[0])


def enum_gate(fields, valid, allow, i, sku):
    """-> (warnings, None). Every off-list value, as a warning.

    This was briefly FATAL, on the evidence of two rejected values. It
    blocked a spec Amazon then accepted cleanly: an off-list `style` and
    `special_size_type` went through 4/4 with "applied without any
    errors", and the gate had already forced an extra upload to "fix"
    them. The valid-value list is advice, not the rule set; what Amazon
    actually accepted for this category lives in the spec library
    (listing_library), and `fill` checks against that. So this names
    every off-list value, and never stops the fill. `allow` (from
    `--allow-unlisted-enum FIELD`) silences a field you have checked.
    """
    allowed_attrs = {base_attr(a) for a in (allow or [])}
    warns = []
    for fname, fval, allowed in enum_violations(fields, valid):
        if base_attr(fname) in allowed_attrs:
            continue
        warns.append(
            f'row {i} sku={sku}: {fname}={fval!r} is not one of '
            f'{sorted(allowed)}{ENUM_HINT}'
        )
    return warns, None


def enum_violation(fields, valid):
    """First (field, value, allowed) outside a NON-EMPTY valid set.

    `fill` treats this as fatal rather than a warning. The warning
    version existed so a new category could carry a token the
    valid-value sheet had not caught up with; what it actually bought
    was an agent reading the warning, judging the sheet wrong, uploading
    anyway, and spending a forty-minute feed cycle being told the same
    thing by Amazon (90244) — twice in one run.
    """
    for fname, fval in fields.items():
        allowed = valid.get(fname)
        low = str(fval).strip().lower()
        if not allowed or low in allowed:
            continue
        # A bare id is the WIRE form of a `label (id)` entry — valid.
        if low.isdigit() and any(id_tail(a) == low for a in allowed):
            continue
        return fname, fval, allowed
    return None


# Some valid-value sheets list a DISPLAY label with the id in brackets —
# `recommended_browse_nodes` reads `>  >  >  >  >  (16667806031)` — while
# the feed wants the bare id and rejects the label (`\A[0-9]*\z`, max 15
# chars). Observed live: the label form failed a whole feed, and the
# fatal enum check then rejected the correct bare id. Both directions are
# settled here: the bare id validates, and the label is written as the id.
_ID_TAIL_RE = re.compile(r'\((\d{4,})\)\s*$')


def id_tail(value):
    """`label (123456)` -> '123456'; None when there is no such tail."""
    m = _ID_TAIL_RE.search(str(value or ''))
    return m.group(1) if m else None


def wire_value(fval, fcase, aliases=None):
    """The value to WRITE: the template's exact-case token, else the input.

    A `label (id)` token collapses to the bare id, because that is what
    the feed accepts. `aliases` maps a common input (an ISO country code)
    to the token the valid set actually holds.
    """
    key = str(fval).strip().lower()
    canon = fcase.get(key)
    if not canon and aliases and key in aliases:
        canon = fcase.get(aliases[key])
    out = canon if canon else fval
    return id_tail(out) or out


_COUNT_LABELS = (
    ('processed', ('skus processed', 'records processed', '已处理')),
    ('successful', ('skus successful', 'records successful', '成功')),
)


def summary_counts(rows):
    """``{processed, successful}`` from a Feed Processing Summary.

    The per-row verdict is not the whole story: Amazon can report a SKU
    as "successful with other errors" (severity WARNING in the cell
    comments) while the status page counts it as NOT successful. A
    batch that landed 2 of 4 therefore reads as "0 errors" if you only
    count ERROR lines — which is how one was signed off as complete.
    The summary's own counts are the arithmetic that cannot be argued
    with, so they are extracted and compared.
    """
    out = {}
    for row in rows:
        text = ' '.join(c for c in row if c).strip().lower()
        if not text:
            continue
        nums = [c.strip() for c in row if c.strip().isdigit()]
        if not nums:
            continue
        for key, needles in _COUNT_LABELS:
            if key in out or not any(n in text for n in needles):
                continue
            if 'unsuccessful' in text or 'with other error' in text:
                continue
            out[key] = int(nums[-1])
    return out


def apply_shortfall(rows, n_err):
    """Raise the error count when fewer SKUs landed than were sent.

    Amazon calls a SKU "successful with other errors" (WARNING severity)
    while the status page counts it as NOT successful, so an
    all-WARNING report can still mean half the batch never landed --
    observed live on a 2/4 that was signed off as clean because no line
    said ERROR. Arithmetic beats severity labels.
    """
    counts = summary_counts(rows)
    done, total = counts.get('successful'), counts.get('processed')
    if done is None or not total or done >= total:
        return n_err
    short = total - done
    print(
        f'\nSHORTFALL: {done}/{total} SKUs successful -- {short} did NOT '
        'land, whatever the severity labels say. Read the per-SKU '
        'comments above for those rows, fix, and re-upload; this batch '
        'is not done.'
    )
    return max(n_err, short)
