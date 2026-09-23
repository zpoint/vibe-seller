"""Validation and verdict arithmetic for listing flat files.

Split out of `listing_schema` (which describes a template's SHAPE) to
keep both under the repo line cap. Everything here decides whether a
value can land and whether a batch did: the enum gate `fill` runs
before writing, the id-tail rule for `label (id)` valid values, and the
successful-vs-processed check `parse-feedback` runs on a report.
"""

import json
import os
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


def unlanded_skus(comment_errs):
    """SKUs whose own `::submission_status` says they were NOT applied.

    Amazon writes one status per SKU: "applied without any errors",
    "applied, but contain other error(s)", or not applied. Only the last
    means the row did not land.
    """
    status = {}
    for sku, field, _sev, msg in comment_errs or []:
        if not str(field).endswith('submission_status'):
            continue
        m = str(msg).lower()
        applied = 'applied' in m and 'not applied' not in m
        status[sku] = status.get(sku, False) or applied
    return sorted(s for s, ok in status.items() if not ok)


def apply_shortfall(rows, n_err, comment_errs=None):
    """Count rows that did not land -- from each SKU's own status.

    First version of this compared the summary's "SKUs successful" with
    "SKUs processed" and called the difference "did not land". That was
    wrong: "successful" counts only CLEAN rows, and a row that was
    "applied, but contain other error(s)" -- a missing main image, a
    warning -- is live. Watched it print "3 did NOT land ... not done" on
    an AE batch whose three children were all applied and resolvable on
    the storefront, and send a reviewer chasing a failure that was not
    there. The per-SKU `::submission_status` is the authoritative line;
    the count is only advisory when no status is present.
    """
    gone = unlanded_skus(comment_errs)
    if gone:
        print(
            f'\nNOT APPLIED: {gone} -- Amazon did not apply these rows. '
            'Read their comments above, fix, and re-upload; this batch is '
            'not done.'
        )
        return max(n_err, len(gone))
    counts = summary_counts(rows)
    done, total = counts.get('successful'), counts.get('processed')
    if done is not None and total and done < total and not comment_errs:
        print(
            f'\nnote: {done}/{total} SKUs counted "successful". Amazon '
            'counts only clean rows here -- a row applied with a warning or '
            'a missing image is live but not "successful". Confirm per SKU '
            'on Manage Inventory before re-uploading anything.'
        )
    return n_err


def report_outcome(comment_errs, n_err):
    """Print the verdict in words; True when the caller must exit non-zero.

    A report whose only error is the missing main image is DONE -- that
    is the one accepted deferral. It used to print "NOT DONE. Fix ALL
    errors and re-upload" for it anyway, which is an instruction to
    upload a batch that cannot change anything.
    """
    blocking = [
        m
        for _s, _f, sev, m in comment_errs
        if sev == 'error' and '18320' not in m and 'main image' not in m.lower()
    ] + unlanded_skus(comment_errs)  # a row not applied is never 'done'
    if n_err and not blocking:
        print(
            'DONE: the only error is the missing main image -- the accepted '
            'deferral. Do not re-upload for it.'
        )
        return False
    if n_err:
        print(
            'NOT DONE. Fix ALL errors (parent first) and re-upload. A SKU with '
            'any error is not created, or created but flagged "Action '
            'required" -- in inventory, yet UNRESOLVED. Only 18320 (missing '
            'main image) is a legit deferral.'
        )
        return True
    return False


_BATCH_TAG_RE = re.compile(r'__batch(\d+)(?=\.[A-Za-z]+$)')


def report_batch_problem(report_path, batch_id, marker_dir='.'):
    """Why `report_path` cannot be batch `batch_id`'s report, else None.

    A processing report names no batch, so nothing inside it can be
    checked. What can be checked: the batch tag `bh_fetch_report` puts in
    the filename, and the time -- a report downloaded BEFORE the batch
    was uploaded is some earlier batch's. Either one is proof, and a
    verdict written from the wrong report is worse than none: it once
    recorded six errors against a batch that went through 4/4 clean.
    Returns a warning string (prefixed `warning:`) when it merely cannot
    confirm, and an `error:` string when it can prove the mismatch.
    """
    if not batch_id:
        return None
    name = os.path.basename(str(report_path))
    tag = _BATCH_TAG_RE.search(name)
    if tag and tag.group(1) != str(batch_id):
        return (
            f"error: {name} is tagged as batch {tag.group(1)}'s report, not "
            f"{batch_id}'s. Fetch batch {batch_id}'s own report "
            f'(bh_fetch_report BATCH_ID={batch_id}) and parse that.'
        )
    marker = os.path.join(marker_dir, f'UPLOAD_BATCH_{batch_id}.json')
    try:
        with open(marker, encoding='utf-8') as fh:
            uploaded = float(json.load(fh).get('uploaded_at') or 0)
        fetched = os.path.getmtime(report_path)
    except (OSError, ValueError, TypeError):
        uploaded = fetched = 0
    if uploaded and fetched and fetched < uploaded:
        return (
            f'error: {name} was downloaded before batch {batch_id} was '
            "uploaded, so it is an EARLIER batch's report. Amazon names "
            'reports after the upload file, and each download overwrites the '
            f"last. Fetch batch {batch_id}'s own report and parse that."
        )
    if not tag:
        return (
            f"warning: cannot confirm {name} is batch {batch_id}'s report "
            '(reports carry no batch id, and same-named reports overwrite '
            'each other). bh_fetch_report saves a `__batch<id>` copy -- '
            'parse that one.'
        )
    return None
