"""Batch-keyed freshness gate for listing flat-file uploads.

``bh_download_template`` writes an ``UPLOAD_PENDING.json`` marker when a
template lands (an upload is now intended this turn), and
``bh_upload_flatfile`` records every submitted batch as an
``UPLOAD_BATCH_<id>.json`` marker. ``listing_bulk.py parse-feedback
--batch-id <id>`` writes the matching ``BATCH_<id>_VERDICT.json`` after
reading THAT batch's processing report. This gate closes the loop: a
task that entered the upload flow cannot finish until the uploaded
batch has a verdict, and the verdict shows no non-image errors (the
missing-main-image deferral is the one accepted "done with caveat"
state).

Arming is deliberately redundant: the batch marker requires the upload
HELPER to have succeeded, and the observed bypass was exactly a helper
failure — the submit click missed, the agent uploaded by hand, no
marker existed, and the gate stayed a quiet no-op. The pending marker
(written at template download, several steps earlier) survives that
fallback, so a manual upload is still gated: at least one batch verdict
must exist, and the latest one must be clean. If no upload happened
after all, the agent removes the pending marker (explicitly, visibly)
instead of the gate silently not existing.

This makes report freshness structural instead of prompted: a reviewer
can no longer pass the turn by reading a stale report from a prior
turn or another marketplace — the verdict is keyed to the batch id the
upload actually produced this turn. Markers/verdicts are moved aside
at turn start together with the review files (``rollover_reviews``),
so each turn is gated only on its own uploads.

No markers → quiet no-op (the gate arms itself only when the helpers
ran).
"""

from __future__ import annotations

import json
from pathlib import Path
import re

GATE_NAME = 'listing_upload_gate'

_MARKER_RE = re.compile(r'^UPLOAD_BATCH_(\w+)\.json$')
_VERDICT_RE = re.compile(r'^BATCH_(\w+)_VERDICT\.json$')

# Glob patterns for the turn-scoped artifacts this gate reads; also used
# by ``rollover_reviews`` to move them aside at turn start.
MARKER_GLOB = 'UPLOAD_BATCH_*.json'
VERDICT_GLOB = 'BATCH_*_VERDICT.json'
PENDING_GLOB = 'UPLOAD_PENDING*.json'


def _ids(task_dir: Path, glob: str, rx: re.Pattern) -> list[str]:
    try:
        names = [p.name for p in Path(task_dir).glob(glob)]
    except OSError:
        return []
    out = []
    for name in names:
        m = rx.match(name)
        if m:
            out.append(m.group(1))
    return out


def check_upload_verdicts(task_dir) -> str | None:
    """Deny reason until every batch is verdicted and the LATEST is clean.

    Rules, matching how the upload loop really converges:
      1. EVERY batch marker needs a verdict — each batch's report must
         have been read (the anti-fake-success core).
      2. Only the LATEST batch must be CLEAN (zero non-image errors).
         Earlier batches are immutable history: a failed intermediate
         that a later upload superseded can never be "fixed", so
         requiring it clean would wedge the loop. "Latest" ranges over
         marker AND verdict-only ids, so a manually-uploaded batch
         (helper failed → no marker) is judged too.
      3. A pending marker (template downloaded) with NO batch activity
         at all means the upload never got verified — or never
         happened; the agent must parse-feedback the batch it uploaded,
         or remove the marker if it genuinely didn't upload.
    Unreadable marker/verdict files count as unresolved (fail closed —
    the agent rewrites them by re-running the helper/parser).
    """
    if task_dir is None:
        return None
    marker_ids = _ids(task_dir, MARKER_GLOB, _MARKER_RE)
    verdict_ids = _ids(task_dir, VERDICT_GLOB, _VERDICT_RE)
    try:
        pending = any(Path(task_dir).glob(PENDING_GLOB))
    except OSError:
        pending = False

    if pending and not marker_ids and not verdict_ids:
        return (
            'A listing template was downloaded this turn '
            '(UPLOAD_PENDING.json) but no uploaded batch has a '
            'parse-feedback verdict. If you uploaded a flat file '
            '(even manually), find its batch id on the upload-status '
            "page, fetch THAT batch's processing report "
            '(bh_fetch_report), and run listing_bulk.py parse-feedback '
            '<report> --batch-id <id> FROM THE TASK WORKSPACE ROOT. '
            'If you did NOT upload anything, delete UPLOAD_PENDING.json '
            'and say why the upload was abandoned.'
        )

    for batch_id in marker_ids:
        if batch_id not in verdict_ids:
            verdict_path = Path(task_dir) / f'BATCH_{batch_id}_VERDICT.json'
            return (
                f'Upload batch {batch_id} has no parse-feedback verdict '
                "yet. Fetch THAT batch's processing report from the SAME "
                'marketplace you uploaded to (bh_fetch_report with '
                f'BATCH_ID={batch_id}), then run listing_bulk.py '
                f'parse-feedback <report> --batch-id {batch_id} FROM THE '
                'TASK WORKSPACE ROOT — the verdict is written to the '
                'current directory and must land at '
                f'{verdict_path}. The task cannot finish on an '
                'unverified upload.'
            )

    # Batch ids are monotonically increasing within an account, so the
    # lexically greatest id is the newest upload — across markers and
    # verdict-only (manual-upload) batches alike.
    latest_id = max((*marker_ids, *verdict_ids), default=None)
    if latest_id is None:
        return None
    verdict_path = Path(task_dir) / f'BATCH_{latest_id}_VERDICT.json'
    try:
        verdict = json.loads(verdict_path.read_text(encoding='utf-8'))
        non_image = int(verdict.get('non_image_errors', 0))
    except (OSError, ValueError, TypeError):
        return (
            f'{verdict_path.name} is unreadable — re-run '
            f'listing_bulk.py parse-feedback --batch-id {latest_id} '
            "on the batch's processing report."
        )
    if non_image:
        return (
            f'Your LATEST batch {latest_id} has {non_image} '
            'unresolved non-image error(s) per its processing report. '
            'Fix exactly the fields the report names, re-upload, and '
            'parse-feedback the new batch. Only the missing-main-image '
            'error is deferrable.'
        )
    return None
