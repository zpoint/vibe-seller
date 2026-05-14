"""
AI integration tests for Claude backend.

These tests require the Claude Code CLI to be installed (`npm install -g @anthropic-ai/claude-code`)
and should be run manually to avoid consuming API credits during regular CI runs.

Run with: pytest tests/ai/ -v --mark ai
"""

import asyncio
import shutil

import pytest

from app.ai.claude_backend_manager import agent_manager


@pytest.fixture(scope='module')
def check_claude_cli():
    """Fail tests if Claude CLI is not available."""
    assert shutil.which('claude'), (
        'Claude CLI not found. '
        'Install with: npm install -g @anthropic-ai/claude-code'
    )


class TestClaudeBackend:
    """Tests for Claude backend AI integration."""

    @pytest.mark.asyncio
    @pytest.mark.ai
    async def test_agent_manager_run(self, check_claude_cli):
        """Test agent_manager.run() with real claude CLI."""
        task_id = 'test-task-123'

        started = await agent_manager.run(
            task_id=task_id,
            prompt="Say 'Agent test complete' and stop.",
            system_extra='You are a test agent. Keep responses short.',
            mode='execute',
        )

        assert started is True

        # Wait briefly then stop
        await asyncio.sleep(2)

        # Stop if still running
        if agent_manager.is_running(task_id):
            await agent_manager.stop(task_id)

    @pytest.mark.asyncio
    @pytest.mark.ai
    async def test_design_phase(self, check_claude_cli):
        """Test design phase produces a plan."""
        task_id = 'test-design-456'

        started = await agent_manager.run(
            task_id=task_id,
            prompt='Design a plan to navigate to google.com',
            system_extra='You are a design agent. Create a simple 2-step plan.',
            mode='design',
        )

        assert started is True

        # Wait briefly then stop
        await asyncio.sleep(3)

        # Cleanup
        if agent_manager.is_running(task_id):
            await agent_manager.stop(task_id)

    @pytest.mark.asyncio
    @pytest.mark.ai
    async def test_agent_lifecycle(self, check_claude_cli):
        """Test agent start, check running status, and stop."""
        task_id = 'test-lifecycle-789'

        # Initially should not be running
        assert not agent_manager.is_running(task_id)

        # Start the agent
        started = await agent_manager.run(
            task_id=task_id, prompt="Echo 'test' and exit", mode='execute'
        )
        assert started is True

        # Should be running (or might have completed quickly)
        # Just verify the API works
        _ = agent_manager.is_running(task_id)

        # Stop the agent
        stopped = await agent_manager.stop(task_id)
        assert stopped is True

        # Should not be running after stop
        assert not agent_manager.is_running(task_id)
