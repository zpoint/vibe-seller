"""The last spec Amazon ACCEPTED, per store x product type x marketplace.

Why this exists: every first create of a known product failed, two days
running, on fields an earlier run had already worked out. Task
workspaces are isolated, so the spec that finally passed died with its
task, and the next task re-learned the category's required set one
Amazon rejection at a time -- 30-60 minutes per round. The template
could not warn about it either: the fields Amazon enforces are flagged
"Conditionally Required" (53 of them in one apparel template), which
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
import sys

from listing_schema import base_attr
from marketplace_ids import COUNTRY_BY_ID

LIB_DIRNAME = 'listing-specs'
_ID_ATTRS = {
    'contribution_sku',
    'item_sku',
    'amzn1.volt.ca.product_id_value',
    'external_product_id',
    'parent_sku',
    'child_parent_sku_relationship',
}


def sidecar_path(out_path):
    """`…/create-sa.xlsm` -> `…/create-sa.spec.json` (next to the .txt)."""
    p = Path(out_path)
    return p.with_name(p.stem + '.spec.json')


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


def save_on_clean(batch_id):
    """After a CLEAN verdict, file the spec behind that batch. -> path|None.

    Clean means what the completion gate means: zero non-image errors.
    Reads the batch's upload marker (written by bh_upload_flatfile) for
    the file, and that file's sidecar for the spec.
    """
    if not batch_id:
        return None
    try:
        verdict = json.loads(
            Path(f'BATCH_{batch_id}_VERDICT.json').read_text('utf-8')
        )
        if int(verdict.get('non_image_errors', 1)):
            return None
        marker = json.loads(
            Path(f'UPLOAD_BATCH_{batch_id}.json').read_text('utf-8')
        )
        upload = marker['file']
        side = json.loads(sidecar_path(upload).read_text('utf-8'))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    lib = library_dir(upload)
    if lib is None:
        return None
    dest = _entry(lib, side['spec'], side.get('marketplace_id'))
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
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


def report_saved(batch_id):
    """`parse-feedback`'s hook: file the spec behind a clean batch."""
    dest = save_on_clean(batch_id)
    if dest:
        print(
            f'accepted spec saved -> {dest} (start the next listing of this '
            'category from it)'
        )
