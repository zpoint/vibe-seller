"""Stream reader + event handling for AgentSession.

Mixed into AgentSession via multiple inheritance.  Methods here
reference attributes initialised by AgentSession.__init__ and call
other methods defined on the primary class.
"""

import asyncio
from datetime import UTC, datetime
import json
import logging
import time

from app.ai.claude_backend_turn_hold import (
    REDRIVE_INSTRUCTION,
    open_work_banner,
)
from app.ai.claude_backend_utils import (
    AGENT_DEBUG,
    get_next_seq,
)
from app.ai.stop_gates.listing_upload_gate import rollover_upload_markers
from app.ai.stream_event_trace import trace_stream_event
from app.ai.task_status_reconcile import reconcile_streaming_run_status
from app.database import async_session
from app.errors import STREAM_ERROR_MAP, categorize_error_text
from app.events.bus import event_bus
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.task_states import TaskStatus
from app.task_usage import meter_event
from app.telemetry_tasks import accumulate_run_usage
from app.todos import valid_todos

logger = logging.getLogger(__name__)

# How long to wait on a single `stdout.readline()` before issuing
# a stall-reaper heartbeat bump. The model can take minutes to
# generate a multi-KB tool input on slow providers; we need to
# distinguish that from a wedged subprocess. Picked at 60s so
# heartbeats land just inside `_maybe_bump_updated_at`'s 60s
# throttle. Module-level so tests can patch.
_READLINE_HEARTBEAT_TIMEOUT_S = 60.0


EMPTY_TURN_CONTINUE = (
    'Your last response ended before you answered or acted — the model '
    "stream stopped early. Continue with the user's last request."
)


class _StreamMixin:
    """Stdout/stderr readers and stream-json event handlers."""

    async def _stream_stderr(self):
        """Read stderr and log it in real-time for debugging."""
        try:
            while self._proc and self._proc.stderr:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode('utf-8', errors='replace').strip()
                if text:
                    logger.warning(
                        'Agent stderr [%s]: %s',
                        self.task_id[:8],
                        text,
                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(
                'Stderr reader error for %s: %s',
                self.task_id[:8],
                e,
            )

    async def _stream_output(self):
        """Read stdout line by line, parse stream-json events."""
        try:
            while self._proc and self._proc.stdout:
                # Timed readline serves two masters: the stall-
                # reaper heartbeat (a healthy provider can compose a
                # multi-KB tool input for 5+ min with ZERO deltas —
                # deepseek, task 73032910 — and must not be reaped
                # while the subprocess is alive) and the turn
                # watchdog (a pending soft linger needs finer wakeups
                # than 60s or a 5s quiet-tier close lands a minute
                # late — so shrink the timeout only while a close is
                # actually plausible).
                timeout_s = _READLINE_HEARTBEAT_TIMEOUT_S
                linger = self._turn_linger_seconds()
                if (
                    linger > 0
                    and self._turn_result_seen
                    and not self._input_closed
                ):
                    timeout_s = min(timeout_s, max(1.0, linger))
                try:
                    line = await asyncio.wait_for(
                        self._proc.stdout.readline(),
                        timeout=timeout_s,
                    )
                except TimeoutError:
                    if self._proc and self._proc.returncode is None:
                        # Subprocess still running. Give the turn
                        # watchdog its tick (soft linger / hard idle /
                        # post-close kill escalation), then bump the
                        # stall-reaper heartbeat — UNLESS stdin is
                        # already closed and the CLI is overstaying:
                        # keeping the heartbeat then would shield a
                        # wedged process from every backstop (the
                        # documented GLM stall-after-result).
                        await self._maybe_close_idle_turn()
                        if self._stdin_closed_at is None:
                            await self._maybe_bump_updated_at()
                        continue
                    # Subprocess died with no output — exit loop so
                    # the wait/cleanup path runs.
                    break
                if not line:
                    break
                text = line.decode('utf-8', errors='replace').strip()
                if not text:
                    continue
                # Any stdout traffic (events, control requests,
                # subagent activity) means the turn is alive — reset
                # the quiescence timer, and give the stall reaper the
                # same answer. Liveness used to be bumped per event
                # KIND (text/thinking/tool-input deltas, persisted
                # messages), so each kind the CLI added was invisible
                # until someone listed it: a model thinking for 3 min
                # streamed ~500 ``system/thinking_tokens`` events a
                # minute and was reaped as stalled. Throttled inside.
                self._last_activity_at = time.monotonic()
                await self._maybe_bump_updated_at()

                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    await self._emit_message('assistant', text)
                    continue

                etype = event.get('type', '')
                if etype == 'control_request':
                    await self._handle_control_request(event)
                else:
                    await self._handle_event(event)

            # stdout closed — close stdin through the shared path (so
            # the post-close clock is armed) and wait for the process
            # with the SAME bound the watchdog enforces. The old inline
            # close + unbounded ``_proc.wait()`` was the one place the
            # "stdin closed + grace + alive = escalate" contract was
            # not enforced: a CLI stalling after its final result wedged
            # the task forever (see _wait_process_exit_after_close).
            await self._close_stdin('stdout_eof', emit=False)
            return_code = await self._wait_process_exit_after_close()

            await self._apply_exit_code(return_code)

            # The LAST accepted result's error state is the turn's:
            # an intermediate error a later turn recovered from must
            # not ship FAILED, and a final error must. Forced errors
            # (circuit breaker, rc!=0 above) are already set and are
            # never cleared by a benign last result.
            if getattr(self, '_last_result_is_error', False):
                self._is_error_result = True

            # Fallback: categorize from result text if no
            # structured error was captured from stream events.
            if self._is_error_result and not self._error_category:
                self._error_category = categorize_error_text(self._result_text)

            # Plan-skip fallback: agent exited plan_then_execute
            # without calling ExitPlanMode, so _executing was
            # never set and _result_text stayed empty.  Use the
            # last result event text so plan-skip detection in
            # auto_run_task can see the result.
            if (
                not self._result_text
                and self._last_result_event
                and self.mode == 'plan_then_execute'
                and not self._plan_saved
                and return_code == 0
            ):
                self._result_text = self._last_result_event

            # Save result for execute and plan_then_execute
            if self._result_text and return_code == 0:
                await self._save_result(self._result_text)

            # Exited plan mode with no plan — benign or a dead run,
            # depending on whether execution ever started.
            if (
                self.mode == 'plan_then_execute'
                and not self._plan_saved
                and return_code == 0
            ):
                await self._settle_missing_plan()

            await event_bus.emit(
                'agent_done',
                {
                    'task_id': self.task_id,
                    'return_code': return_code,
                    'mode': self.mode,
                },
            )

        except asyncio.CancelledError:
            await event_bus.emit(
                'agent_done',
                {
                    'task_id': self.task_id,
                    'return_code': -1,
                    'interrupted': True,
                },
            )
        except Exception as e:
            logger.exception(
                'Agent stream error for task %s: %s',
                self.task_id,
                e,
            )
            await self._emit_message('system', f'Agent error: {e}')
        finally:
            # Snapshot any unanswered AskUserQuestion requests before
            # the handler coroutines get a chance to pop them. The
            # task runner reads `_last_pending_questions` after
            # `done` fires to decide between FAILED and WAITING —
            # `stop()` does the same capture on its own path, but
            # natural subprocess exit never calls `stop()`.
            if self._pending_questions and not self._last_pending_questions:
                self._last_pending_questions = dict(self._pending_questions)
            # End-of-session signal. Runs no matter how the reader
            # exits (normal, cancelled, exception). May also be set
            # from `stop()`'s early-return defensive path —
            # `asyncio.Event.set()` is idempotent, so extra calls are
            # harmless and waiters observe only the first.
            # `_wait_for_session_end` blocks on this instead of
            # polling `running` / `is_running`, closing the race
            # where a waiter could exit between the subprocess dying
            # and a retry registering a fresh session under the same
            # task_id.
            self.done.set()

    async def _handle_event(self, event: dict):
        """Route stream-json events to SSE."""
        etype = event.get('type', '')

        # Partial parts: one STREAM_DELTA line below, not a full dump.
        if AGENT_DEBUG and etype != 'stream_event':
            logger.info(
                'AGENT_DEBUG [%s] event=%s',
                self.task_id[:8],
                json.dumps(event, ensure_ascii=False)[:2000],
            )

        await meter_event(
            self.task_id, self._usage_meter, self._usage_provider, event
        )

        if etype in ('user', 'system'):
            self._track_async_agents(event)
        if etype == 'user':
            # Feed the loop breaker: a repeat call is only a loop if
            # its answer repeats too (claude_backend_utils.check_tool_loop).
            self._note_tool_results(event)

        if etype == 'assistant':
            # Capture error category from assistant error events
            # (e.g. {"error": "authentication_failed"})
            err_type = event.get('error')
            if err_type and not self._error_category:
                self._error_category = STREAM_ERROR_MAP.get(err_type, err_type)
            content = event.get('message', {}).get('content', [])
            for block in content:
                if block.get('type') == 'text':
                    if self._executing:
                        self._exec_phase_text_parts.append(block['text'])
                    await self._emit_message('assistant', block['text'])
                elif block.get('type') == 'thinking':
                    thinking = block.get('thinking', '')
                    if thinking:
                        await self._emit_message('thinking', thinking)
                elif block.get('type') == 'tool_use':
                    self._had_tool_use = True
                    tool_name = block.get('name', '')
                    tool_input = block.get('input', {})
                    # Q&A finalize signal (see task_status_reconcile):
                    # a real tool action after the last answer = progress
                    # toward done. Exclude AskUserQuestion (the question).
                    if 'AskUserQuestion' not in tool_name:
                        self._tool_use_since_answer = True
                    # Subagent-originated events carry the id of the
                    # Agent/Task tool_use that spawned them; the main
                    # agent's own events have it null. This is the
                    # spawn-tracking key for async agents.
                    parent_id = event.get('parent_tool_use_id')
                    if parent_id and block.get('id'):
                        self._subagent_tool_use_ids.add(block['id'])
                    if tool_name in ('Agent', 'Task') and not parent_id:
                        # Track main-agent spawns so the launch ack
                        # (tool_result) can flag async agents.
                        _bid = block.get('id')
                        if _bid:
                            self._agent_spawn_ids.add(_bid)
                    todos = (
                        valid_todos(tool_input.get('todos'))
                        if tool_name == 'TodoWrite'
                        else None
                    )
                    if todos is not None:  # None: not TodoWrite, or malformed
                        await event_bus.emit(
                            'task_todos',
                            {
                                'task_id': self.task_id,
                                'todos': todos,
                            },
                        )
                        await self._persist_todos(todos)
                    await self._emit_message(
                        'tool_use',
                        json.dumps(
                            {
                                'tool': tool_name,
                                'input': tool_input,
                            },
                            ensure_ascii=False,
                        ),
                    )

        elif etype == 'result':
            await accumulate_run_usage(self.task_id, event)
            text = event.get('result', '')
            is_error = event.get('is_error', False)
            # ── Open-work guard ─────────────────────────────────
            # A `result` while async subagents this turn launched are
            # still running is NOT the end of the turn: closing stdin here
            # would drop the approvals that work still needs. Re-drive
            # the agent on the live channel instead, within the budget
            # `_TurnHoldMixin` owns.
            if self._executing and not is_error:
                self._note_hold_turn_end()
                # A turn whose model response stopped with NO stop reason,
                # no text and no action is a stream that ended early, not
                # an answer. Observed in CI: "save this as a skill" got a
                # thinking block, stop_reason "", and the CLI closed the
                # turn as success with nothing done. Continue it once.
                if (
                    event.get('stop_reason') == ''
                    and not (text or '').strip()
                    and not self._empty_turn_continued
                ):
                    self._empty_turn_continued = True
                    await self.send_user_message(EMPTY_TURN_CONTINUE)
                    return
                hold_reason = self._async_agents_pending_reason()
                if hold_reason and not self._hold_exhausted():
                    self._note_hold_redrive()
                    await self._emit_message(
                        'agent_event',
                        json.dumps({
                            'event': 'open_work_redrive',
                            'iter': self._hold_redrive_count,
                        }),
                    )
                    await self.send_user_message(
                        REDRIVE_INSTRUCTION + hold_reason
                    )
                    return
                if hold_reason:
                    self._note_hold_failed_open()
                    await self._emit_message(
                        'agent_event',
                        json.dumps({
                            'event': 'open_work_failed_open',
                            'iter': self._hold_redrive_count,
                            'budget': self._hold_budget_note(),
                        }),
                    )
                    text = open_work_banner(hold_reason) + (text or '')
            if event.get('subtype') == 'success' and not is_error:
                self._agent_success = True
            # A result message is the "turn ended" signal downstream
            # consumers (UI, the e2e test poll, the follow-up agent
            # loop) depend on — and a process now hosts MANY turns
            # (initial turn, hold redrives, task-notification
            # continuations, injected follow-ups). Every ACCEPTED
            # execute-phase result is a turn boundary and gets its own
            # ``result`` card; ``_result_text`` tracks the LAST one
            # (the process's final word is the deliverable).
            #
            # Keep:
            #   - any non-empty text (real result)
            #   - the first result of a TURN even if empty — the only
            #     end-of-turn signal a chat-mode follow-up session
            #     ever produces, and weaker models (GLM-4.7, observed)
            #     sometimes emit their answer only in a ``thinking``
            #     block, leaving the result text empty.
            # Drop:
            #   - empty non-first results (no payload, no new turn)
            #   - empty planning-phase results
            is_turn_first_result = (
                self._executing and not self._turn_result_seen
            )
            should_emit = text or is_turn_first_result
            if should_emit and self._executing:
                self._last_result_event = text
                # Last-wins, assigned not sticky: a recovered turn
                # (error result, then a later success) must not ship
                # FAILED; the fold into ``_is_error_result`` happens
                # at process exit. Forced errors (circuit breaker,
                # rc!=0) set ``_is_error_result`` directly and are
                # never cleared here.
                self._last_result_is_error = bool(is_error)
                # Last non-empty wins; an empty turn-end signal (GLM
                # thinking-only turn) never clears an earlier turn's
                # deliverable.
                if text:
                    self._result_text = text
                self._turn_result_seen = True
                await self._emit_message('result', text)
            elif should_emit:
                # Planning-phase results are conversation, not a turn
                # boundary.
                self._last_result_event = text
                if is_error:
                    self._is_error_result = True
                await self._emit_message('assistant', text)
            # Turn termination. The CLI never exits on its own with
            # stdin open (spike-verified), so closing stdin IS the
            # terminator — the question is when. With a linger window
            # configured, the quiescence watchdog in
            # _maybe_close_idle_turn owns it (close only when gates
            # pass, no async subagents pending, and the stream has
            # been quiet); linger=0 preserves the legacy close-at-
            # result. The plan-skip close stays unconditional: an
            # agent that exited plan_then_execute without ExitPlanMode
            # deadlocks otherwise (we wait for stdout, CLI waits for
            # stdin).
            if not self._plan_saved and not self._executing:
                await self._close_stdin('plan_skip', emit=False)
            elif self._executing and self._turn_linger_seconds() <= 0:
                await self._close_stdin('legacy_result_close', emit=False)

        elif etype == 'stream_event':
            # Partial-message SSE part (CLAUDE_CODE_INCLUDE_PARTIAL_
            # MESSAGES=1). The any-line bump above already fed the
            # stall-reaper heartbeat; this branch only CONSUMES it (no
            # agent_event row per part, no frontend re-emit).
            if AGENT_DEBUG:
                trace_stream_event(self.task_id, event)

        elif etype == 'content_block_delta':
            delta = event.get('delta', {})
            dtype = delta.get('type')
            # Per-delta trace for diagnosing stream stalls (e.g.
            # whether ``input_json_delta`` is arriving during long
            # tool-input composition but failing to trigger the
            # heartbeat). Gated on AGENT_DEBUG so prod logs stay
            # clean.
            if AGENT_DEBUG:
                try:
                    if dtype == 'text_delta':
                        sz = len(delta.get('text', '') or '')
                    elif dtype == 'thinking_delta':
                        sz = len(delta.get('thinking', '') or '')
                    elif dtype == 'input_json_delta':
                        sz = len(delta.get('partial_json', '') or '')
                    else:
                        sz = -1
                    logger.info(
                        'STREAM_DELTA task=%s type=%s size=%d',
                        self.task_id[:8],
                        dtype,
                        sz,
                    )
                except Exception:
                    pass
            if dtype == 'text_delta':
                await self._emit_ephemeral('delta', delta.get('text', ''))
            elif dtype == 'thinking_delta':
                await self._emit_ephemeral(
                    'thinking_delta',
                    delta.get('thinking', ''),
                )
            elif dtype == 'input_json_delta':
                # Tool-input composition (e.g. a multi-KB Write call's
                # `content` arg). The partial JSON is not user-readable
                # so we don't surface it via SSE — but the model IS
                # making forward progress and the stall reaper must
                # see that. Without this bump, `Task.updated_at` goes
                # stale during long tool-input generation and the
                # reaper kills a healthy agent (see seq 188–194 in
                # task 73032910 — 5+ min Write of a 32KB audit report
                # was killed mid-generation). Throttled internally to
                # ≤1 DB write / 60s.
                await self._maybe_bump_updated_at()

        elif etype == 'system':
            sid = event.get('session_id')
            if sid:
                first = self.session_id is None
                self.session_id = sid
                if first and event.get('subtype') == 'init':
                    # New execution turn: move the prior turn's upload
                    # markers aside so this turn's upload gate is armed
                    # only by its own uploads. Once per session.
                    rollover_upload_markers(self.task_dir)
                # Persist task.session_id at the init event, not only
                # at end-of-stream. `--resume X` keeps the session id
                # as X (Claude Code does NOT mint a new id unless
                # --fork-session is passed, which we never do), so
                # this early write is either:
                #   - a no-op for --resume runs (value already in DB)
                #   - the authoritative write for fresh runs
                # The reason we can't rely on end-of-stream alone:
                # Claude Code sometimes stalls between emitting its
                # final `result` event and exiting the process (seen
                # with GLM-4.7), which defers `_save_result`
                # indefinitely and leaves task.session_id=NULL.
                # Skip when resume_session_id is set — task.session_id
                # is already that value, so the DB round-trip has
                # nothing to contribute.
                if (
                    first
                    and event.get('subtype') == 'init'
                    and not self.resume_session_id
                ):
                    await self._persist_session_id(sid)
            # Capture error category from api_retry events
            # (e.g. {"subtype": "api_retry", "error": "unknown"})
            if event.get('subtype') == 'api_retry':
                err_type = event.get('error')
                if err_type and not self._error_category:
                    self._error_category = STREAM_ERROR_MAP.get(
                        err_type, err_type
                    )

        else:
            await self._emit_message(
                'agent_event',
                json.dumps(event, ensure_ascii=False),
            )

    async def _save_design_plan(self, plan_text: str):
        """Save the plan to DB and emit SSE update."""
        try:
            async with async_session() as db:
                task = await db.get(Task, self.task_id)
                if task:
                    try:
                        history = json.loads(task.plan_history or '[]')
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(
                            'Malformed plan_history for task %s, resetting',
                            self.task_id,
                        )
                        history = []
                    history.append({
                        'version': len(history) + 1,
                        'content': plan_text,
                        'created_at': datetime.now(UTC).isoformat(),
                    })
                    task.plan_history = json.dumps(history, ensure_ascii=False)
                    task.plan = plan_text
                    task.status = TaskStatus.PLANNED
                    task.updated_at = datetime.now(UTC).isoformat()
                    # For --resume runs this is a no-op (value
                    # already in DB); for fresh runs it's the
                    # end-of-plan-phase checkpoint.
                    if self.session_id:
                        task.session_id = self.session_id
                    await db.commit()
                    await event_bus.emit(
                        'task_update',
                        {
                            'task_id': self.task_id,
                            'status': TaskStatus.PLANNED,
                            'plan': plan_text,
                            'error': None,
                        },
                    )
                    self._plan_saved = True
                    # Wake any waiter in `_wait_for_session_end`
                    # (interactive plan mode) so ownership of the task
                    # can pass to `execute_planned_task` while the
                    # session stays alive awaiting user approval.
                    self.plan_saved_event.set()
        except Exception as e:
            logger.error(
                'Failed to save design plan for task %s: %s',
                self.task_id,
                e,
            )

    async def _emit_ephemeral(self, role: str, content: str):
        """Emit SSE event only — no DB persistence.

        Use for streaming deltas (text, thinking) that are
        ephemeral and reconstructed from complete blocks.
        Also bumps ``Task.updated_at`` (throttled to once per
        60 s) so the stall reaper sees activity during long
        streaming responses.
        """
        await event_bus.emit(
            'task_message',
            {
                'task_id': self.task_id,
                'role': role,
                'content': content,
            },
        )
        await self._maybe_bump_updated_at()

    async def _maybe_bump_updated_at(self):
        """Throttled ``Task.updated_at`` bump (≤1 write / 60 s).

        Keeps the stall reaper happy during slow delta streaming
        without the cost of a DB write per chunk.
        """
        now = datetime.now(UTC)
        last = getattr(self, '_last_updated_at_bump', None)
        if last and (now - last).total_seconds() < 60:
            return
        self._last_updated_at_bump = now
        try:
            async with async_session() as db:
                task = await db.get(Task, self.task_id)
                if task:
                    task.updated_at = now.isoformat()
                    await db.commit()
        except Exception:
            logger.debug(
                'Failed to bump updated_at for %s',
                self.task_id[:8],
                exc_info=True,
            )

    async def _emit_message(self, role: str, content: str):
        """Emit a task message event via SSE and persist to DB."""
        await self._emit_ephemeral(role, content)
        reconciled_to: str | None = None
        try:
            async with self._emit_lock:
                async with async_session() as db:
                    seq = await get_next_seq(db, self.task_id)
                    msg = TaskMessage(
                        task_id=self.task_id,
                        role=role,
                        content=content,
                        seq=seq,
                    )
                    db.add(msg)
                    # Bump Task.updated_at in the same txn so
                    # stall_reaper's activity signal reflects reality
                    # — without this, `updated_at` only moves on
                    # lifecycle transitions (init / plan / result /
                    # first tool-use) and a busy agent gets reaped
                    # after 5 min despite dozens of messages.
                    task = await db.get(Task, self.task_id)
                    if task is not None:
                        task.updated_at = datetime.now(UTC).isoformat()
                        # Backstop invariant: a session that streams is
                        # live, so it can't still be QUEUED/PENDING.
                        # Reconcile once (see task_status_reconcile) so a
                        # missed on_start transition can't strand the
                        # task — which disables the input bar AND blinds
                        # the RUNNING-only stall reaper.
                        if not self._status_reconciled:
                            self._status_reconciled = True
                            reconciled_to = reconcile_streaming_run_status(task)
                            if reconciled_to:
                                logger.warning(
                                    'Task %s streamed while not RUNNING — '
                                    'reconciled to %s',
                                    self.task_id[:8],
                                    reconciled_to,
                                )
                    await db.commit()
            # Broadcast the corrected status OUTSIDE the txn/lock so
            # every client (and the reaper's next pass) leaves QUEUED.
            if reconciled_to is not None:
                await event_bus.emit(
                    'task_update',
                    {'task_id': self.task_id, 'status': reconciled_to},
                )
        except Exception as e:
            logger.warning(
                'Failed to persist message for task %s: %s',
                self.task_id,
                e,
            )

    async def _persist_todos(self, todos: list[dict]):
        """Save current todo list to the task record in DB."""
        try:
            async with async_session() as db:
                task = await db.get(Task, self.task_id)
                if task:
                    task.todos = json.dumps(todos, ensure_ascii=False)
                    await db.commit()
        except Exception:
            logger.debug(
                'Failed to persist todos for task %s',
                self.task_id,
                exc_info=True,
            )
