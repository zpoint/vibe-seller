"""Abstract interface for AI agent backends.

MVP: ClaudeCodeBackend (wraps claude CLI via subprocess)
Future: OwnAgentRuntime (calls LLM APIs directly with custom tool/memory system)

Design rationale (see DESIGN.md "AI Agent Architecture"):
- CLI Wrapper (MVP): Simple, leverages existing CLI tool capabilities
- Own Agent Runtime (long-term): Full control over memory/tool loading, provider-swappable
- Direct API Calls (legacy): No context, no memory, not extensible

Key Principle: ALL LLM interactions route through the agent system, never call APIs
directly. This ensures the agent loads workspace memory before every interaction.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from typing import Any


class AIAgentBackend(ABC):
    """Abstract interface for AI agent backends."""

    @abstractmethod
    async def run(
        self,
        task_id: str,
        prompt: str,
        system_extra: str = '',
        mode: str = 'execute',
        on_event: Callable | None = None,
        profile_id: str = 'default',
        message_history: list[dict] | None = None,
        no_store: bool = False,
        resume: bool = False,
        auto_approve_plan: bool = False,
        store_slug: str | None = None,
        on_start: (Callable[[], Coroutine[Any, Any, bool]] | None) = None,
        skip_reflection: bool = False,
    ) -> bool:
        """Start an agent run for a task.

        Args:
            task_id: Unique identifier for the task
            prompt: The user prompt / task description
            system_extra: Additional system prompt context
            mode: "execute", "plan_then_execute", or "auto"
            on_event: Optional callback for streaming events
            profile_id: AI profile environment to use
            message_history: Prior conversation for context
            no_store: True if task has no associated store
            resume: Resume previous CLI session if available
            auto_approve_plan: If True, auto-approve plan
                (scheduled tasks); if False, wait for user
            store_slug: Store slug for browser-use CLI isolation
            on_start: Optional async callback invoked after the
                concurrency semaphore is acquired but before the
                agent session launches. Return False to abort.
            skip_reflection: If True, skip Stop hook reflection
                (e.g. catalog sync tasks).

        Returns:
            True if started successfully, False if already running
        """
        ...

    @abstractmethod
    async def stop(self, task_id: str) -> bool:
        """Stop a running agent. Returns False if not running."""
        ...

    @abstractmethod
    async def submit_answer(
        self, task_id: str, request_id: str, answers: dict
    ) -> bool:
        """Submit an answer for a pending question. Returns False if no session."""
        ...

    @abstractmethod
    async def send_message(self, task_id: str, message: str) -> bool:
        """Send a user message to a running agent.

        Returns False if no session is running.
        """
        ...

    @abstractmethod
    async def approve_plan(self, task_id: str) -> bool:
        """Approve a pending plan. Returns False if no session."""
        ...

    @abstractmethod
    async def reject_plan(self, task_id: str, feedback: str = '') -> bool:
        """Reject a pending plan. Returns False if no session."""
        ...

    @abstractmethod
    def is_running(self, task_id: str) -> bool:
        """Check if an agent is currently running for a task."""
        ...
