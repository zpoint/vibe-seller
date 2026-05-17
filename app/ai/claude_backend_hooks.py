"""Hook/control-request handlers for AgentSession.

Mixed into AgentSession via multiple inheritance.  Methods here
reference attributes initialised by AgentSession.__init__ and call
other methods defined on the primary class.
"""

import asyncio
from datetime import UTC, datetime
import json
import logging
from pathlib import Path

from app.ai.bash_safety import (
    check_catalog_first,
    check_catalog_first_tool_args,
    check_dangerous_kill,
    is_catalog_path,
)
from app.ai.claude_backend_utils import (
    AGENT_DEBUG,
    AUTO_APPROVE_CALLBACK,
    STOP_REFLECTION_CALLBACK,
    TOOL_APPROVAL_CALLBACK,
    check_skill_prereqs,
    find_skill_md,
)
from app.database import async_session
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.task import Task
from app.plan_states import PlanStatus
from app.prompts import (
    REFLECTION_PROMPT,
    SCHEDULED_WATERMARK_PROMPT,
    render_prompt,
)
from app.task_states import TaskStatus
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)


class _HookMixin:
    """Control-protocol and hook-callback handlers for AgentSession."""

    async def _send_control_response(
        self,
        request_id: str,
        behavior: str,
        updated_input: dict | None = None,
        updated_permissions: list[dict] | None = None,
        message: str | None = None,
    ):
        """Write a ControlResponse JSON to claude's stdin."""
        response: dict = {
            'type': 'control_response',
            'response': {
                'subtype': 'success',
                'request_id': request_id,
                'response': {
                    'behavior': behavior,
                },
            },
        }
        if updated_input is not None:
            response['response']['response']['updatedInput'] = updated_input
        if updated_permissions is not None:
            response['response']['response']['updatedPermissions'] = (
                updated_permissions
            )
        if message is not None:
            response['response']['response']['message'] = message
        await self._send_stdin(response, label='control_response')

    async def _send_hook_response(self, request_id: str, hook_output: dict):
        """Send a hook callback response to claude's stdin."""
        response: dict = {
            'type': 'control_response',
            'response': {
                'subtype': 'success',
                'request_id': request_id,
                'response': hook_output,
            },
        }
        await self._send_stdin(response, label='hook_response')

    async def _handle_control_request(self, msg: dict):
        """Handle a control_request from claude's stdout.

        Control flow for plan mode:
        1. ExitPlanMode → HookCallback(tool_approval) → respond
           'ask' → forwards to CanUseTool
        2. CanUseTool(ExitPlanMode) → capture plan, approve with
           updatedPermissions: SetMode → bypassPermissions
        """
        if AGENT_DEBUG:
            logger.info(
                'AGENT_DEBUG [%s] control_request=%s',
                self.task_id[:8],
                json.dumps(msg, ensure_ascii=False)[:2000],
            )
        request_id = msg.get('request_id', '')
        request = msg.get('request', {})
        subtype = request.get('subtype', '')

        # Route based on control request subtype
        if subtype == 'hook_callback':
            await self._handle_hook_callback(request_id, request)
        elif subtype == 'can_use_tool':
            await self._handle_can_use_tool(request_id, request)
        else:
            # Legacy format — determine from fields
            tool_name = request.get('tool_name', '')
            callback_id = request.get('callback_id', '')
            tool_input = request.get('input', {})

            if callback_id:
                await self._handle_hook_callback(request_id, request)
            elif tool_name == 'AskUserQuestion':
                await self._handle_ask_user_question(request_id, tool_input)
            elif tool_name == 'ExitPlanMode':
                if self.mode == 'auto':
                    # Auto mode: ExitPlanMode is irrelevant
                    await self._send_control_response(request_id, 'allow')
                else:
                    await self._handle_exit_plan_mode_can_use_tool(
                        request_id, tool_input
                    )
            else:
                await self._send_control_response(request_id, 'allow')

    async def _handle_hook_callback(self, request_id: str, request: dict):
        """Handle a HookCallback control request."""
        callback_id = request.get('callback_id', '')
        tool_input = request.get('input', {})

        if callback_id == TOOL_APPROVAL_CALLBACK:
            # Forward to CanUseTool for interactive handling.
            # In plan mode this is ExitPlanMode; in auto mode
            # this is AskUserQuestion.
            inner_tool = tool_input.get('tool_name', '')
            reason = (
                'Forwarding to question handler'
                if inner_tool == 'AskUserQuestion'
                else 'Forwarding to plan approval'
            )
            await self._send_hook_response(
                request_id,
                {
                    'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse',
                        'permissionDecision': 'ask',
                        'permissionDecisionReason': reason,
                    },
                },
            )
        elif callback_id == AUTO_APPROVE_CALLBACK:
            # Circuit breaker: detect degenerate tool loops
            inner_name = tool_input.get('tool_name', '')
            inner_input = tool_input.get('tool_input', {})
            # Concurrent-task safety: reject unscoped pkill/killall
            # before they can hit sibling tasks' processes.
            if inner_name == 'Bash':
                cmd = inner_input.get('command', '')
                deny_reason = check_dangerous_kill(cmd)
                if deny_reason:
                    logger.warning(
                        'Bash safety: agent %s tried unscoped kill: %r',
                        self.task_id[:8],
                        cmd[:200],
                    )
                    await self._send_hook_response(
                        request_id,
                        {
                            'hookSpecificOutput': {
                                'hookEventName': 'PreToolUse',
                                'permissionDecision': 'deny',
                                'permissionDecisionReason': deny_reason,
                            },
                        },
                    )
                    return
                # Catalog-first guard: deny filesystem search of
                # knowledge/ or stores/ until the agent has read a
                # catalog file this session. The deny reason tells
                # the agent which catalog to read.
                deny_reason = check_catalog_first(cmd, self._catalog_read)
                if deny_reason:
                    logger.info(
                        'Catalog-first: agent %s tried %r before reading catalog',
                        self.task_id[:8],
                        cmd[:120],
                    )
                    await self._send_hook_response(
                        request_id,
                        {
                            'hookSpecificOutput': {
                                'hookEventName': 'PreToolUse',
                                'permissionDecision': 'deny',
                                'permissionDecisionReason': deny_reason,
                            },
                        },
                    )
                    return
            # Catalog-first guard for the Glob/Grep built-in tools.
            # Same intent as the Bash branch above: agents denied at
            # the Bash layer pivot to Glob/Grep on the same trees
            # unless we close that escape hatch too.
            if inner_name in ('Glob', 'Grep'):
                deny_reason = check_catalog_first_tool_args(
                    inner_input, self._catalog_read
                )
                if deny_reason:
                    logger.info(
                        'Catalog-first: agent %s tried %s(%r) before reading catalog',
                        self.task_id[:8],
                        inner_name,
                        {
                            k: v
                            for k, v in inner_input.items()
                            if k in ('path', 'pattern')
                        },
                    )
                    await self._send_hook_response(
                        request_id,
                        {
                            'hookSpecificOutput': {
                                'hookEventName': 'PreToolUse',
                                'permissionDecision': 'deny',
                                'permissionDecisionReason': deny_reason,
                            },
                        },
                    )
                    return
            # Catalog-read tracker: flip the catalog-first guard off
            # once the agent issues a Read against any CATALOG.md.
            # Done in the PreToolUse hook (not on result) so a
            # follow-up Bash in the same assistant turn already sees
            # the updated state — the catalog content reaches the
            # model in the same tool_result that this Read returns.
            # The path must both LOOK like a catalog (shape match)
            # AND actually exist on disk — otherwise an agent could
            # bypass the catalog-first guard by reading a fake
            # ``stores/<bogus>/CATALOG.md`` that doesn't exist,
            # which would still flip the flag and re-enable Bash
            # search even though no catalog content was loaded.
            if inner_name == 'Read':
                path = inner_input.get('file_path', '')
                if is_catalog_path(path) and Path(path).is_file():
                    self._catalog_read = True
            if self._check_tool_loop(inner_name, inner_input):
                logger.warning(
                    'Circuit breaker: agent %s stuck in loop (%s), stopping',
                    self.task_id[:8],
                    inner_name,
                )
                await self._send_hook_response(
                    request_id,
                    {
                        'hookSpecificOutput': {
                            'hookEventName': 'PreToolUse',
                            'permissionDecision': 'deny',
                            'permissionDecisionReason': (
                                'Circuit breaker: agent stuck'
                                ' in degenerate tool call loop'
                            ),
                        },
                    },
                )
                self._is_error_result = True
                self._error_category = 'agent_loop'
                asyncio.create_task(self.stop())
                return
            # Skill prerequisite enforcement: when a skill declares
            # ``requires: [X]`` in its frontmatter, the agent must
            # have loaded X first this session. Same shape as
            # Claude Code's Read-before-Write rule — a deny with a
            # clear retry message turns a prose contract into a
            # mechanism. See parse_skill_requires for the format.
            if inner_name == 'Skill':
                skill_name = inner_input.get('skill', '')
                deny = self._check_skill_prereqs(skill_name)
                if deny:
                    logger.info(
                        'Skill prereq: agent %s tried %r without %s',
                        self.task_id[:8],
                        skill_name,
                        deny,
                    )
                    await self._send_hook_response(
                        request_id,
                        {
                            'hookSpecificOutput': {
                                'hookEventName': 'PreToolUse',
                                'permissionDecision': 'deny',
                                'permissionDecisionReason': deny,
                            },
                        },
                    )
                    return
                # Only track skills we actually ship (a SKILL.md
                # exists in the task workspace or global skills dir).
                # Without this gate the agent could "satisfy" a
                # prereq by calling ``Skill('<arbitrary-name>')`` —
                # the hook would add the name to ``_loaded_skills``
                # and let a dependent skill through even though no
                # actual prerequisite content was loaded. Limiting
                # the set to shipped skills closes that bypass.
                if (
                    skill_name
                    and find_skill_md(
                        self.task_dir or VIBE_SELLER_DIR, skill_name
                    )
                    is not None
                ):
                    self._loaded_skills.add(skill_name)
            # Auto-approve all other tools
            await self._send_hook_response(
                request_id,
                {
                    'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse',
                        'permissionDecision': 'allow',
                        'permissionDecisionReason': (
                            'Auto-approved by backend'
                        ),
                    },
                },
            )
        elif callback_id == STOP_REFLECTION_CALLBACK:
            # Stop hook: block to force reflection, approve on retry.
            # Claude Code sends stop_hook_active=true on the retry
            # after a block, preventing infinite loops.
            if tool_input.get('stop_hook_active'):
                await self._send_hook_response(
                    request_id,
                    {'decision': 'approve'},
                )
            else:
                # Save the real task result before reflection
                # overwrites it in the result event. Persist to
                # DB immediately so it's visible even if the agent
                # calls set_task_result (via MCP) during reflection
                # with a reflection-derived summary instead of the
                # real task outcome.
                #
                # Prefer the full exec-phase transcript — agents often
                # emit the full report in one message and a brief
                # "Done." closing in a second message. The Stop-hook
                # payload's ``last_assistant_message`` only contains
                # the closing, which drops the real content. Fall
                # back to ``last_assistant_message`` if we somehow
                # have no accumulated text.
                pre = '\n\n'.join(p for p in self._exec_phase_text_parts if p)
                if not pre:
                    pre = tool_input.get('last_assistant_message', '')
                if pre:
                    self._pre_reflection_result = pre
                    await self._save_result(pre)
                reason = render_prompt(
                    REFLECTION_PROMPT,
                    store_slug=self.store_slug,
                )
                if await self._is_scheduled_task():
                    reason += '\n\n' + SCHEDULED_WATERMARK_PROMPT
                await self._send_hook_response(
                    request_id,
                    {
                        'decision': 'block',
                        'reason': reason,
                    },
                )
        elif callback_id == 'tool_approval':
            # Legacy format — extract inner tool info
            inner_tool = tool_input.get('tool_name', '')
            inner_input = tool_input.get('input', {})
            if inner_tool == 'AskUserQuestion':
                await self._handle_ask_user_question(request_id, inner_input)
            else:
                await self._send_control_response(request_id, 'allow')
        else:
            # Unknown callback — auto-approve
            await self._send_hook_response(
                request_id,
                {
                    'hookSpecificOutput': {
                        'hookEventName': 'PreToolUse',
                        'permissionDecision': 'allow',
                        'permissionDecisionReason': (
                            'Auto-approved by backend'
                        ),
                    },
                },
            )

    def _check_skill_prereqs(self, skill_name: str) -> str | None:
        """Adapter that forwards to :func:`check_skill_prereqs` with
        this session's state. Kept as a thin method so the existing
        hook call site stays simple; the actual logic lives in
        ``claude_backend_utils`` so this file stays under the 800
        line cap (enforced by the line-limit pre-commit hook).
        """
        return check_skill_prereqs(
            skill_name,
            self.task_dir or VIBE_SELLER_DIR,
            self._loaded_skills,
            self.task_id[:8],
        )

    async def _handle_can_use_tool(self, request_id: str, request: dict):
        """Handle a CanUseTool control request."""
        tool_name = request.get('tool_name', '')
        tool_input = request.get('input', {})

        if tool_name == 'ExitPlanMode':
            if self.mode == 'auto':
                # Auto mode: ExitPlanMode is irrelevant, allow
                # silently without saving plan or changing status.
                await self._send_control_response(request_id, 'allow')
                return
            await self._handle_exit_plan_mode_can_use_tool(
                request_id, tool_input
            )
        elif tool_name == 'AskUserQuestion':
            await self._handle_ask_user_question(request_id, tool_input)
        else:
            # Circuit breaker check
            if self._check_tool_loop(tool_name, tool_input):
                logger.warning(
                    'Circuit breaker: agent %s stuck in loop (%s), stopping',
                    self.task_id[:8],
                    tool_name,
                )
                await self._send_control_response(
                    request_id,
                    'deny',
                    message=(
                        'Circuit breaker: agent stuck in'
                        ' degenerate tool call loop'
                    ),
                )
                self._is_error_result = True
                self._error_category = 'agent_loop'
                asyncio.create_task(self.stop())
                return
            # Auto-approve — include updatedInput so Claude Code's
            # z.union schema unambiguously matches the allow branch.
            await self._send_control_response(
                request_id, 'allow', updated_input=tool_input
            )

    async def _handle_exit_plan_mode_can_use_tool(
        self, request_id: str, tool_input: dict
    ):
        """Handle ExitPlanMode via CanUseTool — save plan,
        optionally wait for approval, then approve with SetMode.
        """
        plan_text = tool_input.get('plan', '')
        await self._save_design_plan(plan_text)
        self._pending_plan_request_id = request_id
        self._pending_plan_tool_input = tool_input

        # Structural validation BEFORE auto-approve / manual review:
        # if the plan violates fanout-schedule constraints (e.g.,
        # embeds an orchestrator that calls vibe_seller_create_task
        # — which would make every per-store fire recursively spawn
        # more children), reject via ControlResponse deny so the
        # agent re-plans in the same session. The prompt block in
        # task_runner.build_system_extra already tells the agent
        # about this; the validator is the backstop.
        violation = await self._validate_fanout_plan_text(plan_text)
        if violation:
            logger.info(
                'Plan-only validation rejected plan for task %s: %s',
                self.task_id,
                violation,
            )
            await self._send_control_response(
                request_id,
                'deny',
                message=(
                    'The tool use was rejected because the plan'
                    ' violates a schedule constraint. Revise the'
                    ' plan and call ExitPlanMode again. Reason: ' + violation
                ),
            )
            # Reset for the next ExitPlanMode call.
            self._plan_approval_event = asyncio.Event()
            return

        if self.auto_approve_plan:
            # Auto-approve — transition straight to execution
            await self._approve_plan_request(request_id, tool_input)
        else:
            # Wait for user approval/rejection
            self._plan_approval_event.clear()
            await self._plan_approval_event.wait()

            if not self.running or self._stopping:
                return

            if self._plan_approved:
                await self._approve_plan_request(request_id, tool_input)
            else:
                # Deny — agent stays in plan mode to re-plan
                feedback = getattr(self, '_rejection_feedback', '')
                if feedback:
                    user_said = feedback
                else:
                    user_said = 'Please revise the plan based on feedback.'
                await self._send_control_response(
                    request_id,
                    'deny',
                    message=(
                        "The user doesn't want to proceed"
                        ' with this tool use. The tool use'
                        ' was rejected (eg. if it was a file'
                        ' edit, the new_string was NOT'
                        ' written to the file). To tell you'
                        ' how to proceed, the user said: ' + user_said
                    ),
                )
                self._rejection_feedback = ''
                # Reset for next ExitPlanMode call
                self._plan_approval_event = asyncio.Event()

    async def _approve_plan_request(
        self, request_id: str, tool_input: dict | None = None
    ):
        """Send allow + SetMode(bypassPermissions) for plan
        and transition task status to RUNNING.

        For plan-only tasks owned by a Schedule (``is_plan_only=True``),
        the flow is different: the approved plan is persisted to the
        owning Schedule (``plan_status='ready'``, ``plan_version += 1``),
        the task terminates at COMPLETED (no execution), and the agent
        receives a ``deny`` control response so it halts rather than
        entering bypass mode.
        """
        self._executing = True

        # Branch: is this a plan-only task? Use the latest task.plan
        # from DB (in case user edited via review-plan UI before
        # approving) rather than tool_input.plan.
        is_plan_only = False
        schedule_id: str | None = None
        latest_plan_text: str | None = None
        try:
            async with async_session() as db:
                task = await db.get(Task, self.task_id)
                if task:
                    is_plan_only = bool(task.is_plan_only)
                    schedule_id = task.schedule_id
                    latest_plan_text = task.plan
        except Exception:
            logger.exception(
                'Failed to load task %s for plan approval',
                self.task_id,
            )

        if is_plan_only and schedule_id:
            await self._commit_plan_only_approval(
                request_id=request_id,
                schedule_id=schedule_id,
                plan_text=latest_plan_text or '',
            )
            return

        # Regular plan-mode execution: PLANNED → RUNNING
        try:
            async with async_session() as db:
                task = await db.get(Task, self.task_id)
                if task and task.status in (
                    TaskStatus.PLANNED,
                    TaskStatus.QUEUED,
                ):
                    task.status = TaskStatus.RUNNING
                    task.started_at = (
                        task.started_at or datetime.now(UTC).isoformat()
                    )
                    task.updated_at = datetime.now(UTC).isoformat()
                    await db.commit()
                    await event_bus.emit(
                        'task_update',
                        {
                            'task_id': self.task_id,
                            'status': TaskStatus.RUNNING,
                        },
                    )
        except Exception as e:
            logger.error(
                'Failed to transition task %s to RUNNING: %s',
                self.task_id,
                e,
            )

        await self._send_control_response(
            request_id,
            'allow',
            updated_input=tool_input,
            updated_permissions=[
                {
                    'type': 'setMode',
                    'mode': 'bypassPermissions',
                    'destination': 'session',
                }
            ],
        )

    # Substrings that invalidate a fanout-schedule plan. The schedule
    # itself fans out per-store each fire, so if the plan instructs
    # the agent to also call ``vibe_seller_create_task`` or reason
    # about ``parent_task_id``, every fire's per-store agent would
    # recursively spawn more children. Matched case-insensitively
    # against the full plan text.
    _FANOUT_FORBIDDEN_PATTERNS = (
        ('vibe_seller_create_task', 'calls the sub-task MCP tool'),
        ('parent_task_id', 'designs a parent/sub-task hierarchy'),
    )

    async def _validate_fanout_plan_text(self, plan_text: str) -> str | None:
        """Return a human-readable reason string if the plan text is
        invalid for a fanout-mode plan-only Task, else None.

        Only runs for ``Task.is_plan_only=True`` whose owning
        ``Schedule.phase_mode='fanout'``. Other cases (single-mode
        schedules, standalone interactive plan-mode tasks) are not
        subject to this check — orchestration is legitimate for
        them.
        """
        try:
            async with async_session() as db:
                task = await db.get(Task, self.task_id)
                if not task or not task.is_plan_only or not task.schedule_id:
                    return None
                sched = await db.get(Schedule, task.schedule_id)
                if not sched or sched.phase_mode != 'fanout':
                    return None
        except Exception:
            logger.debug(
                'Fanout-plan validator could not load context for %s',
                self.task_id,
                exc_info=True,
            )
            return None

        haystack = (plan_text or '').lower()
        for needle, description in self._FANOUT_FORBIDDEN_PATTERNS:
            if needle.lower() in haystack:
                return (
                    f'the plan {description}'
                    f' (found {needle!r}), but this schedule is a'
                    ' fanout schedule — the scheduler already creates'
                    ' one per-store Task per fire and runs the plan'
                    ' once per store. Remove the orchestrator step'
                    ' and describe what a single store-bound agent'
                    ' should do with the per-store L3 catalog.'
                )
        return None

    async def _commit_plan_only_approval(
        self,
        *,
        request_id: str,
        schedule_id: str,
        plan_text: str,
    ):
        """Commit an approved plan-only Task.

        Runs in a single DB transaction so a crash between the Task
        terminal write and the Schedule plan write cannot leave a
        half-applied state. Transitions the Task to COMPLETED, writes
        ``Schedule.plan``, bumps ``plan_version``, sets ``plan_status``
        to ``'ready'``, clears ``current_planning_task_id`` and
        ``plan_error``.  Then responds ``deny`` to the ExitPlanMode
        control request so the agent halts instead of entering bypass
        mode.
        """
        committed = False
        try:
            async with async_session() as db:
                task = await db.get(Task, self.task_id)
                sched = await db.get(Schedule, schedule_id)
                if not task or not sched:
                    logger.error(
                        'Plan-only approval: task or schedule missing'
                        ' (task=%s schedule=%s)',
                        self.task_id,
                        schedule_id,
                    )
                else:
                    # PLANNED → COMPLETED (added to state machine for
                    # this exact flow). Guard on status to tolerate a
                    # concurrent external transition.
                    if task.status in (
                        TaskStatus.PLANNED,
                        TaskStatus.DESIGNING,
                        TaskStatus.QUEUED,
                    ):
                        now = datetime.now(UTC).isoformat()
                        task.status = TaskStatus.COMPLETED
                        task.completed_at = now
                        task.updated_at = now
                        sched.plan = plan_text
                        sched.plan_status = PlanStatus.READY.value
                        sched.plan_version = (sched.plan_version or 0) + 1
                        sched.plan_error = None
                        sched.current_planning_task_id = None
                        sched.updated_at = now
                        await db.commit()
                        committed = True
                        await event_bus.emit(
                            'task_update',
                            {
                                'task_id': self.task_id,
                                'status': TaskStatus.COMPLETED,
                            },
                        )
                        await event_bus.emit(
                            'schedule_plan_ready',
                            {
                                'schedule_id': schedule_id,
                                'plan_version': sched.plan_version,
                            },
                        )
        except Exception:
            logger.exception(
                'Plan-only approval commit failed for task %s',
                self.task_id,
            )

        # Respond to the agent. Deny regardless of commit outcome —
        # we do NOT want the plan-only agent to enter execution.
        reason = (
            'Plan accepted and saved to the schedule.'
            if committed
            else 'Plan approval failed to persist; task will be retried.'
        )
        await self._send_control_response(
            request_id,
            'deny',
            message=reason,
        )
        # Signal any wait loops that this session is done.
        self._executing = False
        try:
            asyncio.create_task(self.stop())
        except Exception:
            logger.debug(
                'Failed to auto-stop plan-only session %s',
                self.task_id,
                exc_info=True,
            )

    async def _handle_ask_user_question(
        self, request_id: str, tool_input: dict
    ):
        """Handle AskUserQuestion — emit to frontend, wait."""
        questions = tool_input.get('questions', [])
        self._pending_questions[request_id] = {
            'request_id': request_id,
            'questions': questions,
        }
        self._answer_events[request_id] = asyncio.Event()

        await event_bus.emit(
            'task_questions',
            {
                'task_id': self.task_id,
                'request_id': request_id,
                'questions': questions,
            },
        )

        await self._answer_events[request_id].wait()
        answers = self._answers.pop(request_id, {})
        self._answer_events.pop(request_id, None)
        self._pending_questions.pop(request_id, None)

        if not self.running or self._stopping:
            return

        updated_input = {**tool_input, 'answers': answers}
        await self._send_control_response(request_id, 'allow', updated_input)
