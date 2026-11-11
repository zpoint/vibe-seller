"""Every in-task MCP tool schema is one the model API accepts.

The Anthropic API rejects a tool whose ``input_schema`` has ``anyOf`` /
``oneOf`` / ``allOf`` (or ``not``) at the top level, and Claude Code then
drops that tool from the session without an error the server ever sees.
That is how ``vibe_seller_set_task_result`` vanished from a live agent's
tool list ("No such tool available") once its schema grew a top-level
``anyOf`` for "send result or result_file". A one-of rule between
arguments belongs in the handler, not in the schema's top level.
"""

import pytest

from app.mcp_tool_schemas import TOOLS

pytestmark = pytest.mark.unit

_TOP_LEVEL_COMBINATORS = ('anyOf', 'oneOf', 'allOf', 'not')


@pytest.mark.parametrize('tool', TOOLS, ids=lambda t: t['name'])
def test_input_schema_is_a_plain_object(tool):
    schema = tool['inputSchema']
    assert schema.get('type') == 'object', tool['name']
    bad = [k for k in _TOP_LEVEL_COMBINATORS if k in schema]
    assert not bad, (
        f'{tool["name"]}: top-level {bad} in inputSchema — the API rejects '
        'it and the tool silently disappears from the agent; enforce the '
        'rule in the handler instead'
    )
    for name in schema.get('required', []):
        assert name in schema.get('properties', {}), (tool['name'], name)
