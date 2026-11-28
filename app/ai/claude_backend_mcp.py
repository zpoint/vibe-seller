"""The SDK-init and in-task MCP registration mixin for AgentSession.

Split out of ``app/ai/claude_backend.py`` (line budget): a PURE move
of the three cohesive methods — the two SDK-initialize senders and
the ``.mcp.json`` registration of the in-task vibe-seller MCP — no
behaviour change. AgentSession mixes this in, so every existing
instance-level patch target (``session._register_vibe_seller_mcp`` …)
keeps working unchanged.
"""

from __future__ import annotations

import logging
import sys

from app.ai.claude_backend_utils import sdk_initialize_request
from app.auth import create_token
from app.browser.manager import atomic_write_json, read_mcp_config
from app.config import AI_BOT_USER_ID, BACKEND_PORT, BASE_DIR

logger = logging.getLogger(__name__)


class _McpSdkMixin:
    """SDK init + the in-task vibe-seller MCP registration."""

    async def _send_sdk_initialize(self):
        """Send SDK control request to configure hooks."""
        await self._send_stdin(sdk_initialize_request('plan_then_execute'))

    async def _send_sdk_initialize_auto(self):
        """Send SDK hooks for auto mode (see sdk_initialize_request)."""
        await self._send_stdin(sdk_initialize_request('auto'))

    async def _register_vibe_seller_mcp(self, ws_dir):
        """Register the vibe-seller MCP server in .mcp.json."""
        try:
            # Always the AI-bot identity (seeded by init_db), never an
            # admin-role fallback: this token is handed to an agent
            # process, and app/interactive_only.py keys on its role.
            token = create_token(AI_BOT_USER_ID, 'ai_bot')

            mcp_config = read_mcp_config()
            servers = mcp_config.get('mcpServers', {})
            active = {
                name: cfg
                for name, cfg in servers.items()
                if name != 'vibe-seller'  # re-added below
            }
            active['vibe-seller'] = {
                'command': sys.executable,
                'args': [
                    '-m',
                    'app.mcp_server',
                    '--token',
                    token,
                    '--port',
                    str(BACKEND_PORT),
                    '--task-id',
                    self.task_id,
                ],
                'cwd': str(BASE_DIR),
            }
            mcp_config['mcpServers'] = active
            atomic_write_json(ws_dir / '.mcp.json', mcp_config)
        except Exception:
            logger.exception('Failed to register vibe-seller MCP')
            # Fallback: write empty config so --strict-mcp-config
            # still blocks global servers.
            try:
                fallback = ws_dir / '.mcp.json'
                if not fallback.exists():
                    atomic_write_json(fallback, {'mcpServers': {}})
            except Exception:
                logger.exception('Fallback .mcp.json write also failed')
