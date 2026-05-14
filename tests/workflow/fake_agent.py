"""Deterministic fake AI agent for workflow tests.

Implements AIAgentBackend ABC with configurable per-task scenarios.
Writes plan/result/todos to DB via async_session (must be patched
by conftest to use the test DB).
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from types import SimpleNamespace as _SNS

from sqlalchemy import func, select

from app.ai.base import AIAgentBackend
import app.database as _db
from app.models.schedule import Schedule
from app.models.task import Task
from app.models.task_message import TaskMessage
from app.plan_states import PlanStatus
from app.task_states import TaskStatus

# Real asyncio.sleep — never affected by fast_polling monkeypatch
_real_sleep = asyncio.sleep


@dataclass
class FakeAgentScenario:
    """Configure how the fake agent behaves for a specific task."""

    plan: str = '## Test Plan\n1. Step one\n2. Step two'
    result: str = 'Task completed successfully'
    todos: list[dict] | None = None
    should_fail: bool = False
    fail_at_phase: str | None = None  # 'design' or 'execute'
    complete_delay: float = 0.01
    design_delay: float = 0.0
    execute_delay: float = 0.0
    # Seconds to sleep AFTER the approval event is received but
    # BEFORE writing the plan-only commit. Simulates the real hook
    # taking time to land the commit — which in production lets
    # execute_planned_task race ahead and (pre-fix) force the task
    # into RUNNING before the hook's commit guard fires.
    post_approval_delay: float = 0.0
    error_result: str | None = None  # agent returns is_error: true
    error_category: str | None = None  # error category for the failure
    skip_plan: bool = False  # agent skips planning, executes directly
    skip_plan_on_followup: bool = (
        False  # on re-entry (plan exists), write result directly
    )
    tool_calls: list[dict] | None = (
        None  # e.g. [{'tool': 'Read', 'input': {...}}]
    )
    thinking_text: str | None = None  # e.g. 'Analyzing...'
    extra_results: list[str] | None = (
        None  # additional results emitted as 'assistant' (post-dedup)
    )
    gate: asyncio.Event | None = None  # if set, _do_work waits on it
    # Simulate `claude --resume <stale_id>` rejection on the FIRST
    # session for this task: subprocess exits rc=1 with no result
    # text, session.resume_session_id is set.  The orchestrator
    # should detect the resume-failure pattern and call
    # `agent_manager.retry_without_resume(task_id)`; the fresh
    # session runs the rest of the scenario normally and succeeds.
    # Test for `app/task_session_lifecycle.py` wiring across
    # `auto_run_task` / `finalize_followup_session` /
    # `execute_planned_task` / `execute_woken_task`.
    simulate_resume_failure_first: bool = False


@dataclass
class FakeAgentCall:
    """Record of a single agent.run() or send_message() call."""

    task_id: str
    action: str = 'run'
    prompt: str = ''
    system_extra: str = ''
    mode: str = 'execute'
    profile_id: str = 'default'
    message: str = ''
    auto_approve_plan: bool = False
    message_history: list[dict] | None = None


class _FakeSession:
    """Minimal session object for plan approval and lifecycle events."""

    def __init__(
        self,
        auto_approve_plan: bool = False,
        resume_session_id: str | None = None,
    ):
        self.auto_approve_plan = auto_approve_plan
        self._plan_approval_event: asyncio.Event = asyncio.Event()
        self._plan_approved: bool = False
        self._plan_saved: bool = False
        self._is_error_result: bool = False
        self._error_category: str | None = None
        self.running: bool = True
        # Lifecycle events — tests can await these instead of sleeping
        self.started: asyncio.Event = asyncio.Event()
        self.work_done: asyncio.Event = asyncio.Event()
        # Mirror AgentSession's event-driven session-end signalling
        # (see app/ai/claude_backend.py). `_wait_for_session_end`
        # blocks on these; keep semantics identical to the real
        # backend so workflow tests exercise the same code path.
        self.done: asyncio.Event = asyncio.Event()
        self.plan_saved_event: asyncio.Event = asyncio.Event()
        # Resume-failure detection (`_is_resume_failure`) reads
        # these three attributes off the session — mirror the real
        # AgentSession surface so the orchestrator's retry path
        # exercises the same code under test.
        self.resume_session_id: str | None = resume_session_id
        self._proc: object | None = None  # filled by _do_work on exit
        self._result_text: str = ''

    async def approve_plan(self):
        self._plan_approved = True
        self.plan_saved_event.clear()
        self._plan_approval_event.set()

    async def reject_plan(self, feedback: str = ''):
        self._plan_approved = False
        self.plan_saved_event.clear()
        self._plan_approval_event.set()


class FakeAgent(AIAgentBackend):
    """Test double for ClaudeCodeBackend.

    - run() spawns an asyncio task that writes plan/result to DB
    - Configurable per task via scenarios dict
    - Records all calls for assertion
    """

    def __init__(self):
        self._running: dict[str, bool] = {}
        self._sessions: dict[str, _FakeSession] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self.calls: list[FakeAgentCall] = []
        self.scenarios: dict[str, FakeAgentScenario] = {}
        self.default_scenario = FakeAgentScenario()
        self._semaphore: asyncio.Semaphore | None = None
        # Track which task_ids have already burned their
        # one-time resume-failure simulation, so the second
        # session (after retry_without_resume) runs cleanly.
        self._resume_failure_consumed: set[str] = set()
        # Per-task mode last passed to run() — used by
        # retry_without_resume to spawn the retry session in the
        # same mode as the original.
        self._last_mode: dict[str, str] = {}

    def set_max_concurrent(self, n: int) -> None:
        """Mirror ClaudeCodeBackend.set_max_concurrent."""
        self._semaphore = asyncio.Semaphore(max(1, n))

    async def _create_message(
        self, task_id: str, role: str, content: str
    ) -> None:
        """Write a TaskMessage to DB with correct seq.

        NOTE: This duplicates message creation logic from
        ClaudeCodeBackend / tasks router. If TaskMessage schema
        changes, update both.
        """
        async with _db.async_session() as db:
            max_seq = (
                await db.execute(
                    select(func.coalesce(func.max(TaskMessage.seq), -1)).where(
                        TaskMessage.task_id == task_id
                    )
                )
            ).scalar()
            msg = TaskMessage(
                task_id=task_id,
                role=role,
                content=content,
                seq=max_seq + 1,
            )
            db.add(msg)
            await db.commit()

    async def run(
        self,
        task_id: str,
        prompt: str,
        system_extra: str = '',
        mode: str = 'execute',
        on_event=None,
        profile_id: str = 'default',
        message_history: list[dict] | None = None,
        no_store: bool = False,
        resume: bool = False,
        auto_approve_plan: bool = False,
        store_slug: str | None = None,
        on_start=None,
        skip_reflection: bool = False,
    ) -> bool:
        if self._running.get(task_id):
            return False
        # Acquire semaphore if configured (mirrors
        # ClaudeCodeBackend concurrency limit).
        if self._semaphore is not None:
            await self._semaphore.acquire()
        # Honour on_start callback (mirrors semaphore-acquired
        # notification in ClaudeCodeBackend).
        if on_start is not None:
            if not await on_start():
                if self._semaphore is not None:
                    self._semaphore.release()
                return False
        self._running[task_id] = True
        session = _FakeSession(auto_approve_plan=auto_approve_plan)
        self._sessions[task_id] = session
        self._last_mode[task_id] = mode
        self.calls.append(
            FakeAgentCall(
                task_id=task_id,
                action='run',
                prompt=prompt,
                system_extra=system_extra,
                mode=mode,
                profile_id=profile_id,
                auto_approve_plan=auto_approve_plan,
                message_history=message_history,
            )
        )
        # Mirror the real manager: when resume=True is requested, look
        # up task.session_id and pin it on the session so the
        # orchestrator's resume-failure detector has a non-None
        # `resume_session_id` to read. Done AFTER recording the call
        # and registering the session so existing tests that check
        # `_sessions[task_id]` / `get_calls()` immediately after
        # `run()` returns continue to see them — the ``async with``
        # below would otherwise yield before recording.
        if resume:
            async with _db.async_session() as db:
                task = await db.get(Task, task_id)
                if task and task.session_id:
                    session.resume_session_id = task.session_id
        scenario = self.scenarios.get(task_id, self.default_scenario)
        t = asyncio.create_task(self._do_work(task_id, mode, scenario))
        self._tasks[task_id] = t
        return True

    def get_session(self, task_id: str) -> _FakeSession | None:
        """Return the fake session for a task, if any."""
        return self._sessions.get(task_id)

    async def wait_started(self, task_id: str, timeout: float = 5.0) -> None:
        """Wait until the agent session for *task_id* has started."""
        for _ in range(int(timeout / 0.02)):
            s = self._sessions.get(task_id)
            if s and s.started.is_set():
                return
            await _real_sleep(0.02)
        raise TimeoutError(
            f'Agent for {task_id} did not start within {timeout}s'
        )

    async def _do_work(
        self,
        task_id: str,
        mode: str,
        scenario: FakeAgentScenario,
    ):
        my_session: _FakeSession | None = self._sessions.get(task_id)
        # Simulate `claude --resume <stale_id>` rejection on the first
        # attempt: rc=1, no output, no result_text. Burn the flag so
        # retry_without_resume's session runs cleanly.
        simulate_failure = (
            scenario.simulate_resume_failure_first
            and task_id not in self._resume_failure_consumed
        )
        if simulate_failure:
            self._resume_failure_consumed.add(task_id)
        try:
            # Honour complete_delay (legacy, minimal by default)
            if scenario.complete_delay > 0:
                await _real_sleep(scenario.complete_delay)

            # Signal that work has begun
            if my_session:
                my_session.started.set()

            if simulate_failure:
                # Skip plan/execute work — exit fast, mimicking the
                # subprocess dying before it could produce any output.
                return

            # If a gate is set, hold here until the test opens it
            if scenario.gate is not None:
                await scenario.gate.wait()

            if mode == 'plan_then_execute':
                await self._do_plan_then_execute(task_id, scenario)
            elif mode in ('execute', 'auto'):
                await self._do_execute(task_id, scenario)
        except asyncio.CancelledError:
            return
        finally:
            # Signal that work is done
            if my_session:
                my_session.work_done.set()
                # Mark the (fake) subprocess return code so
                # `_is_resume_failure` can read it just like it does
                # off `AgentSession._proc.returncode` in production.
                rc = 1 if simulate_failure else 0
                my_session._proc = _SNS(returncode=rc)
                # Real _stream_output sets _is_error_result on any
                # non-zero exit. Mirror that so the orchestrator's
                # "no retry → FAILED" path sees the same signal it
                # does in production.
                if simulate_failure:
                    my_session._is_error_result = True
                # Mirror AgentSession: end-of-session signal. May
                # also be set from FakeAgent.stop(); `asyncio.Event.set()`
                # is idempotent, so multiple sets are harmless.
                # Safe to set on a replaced session — waiters for the
                # replacement look at its own `done` event.
                my_session.done.set()
            # Only clean up OUR state, not what a retry replaced.
            if my_session:
                my_session.running = False
            # Guard: a retry may have already set new _running /
            # _tasks entries — only pop if we're still current.
            if self._sessions.get(task_id) is my_session:
                self._running.pop(task_id, None)
                self._tasks.pop(task_id, None)
            if self._semaphore is not None:
                self._semaphore.release()

    async def retry_without_resume(self, task_id: str) -> bool:
        """Mirror ClaudeCodeBackend.retry_without_resume.

        Spawns a fresh session for `task_id` in the same mode as
        the prior call, but without `resume_session_id`. The
        orchestrator (`app.task_session_lifecycle`) calls this
        after detecting a `--resume` failure pattern. The fresh
        session runs the rest of the scenario normally — by this
        point `_resume_failure_consumed` has the task_id, so
        `simulate_resume_failure_first` no longer fires.
        """
        prior = self._sessions.get(task_id)
        if prior is None:
            return False
        # Acquire concurrency slot for the retry, matching the real
        # manager's contract.
        if self._semaphore is not None:
            await self._semaphore.acquire()
        new_session = _FakeSession(
            auto_approve_plan=prior.auto_approve_plan,
            resume_session_id=None,  # fresh — no --resume
        )
        self._sessions[task_id] = new_session
        self._running[task_id] = True
        self.calls.append(
            FakeAgentCall(
                task_id=task_id,
                action='retry_without_resume',
            )
        )
        mode = self._last_mode.get(task_id, 'auto')
        scenario = self.scenarios.get(task_id, self.default_scenario)
        t = asyncio.create_task(self._do_work(task_id, mode, scenario))
        self._tasks[task_id] = t
        return True

    async def _do_plan_then_execute(
        self,
        task_id: str,
        scenario: FakeAgentScenario,
    ):
        """Simulate plan_then_execute: save plan → wait for
        approval (unless auto_approve_plan) → save result.

        Uses minimal DB sessions to avoid StaticPool contention
        when multiple FakeAgent tasks run concurrently in tests.
        """
        session = self._sessions.get(task_id)

        if scenario.should_fail and (
            scenario.fail_at_phase is None or scenario.fail_at_phase == 'design'
        ):
            # If error_result is set, write it to DB before exiting
            if scenario.error_result:
                async with _db.async_session() as db:
                    task = await db.get(Task, task_id)
                    if task:
                        task.result = scenario.error_result
                        task.updated_at = datetime.now(UTC).isoformat()
                        await db.commit()
                if session:
                    session._is_error_result = True
                    session._error_category = scenario.error_category
            return

        # Optional design delay (allows stop-during-design tests)
        if scenario.design_delay > 0:
            await _real_sleep(scenario.design_delay)

        # Skip planning: agent executes directly without ExitPlanMode
        if scenario.skip_plan:
            result_text = scenario.error_result or scenario.result
            async with _db.async_session() as db:
                task = await db.get(Task, task_id)
                if not task:
                    return
                task.result = result_text
                task.updated_at = datetime.now(UTC).isoformat()
                await db.commit()
            if scenario.error_result and session:
                session._is_error_result = True
                session._error_category = scenario.error_category
            await self._create_message(task_id, 'result', result_text)
            return

        # Follow-up skip: agent re-entered with existing plan from a
        # prior phase, writes result directly without ExitPlanMode.
        # Mirrors the real Phase 2 execution pattern where the agent
        # applies changes but doesn't call ExitPlanMode.
        if scenario.skip_plan_on_followup:
            async with _db.async_session() as db:
                task = await db.get(Task, task_id)
                if task and task.plan:
                    result_text = scenario.error_result or scenario.result
                    task.result = result_text
                    task.updated_at = datetime.now(UTC).isoformat()
                    await db.commit()
                    if scenario.error_result and session:
                        session._is_error_result = True
                        session._error_category = scenario.error_category
                    await self._create_message(task_id, 'result', result_text)
                    return

        # Emit thinking and tool calls before plan (persist only)
        if scenario.thinking_text:
            await self._create_message(
                task_id, 'thinking', scenario.thinking_text
            )
        if scenario.tool_calls:
            for tc in scenario.tool_calls:
                await self._create_message(task_id, 'tool_use', json.dumps(tc))

        # Save plan
        async with _db.async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return
            task.plan = scenario.plan
            task.status = TaskStatus.PLANNED
            task.updated_at = datetime.now(UTC).isoformat()
            await db.commit()

        await self._create_message(task_id, 'plan', scenario.plan)

        if session:
            session._plan_saved = True
            session.plan_saved_event.set()

        if session and not session.auto_approve_plan:
            # Replan loop: wait for approval, replan on rejection
            replan_count = 0
            while True:
                await session._plan_approval_event.wait()
                if session._plan_approved:
                    break
                # Rejected — simulate replan
                replan_count += 1
                session._plan_approval_event.clear()
                revised = f'{scenario.plan}\n\n(revised #{replan_count})'
                async with _db.async_session() as db:
                    task = await db.get(Task, task_id)
                    if not task:
                        return
                    task.plan = revised
                    task.status = TaskStatus.PLANNED
                    task.updated_at = datetime.now(UTC).isoformat()
                    await db.commit()
                await self._create_message(task_id, 'plan', revised)
                if replan_count >= 3:
                    return  # Safety cap

        if scenario.should_fail and (scenario.fail_at_phase == 'execute'):
            return

        # Plan-only tasks (owned by a Schedule in plan-at-creation
        # flow) commit the plan to the Schedule and terminate at
        # COMPLETED — they never enter RUNNING. Mirrors the real
        # branch in app/ai/claude_backend_hooks.py._commit_plan_only_approval,
        # INCLUDING the status guard. If something else has already
        # transitioned the task out of {PLANNED, DESIGNING, QUEUED}
        # (e.g., execute_planned_task racing ahead and forcing
        # RUNNING), we SKIP the commit — exactly like the real hook
        # does. Tests that care about the race will then see
        # Schedule.plan stay empty, surfacing the bug.

        # Optional test knob: hold here so execute_planned_task has
        # time to reach and perform its PLANNED→RUNNING transition.
        # Mirrors the production hook latency (stdin write + agent
        # deny round-trip) that gives the executor a real chance to
        # race.
        if scenario.post_approval_delay > 0:
            await _real_sleep(scenario.post_approval_delay)

        # Plan-only commit mirrors the real hook's guard
        # (``status in {PLANNED, DESIGNING, QUEUED}``) — if the
        # task was force-transitioned to RUNNING by a concurrent
        # executor, skip the commit. Identical rule to
        # ``_commit_plan_only_approval`` in production.
        is_plan_only_task = False
        async with _db.async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return
            is_plan_only_task = bool(task.is_plan_only and task.schedule_id)
            if is_plan_only_task and task.status in (
                TaskStatus.PLANNED,
                TaskStatus.DESIGNING,
                TaskStatus.QUEUED,
            ):
                sched = await db.get(Schedule, task.schedule_id)
                if sched is not None:
                    now = datetime.now(UTC).isoformat()
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = now
                    task.updated_at = now
                    sched.plan = task.plan
                    sched.plan_status = PlanStatus.READY.value
                    sched.plan_version = (sched.plan_version or 0) + 1
                    sched.plan_error = None
                    sched.current_planning_task_id = None
                    sched.updated_at = now
                    await db.commit()

        # Plan-only tasks must NEVER execute — they author a plan
        # and terminate. Return here whether the commit succeeded or
        # was skipped by the status guard. The surrounding pipeline
        # handles the "commit skipped, task stuck in RUNNING" case.
        if is_plan_only_task:
            return

        # Transition to RUNNING first (separate from result save
        # so tests can observe RUNNING state during execute_delay).
        async with _db.async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(UTC).isoformat()
            task.updated_at = datetime.now(UTC).isoformat()
            await db.commit()

        # Optional execute delay (allows stop-during-execution tests)
        if scenario.execute_delay > 0:
            await _real_sleep(scenario.execute_delay)

        # Save result
        result_text = scenario.error_result or scenario.result
        async with _db.async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return
            task.result = result_text
            if scenario.todos:
                task.todos = json.dumps(scenario.todos, ensure_ascii=False)
            task.updated_at = datetime.now(UTC).isoformat()
            await db.commit()

        if scenario.error_result:
            session = self._sessions.get(task_id)
            if session:
                session._is_error_result = True
                session._error_category = scenario.error_category

        await self._create_message(task_id, 'result', result_text)
        if scenario.extra_results:
            for extra in scenario.extra_results:
                await self._create_message(task_id, 'assistant', extra)

    async def _do_execute(
        self,
        task_id: str,
        scenario: FakeAgentScenario,
    ):
        """Simulate execute-only mode."""
        if scenario.should_fail and (
            scenario.fail_at_phase is None
            or scenario.fail_at_phase == 'execute'
        ):
            return

        # Emit thinking and tool calls (persist only)
        if scenario.thinking_text:
            await self._create_message(
                task_id, 'thinking', scenario.thinking_text
            )
        if scenario.tool_calls:
            for tc in scenario.tool_calls:
                await self._create_message(task_id, 'tool_use', json.dumps(tc))

        # Optional execute delay (allows stop-during-execution tests)
        if scenario.execute_delay > 0:
            await _real_sleep(scenario.execute_delay)

        result_text = scenario.error_result or scenario.result
        async with _db.async_session() as db:
            task = await db.get(Task, task_id)
            if not task:
                return
            task.result = result_text
            if scenario.todos:
                task.todos = json.dumps(scenario.todos, ensure_ascii=False)
            task.updated_at = datetime.now(UTC).isoformat()
            await db.commit()

        if scenario.error_result:
            session = self._sessions.get(task_id)
            if session:
                session._is_error_result = True
                session._error_category = scenario.error_category

        await self._create_message(task_id, 'result', result_text)
        if scenario.extra_results:
            for extra in scenario.extra_results:
                await self._create_message(task_id, 'assistant', extra)

    async def stop(self, task_id: str) -> bool:
        # Cancel the asyncio task if still running
        t = self._tasks.pop(task_id, None)
        if t and not t.done():
            t.cancel()
        was_running = self._running.get(task_id, False)
        self._running[task_id] = False
        # Clear session so cancelled _do_work finally block
        # won't match and corrupt a retry's new session.
        # Order matters: pop first, THEN set `done`. Any waiter
        # blocked in `_wait_for_session_end` wakes on `done` and
        # re-checks the registry — by then the slot is empty (or
        # holds the retry's session), so the waiter correctly
        # detects supersession atomically.
        session = self._sessions.pop(task_id, None)
        if session is not None:
            session.done.set()
        return was_running

    async def submit_answer(
        self, task_id: str, request_id: str, answers: dict
    ) -> bool:
        self.calls.append(
            FakeAgentCall(
                task_id=task_id,
                action='submit_answer',
                message=json.dumps(answers),
            )
        )
        return task_id in self._running

    async def send_message(self, task_id: str, message: str) -> bool:
        self.calls.append(
            FakeAgentCall(
                task_id=task_id,
                action='send_message',
                message=message,
            )
        )
        return task_id in self._running

    async def approve_plan(self, task_id: str) -> bool:
        session = self._sessions.get(task_id)
        if not session or not session.running:
            return False
        await session.approve_plan()
        return True

    async def reject_plan(self, task_id: str, feedback: str = '') -> bool:
        session = self._sessions.get(task_id)
        if not session or not session.running:
            return False
        await session.reject_plan(feedback)
        return True

    def is_running(self, task_id: str) -> bool:
        return self._running.get(task_id, False)

    def get_calls(
        self,
        task_id: str | None = None,
        action: str | None = None,
    ) -> list[FakeAgentCall]:
        """Filter recorded calls by task_id and/or action."""
        result = self.calls
        if task_id:
            result = [c for c in result if c.task_id == task_id]
        if action:
            result = [c for c in result if c.action == action]
        return result
