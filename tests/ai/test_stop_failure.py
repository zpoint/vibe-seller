"""Integration test: error categorization with real Claude CLI.

Validates that API errors are captured and categorized via
stream-json event parsing (assistant.error / system.api_retry.error).

Requires: Claude Code CLI (npm install -g @anthropic-ai/claude-code)
Run with: pytest tests/ai/test_stop_failure.py -v -m ai
"""

import asyncio
import os
import shutil

import pytest

from app.ai.claude_backend import AgentSession


@pytest.fixture(scope='module')
def check_claude_cli():
    """Skip all tests if Claude CLI is not installed."""
    if not shutil.which('claude'):
        pytest.skip(
            'Claude CLI not found. '
            'Install with: npm install -g @anthropic-ai/claude-code'
        )


@pytest.fixture()
def _patch_profile_env(monkeypatch):
    """Patch ProfileManager to return custom env overrides."""

    def _make_patcher(env_overrides: dict):
        original = os.environ.copy()
        original.update(env_overrides)

        def _patched_get_env(profile_id: str) -> dict:
            return original

        monkeypatch.setattr(
            'app.ai.profiles.ProfileManager.get_env_for_profile',
            _patched_get_env,
        )

    return _make_patcher


class TestErrorCategorization:
    """Test error categorization from real Claude CLI failures."""

    @pytest.mark.asyncio
    @pytest.mark.ai
    async def test_invalid_api_key_categorized(
        self, check_claude_cli, _patch_profile_env
    ):
        """Invalid API key → error_category == 'auth_failed'."""
        _patch_profile_env({
            'ANTHROPIC_API_KEY': 'sk-ant-fake-invalid-key-for-testing',
        })

        session = AgentSession(
            task_id='test-stopfailure-auth',
            prompt='Say hello',
            mode='execute',
        )

        await session.start()

        # Wait for CLI to fail (auth errors are instant)
        if session._task:
            try:
                await asyncio.wait_for(session._task, timeout=30)
            except (TimeoutError, asyncio.CancelledError):
                pass

        assert session._is_error_result is True
        assert session._error_category == 'auth_failed', (
            f'Expected auth_failed, got {session._error_category!r}'
        )
        assert 'Invalid API key' in (session._result_text or '')

    @pytest.mark.asyncio
    @pytest.mark.ai
    async def test_unreachable_endpoint_categorized(
        self, check_claude_cli, _patch_profile_env
    ):
        """Unreachable endpoint → error_category set (likely 'unknown').

        Note: The CLI retries 10x with exponential backoff, so this
        test may take up to ~2 minutes. We use a 120s timeout.
        """
        _patch_profile_env({
            'ANTHROPIC_BASE_URL': 'http://localhost:1',
            'ANTHROPIC_API_KEY': 'sk-fake-for-unreachable-test',
        })

        session = AgentSession(
            task_id='test-stopfailure-unreachable',
            prompt='Say hello',
            mode='execute',
        )

        await session.start()

        if session._task:
            try:
                await asyncio.wait_for(session._task, timeout=120)
            except (TimeoutError, asyncio.CancelledError):
                pass

        assert session._is_error_result is True
        # Connection errors show as 'unknown' in api_retry events
        assert session._error_category is not None, (
            'Expected error_category to be set for unreachable endpoint'
        )
