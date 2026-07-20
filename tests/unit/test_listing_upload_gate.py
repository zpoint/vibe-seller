"""Batch-keyed upload-freshness gate (stop_gates.listing_upload_gate).

A task that uploaded a listing batch (UPLOAD_BATCH marker present) may
not finish until THAT batch has a parse-feedback verdict with zero
non-image errors — report freshness by construction, not by prompt.
"""

import json

import pytest

from app.ai.stop_gates.listing_upload_gate import check_upload_verdicts
from app.ai.stop_gates.report_reviewer import rollover_reviews

pytestmark = pytest.mark.unit


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


class TestUploadVerdictGate:
    def test_no_markers_is_quiet_noop(self, tmp_path):
        assert check_upload_verdicts(tmp_path) is None
        assert check_upload_verdicts(None) is None

    def test_marker_without_verdict_denies(self, tmp_path):
        batch = _marker(tmp_path)
        deny = check_upload_verdicts(tmp_path)
        assert deny is not None and batch in deny
        assert 'parse-feedback' in deny

    def test_verdict_with_non_image_errors_denies(self, tmp_path):
        batch = _marker(tmp_path)
        _verdict(tmp_path, batch, non_image=3)
        deny = check_upload_verdicts(tmp_path)
        assert deny is not None and '3 unresolved' in deny

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
        deny = check_upload_verdicts(tmp_path)
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
        deny = check_upload_verdicts(tmp_path)
        assert deny is not None and 're-run' in deny

    def test_rollover_moves_markers_and_verdicts(self, tmp_path):
        # A new turn must not be gated (or satisfied) by a prior
        # turn's batches — rollover moves them aside with the reviews.
        batch = _marker(tmp_path)
        _verdict(tmp_path, batch, non_image=0)
        rollover_reviews(tmp_path)
        assert check_upload_verdicts(tmp_path) is None
        assert not list(tmp_path.glob('UPLOAD_BATCH_*.json'))
        moved = list(tmp_path.glob('.prev_turns/turn_*/UPLOAD_BATCH_*'))
        assert moved


def _pending(d):
    (d / 'UPLOAD_PENDING.json').write_text(
        json.dumps({'template': 'WIDGETS.xlsm', 'store': 'Amazon.sa'}),
        encoding='utf-8',
    )


class TestPendingMarkerArming:
    """The template-download marker arms the gate even when the upload
    helper failed and the agent submitted by hand (no batch marker) —
    the observed bypass that let an unverified manual upload finish."""

    def test_pending_without_any_batch_denies(self, tmp_path):
        _pending(tmp_path)
        deny = check_upload_verdicts(tmp_path)
        assert deny is not None
        assert 'parse-feedback' in deny and 'UPLOAD_PENDING' in deny

    def test_pending_with_clean_verdict_only_passes(self, tmp_path):
        # Manual upload: no UPLOAD_BATCH marker, but the agent fetched
        # the report and verdicted the batch — satisfied.
        _pending(tmp_path)
        _verdict(tmp_path, '100000000001', non_image=0)
        assert check_upload_verdicts(tmp_path) is None

    def test_pending_with_failed_verdict_only_denies(self, tmp_path):
        # Manual upload whose report shows errors must still block —
        # "latest batch clean" ranges over verdict-only ids too.
        _pending(tmp_path)
        _verdict(tmp_path, '100000000001', non_image=4)
        deny = check_upload_verdicts(tmp_path)
        assert deny is not None and '4 unresolved' in deny

    def test_verdict_only_latest_judged_without_pending(self, tmp_path):
        # Even with no pending marker, a verdict-only batch (manual
        # upload, manually verdicted) is judged for cleanliness.
        _verdict(tmp_path, '100000000002', non_image=1)
        deny = check_upload_verdicts(tmp_path)
        assert deny is not None and '100000000002' in deny

    def test_pending_removed_disarms(self, tmp_path):
        # The sanctioned no-upload exit: delete the marker.
        _pending(tmp_path)
        (tmp_path / 'UPLOAD_PENDING.json').unlink()
        assert check_upload_verdicts(tmp_path) is None

    def test_rollover_moves_pending(self, tmp_path):
        # A next-turn follow-up must not inherit this turn's pending
        # marker (it would demand a verdict for an upload that turn
        # never made).
        _pending(tmp_path)
        rollover_reviews(tmp_path)
        assert check_upload_verdicts(tmp_path) is None
        assert not list(tmp_path.glob('UPLOAD_PENDING*.json'))
        assert list(tmp_path.glob('.prev_turns/turn_*/UPLOAD_PENDING*'))
