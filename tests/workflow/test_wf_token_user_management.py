"""An API token cannot manage users or mint credentials — end to end.

The C1 pin of the PR review: the refusal lives in ONE place
(``ApiTokenScopeMiddleware`` + ``TOKEN_REFUSALS``), so these workflow
tests run a REAL minted admin token against the REAL app and assert
the 403 on every mutating /api/users route, the own-password route,
and the two credential-issuing /api/auth routes — while a session
caller keeps full access (the web UI is untouched).

Also the endpoint-level pins that need the real HTTP + DB stack:
M8 (a batch item that dies inside a transaction must not poison the
session for the next item), M5 (the ``limit: {}`` INVALID_PARAMS) and
M3 (the batch cap).
"""

import json
import sys

import pytest
from sqlalchemy import text

from app.agent_mcp.registry import ToolDef
import app.agent_mcp.router  # noqa: F401 — loads the submodule
from app.agent_mcp.router import MAX_BATCH_ITEMS
from tests.workflow import test_wf_api_tokens

_mint = test_wf_api_tokens._mint  # noqa: PLC2701 — shared mint helper

# The package __init__ re-exports the APIRouter OBJECT as ``router``,
# shadowing the submodule attribute — resolve through sys.modules.
router_module = sys.modules['app.agent_mcp.router']

pytestmark = pytest.mark.workflow


class TestTokenCannotManageUsers:
    async def test_token_cannot_create_an_admin_user(
        self, admin_client, unauthed_client
    ):
        minted = await _mint(admin_client, 'leaked')
        auth = {'Authorization': f'Bearer {minted["token"]}'}
        r = await unauthed_client.post(
            '/api/users',
            json={
                'username': 'planted',
                'password': 'backdoor-pass-1',
                'role': 'admin',
            },
            headers=auth,
        )
        assert r.status_code == 403, r.text
        assert 'sign in to the web UI' in r.json()['detail']
        # and nothing was created
        listed = await unauthed_client.get('/api/users', headers=auth)
        assert all(
            u['username'] != 'planted' for u in listed.json()
        )

    async def test_token_cannot_update_a_user(
        self, admin_client, unauthed_client, member_user
    ):
        minted = await _mint(admin_client, 'leaked')
        auth = {'Authorization': f'Bearer {minted["token"]}'}
        r = await unauthed_client.put(
            f'/api/users/{member_user.id}',
            json={'password': 'rotated-by-token-1', 'role': 'admin'},
            headers=auth,
        )
        assert r.status_code == 403, r.text

    async def test_token_cannot_delete_a_user(
        self, admin_client, unauthed_client, member_user
    ):
        minted = await _mint(admin_client, 'leaked')
        r = await unauthed_client.delete(
            f'/api/users/{member_user.id}',
            headers={'Authorization': f'Bearer {minted["token"]}'},
        )
        assert r.status_code == 403, r.text

    async def test_token_cannot_change_its_owners_password(
        self, admin_client, unauthed_client
    ):
        minted = await _mint(admin_client, 'leaked')
        r = await unauthed_client.patch(
            '/api/auth/me/password',
            json={'new_password': 'token-set-pass-1'},
            headers={'Authorization': f'Bearer {minted["token"]}'},
        )
        assert r.status_code == 403, r.text

    async def test_session_keeps_full_user_management(
        self, admin_client, member_user
    ):
        """The refusal is keyed on the credential class: the web UI's
        session caller still manages users exactly as before."""
        r = await admin_client.put(
            f'/api/users/{member_user.id}', json={'is_active': True}
        )
        assert r.status_code == 200, r.text

    async def test_a_token_still_reads_the_user_list(
        self, admin_client, unauthed_client
    ):
        """Reading is not managing: the refusal is a route table, not
        a blanket ban on /api/users."""
        minted = await _mint(admin_client, 'leaked')
        r = await unauthed_client.get(
            '/api/users',
            headers={'Authorization': f'Bearer {minted["token"]}'},
        )
        assert r.status_code == 200, r.text


class TestBatchSessionHygiene:
    async def test_a_failed_item_does_not_poison_the_next(
        self, admin_client, monkeypatch
    ):
        """M8: one DB session serves the whole batch; an item that
        dies inside a transaction leaves it rollback-pending. The
        dispatcher resets the session between items, so the SECOND
        item answers normally instead of PendingRollbackError."""

        async def poison(ctx, args):
            await ctx.db.execute(text('select * from no_such_table'))
            return {}

        poison_def = ToolDef(
            name='poison_the_session',
            description='scratch',
            input_schema={'type': 'object', 'properties': {}},
            handler=poison,
        )
        real_find = router_module.find_tool

        def find(name):
            if name == 'poison_the_session':
                return poison_def
            return real_find(name)

        monkeypatch.setattr(router_module, 'find_tool', find)

        r = await admin_client.post(
            '/api/mcp',
            json=[
                {
                    'jsonrpc': '2.0',
                    'id': 1,
                    'method': 'tools/call',
                    'params': {
                        'name': 'poison_the_session',
                        'arguments': {},
                    },
                },
                {
                    'jsonrpc': '2.0',
                    'id': 2,
                    'method': 'tools/call',
                    'params': {'name': 'list_tasks', 'arguments': {}},
                },
            ],
        )
        assert r.status_code == 200, r.text
        first, second = r.json()
        assert first['result']['isError'] is True
        assert 'error' not in second, second
        assert second['result']['isError'] is False
        # list_tasks really ran (an empty, well-formed payload)
        payload = json.loads(second['result']['content'][0]['text'])
        assert payload['tasks'] == []


class TestEndpointLevelGuards:
    async def test_a_mistyped_argument_is_invalid_params(
        self, admin_client
    ):
        """M5, the review's exact case: ``limit: {}`` used to crash at
        ``int()`` inside the tool and answer "Tool failed
        unexpectedly"."""
        r = await admin_client.post(
            '/api/mcp',
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/call',
                'params': {
                    'name': 'list_tasks',
                    'arguments': {'limit': {}},
                },
            },
        )
        body = r.json()
        assert body['error']['code'] == -32602
        assert 'limit' in body['error']['message']

    async def test_an_oversized_batch_is_one_clear_error(self, admin_client):
        batch = [
            {'jsonrpc': '2.0', 'id': i, 'method': 'ping'}
            for i in range(MAX_BATCH_ITEMS + 1)
        ]
        r = await admin_client.post('/api/mcp', json=batch)
        assert r.status_code == 400
        body = r.json()
        assert body['error']['code'] == -32600
        assert str(MAX_BATCH_ITEMS) in body['error']['message']
