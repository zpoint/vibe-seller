"""Subagent lifecycle tracking + Stop-gate deny helpers for AgentSession.

Mixed into AgentSession via multiple inheritance (sibling of
``_HookMixin`` / ``_StreamMixin``). Methods here reference attributes
initialised by ``_init_subagent_state`` (called from
``AgentSession.__init__``) and call methods defined on the other
mixins (``_send_hook_response``).

Two structural rules live here:

- **A turn cannot end under a running async subagent.** The CLI emits
  its ``result`` as soon as the MAIN agent stops; async subagents
  launched with the Agent tool keep running, and closing stdin then
  default-denies every remaining tool call they make (observed live —
  a DoD reviewer died mid-verification while the shipped result
  claimed it was "running in the background").
- **A review verdict counts only if the reviewer subagent wrote it.**
  The stream attributes each review-file Write/Edit to subagent/main
  via the event's ``parent_tool_use_id``; the gates reject an
  accepting verdict the main agent authored itself.
"""

import logging
import re

from app.ai.claude_backend_utils import (
    REVIEW_REDRIVE_MAX,
    build_tasklist_open_reason,
    check_exec_review_status_for_stop,
    check_review_status_for_stop,
    get_open_tasklist_items,
)

logger = logging.getLogger(__name__)


class _SubagentMixin:
    """Async-subagent tracking + the Stop-hook deny chain."""

    def _init_subagent_state(self):
        """Per-session stream signals; see the module docstring.

        ``_review_subagent_ran`` flips when a review-ish Agent/Task
        spawn is seen (weak, spawn-time). ``_review_file_writers`` maps
        each review file written this turn to ``'subagent'``/``'main'``
        (strong authorship signal). ``_agent_spawn_ids`` are the
        Agent/Task tool_use ids spawned by the main agent;
        ``_async_agents`` the subset confirmed ASYNC (launch ack) with
        no ``<task-notification>`` completion yet.
        """
        self._review_subagent_ran: bool = False
        self._review_file_writers: dict[str, str] = {}
        self._agent_spawn_ids: set[str] = set()
        self._async_agents: dict[str, str] = {}

    def _async_agents_pending_reason(self) -> str | None:
        """Deny reason while background subagents are still running."""
        pending = getattr(self, '_async_agents', None)
        if not pending:
            return None
        ids = ', '.join(v or k for k, v in pending.items())
        return (
            f'{len(pending)} background subagent(s) you launched '
            f'this turn are still running ({ids}). A turn must not '
            'end while its subagents are running — once you stop, '
            'their remaining tool calls are denied and their work is '
            'lost, so any "it will report later" claim would be '
            "false. WAIT for each one's <task-notification> "
            'completion message and incorporate its result before '
            'finishing. If a subagent is no longer needed, tell the '
            'user what you launched and why you are abandoning it.'
        )

    def _track_async_agents(self, event: dict):
        """Maintain the set of still-running ASYNC subagents.

        Two signals, both on ``user`` events:

        - launch ack: the tool_result for an Agent/Task spawn whose text
          starts "Async agent launched successfully" (sync spawns return
          the subagent's final answer instead) → the agent is running in
          the background and the turn must not end under it.
        - completion: the CLI injects a ``<task-notification …>`` user
          message when a background agent finishes. Match its
          task-id/tool-use-id/agent-id attributes against what we
          tracked; if the notification carries none we can match,
          clear the whole set (fail open — never wedge a turn on a
          notification format change).
        """
        blocks = event.get('message', {}).get('content', [])
        if isinstance(blocks, str):
            blocks = [{'type': 'text', 'text': blocks}]
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_result':
                tid = block.get('tool_use_id')
                if tid not in self._agent_spawn_ids:
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
                if 'Async agent launched' in text:
                    m = re.search(r'agentId:\s*([A-Za-z0-9_-]+)', text)
                    self._async_agents[tid] = m.group(1) if m else ''
                    # Selects the longer linger tier for this whole
                    # process: late notifications and NESTED subagent
                    # spawns are invisible to this tracker, so a
                    # process that used async agents at all gets the
                    # grace window. See claude_backend_turns.
                    self._had_async_spawns = True
            elif block.get('type') == 'text':
                text = block.get('text', '')
                if '<task-notification' not in text:
                    continue
                ids = set(
                    re.findall(
                        r'(?:task-id|tool-use-id|agent-id)'
                        r'="([^"]+)"',
                        text,
                    )
                )
                matched = [
                    k
                    for k, v in self._async_agents.items()
                    if k in ids or (v and v in ids)
                ]
                for k in matched:
                    del self._async_agents[k]
                if not matched:
                    # Unattributable notification — assume it was ours
                    # rather than block the turn forever.
                    self._async_agents.clear()

    # ── Stop-hook deny chain ────────────────────────────────────────
    # Called in order by the STOP_REFLECTION_CALLBACK handler in
    # claude_backend_hooks; first deny wins.

    async def _deny_stop_if_tasklist_open(self, request_id: str) -> bool:
        """Deny stop if TaskList has open items; return True if denied."""
        items = get_open_tasklist_items(self.task_id)
        if not items:
            return False
        logger.info('Stop denied %s — %d open', self.task_id[:8], len(items))
        await self._send_hook_response(
            request_id,
            {'decision': 'block', 'reason': build_tasklist_open_reason(items)},
        )
        return True

    async def _deny_stop_if_async_agents_running(self, request_id) -> bool:
        """Deny Stop while background subagents launched this turn are
        still running; return True if denied.

        Same fail-open bound as the review gates: past the re-drive
        budget the hook stands down so a lost completion notification
        can never wedge the turn (the result then ships banner-marked
        by the stream path).
        """
        if self._review_redrive_count >= REVIEW_REDRIVE_MAX:
            return False
        deny = self._async_agents_pending_reason()
        if not deny:
            return False
        logger.info(
            'Stop denied %s — %d async subagent(s) still running',
            self.task_id[:8],
            len(self._async_agents),
        )
        await self._send_hook_response(
            request_id, {'decision': 'block', 'reason': deny}
        )
        return True

    async def _deny_stop_if_review_unsatisfied(self, request_id: str) -> bool:
        """Deny stop if the ads-audit reviewer hasn't returned
        ``Status: ok`` (or ``incomplete`` at iter 5+); return True if
        denied.

        See ``check_review_status_for_stop`` in
        ``claude_backend_utils`` for the contract and
        ``amazon-ads/references/reviewer-loop.md`` for the loop the
        main agent runs to satisfy this gate. Quiet no-op for non-ads
        tasks (no ``AD_AUDIT_*.md`` in the workspace).
        """
        # Past the re-drive budget the gate FAILS OPEN: the stream
        # banner-marks the result UNVERIFIED and this hook stands down so
        # the CLI can exit (a live deny + closed approval channel had
        # every tool default-denied mid-recovery).
        if self._review_redrive_count >= REVIEW_REDRIVE_MAX:
            return False
        deny = check_review_status_for_stop(
            self.task_dir,
            subagent_ran=getattr(self, '_review_subagent_ran', False),
            review_writers=getattr(self, '_review_file_writers', None),
        )
        if not deny:
            return False
        logger.info(
            'Stop denied %s — ads-audit reviewer unsatisfied',
            self.task_id[:8],
        )
        await self._send_hook_response(
            request_id,
            {'decision': 'block', 'reason': deny},
        )
        return True

    async def _deny_stop_if_exec_review_unsatisfied(self, request_id):
        """Stop-hook variant of the exec-review gate. The primary gate
        is in the ``set_task_result`` MCP endpoint; this is a backstop
        for backends that do emit Stop events.
        """
        deny = check_exec_review_status_for_stop(
            self.task_dir,
            review_writers=getattr(self, '_review_file_writers', None),
        )
        if not deny:
            return False
        logger.info('Stop denied %s — exec-review', self.task_id[:8])
        await self._send_hook_response(
            request_id, {'decision': 'block', 'reason': deny}
        )
        return True
