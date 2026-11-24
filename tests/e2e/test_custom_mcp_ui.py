"""Settings → Integrations → Custom MCP, driven in the browser.

The config step a user takes, against a stand-in MCP server this test
serves itself (``fake_mcp_server``): paste the vendor's JSON (no
``type``, text around it — what vendors publish) and Save; saving
connects first and lists the server's tool, and a server that does not
connect is not saved. The token the user pasted is never on the page
afterwards. Then turn it off and on, delete it, and add it again.

No model is involved — it runs in the mock-CLI job and on the shared
e2e server alike. Whether a TASK then uses the server is
``test_custom_mcp_live.py``.
"""

import json

import httpx
from playwright.sync_api import Page, expect
import pytest

from tests.e2e import e2e_helpers, fake_mcp_server

pytestmark = pytest.mark.e2e

NAME = 'e2e-ui-widgets'


@pytest.fixture
def mcp_server():
    server = fake_mcp_server.serve()
    yield server
    server.shutdown()


@pytest.fixture
def clean_servers(api_client):
    """Leave no server of ours behind, pass or fail."""

    def _delete_ours():
        listed = api_client.get(
            f'{e2e_helpers.BASE_URL}/api/custom-mcp-servers'
        )
        for s in listed.json():
            if s['name'] == NAME:
                api_client.delete(
                    f'{e2e_helpers.BASE_URL}/api/custom-mcp-servers/{s["id"]}'
                )

    _delete_ours()
    yield
    _delete_ours()


def _open_integrations(page: Page):
    page.get_by_test_id('nav-settings').click()
    page.get_by_role('button', name='Integrations').click()
    card = page.get_by_test_id('custom-mcp-card')
    card.scroll_into_view_if_needed()
    expect(card).to_be_visible()
    return card


def test_add_toggle_delete_and_add_again(
    authenticated_page: Page,
    mcp_server,
    clean_servers,
    api_client: httpx.Client,
):
    page = authenticated_page
    card = _open_integrations(page)

    paste = 'Install this MCP: ' + json.dumps({
        'mcpServers': {NAME: fake_mcp_server.http_config(mcp_server)}
    })
    card.get_by_test_id('custom-mcp-add').click()
    card.get_by_test_id('custom-mcp-config').fill(paste)
    card.get_by_test_id('custom-mcp-name').fill(NAME)
    card.get_by_test_id('custom-mcp-save').click()

    row = card.get_by_test_id(f'custom-mcp-row-{NAME}')
    expect(row).to_be_visible()
    expect(row.get_by_test_id('custom-mcp-state')).to_have_text('On')
    # Saving connected first and shows what it found.
    result = row.get_by_test_id('custom-mcp-test-result')
    expect(result).to_contain_text(
        f'Connected to {fake_mcp_server.SERVER_NAME} — 1 tools',
        timeout=30_000,
    )
    row.get_by_role('button', name='Show tools').click()
    expect(row.get_by_test_id('custom-mcp-tools')).to_contain_text(
        fake_mcp_server.TOOL_NAME
    )
    # Normalized for Claude Code, and the credential is gone from view.
    expect(row).to_contain_text('HTTP')
    assert fake_mcp_server.TOKEN not in page.content()

    # The server saw exactly the handshake a task's CLI performs.
    methods = [c.get('method') for c in mcp_server.received()]
    assert methods[:3] == [
        'initialize',
        'notifications/initialized',
        'tools/list',
    ], methods

    row.get_by_test_id('custom-mcp-toggle').click()
    expect(row.get_by_test_id('custom-mcp-state')).to_have_text('Off')
    listed = api_client.get(f'{e2e_helpers.BASE_URL}/api/custom-mcp-servers')
    (ours,) = [s for s in listed.json() if s['name'] == NAME]
    assert ours['enabled'] is False
    row.get_by_test_id('custom-mcp-toggle').click()
    expect(row.get_by_test_id('custom-mcp-state')).to_have_text('On')

    page.once('dialog', lambda d: d.accept())
    row.get_by_test_id('custom-mcp-delete').click()
    expect(row).to_have_count(0)
    listed = api_client.get(f'{e2e_helpers.BASE_URL}/api/custom-mcp-servers')
    assert NAME not in [s['name'] for s in listed.json()]

    # Add the same server again: the name is free, and it connects.
    card.get_by_test_id('custom-mcp-add').click()
    card.get_by_test_id('custom-mcp-config').fill(paste)
    card.get_by_test_id('custom-mcp-name').fill(NAME)
    card.get_by_test_id('custom-mcp-save').click()
    expect(row.get_by_test_id('custom-mcp-test-result')).to_contain_text(
        f'Connected to {fake_mcp_server.SERVER_NAME} — 1 tools',
        timeout=30_000,
    )
    listed = api_client.get(f'{e2e_helpers.BASE_URL}/api/custom-mcp-servers')
    assert [s['name'] for s in listed.json()].count(NAME) == 1


def test_a_paste_claude_code_would_refuse_is_explained_not_saved(
    authenticated_page: Page, clean_servers
):
    """A token-in-a-variable config would be filled from the AGENT's
    environment (its model key); the form says what to do instead."""
    page = authenticated_page
    card = _open_integrations(page)
    card.get_by_test_id('custom-mcp-add').click()
    card.get_by_test_id('custom-mcp-config').fill(
        json.dumps({
            'mcpServers': {
                NAME: {
                    'url': 'https://mcp.example.com/mcp',
                    'headers': {'Authorization': 'Bearer ${MY_TOKEN}'},
                }
            }
        })
    )
    card.get_by_test_id('custom-mcp-name').fill(NAME)
    card.get_by_test_id('custom-mcp-save').click()
    expect(card.get_by_test_id('custom-mcp-error')).to_contain_text(
        'replace it with the real token'
    )
    expect(card.get_by_test_id(f'custom-mcp-row-{NAME}')).to_have_count(0)


def test_a_server_that_does_not_connect_is_not_saved(
    authenticated_page: Page, mcp_server, clean_servers
):
    """Like the AI-profile save: the panel connects first and refuses
    to save a server that does not answer (here, a wrong token)."""
    page = authenticated_page
    card = _open_integrations(page)
    card.get_by_test_id('custom-mcp-add').click()
    card.get_by_test_id('custom-mcp-config').fill(
        json.dumps({
            'mcpServers': {
                NAME: {
                    'url': fake_mcp_server.url_of(mcp_server),
                    'headers': {'Authorization': 'Bearer wrong-token'},
                }
            }
        })
    )
    card.get_by_test_id('custom-mcp-name').fill(NAME)
    card.get_by_test_id('custom-mcp-save').click()
    expect(card.get_by_test_id('custom-mcp-error')).to_contain_text(
        f"Couldn't connect to {NAME}, so it wasn't saved: the token is "
        'wrong or no longer valid'
    )
    expect(card.get_by_test_id(f'custom-mcp-row-{NAME}')).to_have_count(0)
    expect(card.get_by_test_id('custom-mcp-form')).to_be_visible()
