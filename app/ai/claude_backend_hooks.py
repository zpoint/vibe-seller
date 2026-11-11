"""Hook/control-request handlers for AgentSession.

Mixed into AgentSession via multiple inheritance.  Methods here
reference attributes initialised by AgentSession.__init__ and call
other methods defined on the primary class.
"""

import asyncio
from datetime import UTC, datetime
import json
import logging

from app import ads_routing
from app.ai.background_wait import long_sleep_deny
from app.ai.bash_safety import check_skill_file_write, first_bash_deny
from app.ai.catalog_read import should_mark_catalog_read
from app.ai.claude_backend_utils import (
    AGENT_DEBUG,
    AUTO_APPROVE_CALLBACK,
    STOP_CALLBACK,
    TOOL_APPROVAL_CALLBACK,
    check_skill_prereqs,
    check_tool_loop,
    validate_fanout_plan_text,
)
from app.ai.image_guards import (
    check_generated_image_write,
    check_image_read_without_vision,
)
from app.ai.profiles import ProfileManager, model_sees_images
from app.ai.skill_gate_utils import find_skill_md, unloaded_skill_read_hint
from app.ai.stop_gates import record_skill_load
from app.ai.tool_call_guards import tool_call_deny
from app.database import async_session
from app.events.bus import event_bus
from app.models.schedule import Schedule
from app.models.task import Task
from app.plan_states import freeze_schedule_plan
from app.question_answers import expand_free_text_answers
from app.task_states import TaskStatus
from app.telemetry_tasks import (
    send_question_answered,
    send_question_asked,
)
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

    async def _deny_pre_tool_use(self, request_id: str, reason: str):
        """Deny a PreToolUse hook callback with *reason*."""
        await self._send_hook_response(
            request_id,
            {
                'hookSpecificOutput': {
                    'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': reason,
                },
            },
        )

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
            if inner_name == 'Bash':
                cmd = inner_input.get('command', '')
                # One thread hop: the chain is string work except the
                # bid guard, which may read the task's frozen approval
                # TSV (see app.ai.bash_safety). Keep that off the loop.
                deny = await asyncio.to_thread(
                    first_bash_deny, cmd, self.task_dir, self._catalog_read
                )
                deny = deny or long_sleep_deny(inner_input, self._async_agents)
                if deny:
                    label, deny_reason = deny
                    logger.warning(
                        '%s: agent %s denied: %r',
                        label,
                        self.task_id[:8],
                        cmd[:200],
                    )
                    await self._deny_pre_tool_use(request_id, deny_reason)
                    return
            # Skill-file guard: built-in Write/Edit against
            # .claude/skills/** only mutates the throwaway per-task copy
            # and silently fails to persist — force vibe_seller_save_skill.
            deny_reason = check_skill_file_write(inner_name, inner_input)
            if deny_reason:
                logger.warning('Skill-file write guard: %s', self.task_id[:8])
                await self._deny_pre_tool_use(request_id, deny_reason)
                return
            # Generated-image guard: generated_images/ is written only by
            # the vision router, so the file tools may not land there —
            # the Write-tool hop around the Bash-layer image guard.
            deny_reason = check_generated_image_write(inner_name, inner_input)
            if deny_reason:
                logger.warning(
                    'Generated-image write guard: %s', self.task_id[:8]
                )
                await self._deny_pre_tool_use(request_id, deny_reason)
                return
            # Blind-read guard: a text-only model's Read of an image is
            # silently dropped, and the model then invents what it saw.
            deny_reason = check_image_read_without_vision(
                inner_name, inner_input, *self._model_capabilities()
            )
            if deny_reason:
                logger.warning('Blind image read denied: %s', self.task_id[:8])
                await self._deny_pre_tool_use(request_id, deny_reason)
                return
            if should_mark_catalog_read(inner_name, inner_input):
                self._catalog_read = True
            barred = ads_routing.refusal_for(
                self.task_id, inner_name, inner_input
            ) or await asyncio.to_thread(tool_call_deny, self, tool_input)
            if barred:  # routed away, malformed MCP args, or app source
                await self._deny_pre_tool_use(request_id, barred)
                return
            # Skills load (and bind their exit gates) ONLY via the Skill
            # tool below; a plain read of a SKILL.md gets a pointer.
            read_hint = unloaded_skill_read_hint(
                inner_name, inner_input, self._loaded_skills
            )
            if check_tool_loop(
                self._recent_tool_calls, inner_name, inner_input
            ):
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
            # A skill that declares ``requires: [X]`` gets a hint to load
            # X too when it has not been loaded yet — advice, not a deny.
            if inner_name == 'Skill':
                skill_name = inner_input.get('skill', '')
                prereq_hint = self._check_skill_prereqs(skill_name)
                if prereq_hint:
                    # The server's record that the hint was sent (the
                    # e2e reads it; the agent's own narration is not
                    # evidence).
                    logger.info(
                        'Skill prereq: agent %s loaded %r early; hint: %s',
                        self.task_id[:8],
                        skill_name,
                        prereq_hint,
                    )
                    read_hint = (
                        f'{read_hint}\n{prereq_hint}'
                        if read_hint
                        else prereq_hint
                    )
                # Only track skills we actually ship (a SKILL.md
                # exists in workspace/global skills): otherwise
                # ``Skill('<arbitrary-name>')`` would "satisfy" a
                # prereq without loading any prerequisite content.
                if (
                    skill_name
                    and find_skill_md(
                        self.task_dir or VIBE_SELLER_DIR, skill_name
                    )
                    is not None
                ):
                    self._loaded_skills.add(skill_name)
                    record_skill_load(self.task_id, skill_name)
            # Auto-approve all other tools
            allow = {
                'hookEventName': 'PreToolUse',
                'permissionDecision': 'allow',
                'permissionDecisionReason': 'Auto-approved by backend',
            }
            if read_hint:
                allow['additionalContext'] = read_hint
            await self._send_hook_response(
                request_id, {'hookSpecificOutput': allow}
            )
        elif callback_id == STOP_CALLBACK:
            # Stop hook: refuse to end while the turn's own work is
            # still open. Claude Code sends stop_hook_active=true on the
            # stop that follows a block, so each check fires once.
            if not tool_input.get('stop_hook_active'):
                if await self._deny_stop_if_async_agents_running(request_id):
                    return
            await self._send_hook_response(request_id, {'decision': 'approve'})
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

    # (model id, can it see an image?) for this run -- resolved once from
    # the same profile env the agent is spawned with; None = unknown.
    _model_caps: tuple[str | None, bool | None] | None = None

    def _model_capabilities(self) -> tuple[str | None, bool | None]:
        if self._model_caps is None:
            try:
                env = ProfileManager.get_env_for_profile(self.profile_id)
            except (OSError, ValueError):
                env = {}
            model = env.get('ANTHROPIC_MODEL')
            self._model_caps = (model, model_sees_images(model))
        return self._model_caps

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
            if check_tool_loop(self._recent_tool_calls, tool_name, tool_input):
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
        violation = await validate_fanout_plan_text(self.task_id, plan_text)
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
                        freeze_schedule_plan(sched, plan_text, now)
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
        # Mark that this session engaged in interactive Q&A — scopes the
        # finalize-time "prose follow-up → WAITING" check so single-turn
        # tasks that never asked anything are unaffected.
        self._asked_user_question = True
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
        asked_at = await send_question_asked(self.task_id)
        await self._answer_events[request_id].wait()
        await send_question_answered(self.task_id, asked_at, request_id)
        answers = self._answers.pop(request_id, {})
        self._answer_events.pop(request_id, None)
        self._pending_questions.pop(request_id, None)
        # A fresh answer opens a new sub-turn: the agent must take a
        # real action (or explicitly set a result) to be considered
        # done. Until then, another prose reply means it's still asking.
        self._tool_use_since_answer = False
        if not self.running or self._stopping:
            return
        # Expand the UI free-text sentinel into per-question answers (#211).
        answers = expand_free_text_answers(answers, questions)
        updated_input = {**tool_input, 'answers': answers}
        await self._send_control_response(request_id, 'allow', updated_input)
