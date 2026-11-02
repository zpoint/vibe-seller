"""Debug trace for the CLI's partial-message ``stream_event`` lines.

Every agent spawn sets ``CLAUDE_CODE_INCLUDE_PARTIAL_MESSAGES=1`` so the
CLI forwards raw SSE parts (message_start, content_block_* , pings) as
``{type: 'stream_event', event: {...}}``. The stream reader consumes them
for liveness only; this helper logs delta sizes under ``AGENT_DEBUG``.
"""

import logging

logger = logging.getLogger(__name__)

_DELTA_TEXT_FIELD = {
    'text_delta': 'text',
    'thinking_delta': 'thinking',
    'input_json_delta': 'partial_json',
}


def trace_stream_event(task_id: str, event: dict) -> None:
    """Log one ``stream_event`` content delta (type + payload size)."""
    inner = event.get('event') or {}
    if inner.get('type') != 'content_block_delta':
        return
    delta = inner.get('delta', {})
    dtype = delta.get('type')
    field = _DELTA_TEXT_FIELD.get(dtype)
    size = len(delta.get(field, '') or '') if field else -1
    logger.info(
        'STREAM_DELTA task=%s type=%s size=%d', task_id[:8], dtype, size
    )
