"""Workflow tests: NO caller ever reads a stored secret over REST.

The guard is one middleware (``app/rest_secret_guard.py``) that
masks secret-classified fields out of every JSON response for every
credential class — admin session, member session, ``vst_`` token
(either role), and the no-auth default admin — and restores a masked
(or empty) echo on write routes so an untouched form keeps the
stored secret. The only way out is ``POST /api/secrets/reveal``:
admin-only, token-refused, logged per call.

Pinned here, per concern:

* READ — for every curated route: all four caller classes receive
  the mask; nobody receives the stored value;
* WRITE — a mask (or an empty password box) echoed back through an
  update keeps the stored secret, verified from STORAGE (the
  manager/DB), never through the API; a member OVERWRITING a secret
  with a fresh real value works (members may write, never read); a
  mask on a create-style route is a 400, never a stored ``'****'``;
* DISCLOSURE — minting a token still hands the fresh plaintext to
  its creator once; the reveal endpoint answers an admin session
  with the value + one log line that does NOT contain it, refuses
  members and every token caller, and is ``Cache-Control: no-store``.

The curated route list these behaviour pins must cover lives with
the unit drift guard (``tests/unit/test_rest_secret_guard.py``);
``test_every_curated_secret_route_is_pinned_here`` keeps the two
files from drifting apart.
"""

import logging

import pytest

from app.ai.profiles import ProfileManager
from app.models.chat_bot import ChatBot
from app.routers import profiles as profiles_router
import app.vision as vision_module
from tests.unit.test_rest_secret_guard import SECRET_READ_ROUTES

pytestmark = pytest.mark.workflow

SECRET = 'sk-ant-secret-token-1234'
BASE_URL = 'https://api.example.com'
WECOM_URL = 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdefgh1234'
DINGTALK_URL = 'https://oapi.dingtalk.com/robot/send?access_token=tok1234'

# The routes whose masked-for-everyone behaviour is pinned by an
# explicit test in this file (see the class docstrings).
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


def _bearer(token):
    return {'Authorization': f'Bearer {token}'}


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


def _stored_env(profile_id):
    """The stored env, read from the manager (storage), not the API."""
    profile = ProfileManager.get_profile(profile_id)
    assert profile is not None, profile_id
    return profile['env']


async def _stored_bot(bot_id, override_async_session):
    """The stored bot row, read from the test DB, not the API."""
    async with override_async_session() as db:
        bot = await db.get(ChatBot, bot_id)
    assert bot is not None, bot_id
    return bot


async def _create_bot(client, name):
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


class TestProfileSecretsMaskedForEveryone:
    """GET/POST/PUT /api/profiles — one test per curated route, each
    checking all four caller classes."""

    async def test_list_is_masked_for_every_caller(
        self, admin_client, member_client, unauthed_client
    ):
        admin_token = await _mint(admin_client)
        member_token = await _mint(member_client, 'member-token')
        await _create_profile(admin_client, 'acme-probe')

        calls = [
            ('admin session', admin_client, {}),
            ('member session', member_client, {}),
            (
                'admin token',
                unauthed_client,
                _bearer(admin_token),
            ),
            (
                'member token',
                unauthed_client,
                _bearer(member_token),
            ),
        ]
        for label, client, headers in calls:
            r = await client.get('/api/profiles', headers=headers)
            assert r.status_code == 200, label
            env = _env_of(r.json(), 'acme-probe')
            masked = env['ANTHROPIC_AUTH_TOKEN']
            assert masked != SECRET, label
            assert masked == f'****{SECRET[-4:]}', label
            # non-secret env rows stay readable so profiles remain
            # tellable apart
            assert env['ANTHROPIC_BASE_URL'] == BASE_URL, label
        assert _stored_env('acme-probe')['ANTHROPIC_AUTH_TOKEN'] == SECRET

    async def test_create_response_is_masked_for_every_caller(
        self, admin_client, member_client
    ):
        r = await admin_client.post(
            '/api/profiles',
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
        assert r.json()['env']['ANTHROPIC_AUTH_TOKEN'] == '****9876'

        # a MEMBER creating a profile: same mask, and the real key is
        # what lands in storage (members may write secrets)
        r = await member_client.post(
            '/api/profiles',
            json={
                'id': 'acme-member',
                'name': 'acme-member',
                'env': {
                    'ANTHROPIC_BASE_URL': BASE_URL,
                    'ANTHROPIC_AUTH_TOKEN': 'member-key-5555',
                },
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()['env']['ANTHROPIC_AUTH_TOKEN'] == '****5555'
        assert (
            _stored_env('acme-member')['ANTHROPIC_AUTH_TOKEN']
            == 'member-key-5555'
        )

    async def test_update_response_is_masked_for_every_caller(
        self, admin_client, member_client, unauthed_client
    ):
        admin_token = await _mint(admin_client)
        await _create_profile(admin_client, 'acme-update')

        r = await admin_client.put(
            '/api/profiles/acme-update', json={'name': 'renamed-acme'}
        )
        assert r.status_code == 200, r.text
        assert r.json()['name'] == 'renamed-acme'
        assert r.json()['env']['ANTHROPIC_AUTH_TOKEN'].startswith('****')

        r = await unauthed_client.put(
            '/api/profiles/acme-update',
            headers=_bearer(admin_token),
            json={'name': 'renamed-again'},
        )
        assert r.status_code == 200, r.text
        assert r.json()['env']['ANTHROPIC_AUTH_TOKEN'].startswith('****')


class TestChatBotSecretsMaskedForEveryone:
    async def test_single_get_is_masked_for_every_caller(
        self, admin_client, member_client, unauthed_client
    ):
        admin_token = await _mint(admin_client)
        member_token = await _mint(member_client, 'member-token')
        bot = await _create_bot(admin_client, 'ops-bot')
        bot_id = bot['id']

        for label, client, headers in (
            ('admin session', admin_client, {}),
            ('member session', member_client, {}),
            ('admin token', unauthed_client, _bearer(admin_token)),
            ('member token', unauthed_client, _bearer(member_token)),
        ):
            r = await client.get(f'/api/chat-bots/{bot_id}', headers=headers)
            assert r.status_code == 200, label
            data = r.json()
            assert data['webhook_url'] == '****', label  # URL IS the secret
            # mask keeps the last four of 'signing-secret-42'
            assert data['secret'] == '****t-42', label
            assert data['name'] == 'ops-bot', label

    async def test_create_and_update_responses_are_masked(
        self, admin_client, member_client
    ):
        r = await admin_client.post(
            '/api/chat-bots',
            json={'name': 'wecom-bot', 'webhook_url': WECOM_URL},
        )
        assert r.status_code == 200, r.text
        assert r.json()['webhook_url'] == '****'

        r = await member_client.post(
            '/api/chat-bots',
            json={'name': 'member-bot', 'webhook_url': WECOM_URL},
        )
        assert r.status_code == 200, r.text
        assert r.json()['webhook_url'] == '****'

        bot_id = r.json()['id']
        r = await member_client.put(
            f'/api/chat-bots/{bot_id}', json={'name': 'member-bot-2'}
        )
        assert r.status_code == 200, r.text
        assert r.json()['name'] == 'member-bot-2'
        assert r.json()['webhook_url'] == '****'


class TestMaskedEchoKeepsTheStoredSecret:
    """The round trip every edit form now relies on: read masked,
    post the whole form back — the stored secret survives."""

    async def test_profile_env_echo_keeps_the_stored_key(
        self, admin_client, member_client
    ):
        await _create_profile(admin_client, 'acme-echo')
        masked_env = _env_of(
            (await admin_client.get('/api/profiles')).json(), 'acme-echo'
        )
        assert masked_env['ANTHROPIC_AUTH_TOKEN'] != SECRET

        # the ADMIN's form echo…
        r = await admin_client.put(
            '/api/profiles/acme-echo',
            json={'name': 'acme-echo', 'env': masked_env},
        )
        assert r.status_code == 200, r.text
        assert _stored_env('acme-echo')['ANTHROPIC_AUTH_TOKEN'] == SECRET
        assert _stored_env('acme-echo')['ANTHROPIC_BASE_URL'] == BASE_URL

        # …and a MEMBER's (the member read the same masks)
        masked_env = _env_of(
            (await member_client.get('/api/profiles')).json(), 'acme-echo'
        )
        r = await member_client.put(
            '/api/profiles/acme-echo',
            json={'name': 'acme-echo', 'env': masked_env},
        )
        assert r.status_code == 200, r.text
        assert _stored_env('acme-echo')['ANTHROPIC_AUTH_TOKEN'] == SECRET

    async def test_a_member_overwrites_a_secret_with_a_real_value(
        self, admin_client, member_client
    ):
        """Members may WRITE a secret they can never read."""
        await _create_profile(admin_client, 'acme-rotate')
        r = await member_client.put(
            '/api/profiles/acme-rotate',
            json={
                'env': {
                    'ANTHROPIC_BASE_URL': BASE_URL,
                    'ANTHROPIC_AUTH_TOKEN': 'rotated-by-member-99',
                }
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()['env']['ANTHROPIC_AUTH_TOKEN'] == '****r-99'
        assert (
            _stored_env('acme-rotate')['ANTHROPIC_AUTH_TOKEN']
            == 'rotated-by-member-99'
        )

    async def test_an_empty_secret_field_keeps_the_stored_value(
        self, admin_client, override_async_session
    ):
        """An untouched password box posts '' — unchanged, not empty."""
        bot = await _create_bot(admin_client, 'blank-bot')
        bot_id = bot['id']
        r = await admin_client.put(
            f'/api/chat-bots/{bot_id}',
            json={'name': 'blank-bot', 'secret': ''},
        )
        assert r.status_code == 200, r.text
        stored = await _stored_bot(bot_id, override_async_session)
        assert stored.secret == 'signing-secret-42'

    async def test_chat_bot_echo_keeps_url_and_secret(
        self, admin_client, member_client, override_async_session
    ):
        bot = await _create_bot(admin_client, 'echo-bot')
        bot_id = bot['id']

        # exactly the masks the edit forms read from the GET
        for client in (admin_client, member_client):
            r = await client.put(
                f'/api/chat-bots/{bot_id}',
                json={'webhook_url': '****', 'secret': '****t-42'},
            )
            assert r.status_code == 200, r.text
        stored = await _stored_bot(bot_id, override_async_session)
        assert stored.webhook_url == DINGTALK_URL
        assert stored.secret == 'signing-secret-42'

    async def test_a_fresh_key_through_the_same_put_is_stored(
        self, admin_client
    ):
        await _create_profile(admin_client, 'acme-fresh')
        r = await admin_client.put(
            '/api/profiles/acme-fresh',
            json={
                'env': {
                    'ANTHROPIC_BASE_URL': BASE_URL,
                    'ANTHROPIC_AUTH_TOKEN': 'rotated-key-4321',
                }
            },
        )
        assert r.status_code == 200, r.text
        assert (
            _stored_env('acme-fresh')['ANTHROPIC_AUTH_TOKEN']
            == 'rotated-key-4321'
        )

    async def test_a_mask_on_create_is_refused_not_stored(
        self, admin_client, member_client
    ):
        for client in (admin_client, member_client):
            r = await client.post(
                '/api/profiles',
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

    async def test_validate_restores_a_masked_key_against_the_profile(
        self, admin_client
    ):
        """The save-flow probe: the form holds the mask for an
        untouched key and names the profile — the probe runs with the
        STORED key (validate_profile_env is stubbed to echo it)."""
        await _create_profile(admin_client, 'acme-validate')
        masked_env = _env_of(
            (await admin_client.get('/api/profiles')).json(),
            'acme-validate',
        )
        seen = {}

        class _ProbeResult:
            def to_dict(self):
                return {'ok': True}

        async def fake_probe(env):
            seen.update(env)
            return _ProbeResult()

        original = profiles_router.validate_profile_env
        profiles_router.validate_profile_env = fake_probe
        try:
            r = await admin_client.post(
                '/api/profiles/validate',
                json={
                    'profile_id': 'acme-validate',
                    'env': masked_env,
                },
            )
        finally:
            profiles_router.validate_profile_env = original
        assert r.status_code == 200, r.text
        assert r.json()['ok'] is True
        # the probe ran with the STORED key, not the mask
        assert seen['ANTHROPIC_AUTH_TOKEN'] == SECRET


class TestTheDisclosedCredentialRoutes:
    async def test_mint_hands_the_fresh_token_to_its_creator(
        self, admin_client
    ):
        r = await admin_client.post(
            '/api/auth/tokens', json={'name': 'shown-once'}
        )
        assert r.status_code == 200, r.text
        assert r.json()['token'].startswith('vst_')

    async def test_a_token_cannot_mint_another_token_over_rest_either(
        self, admin_client, unauthed_client
    ):
        token = await _mint(admin_client)
        r = await unauthed_client.post(
            '/api/auth/tokens', headers=_bearer(token), json={'name': 'x'}
        )
        assert r.status_code == 403
        assert 'token' not in r.json()


class TestTheRevealEndpoint:
    """The one way out — admin session only, logged, never cached."""

    async def test_an_admin_session_receives_the_value_and_one_log_line(
        self, admin_client, caplog
    ):
        await _create_profile(admin_client, 'acme-reveal')
        with caplog.at_level(logging.INFO, logger='app.routers.secrets'):
            r = await admin_client.post(
                '/api/secrets/reveal',
                json={
                    'resource': 'profile',
                    'id': 'acme-reveal',
                    'field': 'ANTHROPIC_AUTH_TOKEN',
                },
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['value'] == SECRET
        assert body['resource'] == 'profile'
        # never cached
        assert r.headers['cache-control'] == 'no-store'

        lines = [
            rec.getMessage()
            for rec in caplog.records
            if rec.getMessage().startswith('secret revealed:')
        ]
        assert len(lines) == 1, lines
        line = lines[0]
        assert 'user=admin' in line
        assert 'role=admin' in line
        assert 'resource=profile:acme-reveal' in line
        assert 'field=ANTHROPIC_AUTH_TOKEN' in line
        assert 'via=session' in line
        # the value NEVER appears in the log
        assert SECRET not in line
        assert SECRET[-4:] not in line

    async def test_a_member_is_refused_whatever_the_credential(
        self, admin_client, member_client, unauthed_client
    ):
        await _create_profile(admin_client, 'acme-lock')
        admin_token = await _mint(admin_client)
        member_token = await _mint(member_client, 'member-token')
        payload = {
            'resource': 'profile',
            'id': 'acme-lock',
            'field': 'ANTHROPIC_AUTH_TOKEN',
        }

        r = await member_client.post('/api/secrets/reveal', json=payload)
        assert r.status_code == 403
        r = await unauthed_client.post(
            '/api/secrets/reveal', headers=_bearer(member_token), json=payload
        )
        assert r.status_code == 403
        # an ADMIN's token is refused too — tokens never read secrets
        r = await unauthed_client.post(
            '/api/secrets/reveal', headers=_bearer(admin_token), json=payload
        )
        assert r.status_code == 403
        assert 'value' not in r.json()

    async def test_unknown_resource_or_field_is_a_404(self, admin_client):
        for payload in (
            {'resource': 'bank', 'id': 'x', 'field': 'password'},
            {
                'resource': 'profile',
                'id': 'no-such-profile',
                'field': 'ANTHROPIC_AUTH_TOKEN',
            },
            {
                'resource': 'profile',
                'id': 'no-such-profile',
                'field': 'NOT_A_FIELD',
            },
            {'resource': 'chat_bot', 'id': 'no-such-bot', 'field': 'secret'},
            {'resource': 'vision', 'id': '', 'field': 'not_the_key'},
        ):
            r = await admin_client.post('/api/secrets/reveal', json=payload)
            assert r.status_code == 404, payload

    async def test_every_resource_shape_answers(
        self, admin_client, monkeypatch, tmp_path
    ):
        """chat_bot / email_account / ziniao_account / vision /
        dida365 all resolve through the one endpoint shape."""
        bot = await _create_bot(admin_client, 'reveal-bot')
        r = await admin_client.post(
            '/api/secrets/reveal',
            json={
                'resource': 'chat_bot',
                'id': bot['id'],
                'field': 'webhook_url',
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()['value'] == DINGTALK_URL

        r = await admin_client.post(
            '/api/secrets/reveal',
            json={
                'resource': 'chat_bot',
                'id': bot['id'],
                'field': 'secret',
            },
        )
        assert r.json()['value'] == 'signing-secret-42'

        r = await admin_client.post(
            '/api/secrets/reveal',
            json={'resource': 'email_account', 'id': 'none', 'field': 'x'},
        )
        assert r.status_code == 404

        monkeypatch.delenv('KIE_API_KEY', raising=False)
        monkeypatch.setattr(
            vision_module, 'VISION_CONFIG_PATH', tmp_path / 'vision.json'
        )
        vision_module.save_vision_config('sk-kie-real-77')
        r = await admin_client.post(
            '/api/secrets/reveal',
            json={'resource': 'vision', 'id': '', 'field': 'kie_api_key'},
        )
        assert r.status_code == 200, r.text
        assert r.json()['value'] == 'sk-kie-real-77'


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
    """The middleware also walks /api/mcp JSON envelopes for every
    caller — idempotent over the tool layer's own redaction, and the
    envelope must survive the pass."""

    async def test_tools_list_survives_the_middleware(
        self, admin_client, unauthed_client
    ):
        r = await admin_client.post(
            '/api/mcp',
            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
        )
        assert r.status_code == 200, r.text
        assert r.json()['result']['tools']

        token = await _mint(admin_client)
        r = await unauthed_client.post(
            '/api/mcp',
            headers=_bearer(token),
            json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['result']['tools']
        assert 'manage_profile' in {t['name'] for t in body['result']['tools']}
