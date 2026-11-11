"""Batch-keyed freshness gate for listing flat-file uploads.

The obligation is armed by the act of UPLOADING, not by any earlier step
of the listing flow:

* ``bh_upload_flatfile`` records an ``UPLOAD_ATTEMPT.json`` marker the
  moment it accepts an upload — after every refusal it can make before
  anything could reach Amazon (disabled rows in the file, a signed-out
  session, a marketplace triple-check that did not agree) and before it
  hands the file to the page. The agent declared an upload run.
* The same helper records ``UPLOAD_BATCH_<id>.json`` per submitted batch.
* ``listing_bulk.py parse-feedback --batch-id <id>`` writes the matching
  ``BATCH_<id>_VERDICT.json`` after reading THAT batch's processing
  report.

This check closes the loop: an upload that was started must end with its
batch verdicted, and the latest batch must be clean. It is one round like
every other check — refused once, naming what is unmet, then the result
ships with the finding as a caveat.

Arming on the ATTEMPT (not on a successful submit) is what keeps the
anti-fake-success property: the bypass this gate was written against was
a helper failure — the Submit click missed, the agent uploaded by hand,
no batch marker existed, and the gate stayed a quiet no-op. The attempt
marker survives that fallback, so a hand finish of a started upload is
still gated: at least one batch verdict must exist, and the latest one
must be clean. If the upload never happened after all, the agent removes
the attempt marker (explicitly, visibly) instead of the gate silently not
existing.

Downloading a TEMPLATE no longer arms anything. ``bh_download_template``
used to write ``UPLOAD_PENDING.json``, but a template download is step 1
of every listing task — the skill's own loop is "download, INSPECT,
fill, upload" — so a run that only fetched a template to read its field
set was refused for an "upload not verified". An inspection is not a
declaration of an upload, and the obligation must come from the
declaration (CLAUDE.md: no declaration, no inferred obligation).

The gate is SKILL-BOUND: ``amazon-listing`` declares
``gates: [upload_verdict]``, so it runs for the sessions that loaded that
skill — the agent's own decision — and not as a probe of every task
workspace on every submit, which is how it used to run.

No markers → quiet no-op (the gate arms only when the upload helper ran).
"""

from __future__ import annotations

import json
from pathlib import Path
import re

from app.ai.stop_gates import GateDeny
from app.config import VIBE_SELLER_DIR

GATE_NAME = 'upload_verdict'

_MARKER_RE = re.compile(r'^UPLOAD_BATCH_(\w+)\.json$')
_VERDICT_RE = re.compile(r'^BATCH_(\w+)_VERDICT\.json$')

# Glob patterns for the turn-scoped artifacts this gate reads; also the
# files ``rollover_upload_markers`` moves aside at turn start.
MARKER_GLOB = 'UPLOAD_BATCH_*.json'
VERDICT_GLOB = 'BATCH_*_VERDICT.json'
ATTEMPT_GLOB = 'UPLOAD_ATTEMPT*.json'
# The name a RELEASED server (any install older than this check's
# re-arming) arms on. ``bh_upload_flatfile`` writes it beside
# ``UPLOAD_ATTEMPT.json`` so those installs keep gating a real upload,
# and an old ``bh_download_template`` may still have left one from a
# template fetch. Either way it arms NOTHING here — an obligation may not
# be inferred from a download — and it is swept at turn start so the
# workspace does not accumulate a file nothing reads.
LEGACY_PENDING_GLOB = 'UPLOAD_PENDING*.json'


def task_workspace(task_id: str | None):
    """The task workspace a gate's markers live in, or None."""
    if not task_id:
        return None
    return (VIBE_SELLER_DIR / 'tasks' / task_id).resolve()


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


def _attempted_upload(task_dir: Path) -> str | None:
    """What the upload helper was about to push, or None if it never ran.

    The marker names the FILE, so the refusal can say which upload is
    unverified instead of telling the agent its own work looks odd.
    """
    for path in sorted(Path(task_dir).glob(ATTEMPT_GLOB)):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            # Written by the helper; unreadable means the helper died
            # mid-write, not that no upload was attempted — fail closed.
            return path.name
        name = str(payload.get('file') or '').rsplit('/', 1)[-1]
        return name or path.name
    return None


def check_upload_verdicts(task_dir) -> tuple[str, str] | None:
    """``(reason for the agent, the one unmet item)`` while unverified.

    Rules, matching how the upload loop really converges:
      1. EVERY batch marker needs a verdict — each batch's report must
         have been read (the anti-fake-success core).
      2. Only the LATEST batch must be CLEAN (zero non-image errors).
         Earlier batches are immutable history: a failed intermediate
         that a later upload superseded can never be "fixed", so
         requiring it clean would wedge the loop. "Latest" ranges over
         marker AND verdict-only ids, so a manually-finished upload
         (helper failed → no marker) is judged too.
      3. An upload ATTEMPT with no batch activity at all means the
         helper got the go-ahead and never produced a verdicted batch —
         the agent must parse-feedback the batch it uploaded (even by
         hand), or remove the marker if it genuinely never submitted.
         Template downloads are NOT in this rule: only the upload
         helper's own attempt marker arms it.
    Unreadable marker/verdict files count as unresolved (fail closed —
    the agent rewrites them by re-running the helper/parser).
    """
    if task_dir is None:
        return None
    marker_ids = _ids(task_dir, MARKER_GLOB, _MARKER_RE)
    verdict_ids = _ids(task_dir, VERDICT_GLOB, _VERDICT_RE)
    attempted = _attempted_upload(Path(task_dir))

    if attempted and not marker_ids and not verdict_ids:
        return (
            'An upload was started this turn '
            f'({attempted}) but no uploaded batch has a parse-feedback '
            'verdict. If you uploaded a flat file (even manually, after '
            'the helper failed), find its batch id on the upload-status '
            "page, fetch THAT batch's processing report "
            '(bh_fetch_report), and run listing_bulk.py parse-feedback '
            '<report> --batch-id <id> FROM THE TASK WORKSPACE ROOT. If '
            'nothing was ever submitted, delete UPLOAD_ATTEMPT.json and '
            'say why the upload was abandoned.',
            f'Upload of {attempted} left no verdicted batch.',
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
                f'{verdict_path}. Until then the upload is unverified.',
                f'Batch {batch_id} was uploaded with no report verdict.',
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
            "on the batch's processing report.",
            f'Verdict for batch {latest_id} could not be read.',
        )
    if non_image:
        return (
            f'Your LATEST batch {latest_id} has {non_image} '
            'unresolved non-image error(s) per its processing report. '
            'Fix exactly the fields the report names, re-upload, and '
            'parse-feedback the new batch. Only the missing-main-image '
            'error is deferrable.',
            f'Latest batch {latest_id} has {non_image} unresolved '
            'non-image error(s).',
        )
    return None


def check(result_text=None, task_id=None, rules=None) -> GateDeny | None:
    """The skill-declared gate contract (``app/ai/stop_gates``).

    ``result_text`` and ``rules`` are unused: what this checks is the
    turn's upload artifacts, not the wording of the report. They stay in
    the signature because ``set_task_result`` calls every gate
    positionally with all three.
    """
    found = check_upload_verdicts(task_workspace(task_id))
    if not found:
        return None
    reason, gap = found
    # What to do is the agent's; the one item is the seller's caveat.
    return GateDeny(gate=GATE_NAME, reason=reason, gaps=(gap,))


PREV_TURNS_DIR = '.prev_turns'


def rollover_upload_markers(task_dir) -> None:
    """Move the prior turn's upload markers and verdicts aside.

    A follow-up is a new turn with its own uploads; its gate must not be
    armed or satisfied by an earlier turn's batches. Files are moved,
    not deleted, under ``.prev_turns/turn_<n>/``. Best-effort; called
    once per session at the ``system/init`` event.
    """
    if task_dir is None:
        return
    try:
        d = Path(task_dir)
        stale = [
            p
            for glob in (
                MARKER_GLOB,
                VERDICT_GLOB,
                ATTEMPT_GLOB,
                LEGACY_PENDING_GLOB,
            )
            for p in d.glob(glob)
        ]
        if not stale:
            return
        root = d / PREV_TURNS_DIR
        root.mkdir(parents=True, exist_ok=True)
        dest = root / f'turn_{1 + sum(1 for _ in root.glob("turn_*"))}'
        dest.mkdir(parents=True, exist_ok=True)
        for p in stale:
            try:
                p.rename(dest / p.name)
            except OSError:  # pragma: no cover
                pass
    except OSError:  # pragma: no cover — best-effort
        pass
