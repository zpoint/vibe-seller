"""ClaudeCodeBackend — AgentSession orchestrator / singleton."""

import asyncio
import logging

from app.ai.base import AIAgentBackend
from app.ai.claude_backend import AgentSession
from app.ai.profiles import DEFAULT_PROFILE_ID
from app.ai.stop_gates import recorded_skills
from app.database import async_session
from app.env_options import Options
from app.models.task import Task
from app.workspace.manager import workspace_manager
from app.workspace.skills_sync import skills_sync

logger = logging.getLogger(__name__)


class ClaudeCodeBackend(AIAgentBackend):
    """AI agent backend that wraps the Claude Code CLI."""

    MAX_CONCURRENT = Options.MAX_AGENT_CONCURRENCY.get_int()

    def __init__(self):
        self._sessions: dict[str, AgentSession] = {}
        self._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._in_flight = 0

    def set_max_concurrent(self, n: int) -> None:
        """Update concurrency limit at runtime.

        Creates a new semaphore with permits reduced by the
        number of currently in-flight agents, so new runs
        block until active count drops below the new max.
        """
        old = self.MAX_CONCURRENT
        n = max(1, n)
        self.MAX_CONCURRENT = n
        free = max(0, n - self._in_flight)
        self._semaphore = asyncio.Semaphore(free)
        logger.info(
            'Agent concurrency changed: %d -> %d (in-flight=%d, free=%d)',
            old,
            n,
            self._in_flight,
            free,
        )

    async def run(
        self,
        task_id: str,
        prompt: str,
        system_extra: str = '',
        mode: str = 'execute',
        on_event=None,
        profile_id: str = DEFAULT_PROFILE_ID,
        message_history: list[dict] | None = None,
        no_store: bool = False,
        resume: bool = False,
        auto_approve_plan: bool = False,
        store_slug: str | None = None,
        on_start=None,
        skip_reflection: bool = False,
    ) -> bool:
        if task_id in self._sessions and self._sessions[task_id].running:
            return False
        await self._semaphore.acquire()
        self._in_flight += 1
        try:
            # Notify caller that semaphore was acquired (e.g. to
            # transition task status).  Return False to abort.
            if on_start is not None:
                if not await on_start():
                    self._in_flight -= 1
                    self._semaphore.release()
                    return False

            # Skill scripts may import their requirements — join the
            # deferred boot-time dep install before the agent starts
            # (no-op once installs finish; SERVING never waits, only
            # task launches do).
            await skills_sync.wait_deps_ready()

            # Prepare isolated per-task workspace. All skills (including
            # browser-use) are copied regardless of store — no-store
            # tasks now have the store-less web browser.
            task_dir = await workspace_manager.prepare_task_workspace(
                task_id,
            )

            session = AgentSession(
                task_id,
                prompt,
                store_slug=store_slug,
                system_prompt_extra=system_extra,
                mode=mode,
                profile_id=profile_id,
                message_history=message_history or [],
                no_store=no_store,
                auto_approve_plan=auto_approve_plan,
                task_dir=task_dir,
                skip_reflection=skip_reflection,
            )
            if resume:
                async with async_session() as db:
                    task = await db.get(Task, task_id)
                    if task and task.session_id:
                        session.resume_session_id = task.session_id
            self._sessions[task_id] = session
            await session.start()
        except Exception:
            self._in_flight -= 1
            self._semaphore.release()
            raise

        async def _release_on_done():
            """Wait for the session to finish, persist session_id,
            release the semaphore. Lifecycle decisions (finalize,
            retry-without-resume) are owned by the orchestrator
            (``task_runner_auto.auto_run_task`` /
            ``finalize_followup_session``) — this coroutine is
            session-bookkeeping only.
            """
            cur_session = session
            if cur_session._task:
                try:
                    await cur_session._task
                except (asyncio.CancelledError, Exception):
                    pass

            # Persist session_id. For --resume runs this is a no-op
            # (Claude Code keeps session_id = resume_session_id,
            # which is already the DB value); for fresh runs it's
            # the final checkpoint alongside the init-event early-
            # persist.
            if cur_session.session_id:
                try:
                    async with async_session() as db:
                        task = await db.get(Task, task_id)
                        if task:
                            task.session_id = cur_session.session_id
                            await db.commit()
                except Exception:
                    logger.debug(
                        'Failed to persist session_id for %s',
                        task_id,
                        exc_info=True,
                    )
            self._in_flight -= 1
            self._semaphore.release()

        asyncio.create_task(_release_on_done())
        return True

    async def retry_without_resume(self, task_id: str) -> bool:
        """Restart a just-finished session fresh, with no
        ``--resume``. Inherits all session args (prompt,
        system_prompt_extra, mode, profile_id, message_history,
        store_slug, etc.) from the prior session so the orchestrator
        doesn't have to re-thread them through.

        Called by ``task_runner_auto`` after detecting a
        resume-failure pattern (rc != 0, no result text, was a
        ``--resume`` attempt). The orchestrator owns the lifecycle:
        it clears stale ``task.result`` / ``task.error`` /
        ``task.session_id`` BEFORE calling this so the next
        finalizer sees only this attempt's outcome.

        Returns True if a fresh session was started successfully,
        False if there's nothing to retry (no prior session) or
        startup failed.
        """
        prior = self._sessions.get(task_id)
        if prior is None:
            return False

        await self._semaphore.acquire()
        self._in_flight += 1
        try:
            new_session = AgentSession(
                task_id,
                prior.prompt,
                store_slug=prior.store_slug,
                system_prompt_extra=prior.system_prompt_extra,
                mode=prior.mode,
                profile_id=prior.profile_id,
                message_history=list(prior.message_history or []),
                no_store=prior.no_store,
                auto_approve_plan=prior.auto_approve_plan,
                task_dir=prior.task_dir,
                skip_reflection=prior.skip_reflection,
            )
            self._sessions[task_id] = new_session
            try:
                await new_session.start()
            except Exception:
                logger.exception(
                    'Failed to start retry session for %s',
                    task_id,
                )
                self._in_flight -= 1
                self._semaphore.release()
                return False
        except Exception:
            self._in_flight -= 1
            self._semaphore.release()
            raise

        async def _release_retry_on_done():
            cur = new_session
            if cur._task:
                try:
                    await cur._task
                except (asyncio.CancelledError, Exception):
                    pass
            if cur.session_id:
                try:
                    async with async_session() as db:
                        task_obj = await db.get(Task, task_id)
                        if task_obj:
                            task_obj.session_id = cur.session_id
                            await db.commit()
                except Exception:
                    logger.debug(
                        'Failed to persist retry session_id for %s',
                        task_id,
                        exc_info=True,
                    )
            self._in_flight -= 1
            self._semaphore.release()

        asyncio.create_task(_release_retry_on_done())
        return True

    def get_session(self, task_id: str) -> AgentSession | None:
        """Return the active session for a task, if any."""
        return self._sessions.get(task_id)

    def get_pending_questions(self, task_id: str) -> dict | None:
        """Return pending question data if agent is waiting."""
        session = self._sessions.get(task_id)
        if not session or not session._pending_questions:
            return None
        # Return the first (and typically only) pending question
        for data in session._pending_questions.values():
            return data
        return None

    async def stop(self, task_id: str) -> bool:
        session = self._sessions.get(task_id)
        if not session or not session.running:
            return False
        await session.stop()
        return True

    async def stop_all(self) -> int:
        """Stop every running task agent (used on server shutdown).

        Each session's ``stop()`` killpg's the whole ``claude -p`` subtree
        (MCP server, skill_cli.daemon, browser-use), so this prevents
        agents being orphaned across a restart — orphans keep calling
        ``browser/start`` on the next server and thrash the shared Ziniao
        client. Best-effort: a failure on one session never blocks the
        others or shutdown.
        """
        running = [
            (tid, s)
            for tid, s in list(self._sessions.items())
            if s and s.running
        ]
        if not running:
            return 0
        logger.info(
            'Stopping %d running task agent(s) on shutdown', len(running)
        )
        results = await asyncio.gather(
            *(s.stop() for _tid, s in running),
            return_exceptions=True,
        )
        for (tid, _s), res in zip(running, results, strict=True):
            if isinstance(res, Exception):
                logger.warning('stop_all: session %s stop failed: %s', tid, res)
        return len(running)

    async def submit_answer(
        self, task_id: str, request_id: str, answers: dict
    ) -> bool:
        session = self._sessions.get(task_id)
        if not session or not session.running:
            return False
        await session.submit_answer(request_id, answers)
        return True

    async def send_message(self, task_id: str, message: str) -> bool:
        session = self._sessions.get(task_id)
        if not session or not session.running:
            return False
        await session.send_user_message(message)
        return True

    async def approve_plan(self, task_id: str) -> bool:
        """Approve the pending plan in a running session."""
        session = self._sessions.get(task_id)
        if not session or not session.running:
            return False
        await session.approve_plan()
        return True

    async def reject_plan(self, task_id: str, feedback: str = '') -> bool:
        """Reject the pending plan in a running session."""
        session = self._sessions.get(task_id)
        if not session or not session.running:
            return False
        await session.reject_plan(feedback)
        return True

    def loaded_skills_and_workspace(self, task_id: str):
        """Return (loaded_skills, task_dir) for a task.

        Used by ``set_task_result`` to resolve skill-declared exit
        gates. Skills are the union of the live session's loads and
        the task's durable bindings (``stop_gates.recorded_skills``):
        a retry-resume session that edits the report without
        re-Reading SKILL.md still faces the gates the original
        session bound, across server restarts.
        """
        durable = recorded_skills(task_id)
        session = self.get_session(task_id)
        if session is None:
            return durable, None
        return (
            durable | frozenset(getattr(session, '_loaded_skills', ()) or ()),
            getattr(session, 'task_dir', None),
        )

    def is_running(self, task_id: str) -> bool:
        session = self._sessions.get(task_id)
        return session.running if session else False


# Singleton
agent_backend = ClaudeCodeBackend()

# Backward-compatible alias
agent_manager = agent_backend
