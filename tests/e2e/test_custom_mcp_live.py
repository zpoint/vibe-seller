"""A custom MCP server reaches a real task's agent, and its tool is used.

Adds the stand-in server (``fake_mcp_server``) through the same API the
Settings panel calls — in the vendor's shape, which Claude Code would
refuse as-is — then runs a task that can only be answered with that
server's tool. Passing proves the whole path: the paste is normalized
and stored, the launch hands the CLI a config it accepts, the model is
told the server is there, the tool call goes out over MCP, and the
answer — :data:`fake_mcp_server.ANSWER`, which exists nowhere else —
comes back into the result.

Runs on a server this module OWNS (``owned_agent_stack``): a custom
server is server-global, and on the shared server every other worker's
tasks would get this one too.

Needs a real model (``E2E_PROVIDER_MAP``) — skipped in the mock-CLI
job, where no MCP config is ever launched.
"""

import os

import httpx
import pytest

from tests.e2e import e2e_helpers, fake_mcp_server
from tests.e2e.own_server import owned_agent_stack

pytestmark = [pytest.mark.e2e]

if os.environ.get('MOCK_CLI'):
    pytest.skip(
        'needs a real CLI: the mock never connects to MCP servers',
        allow_module_level=True,
    )

SERVER_NAME = 'e2e-widgets'
SKU = 'WIDGET-006'


@pytest.fixture(scope='module', autouse=True)
def own_stack(tmp_path_factory):
    with owned_agent_stack(tmp_path_factory, 'custom-mcp') as server:
        yield server


@pytest.fixture(scope='module')
def mcp_server():
    server = fake_mcp_server.serve()
    yield server
    server.shutdown()


def _tool_uses(client: httpx.Client, task_id: str) -> list[str]:
    names = []
    for m in e2e_helpers.get_messages(client, task_id):
        if m.get('role') != 'tool_use':
            continue
        content = m.get('content') or ''
        if f'mcp__{SERVER_NAME}__' in content:
            names.append(content)
    return names


def test_a_task_answers_with_the_custom_servers_tool(
    api_client: httpx.Client, mcp_server
):
    base = e2e_helpers.BASE_URL
    r = api_client.post(
        f'{base}/api/custom-mcp-servers',
        json={
            'config': {
                'mcpServers': {
                    SERVER_NAME: fake_mcp_server.http_config(mcp_server)
                }
            }
        },
    )
    assert r.status_code == 201, r.text
    probe = api_client.post(
        f'{base}/api/custom-mcp-servers/{r.json()[0]["id"]}/test'
    ).json()
    assert probe['ok'], probe

    task = e2e_helpers.create_task(
        api_client,
        'Look up a widget code',
        description=(
            f'Use the {fake_mcp_server.TOOL_NAME} tool to look up the '
            f'internal catalog code of the widget with SKU {SKU}, and '
            'reply with that code. Do not guess it and do not use a '
            'browser — only that tool knows it.'
        ),
    )
    task_id = task['id']
    data = e2e_helpers.poll_task_status(
        api_client,
        task_id,
        {'planned', 'completed'},
        fail_statuses={'failed', 'error', 'cancelled'},
    )
    if data['status'] == 'planned':
        # A store-less task always plans first; approve it as the UI's
        # "Confirm and run" does.
        r = api_client.post(f'{base}/api/tasks/{task_id}/execute-plan')
        assert r.status_code == 200, r.text
        data = e2e_helpers.poll_task_status(
            api_client,
            task_id,
            {'completed'},
            fail_statuses={'failed', 'error', 'cancelled'},
        )
    assert data['status'] == 'completed', data.get('error')

    # Server-side evidence: the call reached the MCP server, with the SKU.
    calls = mcp_server.tool_calls()
    assert calls, 'the agent never called the custom server'
    args = [(c.get('params') or {}).get('arguments') or {} for c in calls]
    assert any(a.get('sku') == SKU for a in args), args
    # Agent-side evidence: recorded under the CLI's MCP tool name.
    assert _tool_uses(api_client, task_id), 'no mcp__e2e-widgets__ call'
    # And the answer only that tool knows is the result.
    assert fake_mcp_server.ANSWER in (data.get('result') or ''), data.get(
        'result'
    )
