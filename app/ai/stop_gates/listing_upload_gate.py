"""Batch-keyed freshness gate for listing flat-file uploads.

``bh_upload_flatfile`` records every submitted batch as an
``UPLOAD_BATCH_<id>.json`` marker in the task workspace, and
``listing_bulk.py parse-feedback --batch-id <id>`` writes the matching
``BATCH_<id>_VERDICT.json`` after reading THAT batch's processing
report. This gate closes the loop: a task that uploaded a batch cannot
finish until the batch has a verdict, and the verdict shows no
non-image errors (the missing-main-image deferral is the one accepted
"done with caveat" state).

This makes report freshness structural instead of prompted: a reviewer
can no longer pass the turn by reading a stale report from a prior
turn or another marketplace — the verdict is keyed to the batch id the
upload actually produced this turn. Markers/verdicts are moved aside
at turn start together with the review files (``rollover_reviews``),
so each turn is gated only on its own uploads.

No markers → quiet no-op (the gate arms itself only when the upload
helper ran).
"""

from __future__ import annotations

import json
from pathlib import Path
import re

GATE_NAME = 'listing_upload_gate'

_MARKER_RE = re.compile(r'^UPLOAD_BATCH_(\w+)\.json$')

# Glob patterns for the turn-scoped artifacts this gate reads; also used
# by ``rollover_reviews`` to move them aside at turn start.
MARKER_GLOB = 'UPLOAD_BATCH_*.json'
VERDICT_GLOB = 'BATCH_*_VERDICT.json'


def check_upload_verdicts(task_dir) -> str | None:
    """Deny reason if any uploaded batch lacks a clean verdict; else None.

    A batch is clean when its verdict exists and reports zero non-image
    errors. Unreadable marker/verdict files count as unresolved (fail
    closed — the agent rewrites them by re-running the helper/parser).
    """
    if task_dir is None:
        return None
    try:
        markers = sorted(Path(task_dir).glob(MARKER_GLOB))
    except OSError:
        return None
    for marker in markers:
        m = _MARKER_RE.match(marker.name)
        if not m:
            continue
        batch_id = m.group(1)
        verdict_path = Path(task_dir) / f'BATCH_{batch_id}_VERDICT.json'
        if not verdict_path.is_file():
            return (
                f'Upload batch {batch_id} has no parse-feedback verdict '
                "yet. Fetch THAT batch's processing report from the SAME "
                'marketplace you uploaded to (bh_fetch_report with '
                f'BATCH_ID={batch_id}), then run listing_bulk.py '
                f'parse-feedback <report> --batch-id {batch_id}. The task '
                'cannot finish on an unverified upload.'
            )
        try:
            verdict = json.loads(verdict_path.read_text(encoding='utf-8'))
            non_image = int(verdict.get('non_image_errors', 0))
        except (OSError, ValueError, TypeError):
            return (
                f'{verdict_path.name} is unreadable — re-run '
                f'listing_bulk.py parse-feedback --batch-id {batch_id} '
                "on the batch's processing report."
            )
        if non_image > 0:
            return (
                f'Batch {batch_id} has {non_image} unresolved non-image '
                'error(s) per its processing report. Fix exactly the '
                'fields the report names, re-upload, and parse-feedback '
                'the new batch. Only the missing-main-image error is '
                'deferrable.'
            )
    return None
