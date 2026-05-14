"""AI Agent abstraction layer.

Provides a pluggable interface for AI agent backends.
MVP: ClaudeCodeBackend (wraps claude CLI)
Future: Own agent runtime (calls LLM APIs directly with custom tool/memory system)
"""

from app.ai.base import AIAgentBackend
from app.ai.claude_backend_manager import ClaudeCodeBackend, agent_manager

__all__ = ['AIAgentBackend', 'ClaudeCodeBackend', 'agent_manager']
