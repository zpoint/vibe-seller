"""Settings → Integrations → Custom MCP, through the real app.

Covers the user journey and its guards:

- an admin pastes a vendor's config (no ``type``, text around it) and
  it is stored normalized and ENCRYPTED; every response masks the
  credential, list and create alike;
- a name that exists is a 409 naming it, and ``replace`` overwrites;
- members see the list but cannot add, toggle, test or delete;
- an API token cannot add one even as an admin (``TOKEN_REFUSALS`` —
  a stdio server is a command this machine runs);
- Test connects the way a task would — HTTP and stdio against the
  stand-in server — and a wrong token is an answer, not a 500;
- the launch reads only ENABLED servers, and toggling applies to the
  next launch;
- the system prompt names the enabled servers.
"""

import json

import pytest
from sqlalchemy import select

from app.custom_mcp import load_launch_servers
from app.models.custom_mcp_server import CustomMcpServer
from app.models.task import Task
from app.task_runner import TaskHeader, build_system_extra
from tests.e2e import fake_mcp_server
from tests.workflow import test_wf_api_tokens

_mint = test_wf_api_tokens._mint  # noqa: PLC2701 — shared mint helper

pytestmark = pytest.mark.workflow

URL = '/api/custom-mcp-servers'


@pytest.fixture(scope='module')
def mcp_server():
    server = fake_mcp_server.serve()
    yield server
    server.shutdown()


def vendor_paste(server, name='widgets') -> str:
    """What a vendor's "copy config" button hands the user."""
    config = {'mcpServers': {name: fake_mcp_server.http_config(server)}}
    return 'Install this MCP for me: ' + json.dumps(config)


async def _add(client, config, **extra):
    return await client.post(URL, json={'config': config, **extra})


class TestAddAndList:
    async def test_a_vendor_paste_is_stored_normalized_and_encrypted(
        self, admin_client, mcp_server, override_async_session
    ):
        r = await _add(admin_client, vendor_paste(mcp_server))
        assert r.status_code == 201, r.text
        (created,) = r.json()
        assert created['name'] == 'widgets'
        assert created['type'] == 'http'
        assert created['enabled'] is True and created['valid'] is True
        # The credential never comes back — not even to the admin who
        # just typed it.
        assert fake_mcp_server.TOKEN not in r.text
        assert created['headers']['Authorization'].startswith('****')

        listed = await admin_client.get(URL)
        assert fake_mcp_server.TOKEN not in listed.text
        assert [s['name'] for s in listed.json()] == ['widgets']

        async with override_async_session() as db:
            row = (await db.execute(select(CustomMcpServer))).scalar_one()
        assert fake_mcp_server.TOKEN not in row.config_enc
        assert '127.0.0.1' not in row.config_enc

        # What a launch gets is the full, typed config.
        servers = await load_launch_servers()
        assert servers == {
            'widgets': {
                'type': 'http',
                **fake_mcp_server.http_config(mcp_server),
            }
        }

    async def test_an_invalid_paste_is_a_400_saying_why(self, admin_client):
        r = await _add(
            admin_client,
            {'mcpServers': {'x': {'url': 'https://h/mcp', 'oauth': {}}}},
        )
        assert r.status_code == 201  # empty oauth is no oauth
        r = await _add(
            admin_client,
            {'mcpServers': {'y': {'url': 'https://h', 'oauth': {'a': 1}}}},
        )
        assert r.status_code == 400
        assert 'OAuth' in r.json()['detail']
        r = await _add(admin_client, 'https://h.example/mcp')
        assert r.status_code == 400
        assert 'name' in r.json()['detail']

    async def test_a_reserved_name_is_refused(self, admin_client):
        r = await _add(
            admin_client, {'mcpServers': {'vibe-seller': {'url': 'https://h'}}}
        )
        assert r.status_code == 400
        assert 'uses itself' in r.json()['detail']

    async def test_an_existing_name_is_409_then_replace_overwrites(
        self, admin_client, mcp_server
    ):
        assert (
            await _add(admin_client, vendor_paste(mcp_server))
        ).status_code == 201
        r = await _add(
            admin_client, 'https://other.example/mcp', name='widgets'
        )
        assert r.status_code == 409
        assert r.json()['detail']['names'] == ['widgets']

        r = await _add(
            admin_client,
            'https://other.example/mcp',
            name='widgets',
            replace=True,
        )
        assert r.status_code == 201
        (only,) = (await admin_client.get(URL)).json()
        assert only['url'] == 'https://other.example/mcp'
        assert 'headers' not in only


class TestWhoMayChangeIt:
    async def test_a_member_sees_the_list_but_changes_nothing(
        self, admin_client, member_client, mcp_server
    ):
        (created,) = (await _add(admin_client, vendor_paste(mcp_server))).json()
        sid = created['id']
        assert (await member_client.get(URL)).status_code == 200
        assert (
            await _add(member_client, vendor_paste(mcp_server, 'm'))
        ).status_code == 403
        r = await member_client.patch(f'{URL}/{sid}', json={'enabled': False})
        assert r.status_code == 403
        assert (
            await member_client.post(f'{URL}/{sid}/test')
        ).status_code == 403
        assert (await member_client.delete(f'{URL}/{sid}')).status_code == 403

    async def test_an_admins_api_token_cannot_add_one(
        self, admin_client, unauthed_client, mcp_server
    ):
        minted = await _mint(admin_client, 'leaked')
        r = await unauthed_client.post(
            URL,
            json={'config': fake_mcp_server.stdio_config(), 'name': 'planted'},
            headers={'Authorization': f'Bearer {minted["token"]}'},
        )
        assert r.status_code == 403, r.text
        assert 'sign in to the web UI' in r.json()['detail']
        assert (await admin_client.get(URL)).json() == []


class TestConnectionTest:
    async def test_http_server_lists_its_tools(self, admin_client, mcp_server):
        (created,) = (await _add(admin_client, vendor_paste(mcp_server))).json()
        r = await admin_client.post(f'{URL}/{created["id"]}/test')
        assert r.status_code == 200
        body = r.json()
        assert body['ok'] is True, body
        assert body['server_name'] == fake_mcp_server.SERVER_NAME
        assert body['tool_count'] == 1
        assert body['tools'][0]['name'] == fake_mcp_server.TOOL_NAME

    async def test_legacy_sse_server_lists_its_tools(
        self, admin_client, mcp_server
    ):
        config = {
            'url': fake_mcp_server.url_of(mcp_server, '/sse'),
            'headers': {'Authorization': f'Bearer {fake_mcp_server.TOKEN}'},
        }
        (created,) = (await _add(admin_client, config, name='sse')).json()
        assert created['type'] == 'sse'
        body = (await admin_client.post(f'{URL}/{created["id"]}/test')).json()
        assert body['ok'] is True, body
        assert body['tool_count'] == 1

    async def test_stdio_server_lists_its_tools(self, admin_client):
        r = await _add(
            admin_client, fake_mcp_server.stdio_config(), name='local'
        )
        (created,) = r.json()
        assert created['type'] == 'stdio'
        body = (await admin_client.post(f'{URL}/{created["id"]}/test')).json()
        assert body['ok'] is True, body
        assert body['tools'][0]['name'] == fake_mcp_server.TOOL_NAME

    async def test_a_wrong_token_is_an_answer_not_an_error(
        self, admin_client, mcp_server
    ):
        config = {
            'url': fake_mcp_server.url_of(mcp_server),
            'headers': {'Authorization': 'Bearer wrong'},
        }
        (created,) = (await _add(admin_client, config, name='bad')).json()
        r = await admin_client.post(f'{URL}/{created["id"]}/test')
        assert r.status_code == 200
        assert r.json()['ok'] is False
        assert '401' in r.json()['error']


class TestLaunchAndPrompt:
    async def test_only_enabled_servers_reach_a_launch(
        self, admin_client, mcp_server
    ):
        (created,) = (await _add(admin_client, vendor_paste(mcp_server))).json()
        assert set(await load_launch_servers()) == {'widgets'}

        r = await admin_client.patch(
            f'{URL}/{created["id"]}', json={'enabled': False}
        )
        assert r.status_code == 200 and r.json()['enabled'] is False
        assert await load_launch_servers() == {}

        await admin_client.patch(
            f'{URL}/{created["id"]}', json={'enabled': True}
        )
        assert set(await load_launch_servers()) == {'widgets'}

        assert (
            await admin_client.delete(f'{URL}/{created["id"]}')
        ).status_code == 200
        assert await load_launch_servers() == {}
        assert (await admin_client.get(URL)).json() == []

    async def test_the_system_prompt_names_enabled_servers(
        self, admin_client, mcp_server
    ):
        task = Task(id='prompt-1', title='t', description='d')
        without = await build_system_extra(task, None, header=TaskHeader.AUTO)
        assert 'Custom MCP servers' not in without.system_extra

        await _add(admin_client, vendor_paste(mcp_server))
        with_server = await build_system_extra(
            task, None, header=TaskHeader.AUTO
        )
        assert 'mcp__widgets__*' in with_server.system_extra
        assert fake_mcp_server.TOKEN not in with_server.system_extra
