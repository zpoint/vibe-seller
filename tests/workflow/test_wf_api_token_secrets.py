"""Workflow tests: API-token callers never read secrets over REST.

MCP results were already masked (``app/agent_mcp/redact.py``), but a
``vst_`` token also authenticates on every REST route — and two REST
families answer the web UI with stored secrets in plaintext (a
profile's ``env`` with ``ANTHROPIC_AUTH_TOKEN``/``ANTHROPIC_API_KEY``,
the chat-bot ``webhook_url`` + signing ``secret``), so a leaked token
could bypass the MCP masks with plain curl.

Pinned here, per route family:

* READ — the same call masked for a token bearer, plaintext for the
  session (web UI) caller; the no-auth caller (auth-off server)
  unchanged too;
* WRITE — a mask echoed back through a REST update keeps the stored
  secret (the ``restore_masked`` contract, now applied at the REST
  enforcement point); a mask on a create-style route is a 400, never
  a stored ``'****'``;
* the one REST route that hands out a fresh credential (minting an
  API token) stays REFUSED for a token caller.

The curated route list these behaviour pins must cover lives with
the unit drift guard (``tests/unit/test_api_token_secrets.py``);
``test_every_curated_secret_route_is_pinned`` keeps the two files
from drifting apart.
"""

import pytest

from app.config import DEFAULT_USER_ID
from app.models.app_settings import AppSettings
from app.models.user import User
import app.vision as vision_module
from tests.unit.test_api_token_secrets import SECRET_READ_ROUTES

pytestmark = pytest.mark.workflow

SECRET = 'sk-ant-secret-token-1234'
BASE_URL = 'https://api.example.com'
WECOM_URL = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdefgh1234'
DINGTALK_URL = 'https://oapi.dingtalk.com/robot/send?access_token=tok1234'

# The routes whose token-caller behaviour is pinned by an explicit
# test in this file (see the class docstrings).
PINNED_MASKED_ROUTES = {
    ('GET', '/api/profiles'),
    ('POST', '/api/profiles'),
    ('PUT', '/api/profiles/{profile_id}'),
    ('GET', '/api/chat-bots/{bot_id}'),
    ('POST', '/api/chat-bots'),
    ('PUT', '/api/chat-bots/{bot_id}'),
}


async def _mint(client, name='ci-token'):
    """Mint a personal API token through the UI route."""
    r = await client.post('/api/auth/tokens', json={'name': name})
    assert r.status_code == 200, r.text
    return r.json()['token']


async def _create_profile(client, profile_id, env=None):
    r = await client.post(
        '/api/profiles',
        json={
            'id': profile_id,
            'name': profile_id,
            'env': env
            or {
                'ANTHROPIC_BASE_URL': BASE_URL,
                'ANTHROPIC_AUTH_TOKEN': SECRET,
            },
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _env_of(profiles_response, profile_id):
    for profile in profiles_response['profiles']:
        if profile['id'] == profile_id:
            return profile['env']
    raise AssertionError(f'profile {profile_id} not listed')


class TestProfileSecrets:
    async def test_list_is_masked_for_a_token_and_plain_for_a_session(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        created = await _create_profile(admin_client, 'acme-probe')
        # the session caller (the UI's edit form) still sees plaintext
        assert created['env']['ANTHROPIC_AUTH_TOKEN'] == SECRET

        r = await unauthed_client.get(
            '/api/profiles',
            headers={'Authorization': f'Bearer {token}'},
        )
        assert r.status_code == 200
        env = _env_of(r.json(), 'acme-probe')
        assert env['ANTHROPIC_AUTH_TOKEN'] != SECRET
        assert env['ANTHROPIC_AUTH_TOKEN'].startswith('****')
        assert env['ANTHROPIC_AUTH_TOKEN'].endswith(SECRET[-4:])
        # non-secret env rows stay readable so profiles remain
        # tellable apart
        assert env['ANTHROPIC_BASE_URL'] == BASE_URL

        r = await admin_client.get('/api/profiles')
        assert _env_of(r.json(), 'acme-probe')['ANTHROPIC_AUTH_TOKEN'] == SECRET

    async def test_create_response_is_masked_for_a_token_caller(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        r = await unauthed_client.post(
            '/api/profiles',
            headers={'Authorization': f'Bearer {token}'},
            json={
                'id': 'acme-created',
                'name': 'acme-created',
                'env': {
                    'ANTHROPIC_BASE_URL': BASE_URL,
                    'ANTHROPIC_AUTH_TOKEN': 'brand-new-key-9876',
                },
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()['env']['ANTHROPIC_AUTH_TOKEN'].startswith('****')
        # the real key was stored (the write path takes plaintext)
        r = await admin_client.get('/api/profiles')
        assert (
            _env_of(r.json(), 'acme-created')['ANTHROPIC_AUTH_TOKEN']
            == 'brand-new-key-9876'
        )

    async def test_update_response_is_masked_for_a_token_caller(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        await _create_profile(admin_client, 'acme-update')
        r = await unauthed_client.put(
            '/api/profiles/acme-update',
            headers={'Authorization': f'Bearer {token}'},
            json={'name': 'renamed-acme'},
        )
        assert r.status_code == 200, r.text
        assert r.json()['name'] == 'renamed-acme'
        assert r.json()['env']['ANTHROPIC_AUTH_TOKEN'].startswith('****')

    async def test_a_masked_env_echo_keeps_the_stored_key(
        self, admin_client, unauthed_client
    ):
        """The required round trip: read masked through REST, echo the
        whole form back — the stored secret survives, exactly like the
        UI's edit form (which posts plaintext)."""
        token = await _mint(admin_client)
        await _create_profile(admin_client, 'acme-echo')

        r = await unauthed_client.get(
            '/api/profiles',
            headers={'Authorization': f'Bearer {token}'},
        )
        masked_env = _env_of(r.json(), 'acme-echo')
        assert masked_env['ANTHROPIC_AUTH_TOKEN'] != SECRET

        r = await unauthed_client.put(
            '/api/profiles/acme-echo',
            headers={'Authorization': f'Bearer {token}'},
            json={'name': 'acme-echo', 'env': masked_env},
        )
        assert r.status_code == 200, r.text

        r = await admin_client.get('/api/profiles')
        env = _env_of(r.json(), 'acme-echo')
        assert env['ANTHROPIC_AUTH_TOKEN'] == SECRET
        assert env['ANTHROPIC_BASE_URL'] == BASE_URL

    async def test_a_fresh_key_through_the_same_put_is_stored(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        await _create_profile(admin_client, 'acme-fresh')
        r = await unauthed_client.put(
            '/api/profiles/acme-fresh',
            headers={'Authorization': f'Bearer {token}'},
            json={
                'env': {
                    'ANTHROPIC_BASE_URL': BASE_URL,
                    'ANTHROPIC_AUTH_TOKEN': 'rotated-key-4321',
                }
            },
        )
        assert r.status_code == 200, r.text
        r = await admin_client.get('/api/profiles')
        assert (
            _env_of(r.json(), 'acme-fresh')['ANTHROPIC_AUTH_TOKEN']
            == 'rotated-key-4321'
        )

    async def test_a_mask_on_create_is_refused_not_stored(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        r = await unauthed_client.post(
            '/api/profiles',
            headers={'Authorization': f'Bearer {token}'},
            json={
                'id': 'acme-masked',
                'name': 'acme-masked',
                'env': {'ANTHROPIC_AUTH_TOKEN': '****1234'},
            },
        )
        assert r.status_code == 400
        assert 'real secret' in r.json()['detail']
        r = await admin_client.get('/api/profiles')
        assert not any(p['id'] == 'acme-masked' for p in r.json()['profiles'])

    async def test_session_callers_storing_a_mask_is_unchanged(
        self, admin_client
    ):
        """The UI keeps posting the plaintext its form loaded; a
        literal '****1234' typed into the form is the user's value,
        not an echo the server should second-guess."""
        await _create_profile(admin_client, 'acme-session')
        r = await admin_client.put(
            '/api/profiles/acme-session',
            json={'env': {'ANTHROPIC_AUTH_TOKEN': '****1234'}},
        )
        assert r.status_code == 200, r.text
        r = await admin_client.get('/api/profiles')
        assert (
            _env_of(r.json(), 'acme-session')['ANTHROPIC_AUTH_TOKEN']
            == '****1234'
        )

    async def test_a_no_auth_caller_still_sees_plaintext(
        self, admin_client, unauthed_client, override_async_session
    ):
        """Auth-off servers fall back to the default admin — that
        caller is 'no_auth', not a token, and keeps today's
        behaviour."""
        await _create_profile(admin_client, 'acme-noauth')
        async with override_async_session() as db:
            await db.merge(AppSettings(key='auth_required', value='false'))
            if not await db.get(User, DEFAULT_USER_ID):
                db.add(
                    User(
                        id=DEFAULT_USER_ID,
                        username='default-admin',
                        password_hash='disabled',
                        role='admin',
                        is_active=True,
                    )
                )
            await db.commit()

        r = await unauthed_client.get('/api/profiles')
        assert r.status_code == 200
        assert (
            _env_of(r.json(), 'acme-noauth')['ANTHROPIC_AUTH_TOKEN'] == SECRET
        )


class TestChatBotSecrets:
    async def _create_bot(self, client, name):
        r = await client.post(
            '/api/chat-bots',
            json={
                'name': name,
                'webhook_url': DINGTALK_URL,
                'secret': 'signing-secret-42',
            },
        )
        assert r.status_code == 200, r.text
        return r.json()

    async def test_single_get_is_masked_for_a_token_and_raw_for_a_session(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        bot = await self._create_bot(admin_client, 'ops-bot')
        bot_id = bot['id']

        r = await unauthed_client.get(
            f'/api/chat-bots/{bot_id}',
            headers={'Authorization': f'Bearer {token}'},
        )
        assert r.status_code == 200
        data = r.json()
        assert data['webhook_url'] == '****'  # the URL IS the secret
        # mask_secret keeps the last four of 'signing-secret-42'
        assert data['secret'] == '****t-42'
        assert data['name'] == 'ops-bot'

        r = await admin_client.get(f'/api/chat-bots/{bot_id}')
        assert r.json()['webhook_url'] == DINGTALK_URL
        assert r.json()['secret'] == 'signing-secret-42'

    async def test_create_and_update_responses_are_masked_for_a_token(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        r = await unauthed_client.post(
            '/api/chat-bots',
            headers={'Authorization': f'Bearer {token}'},
            json={
                'name': 'token-bot',
                'webhook_url': WECOM_URL,
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()['webhook_url'] == '****'

        r = await unauthed_client.put(
            f'/api/chat-bots/{r.json()["id"]}',
            headers={'Authorization': f'Bearer {token}'},
            json={'name': 'token-bot-2'},
        )
        assert r.status_code == 200, r.text
        assert r.json()['name'] == 'token-bot-2'
        assert r.json()['webhook_url'] == '****'

    async def test_a_masked_echo_keeps_the_stored_url_and_secret(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        bot = await self._create_bot(admin_client, 'echo-bot')
        bot_id = bot['id']

        # exactly the masks a token caller reads from the GET above
        r = await unauthed_client.put(
            f'/api/chat-bots/{bot_id}',
            headers={'Authorization': f'Bearer {token}'},
            json={'webhook_url': '****', 'secret': '****t-42'},
        )
        assert r.status_code == 200, r.text

        r = await admin_client.get(f'/api/chat-bots/{bot_id}')
        assert r.json()['webhook_url'] == DINGTALK_URL
        assert r.json()['secret'] == 'signing-secret-42'

    async def test_a_masked_url_on_create_is_refused(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        r = await unauthed_client.post(
            '/api/chat-bots',
            headers={'Authorization': f'Bearer {token}'},
            json={'name': 'masked-bot', 'webhook_url': '****'},
        )
        assert r.status_code == 400
        assert 'real secret' in r.json()['detail']
        r = await admin_client.get('/api/chat-bots')
        assert not any(b['name'] == 'masked-bot' for b in r.json())


class TestOtherSecretWriters:
    """The write guard is total (unit); these pin the breadth."""

    async def test_vision_key_echo_keeps_the_stored_key(
        self,
        admin_client,
        unauthed_client,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.delenv('KIE_API_KEY', raising=False)
        monkeypatch.setattr(
            vision_module,
            'VISION_CONFIG_PATH',
            tmp_path / 'vision.json',
        )

        r = await admin_client.put(
            '/api/vision/config', json={'kie_api_key': 'sk-kie-real-77'}
        )
        assert r.status_code == 200, r.text
        token = await _mint(admin_client)
        # the masked form a token caller reads (the route's own
        # bullet mask) echoed back must not store the bullets
        r = await unauthed_client.get(
            '/api/vision/config',
            headers={'Authorization': f'Bearer {token}'},
        )
        masked = r.json()['kie_api_key_masked']
        assert masked.endswith('77')

        r = await unauthed_client.put(
            '/api/vision/config',
            headers={'Authorization': f'Bearer {token}'},
            json={'kie_api_key': masked},
        )
        assert r.status_code == 200, r.text
        assert vision_module.get_kie_api_key() == 'sk-kie-real-77'

    async def test_email_account_create_with_a_mask_is_refused(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        r = await unauthed_client.post(
            '/api/email-accounts',
            headers={'Authorization': f'Bearer {token}'},
            json={'email': 'ops@example.com', 'password': '****1234'},
        )
        assert r.status_code == 400
        assert 'real secret' in r.json()['detail']

    async def test_ziniao_account_create_with_a_mask_is_refused(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        r = await unauthed_client.post(
            '/api/ziniao-accounts',
            headers={'Authorization': f'Bearer {token}'},
            json={
                'name': 'acme-ziniao',
                'company': 'acme',
                'username': 'acme-user',
                'password': '****',
            },
        )
        assert r.status_code == 400
        assert 'real secret' in r.json()['detail']


class TestTheRefusedCredentialRoute:
    async def test_a_token_cannot_mint_another_token_over_rest_either(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        r = await unauthed_client.post(
            '/api/auth/tokens',
            headers={'Authorization': f'Bearer {token}'},
            json={'name': 'begets'},
        )
        assert r.status_code == 403
        assert 'token' not in r.json()


class TestCurationStaysPinned:
    def test_every_curated_secret_route_is_pinned_here(self):
        masked = {
            route
            for route, behaviour in SECRET_READ_ROUTES.items()
            if behaviour == 'masked'
        }
        assert masked == PINNED_MASKED_ROUTES, (
            'SECRET_READ_ROUTES (unit drift guard) and the explicit '
            'pins in this file drifted apart — a curated route '
            'without a behaviour test is a promise nobody checks'
        )


class TestMcpEndpointUnharmed:
    """The middleware also walks /api/mcp JSON envelopes for token
    callers — idempotent over the tool layer's own redaction, and the
    envelope must survive the pass."""

    async def test_tools_list_survives_the_middleware(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        r = await unauthed_client.post(
            '/api/mcp',
            headers={'Authorization': f'Bearer {token}'},
            json={
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'tools/list',
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['result']['tools']
        assert 'manage_profile' in {t['name'] for t in body['result']['tools']}
