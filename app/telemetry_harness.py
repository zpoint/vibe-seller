"""Result-gate, ad-declaration and knowledge-cleanup telemetry."""

import logging

from app import telemetry
from app.models.ad_declaration import AD_TASK_KINDS
from app.telemetry_events import TelemetryEvent

logger = logging.getLogger(__name__)

_GATES = frozenset({
    'ad_changes',
    'ad_coverage',
    'ad_declaration',
    'deliverable_manifest',
    'upload_verdict',
    'review_completeness_review',
    'review_output_gate',
})


def _send(event: str, props: dict) -> None:
    try:
        telemetry.send(event, props)
    except Exception:
        logger.debug('%s telemetry failed', event, exc_info=True)


def send_result_gated(
    task_id: str,
    gate: str,
    outcome: str,
    *,
    attempt: int,
    gap_count: int,
) -> None:
    _send(
        TelemetryEvent.TASK_RESULT_GATED,
        {
            'task_id': task_id,
            'gate': gate if gate in _GATES else 'custom',
            'outcome': outcome,
            'attempt': attempt,
            'gap_count_bucket': telemetry.count_bucket(gap_count),
        },
    )


def scope_kind(scope: dict | None) -> str:
    """Narrowest dimension a normalised ad scope names."""
    scope = scope or {}
    for key in ('products', 'campaigns', 'combos'):
        if scope.get(key):
            return key
    return 'store'


def send_ad_task_declared(
    task_id: str,
    kind: str,
    scope: dict | None,
    outcome: str,
    approved_count: int | None = None,
) -> None:
    props = {
        'task_id': task_id,
        'kind': kind if kind in AD_TASK_KINDS else 'other',
        'scope_kind': scope_kind(scope),
        'outcome': outcome,
    }
    if approved_count is not None:
        props['approved_count_bucket'] = telemetry.count_bucket(approved_count)
    _send(TelemetryEvent.AD_TASK_DECLARED, props)


def send_cleanup_finished(
    task_id: str, tier: str, outcome: str, restored_count: int
) -> None:
    _send(
        TelemetryEvent.KNOWLEDGE_CLEANUP_FINISHED,
        {
            'task_id': task_id,
            'tier': tier,
            'outcome': outcome,
            'restored_file_count': restored_count,
        },
    )
