"""The last spec Amazon ACCEPTED, per store x product type x marketplace.

Why this exists: every first create of a known product failed, two days
running, on fields an earlier run had already worked out. Task
workspaces are isolated, so the spec that finally passed died with its
task, and the next task re-learned the category's required set one
Amazon rejection at a time -- 30-60 minutes per round. The template
could not warn about it either: the fields Amazon enforces are flagged
"Conditionally Required" (53 of them in one template), which
`fill` rightly does not treat as hard requirements.

So the lesson is stored as an artifact, at the one moment it is proven:
`parse-feedback --batch-id` saves the spec behind a batch whose verdict
is clean into the store's library, and `fill` compares a new spec
against it and names every field the accepted spec set that the new one
leaves empty. Pure file I/O -- no browser, no openpyxl.
"""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import sys

from listing_schema import base_attr
from marketplace_ids import COUNTRY_BY_ID

LIB_DIRNAME = 'listing-specs'
# Every SKU's product type as Amazon holds it NOW, beside the specs.
SKU_TYPES_NAME = 'sku-product-types.json'
# Amazon's own words when it files a SKU under another product type than
# the one submitted (a warning: the row is still applied).
_RECLASSIFIED = re.compile(
    r'product type has been updated from\s+([A-Z0-9_]+)\s+to\s+([A-Z0-9_]+)',
    re.IGNORECASE,
)
_ID_ATTRS = {
    'contribution_sku',
    'item_sku',
    'amzn1.volt.ca.product_id_value',
    'external_product_id',
    'parent_sku',
    'child_parent_sku_relationship',
}


def task_workspace():
    """The task workspace, derived exactly as the app derives it.

    VIBE_HOME (else ~/.vibe-seller) / tasks / VIBE_TASK_ID. None outside a
    task. Gate artifacts are resolved against THIS, never against the
    caller's $PWD: an agent that worked from a scratch dir once scattered
    a whole run's markers and review into /tmp, where no gate looks.
    """
    tid = os.environ.get('VIBE_TASK_ID')
    if not tid:
        return None
    root = os.environ.get('VIBE_HOME') or os.path.join(
        os.path.expanduser('~'), '.vibe-seller'
    )
    ws = Path(root) / 'tasks' / tid
    return ws if ws.is_dir() else None


def gate_dirs():
    """Where gate artifacts are written and read: workspace first, then cwd."""
    dirs = [d for d in (task_workspace(),) if d]
    cwd = Path.cwd()
    if not any(cwd.resolve() == d.resolve() for d in dirs):
        dirs.append(cwd)
    return dirs


def find_gate_file(name):
    """First existing `name` across gate_dirs(), else None."""
    for d in gate_dirs():
        if (d / name).is_file():
            return d / name
    return None


def sidecar_path(out_path):
    """`…/create-sa.xlsm` -> `…/create-sa.spec.json` (next to the .txt)."""
    p = Path(out_path)
    return p.with_name(p.stem + '.spec.json')


def load_spec(path):
    """A spec, also when handed the library's or a sidecar's record of one.

    `parse-feedback` files an accepted spec as `{accepted_batch_id, ...,
    spec: {...}}` and says to start the next listing from it; fill read
    that record as a spec with no rows. Observed live: an agent copied
    the library file, got "spec has no rows", and unwrapped it by hand.
    """
    with open(path, encoding='utf-8') as fh:
        spec = json.load(fh)
    if 'rows' not in spec and isinstance(spec.get('spec'), dict):
        spec = spec['spec']
    return spec


def spec_path_guard(spec_path, out_path):
    """Refuse an --out that is not a workbook, or a --spec it would clobber.

    Observed live: a spec saved as `x.spec.json` and filled to `--out
    x.xlsm` was overwritten by its own `{marketplace_id, spec}` record,
    so the next fill from it found no rows.
    """
    if not str(out_path).lower().endswith(('.xlsm', '.xlsx')):
        # Observed live: `--out x.txt` copied the template there and then
        # died in openpyxl with a raw traceback, leaving a junk file.
        raise SystemExit(
            f'error: --out {out_path} must be the workbook (.xlsm). fill '
            'writes the tab-delimited upload file next to it as <name>.txt '
            '-- that is the file you upload, not the one you name here.'
        )
    side = sidecar_path(out_path)
    if Path(spec_path).resolve() == side.resolve():
        raise SystemExit(
            f'error: --spec {spec_path} is the record fill writes next to '
            f'--out ({side.name}), so this fill would overwrite your spec. '
            'Rename the spec (e.g. drop ".spec") or choose another --out.'
        )


def write_sidecar(out_path, spec, mkt_id):
    """Record the exact spec `fill` used, so a clean verdict can save it."""
    try:
        sidecar_path(out_path).write_text(
            json.dumps({'marketplace_id': mkt_id, 'spec': spec}, indent=1),
            encoding='utf-8',
        )
    except OSError:
        pass


def _product_type(spec):
    pt = spec.get('product_type')
    if not pt:
        pt = next(
            (r.get('product_type') for r in spec.get('rows') or [] if r),
            None,
        )
    return str(pt or 'unknown').strip().lower()


def library_dir(upload_path):
    """`<store-data>/<slug>/listing-specs`, or None when there is none.

    The slug is the store downloads dir the file lives in
    (`…/downloads/<slug>/file.txt`). `LISTING_SPEC_LIBRARY` overrides.
    """
    env = os.environ.get('LISTING_SPEC_LIBRARY')
    if env:
        return Path(env)
    here = Path(upload_path).resolve().parent
    slug = here.name
    for root in (here.parent.parent, Path.cwd()):
        store_data = root / 'store-data'
        if store_data.is_dir():
            return store_data / slug / LIB_DIRNAME
    return None


def _entry(lib, spec, mkt_id):
    cc = COUNTRY_BY_ID.get(mkt_id, mkt_id or 'XX')
    return lib / f'{_product_type(spec)}__{cc}.json'


def reclassified_type(messages):
    """The product type Amazon moved a batch's SKUs to, or None.

    Amazon may re-file a created SKU under a type of its own choosing and
    says so only as a warning on the report. Every later partial update
    of that SKU must name the NEW type -- the create spec's type is
    stale -- and the only place that fact was ever written was a report
    nobody reads again: an edit task built its file from the create spec
    and had to be corrected by hand.
    """
    for msg in messages or ():
        m = _RECLASSIFIED.search(msg or '')
        if m:
            return m.group(2).upper()
    return None


def _retyped(spec, new_type):
    """`spec` with every product type replaced by `new_type`."""
    spec = json.loads(json.dumps(spec))
    spec['product_type'] = new_type.lower()
    for row in spec.get('rows') or []:
        if isinstance(row, dict) and row.get('product_type'):
            row['product_type'] = new_type.lower()
    return spec


def _record_sku_types(lib, spec):
    """Merge every SKU of `spec` into the store's SKU -> product type map."""
    path = lib / SKU_TYPES_NAME
    try:
        known = json.loads(path.read_text('utf-8'))
    except (OSError, ValueError):
        known = {}
    pt = _product_type(spec).upper()
    for row in spec.get('rows') or []:
        sku = isinstance(row, dict) and row.get('sku')
        if sku:
            known[sku] = str(row.get('product_type') or pt).upper()
    path.write_text(
        json.dumps(known, indent=1, sort_keys=True, ensure_ascii=False),
        encoding='utf-8',
    )


def save_on_clean(batch_id, new_type=None):
    """After a CLEAN verdict, file the spec behind that batch. -> path|None.

    Clean means what the completion gate means: zero non-image errors.
    Reads the batch's upload marker (written by bh_upload_flatfile) for
    the file, and that file's sidecar for the spec. When Amazon re-filed
    the SKUs under `new_type`, the spec is filed under that type, as the
    type the listings now have.
    """
    if not batch_id:
        return None
    try:
        verdict = json.loads(
            find_gate_file(f'BATCH_{batch_id}_VERDICT.json').read_text('utf-8')
        )
        if int(verdict.get('non_image_errors', 1)):
            return None
        marker = json.loads(
            find_gate_file(f'UPLOAD_BATCH_{batch_id}.json').read_text('utf-8')
        )
        upload = marker['file']
        side = json.loads(sidecar_path(upload).read_text('utf-8'))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    lib = library_dir(upload)
    if lib is None:
        return None
    if new_type:
        side['spec'] = _retyped(side['spec'], new_type)
    dest = _entry(lib, side['spec'], side.get('marketplace_id'))
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _record_sku_types(dest.parent, side['spec'])
        dest.write_text(
            json.dumps(
                {
                    'accepted_batch_id': batch_id,
                    'saved_at': datetime.now(UTC).isoformat(),
                    'source_file': upload,
                    'marketplace_id': side.get('marketplace_id'),
                    'spec': side['spec'],
                },
                indent=1,
                ensure_ascii=False,
            ),
            encoding='utf-8',
        )
    except OSError:
        return None
    return dest


def _field_sets(spec):
    """{'parent': attrs, 'child': attrs} a spec actually sets."""
    out = {'parent': set(), 'child': set()}
    for row in spec.get('rows') or []:
        kind = (
            'parent'
            if str(row.get('parentage', '')).lower() == 'parent'
            else 'child'
        )
        attrs = {
            base_attr(k)
            for k, v in (row.get('fields') or {}).items()
            if v not in (None, '', [])
        }
        out[kind] |= attrs - _ID_ATTRS
    return out


def missing_vs_library(spec, mkt_id, out_path):
    """Fields the last ACCEPTED spec set that this one leaves empty.

    -> (library_path, {'parent': [...], 'child': [...]}) or None. These
    are the precise candidates for the next "is required but missing":
    Amazon already demanded them once for this store and category.
    """
    lib = library_dir(out_path)
    if lib is None:
        return None
    entry = _entry(lib, spec, mkt_id)
    if not entry.is_file():
        # The attribute set Amazon enforces is the CATEGORY's, not the
        # storefront's, so another marketplace's accepted spec for the
        # same product type is the next-best reference.
        others = sorted(lib.glob(f'{_product_type(spec)}__*.json'))
        if not others:
            return None
        entry = others[0]
    try:
        accepted = json.loads(entry.read_text('utf-8'))['spec']
    except (OSError, ValueError, KeyError):
        return None
    had, has = _field_sets(accepted), _field_sets(spec)
    gaps = {
        kind: sorted(had[kind] - has[kind])
        for kind in ('parent', 'child')
        if has[kind] or kind == 'child'
    }
    if not any(gaps.values()):
        return None
    return entry, gaps


def after_fill(out_path, spec, mkt_id):
    """`fill`'s hook: record the spec, and name what the library knew."""
    write_sidecar(out_path, spec, mkt_id)
    hit = missing_vs_library(spec, mkt_id, out_path)
    if not hit:
        return
    entry, gaps = hit
    for kind, attrs in gaps.items():
        if attrs:
            print(
                f'warning: the last spec Amazon ACCEPTED for this category '
                f'({entry.name}) set these on {kind} rows, and this spec '
                f'leaves them empty: {attrs}. Amazon has already demanded '
                'them once -- fill them from that spec before uploading, '
                'or expect "is required but missing".',
                file=sys.stderr,
            )


def report_saved(batch_id, messages=()):
    """`parse-feedback`'s hook: file the spec behind a clean batch."""
    new_type = reclassified_type(messages)
    if new_type:
        print(
            f'Amazon re-filed these SKUs as product type {new_type}: every '
            f'later partial update of them must use the {new_type} template '
            f'(recorded in listing-specs/{SKU_TYPES_NAME})'
        )
    dest = save_on_clean(batch_id, new_type)
    if dest:
        print(
            f'accepted spec saved -> {dest} (start the next listing of this '
            'category from it)'
        )


def write_verdict(batch_id, n_err, n_warn, error_msgs):
    """Write ``BATCH_<id>_VERDICT.json`` to the task workspace (and CWD).

    The machine-checkable verdict the completion gate matches against the
    ``UPLOAD_BATCH_<id>.json`` marker bh_upload_flatfile wrote: the task
    cannot finish while a batch has non-image errors. When the caller
    could not extract per-error text, every error counts as non-image
    (conservative -- never lets an unknown error pass as deferrable).
    """
    if not batch_id:
        return
    non_image = [
        m
        for m in error_msgs
        if '18320' not in m and 'main image' not in m.lower()
    ]
    strict = error_msgs or n_err == 0
    for d in gate_dirs():  # the workspace the gate reads, then cwd
        with open(
            d / f'BATCH_{batch_id}_VERDICT.json', 'w', encoding='utf-8'
        ) as fh:
            json.dump(
                {
                    'batch_id': batch_id,
                    'errors': n_err,
                    'warnings': n_warn,
                    'non_image_errors': len(non_image) if strict else n_err,
                },
                fh,
            )
