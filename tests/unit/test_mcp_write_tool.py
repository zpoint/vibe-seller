"""Unit test: MCP write_workspace_file tool."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.mcp_server import handle_tool_call

pytestmark = pytest.mark.unit


class TestWriteWorkspaceFile:
    @pytest.mark.asyncio
    async def test_calls_workspace_api(self):
        """write_workspace_file calls PUT /api/workspace/file."""
        with patch(
            'app.mcp_server.call_api',
            new_callable=AsyncMock,
            return_value={'path': 'stores/test/CATALOG.md', 'status': 'ok'},
        ) as mock_api:
            result = await handle_tool_call(
                'vibe_seller_write_workspace_file',
                {
                    'path': 'stores/test/CATALOG.md',
                    'content': '# Catalog\n| File | Summary |\n',
                },
            )
            mock_api.assert_awaited_once_with(
                'PUT',
                '/api/workspace/file?path=stores/test/CATALOG.md',
                {'content': '# Catalog\n| File | Summary |\n'},
            )
            data = json.loads(result)
            assert data['status'] == 'ok'

    @pytest.mark.asyncio
    async def test_returns_error_on_failure(self):
        """API error → error in result JSON."""
        with patch(
            'app.mcp_server.call_api',
            new_callable=AsyncMock,
            side_effect=Exception('Connection refused'),
        ):
            result = await handle_tool_call(
                'vibe_seller_write_workspace_file',
                {'path': 'bad/path', 'content': 'x'},
            )
            data = json.loads(result)
            assert 'error' in data
