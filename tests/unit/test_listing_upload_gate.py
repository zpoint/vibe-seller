"""Batch-keyed upload-freshness gate (stop_gates.listing_upload_gate).

A task that UPLOADED a listing batch may not finish until THAT batch has
a parse-feedback verdict with zero non-image errors — report freshness by
construction, not by prompt.

The gate is armed by the upload helper's own attempt marker
(`UPLOAD_ATTEMPT.json`, written when it is about to put the file on the
live page), never by a step that merely precedes an upload. Arming on a
template download used to refuse a run that only downloaded a template to
INSPECT its field set — an obligation inferred from an unrelated action,
which is what this file's regression tests pin.
"""

import json

import pytest

from app.ai.stop_gates import listing_upload_gate as gate
from app.ai.stop_gates.listing_upload_gate import (
    check_upload_verdicts,
    rollover_upload_markers,
)

pytestmark = pytest.mark.unit


def _reason(d):
    """The prose a refusal carries (the tuple's first half)."""
    found = check_upload_verdicts(d)
    return None if found is None else found[0]


def _gap(d):
    """The one unmet item a refusal leaves as a caveat."""
    found = check_upload_verdicts(d)
    return None if found is None else found[1]


def _marker(d, batch='100000000001'):
    (d / f'UPLOAD_BATCH_{batch}.json').write_text(
        json.dumps({'batch_id': batch}), encoding='utf-8'
    )
    return batch


def _verdict(d, batch, non_image):
    (d / f'BATCH_{batch}_VERDICT.json').write_text(
        json.dumps({
            'batch_id': batch,
            'errors': non_image,
            'warnings': 0,
            'non_image_errors': non_image,
        }),
        encoding='utf-8',
    )


def _attempt(d, name='UPLOAD_ATTEMPT.json', upload='out.txt'):
    """What bh_upload_flatfile writes at its arm point."""
    (d / name).write_text(
        json.dumps({
            'file': f'/home/u/.vibe-seller/downloads/acme/{upload}',
            'host': 'sellercentral.amazon.sa',
            'marketplace': 'A17E79C6D8DWNP',
            'intended_marketplace': 'A17E79C6D8DWNP',
            'attempted_at': 1.0,
        }),
        encoding='utf-8',
    )


class TestUploadVerdictGate:
    def test_no_markers_is_quiet_noop(self, tmp_path):
        assert check_upload_verdicts(tmp_path) is None
        assert check_upload_verdicts(None) is None

    def test_marker_without_verdict_denies(self, tmp_path):
        batch = _marker(tmp_path)
        deny = _reason(tmp_path)
        assert deny is not None and batch in deny
        assert 'parse-feedback' in deny
        # gaps carry one structured item per unmet batch, so the caveat
        # the user sees names the batch instead of repeating the prose.
        assert _gap(tmp_path) == (
            f'Batch {batch} was uploaded with no report verdict.'
        )

    def test_verdict_with_non_image_errors_denies(self, tmp_path):
        batch = _marker(tmp_path)
        _verdict(tmp_path, batch, non_image=3)
        assert '3 unresolved' in _reason(tmp_path)

    def test_superseded_failed_batch_does_not_block(self, tmp_path):
        # An intermediate failed batch is immutable history; only the
        # LATEST batch must be clean (every batch still needs a verdict).
        old_b = _marker(tmp_path, batch='100000000001')
        _verdict(tmp_path, old_b, non_image=12)
        new_b = _marker(tmp_path, batch='100000000002')
        _verdict(tmp_path, new_b, non_image=0)
        assert check_upload_verdicts(tmp_path) is None

    def test_latest_failed_batch_blocks_despite_old_clean(self, tmp_path):
        old_b = _marker(tmp_path, batch='100000000001')
        _verdict(tmp_path, old_b, non_image=0)
        new_b = _marker(tmp_path, batch='100000000002')
        _verdict(tmp_path, new_b, non_image=2)
        deny = _reason(tmp_path)
        assert deny is not None and '100000000002' in deny

    def test_clean_verdict_passes(self, tmp_path):
        batch = _marker(tmp_path)
        _verdict(tmp_path, batch, non_image=0)
        assert check_upload_verdicts(tmp_path) is None

    def test_unreadable_verdict_fails_closed(self, tmp_path):
        batch = _marker(tmp_path)
        (tmp_path / f'BATCH_{batch}_VERDICT.json').write_text(
            'not json', encoding='utf-8'
        )
        assert 're-run' in _reason(tmp_path)

    def test_rollover_moves_markers_and_verdicts(self, tmp_path):
        # A new turn must not be gated (or satisfied) by a prior
        # turn's batches — rollover moves them aside.
        batch = _marker(tmp_path)
        _verdict(tmp_path, batch, non_image=0)
        rollover_upload_markers(tmp_path)
        assert check_upload_verdicts(tmp_path) is None
        assert not list(tmp_path.glob('UPLOAD_BATCH_*.json'))
        moved = list(tmp_path.glob('.prev_turns/turn_*/UPLOAD_BATCH_*'))
        assert moved


class TestUploadAttemptArming:
    """The upload helper arms the gate — including when it then FAILED.

    The bypass this covers: the helper staged the file, the Submit click
    missed, the agent finished the upload by hand, so no
    ``UPLOAD_BATCH_*`` marker exists. The attempt marker is already there,
    so the batch the agent pushed by hand is still owed a verdict.
    """

    def test_attempt_without_any_batch_denies(self, tmp_path):
        _attempt(tmp_path)
        deny = _reason(tmp_path)
        assert deny is not None
        assert 'parse-feedback' in deny and 'UPLOAD_ATTEMPT' in deny
        # The refusal names the file that is unverified, not just "an
        # upload" — the agent has to work out which batch it means.
        assert 'out.txt' in deny

    def test_attempt_with_clean_verdict_only_passes(self, tmp_path):
        # Manual finish of a started upload: no UPLOAD_BATCH marker, but
        # the agent fetched the report and verdicted the batch.
        _attempt(tmp_path)
        _verdict(tmp_path, '100000000001', non_image=0)
        assert check_upload_verdicts(tmp_path) is None

    def test_attempt_with_failed_verdict_only_denies(self, tmp_path):
        # Manual upload whose report shows errors must still block —
        # "latest batch clean" ranges over verdict-only ids too.
        _attempt(tmp_path)
        _verdict(tmp_path, '100000000001', non_image=4)
        assert '4 unresolved' in _reason(tmp_path)

    def test_verdict_only_latest_judged_without_attempt(self, tmp_path):
        # Even with no attempt marker, a verdict-only batch (manual
        # upload, manually verdicted) is judged for cleanliness.
        _verdict(tmp_path, '100000000002', non_image=1)
        deny = _reason(tmp_path)
        assert deny is not None and '100000000002' in deny

    def test_attempt_removed_disarms(self, tmp_path):
        # The sanctioned no-upload exit: delete the marker.
        _attempt(tmp_path)
        (tmp_path / 'UPLOAD_ATTEMPT.json').unlink()
        assert check_upload_verdicts(tmp_path) is None

    def test_rollover_moves_attempt(self, tmp_path):
        # A next-turn follow-up must not inherit this turn's attempt
        # marker (it would demand a verdict for an upload that turn
        # never made).
        _attempt(tmp_path)
        rollover_upload_markers(tmp_path)
        assert check_upload_verdicts(tmp_path) is None
        assert not list(tmp_path.glob('UPLOAD_ATTEMPT*.json'))
        assert list(tmp_path.glob('.prev_turns/turn_*/UPLOAD_ATTEMPT*'))


class TestReadingIsNotUploading:
    """THE REGRESSION: a template download must not owe an upload.

    ``bh_download_template.py`` wrote ``UPLOAD_PENDING.json``, and rule 3
    treated it as "an upload is intended this turn". But the skill's own
    loop is download → INSPECT → fill → upload, so a task that only read a
    category's field set — or filled a file for the user to review — was
    refused for an "upload not verified", with a marker it had no honest
    way to have created.
    """

    def test_a_template_in_the_workspace_arms_nothing(self, tmp_path):
        (tmp_path / 'WIDGETS__SA.xlsm').write_text('template bytes', 'utf-8')
        assert check_upload_verdicts(tmp_path) is None

    def test_a_stale_pending_marker_arms_nothing(self, tmp_path):
        # What an older release's template downloader wrote, and what that
        # release's gate armed on. Nothing current writes it (a released
        # install runs its own bundled helpers), so a copy like this only
        # survives in a task workspace that predates an upgrade — and it
        # must arm nothing there either.
        (tmp_path / 'UPLOAD_PENDING.json').write_text(
            json.dumps({'template': 'WIDGETS.xlsm', 'store': 'Amazon.sa'}),
            encoding='utf-8',
        )
        assert check_upload_verdicts(tmp_path) is None

    def test_rollover_leaves_the_unread_legacy_file_alone(self, tmp_path):
        # Nothing reads that name any more, so nothing moves it: the
        # turn-scoped sweep only handles the three artifacts the gate
        # actually judges.
        (tmp_path / 'UPLOAD_PENDING.json').write_text('{}', encoding='utf-8')
        _attempt(tmp_path)
        rollover_upload_markers(tmp_path)
        assert (tmp_path / 'UPLOAD_PENDING.json').is_file()
        assert not list(tmp_path.glob('UPLOAD_ATTEMPT*.json'))
        assert list(tmp_path.glob('.prev_turns/turn_*/UPLOAD_ATTEMPT*'))


class TestGateContract:
    """The shape set_task_result calls skill-declared gates through."""

    def test_check_takes_the_task_id_and_builds_the_workspace_path(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(gate, 'VIBE_SELLER_DIR', tmp_path)
        workspace = tmp_path / 'tasks' / 'task-1'
        workspace.mkdir(parents=True)
        assert gate.check('report', 'task-1', None) is None

        _marker(workspace)
        deny = gate.check('report', 'task-1', None)
        assert deny is not None
        assert deny.gate == 'upload_verdict'
        assert deny.gaps and not deny.contradictions

    def test_check_without_a_task_id_is_a_noop(self):
        assert gate.check('report', None, None) is None
