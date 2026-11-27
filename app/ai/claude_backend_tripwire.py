"""The output-tripwire mixin for AgentSession.

Split out of ``claude_backend_stream`` (line budget): the stream
reader calls :meth:`_scan_tool_results_tripwire` on every ``user``
event, next to the loop breaker's ``_note_tool_results``.
"""

from __future__ import annotations

import asyncio
import json
import logging

from app.ai.output_tripwire import scan_tool_result

logger = logging.getLogger(__name__)


class _TripwireMixin:
    """Stops the task when a tool result leaks vendor private data."""

    async def _scan_tool_results_tripwire(self, event: dict) -> None:
        """The second net behind the pre-tool guard.

        A tool result carrying a vendor's private PATH (the tripwire
        matches paths only — user decision 2026-11-27) ends the run
        FAILED: output that reached the model is already out of our
        control, so there are no redaction games. The reason names
        what matched, never a key value.
        """
        blocks = event.get('message', {}).get('content', [])
        if not isinstance(blocks, list) or self._fatal_error:
            return
        for block in blocks:
            if (
                not isinstance(block, dict)
                or block.get('type') != 'tool_result'
            ):
                continue
            raw = block.get('content')
            if isinstance(raw, list):
                text = ' '.join(
                    b.get('text', '')
                    for b in raw
                    if isinstance(b, dict) and b.get('type') == 'text'
                )
            else:
                text = str(raw or '')
            reason = await asyncio.to_thread(scan_tool_result, text)
            if reason is None:
                continue
            tool_use_id = block.get('tool_use_id')
            logger.warning(
                'Output tripwire: task %s tool_use %s tripped — '
                'stopping the task (%s)',
                self.task_id[:8],
                tool_use_id or '-',
                reason.splitlines()[0][:160],
            )
            self._fatal_error = (
                'Stopped by the server: a tool result contained '
                'browser-client private data. ' + reason
            )
            self._is_error_result = True
            self._error_category = 'security_tripwire'
            await self._emit_message(
                'agent_event',
                json.dumps({
                    'event': 'output_tripwire',
                    'tool_use_id': tool_use_id,
                }),
            )
            asyncio.create_task(self.stop())
            return
