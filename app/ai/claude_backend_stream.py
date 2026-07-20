"""Stream reader + event handling for AgentSession.

Mixed into AgentSession via multiple inheritance.  Methods here
reference attributes initialised by AgentSession.__init__ and call
other methods defined on the primary class.
"""

import asyncio
from datetime import UTC, datetime
import json
import logging
import re

from app.ai.claude_backend_utils import (
    AGENT_DEBUG,
    REVIEW_REDRIVE_MAX,
    check_exec_review_status_for_stop,
    check_review_status_for_stop,
    get_next_seq,
    parse_wait_condition,
)
from app.ai.stop_gates.report_reviewer import (
    is_review_file_name,
    partial_banner,
    rollover_reviews,
)
from app.database import async_session
from app.errors import STREAM_ERROR_MAP, categorize_error_text
from app.events.bus import event_bus
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.task_states import TaskStatus

logger = logging.getLogger(__name__)

# Max times a session will re-drive the agent when a `result` arrives
# with a review gate still unsatisfied, before giving up and letting the
# turn end. Matches the reviewer loop's iter-5 `incomplete` ceiling so a
# gate the agent genuinely cannot satisfy still terminates.

# How long to wait on a single `stdout.readline()` before issuing
# a stall-reaper heartbeat bump. The model can take minutes to
# generate a multi-KB tool input on slow providers; we need to
# distinguish that from a wedged subprocess. Picked at 60s so
# heartbeats land just inside `_maybe_bump_updated_at`'s 60s
# throttle. Module-level so tests can patch.
_READLINE_HEARTBEAT_TIMEOUT_S = 60.0


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
                # Wrap readline in a timeout so we can bump the
                # stall-reaper heartbeat while the subprocess is
                # legitimately waiting on a slow upstream response.
                #
                # Some provider transports (observed with deepseek
                # via claude-code) emit ZERO stream deltas during
                # tool-input composition: a multi-KB Write call's
                # `content` arg is generated server-side and the
                # CLI only forwards the complete tool_use at the
                # end. Composition can take 5+ min; with no events
                # arriving, `Task.updated_at` goes stale and the
                # stall reaper kills a healthy agent mid-generation
                # (see task 73032910 — heartbeat dies during 32KB
                # audit-report Write). Subprocess-alive IS the
                # signal the agent is still healthy.
                try:
                    line = await asyncio.wait_for(
                        self._proc.stdout.readline(),
                        timeout=_READLINE_HEARTBEAT_TIMEOUT_S,
                    )
                except TimeoutError:
                    if self._proc and self._proc.returncode is None:
                        # Subprocess still running → upstream is
                        # generating, just slowly. Bump heartbeat
                        # (internally throttled to ≤1 DB write/60s,
                        # so this is cheap) and keep waiting.
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

            # stdout closed — close stdin
            if self._proc and self._proc.stdin:
                try:
                    self._proc.stdin.close()
                except Exception:
                    pass
            self._input_closed = True

            # Process finished
            return_code = await self._proc.wait() if self._proc else -1

            if return_code != 0:
                self._is_error_result = True
                await self._emit_message(
                    'system',
                    f'Agent exited with code {return_code}',
                )

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

            # plan_then_execute exited without plan → fail
            if (
                self.mode == 'plan_then_execute'
                and not self._plan_saved
                and return_code == 0
            ):
                logger.warning(
                    'plan_then_execute ended without ExitPlanMode for task %s',
                    self.task_id,
                )

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

    def _async_agents_pending_reason(self) -> str | None:
        """Deny reason while background subagents are still running.

        The CLI emits its ``result`` (end-of-turn) as soon as the MAIN
        agent stops — async subagents launched with the Agent tool keep
        running. Ending the turn then is always wrong: closing stdin
        default-denies every remaining tool call the subagent makes
        (observed live — a DoD reviewer died mid-verification while the
        shipped result claimed it was "running in the background").
        A turn ends only when its subagents have.
        """
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

    async def _handle_event(self, event: dict):
        """Route stream-json events to SSE."""
        etype = event.get('type', '')

        if AGENT_DEBUG:
            logger.info(
                'AGENT_DEBUG [%s] event=%s',
                self.task_id[:8],
                json.dumps(event, ensure_ascii=False)[:2000],
            )

        if etype == 'user':
            self._track_async_agents(event)

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
                    # Subagent-originated events carry the id of the
                    # Agent/Task tool_use that spawned them; the main
                    # agent's own events have it null. This is the
                    # authorship signal for review verdicts and the
                    # spawn-tracking key for async agents.
                    parent_id = event.get('parent_tool_use_id')
                    # A DoD/review verdict only counts when a real reviewer
                    # SUBAGENT produced it. The main agent can't fabricate
                    # this tool_use it never made, so flag a review-ish
                    # Agent/Task spawn; the Stop gate rejects a self-written
                    # verdict when this stayed False.
                    if tool_name in ('Agent', 'Task'):
                        if not parent_id:
                            # Track main-agent spawns so the launch ack
                            # (tool_result) can flag async agents.
                            _bid = block.get('id')
                            if _bid:
                                self._agent_spawn_ids.add(_bid)
                        _blob = json.dumps(
                            tool_input, ensure_ascii=False
                        ).lower()
                        if any(
                            k in _blob
                            for k in ('review', 'verif', 'dod', 'audit')
                        ):
                            self._review_subagent_ran = True
                    # Review-verdict authorship: record WHO wrote each
                    # review-named .md this turn. A file last written by
                    # the main agent can never satisfy the review gates
                    # (see report_reviewer.reviewer_verdict).
                    if tool_name in ('Write', 'Edit', 'MultiEdit'):
                        _fp = str(tool_input.get('file_path', ''))
                        _base = _fp.rsplit('/', 1)[-1]
                        if is_review_file_name(_base):
                            self._review_file_writers[_base] = (
                                'subagent' if parent_id else 'main'
                            )
                    if tool_name == 'TodoWrite':
                        todos = tool_input.get('todos', [])
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
            text = event.get('result', '')
            is_error = event.get('is_error', False)
            # ── Review-gate guard (project-wide) ───────────────
            # A `result` while a server-mandated review gate is still
            # unsatisfied is NOT the end of the turn. The DoD/exec
            # review loop runs AFTER the deliverable — it spawns a
            # reviewer subagent and fixes the deliverable in place, and
            # every one of those tool calls needs the approval channel.
            # If we close stdin here (below), those approvals get
            # dropped and the CLI default-denies them, so the review is
            # abandoned and a placeholder result ships as "completed".
            # Instead: keep the channel open and re-drive the agent with
            # the gate's own deny reason (the same one the Stop hook
            # uses), so it actually satisfies the gate on a LIVE channel.
            # Bounded so an unsatisfiable gate still terminates. Uses the
            # shared gate helpers, so this covers every review gate, not
            # one task type.
            if self._executing and not is_error:
                gate_reason = (
                    self._async_agents_pending_reason()
                    or check_review_status_for_stop(
                        self.task_dir,
                        subagent_ran=getattr(
                            self, '_review_subagent_ran', False
                        ),
                        review_writers=getattr(
                            self, '_review_file_writers', None
                        ),
                    )
                    or check_exec_review_status_for_stop(
                        self.task_dir,
                        review_writers=getattr(
                            self, '_review_file_writers', None
                        ),
                    )
                )
                if (
                    gate_reason
                    and self._review_redrive_count < REVIEW_REDRIVE_MAX
                ):
                    self._review_redrive_count += 1
                    logger.warning(
                        'Review gate unsatisfied at result for %s — '
                        're-driving agent (%d/%d) instead of closing the '
                        'control channel',
                        self.task_id[:8],
                        self._review_redrive_count,
                        REVIEW_REDRIVE_MAX,
                    )
                    await self._emit_message(
                        'agent_event',
                        json.dumps({
                            'event': 'review_gate_redrive',
                            'iter': self._review_redrive_count,
                        }),
                    )
                    await self.send_user_message(
                        'You cannot finish yet — a required review gate '
                        'is not satisfied. Do NOT just re-answer; act on '
                        'this and then finish:\n\n' + gate_reason
                    )
                    return
                if gate_reason:
                    # Re-drive budget exhausted → fail OPEN, never a
                    # tool-denial limbo: the Stop hook stands down (see
                    # _deny_stop_if_review_unsatisfied) and the result
                    # ships banner-marked UNVERIFIED so nobody mistakes
                    # it for a reviewed deliverable.
                    logger.warning(
                        'Review gate still unsatisfied after %d re-drives '
                        'for %s — failing open with UNVERIFIED banner',
                        REVIEW_REDRIVE_MAX,
                        self.task_id[:8],
                    )
                    text = partial_banner() + (text or '')
            if event.get('subtype') == 'success' and not is_error:
                self._agent_success = True
            # ``None`` means Stop-hook reflection never fired this
            # turn; '' means it fired but the agent had no pre-
            # reflection text — in that case the post-reflection
            # text is reflection content and must NOT become the
            # user-facing result. Distinguish via ``is not None``.
            reflection_suppressed = False
            if self._pre_reflection_result is not None:
                if not is_error:
                    text = self._pre_reflection_result
                reflection_suppressed = self._pre_reflection_result == ''
                self._pre_reflection_result = None
            # The result event existence is the "turn ended" signal
            # that downstream consumers (UI, the e2e test poll, the
            # follow-up agent loop) depend on. Drop it only when it
            # is NOT the canonical end-of-turn for this session.
            #
            # Keep:
            #   - any non-empty text (real result)
            #   - reflection_suppressed (text intentionally cleared)
            #   - first execute-phase result, even if empty — this is
            #     the only end-of-turn signal a chat-mode follow-up
            #     session ever produces, and weaker models (GLM-4.7,
            #     observed) sometimes emit their answer only in a
            #     ``thinking`` block, leaving the result text empty.
            # Drop:
            #   - empty subsequent results (no useful payload, and the
            #     first result already carried the end-of-turn signal)
            #   - empty planning-phase results
            is_first_execute_result = (
                self._executing and not self._first_result_emitted
            )
            should_emit = (
                text or reflection_suppressed or is_first_execute_result
            )
            if should_emit:
                self._last_result_event = text
                if is_error:
                    self._is_error_result = True
                # First execution-phase result → green card;
                # subsequent results → regular assistant message
                if is_first_execute_result:
                    self._result_text = text
                    await self._emit_message('result', text)
                    self._first_result_emitted = True
                else:
                    logger.debug(
                        'Demoting extra result to assistant for task %s',
                        self.task_id[:8],
                    )
                    await self._emit_message('assistant', text)
            # Close stdin when execution finishes so CLI exits.
            # Skip during planning phase — multi-turn feedback
            # needs the session alive.  Also close if the agent
            # exited plan_then_execute without ever calling
            # ExitPlanMode (no plan saved) to avoid a deadlock
            # where we wait for stdout and the CLI waits for stdin.
            if self._executing or not self._plan_saved:
                if self._proc and self._proc.stdin:
                    try:
                        self._proc.stdin.close()
                    except Exception:
                        pass
                self._input_closed = True

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
                    # New execution turn: move the prior turn's review
                    # verdicts aside so THIS turn is reviewed on a clean
                    # slate (a follow-up can't inherit an earlier turn's
                    # verdict/iter count). Once per session — guarded by
                    # `first` so a stray later init can't wipe reviews
                    # written during this turn. See rollover_reviews.
                    rollover_reviews(self.task_dir)
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

    async def _persist_session_id(self, session_id: str):
        """Write task.session_id from the init event.

        Called only for fresh runs (no --resume): writes the brand-
        new id Claude Code just minted. The alternative is waiting
        for `_save_result` at end-of-stream, which doesn't run if
        Claude Code stalls after emitting its final result event.
        """
        try:
            async with async_session() as db:
                task = await db.get(Task, self.task_id)
                if task:
                    task.session_id = session_id
                    task.updated_at = datetime.now(UTC).isoformat()
                    await db.commit()
                    logger.info(
                        'Persisted session_id=%s for task %s (early, on init)',
                        session_id,
                        self.task_id[:8],
                    )
                else:
                    logger.warning(
                        'Cannot persist session_id for %s: task not found',
                        self.task_id[:8],
                    )
        except Exception:
            logger.warning(
                'Failed to persist session_id early for %s',
                self.task_id[:8],
                exc_info=True,
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
                    await db.commit()
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
