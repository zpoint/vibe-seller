"""Result persistence for AgentSession.

Mixed into AgentSession via multiple inheritance (sibling of
``_StreamMixin`` / ``_HookMixin`` / ...). Extracted from
``claude_backend_stream`` to keep that module under the per-file line
limit; methods here reference attributes initialised by
``AgentSession.__init__``.
"""

from datetime import UTC, datetime
import json
import logging

from app.ai.claude_backend_utils import parse_wait_condition
from app.database import async_session
from app.models.task import Task

logger = logging.getLogger(__name__)


class _PersistMixin:
    """Deliverable persistence — the streaming-prose result fallback."""

    async def _save_result(self, result_text: str):
        """Save the execution result and parse wait-condition.

        Streaming-prose write is the **fallback** when the agent
        didn't call ``vibe_seller_set_task_result`` itself. If
        ``task.result`` is already populated (the MCP tool ran
        earlier in the session and persisted an explicit summary
        via ``POST /api/tasks/<id>/result``), keep the explicit
        value — that's exactly what the agent intended the user to
        see, and overwriting it with the raw streaming prose
        clobbers a deliberate choice. Wait-condition parsing still
        runs against ``result_text`` so end-of-stream
        ``wait-condition`` blocks aren't lost.
        """
        try:
            async with async_session() as db:
                task = await db.get(Task, self.task_id)
                if task:
                    if not (task.result and task.result.strip()):
                        task.result = result_text
                    wait_cond = parse_wait_condition(result_text)
                    if wait_cond:
                        task.wait_condition = json.dumps(wait_cond)
                    # Authoritative end-of-stream checkpoint. For
                    # --resume runs this is a no-op; for fresh runs
                    # it's a belt-and-suspenders write alongside
                    # `_persist_session_id` on init.
                    if self.session_id:
                        task.session_id = self.session_id
                    task.updated_at = datetime.now(UTC).isoformat()
                    await db.commit()
        except Exception as e:
            logger.error(
                'Failed to save result for task %s: %s',
                self.task_id,
                e,
            )
