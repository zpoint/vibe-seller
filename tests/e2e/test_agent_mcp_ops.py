"""E2E: the MCP operator-parity tools over the Settings pages —
stores, browsers, Ziniao accounts, AI profiles, app settings + vision,
email accounts, chat bots, TickTick.

Companion to ``test_agent_mcp.py``; same mock-CLI lane (no model
credits, no real browser account, no real mailbox) and the same skip
guard. A SEPARATE FILE on purpose: the e2e-mock-cli CI job names its
test files explicitly.

The network edges these tools own are exercised only where they fail
DETERMINISTICALLY without the real thing: mailbox logins go to the
reserved ``.invalid`` domain (instant NXDOMAIN, a structured ``ok:
false``), a loopback chat-bot webhook is refused outright, and no tool
here posts to a real group or spends a model call. Everything else is
the real route stack: the same DB rows, the same redaction layer, the
same browser wrapper the UI starts.
"""

import json
import logging
import os

import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import create_store
from tests.e2e.test_agent_mcp import McpClient

logger = logging.getLogger(__name__)

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — skipping MCP e2e tests. '
        'Run with: MOCK_CLI=tests/e2e/mock_cli.py ./start.sh 7777',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

# A real-shaped WeCom webhook with an all-zero key: the platform is
# derived from the host (the URL is validated, never called).
WECOM_WEBHOOK = (
    'https://qyapi.weixin.qq.com/cgi-bin/webhook/send'
    '?key=00000000-0000-0000-0000-000000000000'
)

PROFILE_ENV = {
    'ANTHROPIC_BASE_URL': 'https://api.acme.example/anthropic',
    'ANTHROPIC_MODEL': 'acme-large',
    'ANTHROPIC_AUTH_TOKEN': 'sk-mcp-e2e-secret-1234',
}


# ── Fixtures ────────────────────────────────────────


@pytest.fixture(scope='module')
def api_token(api_client):
    """A minted token, with auth ON for the module (mirrors
    test_agent_mcp)."""
    resp = api_client.put(
        f'{BASE_URL}/api/settings', json={'auth_required': 'true'}
    )
    resp.raise_for_status()

    created = api_client.post(
        f'{BASE_URL}/api/auth/tokens',
        json={'name': 'e2e-mcp-ops', 'expires_in_days': 1},
    )
    created.raise_for_status()
    body = created.json()
    try:
        yield {'token': body['token'], 'id': body['id']}
    finally:
        api_client.delete(f'{BASE_URL}/api/auth/tokens/{body["id"]}')
        api_client.put(
            f'{BASE_URL}/api/settings', json={'auth_required': 'false'}
        )


@pytest.fixture(scope='module')
def mcp(api_token) -> McpClient:
    client = McpClient(token=api_token['token'])
    yield client
    client.close()


# ── manage_store ─────────────────────────────────────


class TestManageStore:
    def test_crud_and_bookmarks(self, mcp):
        created = mcp.call_tool(
            'manage_store',
            action='create',
            name='MCP Ops E2E Store',
            browser_backend='chrome',
            browser_config={
                'proxy_server': 'http://127.0.0.1:7890',
            },
        )
        assert created['ok'], created['text']
        store = created['data']['created']
        assert store['name'] == 'MCP Ops E2E Store'
        assert store['browser_backend'] == 'chrome'
        # Create mirrors the UI's payload: platform/country defaults
        # come from the schema, not from the caller.
        assert store['platforms'] == ['amazon']
        assert store['countries'] == ['US']
        store_id = store['id']

        listed = mcp.call_tool('manage_store', action='list')
        assert listed['ok'], listed['text']
        assert store_id in {s['id'] for s in listed['data']['stores']}

        got = mcp.call_tool('manage_store', action='get', store_id=store_id)
        assert got['ok'], got['text']
        assert got['data']['store']['id'] == store_id

        updated = mcp.call_tool(
            'manage_store',
            action='update',
            store_id=store_id,
            platform_countries={'amazon': ['US', 'AE']},
        )
        assert updated['ok'], updated['text']
        assert updated['data']['updated']['platform_countries'] == {
            'amazon': ['US', 'AE']
        }

        bookmarks = mcp.call_tool(
            'manage_store', action='bookmarks', store_id=store_id
        )
        assert bookmarks['ok'], bookmarks['text']

        deleted = mcp.call_tool(
            'manage_store', action='delete', store_id=store_id
        )
        assert deleted['ok'], deleted['text']

    def test_create_requires_a_name(self, mcp):
        refused = mcp.call_tool('manage_store', action='create')
        assert not refused['ok']
        assert 'name' in refused['text']

    def test_update_requires_a_field(self, mcp, api_client):
        store_id = create_store(api_client, 'MCP Ops No-Fields')['id']
        refused = mcp.call_tool(
            'manage_store', action='update', store_id=store_id
        )
        assert not refused['ok']
        assert 'platform_countries' in refused['text']
        deleted = mcp.call_tool(
            'manage_store', action='delete', store_id=store_id
        )
        assert deleted['ok'], deleted['text']

    def test_unknown_action_names_the_valid_ones(self, mcp):
        # Refused by the dispatcher's schema validation (INVALID_PARAMS),
        # naming the bad value and the valid choices.
        error = mcp.call_tool_rejected('manage_store', action='remove')
        assert error['code'] == -32602
        assert 'remove' in error['message']
        assert 'delete' in error['message']


# ── browser ──────────────────────────────────────────


class TestBrowserTool:
    def test_start_store_launches_the_store_browser(self, mcp, api_client):
        """A real launch: the same wrapper + backend the store's task
        agents use, asserted through the route's own result."""
        store_id = create_store(api_client, 'MCP Ops Browser Store')['id']
        try:
            started = mcp.call_tool(
                'browser', action='start_store', store_id=store_id
            )
            assert started['ok'], started['text']
            assert started['data']['ok'] is True
        finally:
            deleted = mcp.call_tool(
                'manage_store',
                action='delete',
                store_id=store_id,
                delete_files=True,
            )
            assert deleted['ok'], deleted['text']

    def test_start_store_missing_store_is_an_error(self, mcp):
        refused = mcp.call_tool(
            'browser', action='start_store', store_id='nope'
        )
        assert not refused['ok']
        assert 'not found' in refused['text'].lower()

    def test_start_store_requires_store_id(self, mcp):
        refused = mcp.call_tool('browser', action='start_store')
        assert not refused['ok']
        assert 'store_id' in refused['text']

    def test_ziniao_launcher_returns_a_download_url_for_a_human(self, mcp):
        data = mcp.call_tool('browser', action='ziniao_launcher')
        assert data['ok'], data['text']
        assert data['data']['download_url'].endswith('/api/ziniao/launcher')
        assert data['data']['download_url'].startswith('http')
        assert 'user' in data['data']['note'].lower()


# ── manage_ziniao_account ────────────────────────────


class TestManageZiniaoAccount:
    """CRUD with the password write-only. The actions that need the
    real Ziniao client (browsers, restart) are not exercised here —
    this lane has no client, and a clean refusal of CRUD shape is the
    deterministic part."""

    def test_crud_and_password_never_comes_back(self, mcp):
        created = mcp.call_tool(
            'manage_ziniao_account',
            action='create',
            company='acme',
            username='ops@acme.example',
            password='ziniao-pass-1234',
            name='',
        )
        assert created['ok'], created['text']
        account = created['data']['created']
        # The UI's rule: a blank name falls back to the company.
        assert account['name'] == 'acme'
        assert 'ziniao-pass-1234' not in json.dumps(created)
        account_id = account['id']

        listed = mcp.call_tool('manage_ziniao_account', action='list')
        assert listed['ok'], listed['text']
        assert account_id in {a['id'] for a in listed['data']['accounts']}
        assert 'ziniao-pass-1234' not in json.dumps(listed)

        updated = mcp.call_tool(
            'manage_ziniao_account',
            action='update',
            account_id=account_id,
            socket_port=16861,
        )
        assert updated['ok'], updated['text']
        assert updated['data']['updated']['socket_port'] == 16861
        assert 'ziniao-pass-1234' not in json.dumps(updated)

        deleted = mcp.call_tool(
            'manage_ziniao_account',
            action='delete',
            account_id=account_id,
        )
        assert deleted['ok'], deleted['text']

    def test_create_requires_a_password(self, mcp):
        refused = mcp.call_tool(
            'manage_ziniao_account',
            action='create',
            company='acme',
            username='ops@acme.example',
        )
        assert not refused['ok']
        assert 'password' in refused['text']


# ── manage_profile ───────────────────────────────────


class TestManageProfile:
    PROFILE_ID = 'mcp-e2e-acme'

    @pytest.fixture(autouse=True)
    def _clean_profile(self, mcp):
        mcp.call_tool(
            'manage_profile', action='delete', profile_id=self.PROFILE_ID
        )
        yield
        mcp.call_tool(
            'manage_profile', action='delete', profile_id=self.PROFILE_ID
        )

    def test_create_masks_the_key_and_merges_updates(self, mcp):
        created = mcp.call_tool(
            'manage_profile',
            action='create',
            profile_id=self.PROFILE_ID,
            name='MCP E2E Acme',
            env=PROFILE_ENV,
        )
        assert created['ok'], created['text']
        env = created['data']['created']['env']
        # The key is write-only; the model row stays readable so an
        # agent can tell profiles apart.
        assert env['ANTHROPIC_AUTH_TOKEN'] != ('sk-mcp-e2e-secret-1234')
        assert env['ANTHROPIC_MODEL'] == 'acme-large'

        listed = mcp.call_tool('manage_profile', action='list')
        assert listed['ok'], listed['text']
        entry = next(
            p for p in listed['data']['profiles'] if p['id'] == self.PROFILE_ID
        )
        assert entry['env']['ANTHROPIC_AUTH_TOKEN'].startswith('****')
        assert 'sk-mcp-e2e-secret-1234' not in json.dumps(listed)

        # An update that touches ONE key keeps the stored secret — the
        # natural get → edit → update flow posts no key at all.
        updated = mcp.call_tool(
            'manage_profile',
            action='update',
            profile_id=self.PROFILE_ID,
            env={'ANTHROPIC_MODEL': 'acme-small'},
        )
        assert updated['ok'], updated['text']
        env = updated['data']['updated']['env']
        assert env['ANTHROPIC_MODEL'] == 'acme-small'
        assert env['ANTHROPIC_AUTH_TOKEN'].startswith('****')
        assert 'sk-mcp-e2e-secret-1234' not in json.dumps(updated)

        # The secret round-trips: a REAL new key replaces the old one
        # and reads back masked, never plaintext.
        rotated = mcp.call_tool(
            'manage_profile',
            action='update',
            profile_id=self.PROFILE_ID,
            env={'ANTHROPIC_AUTH_TOKEN': 'sk-rotated-9999'},
        )
        assert rotated['ok'], rotated['text']
        got = mcp.call_tool(
            'manage_profile', action='get', profile_id=self.PROFILE_ID
        )
        assert got['data']['profile']['env']['ANTHROPIC_AUTH_TOKEN'] != (
            'sk-rotated-9999'
        )

    def test_create_requires_a_name(self, mcp):
        refused = mcp.call_tool(
            'manage_profile', action='create', profile_id='x'
        )
        assert not refused['ok']
        assert 'name' in refused['text']

    def test_get_unknown_profile_is_an_error(self, mcp):
        refused = mcp.call_tool(
            'manage_profile', action='get', profile_id='nope'
        )
        assert not refused['ok']

    def test_presets_are_listed(self, mcp):
        presets = mcp.call_tool('manage_profile', action='presets')
        assert presets['ok'], presets['text']
        assert presets['data']['presets']


# ── app_settings (+ Google Workspace, vision) ────────


class TestAppSettings:
    def test_get_and_update_round_trip_with_clamping(self, mcp):
        current = mcp.call_tool('app_settings', action='get')
        assert current['ok'], current['text']
        assert 'max_agent_concurrency' in current['data']

        updated = mcp.call_tool(
            'app_settings',
            action='update',
            settings={'max_agent_concurrency': 99},
        )
        assert updated['ok'], updated['text']
        # The echo is what LANDED (clamped by the route), not what was
        # sent.
        assert updated['data']['values']['max_agent_concurrency'] == '10'

    def test_update_rejects_unknown_keys(self, mcp):
        refused = mcp.call_tool(
            'app_settings',
            action='update',
            settings={'max_agent_concurency': 3},
        )
        assert not refused['ok']
        assert 'max_agent_concurency' in refused['text']

    def test_update_requires_a_settings_object(self, mcp):
        refused = mcp.call_tool('app_settings', action='update')
        assert not refused['ok']
        assert 'settings' in refused['text']

    def test_gws_status_reports_without_installing(self, mcp):
        status = mcp.call_tool('app_settings', action='gws_status')
        assert status['ok'], status['text']
        assert 'binary' in status['data']

    def test_vision_key_is_write_only(self, mcp):
        empty = mcp.call_tool('app_settings', action='vision_get')
        assert empty['ok'], empty['text']
        assert 'kie_api_key_set' in empty['data']

        updated = mcp.call_tool(
            'app_settings',
            action='vision_update',
            kie_api_key='kie-mcp-e2e-123456',
        )
        assert updated['ok'], updated['text']
        assert updated['data']['kie_api_key_set'] is True
        assert updated['data']['kie_api_key_masked']
        assert 'kie-mcp-e2e-123456' not in json.dumps(updated)

        after = mcp.call_tool('app_settings', action='vision_get')
        assert after['data']['kie_api_key_set'] is True
        assert 'kie-mcp-e2e-123456' not in json.dumps(after)


# ── manage_email_account ─────────────────────────────


class TestManageEmailAccount:
    EMAIL = 'ops@acme.example'

    @pytest.fixture(autouse=True)
    def _clean_account(self, mcp):
        listed = mcp.call_tool('manage_email_account', action='list')
        if listed['ok']:
            for account in listed['data']['accounts']:
                if account['email'] == self.EMAIL:
                    mcp.call_tool(
                        'manage_email_account',
                        action='delete',
                        account_id=account['id'],
                    )
        yield

    def _create(self, mcp) -> str:
        created = mcp.call_tool(
            'manage_email_account',
            action='create',
            email=self.EMAIL,
            password='mail-pass-1234',
            imap_host='imap.invalid',
            smtp_host='smtp.invalid',
        )
        assert created['ok'], created['text']
        assert 'mail-pass-1234' not in json.dumps(created)
        assert 'password' not in created['data']['created']
        return created['data']['created']['id']

    def test_crud_with_password_write_only(self, mcp):
        account_id = self._create(mcp)

        listed = mcp.call_tool('manage_email_account', action='list')
        assert listed['ok'], listed['text']
        assert account_id in {a['id'] for a in listed['data']['accounts']}
        assert 'mail-pass-1234' not in json.dumps(listed)

        got = mcp.call_tool(
            'manage_email_account', action='get', account_id=account_id
        )
        assert got['ok'], got['text']
        assert got['data']['account']['id'] == account_id

        updated = mcp.call_tool(
            'manage_email_account',
            action='update',
            account_id=account_id,
            smtp_port=587,
        )
        assert updated['ok'], updated['text']
        assert updated['data']['updated']['smtp_port'] == 587
        assert 'mail-pass-1234' not in json.dumps(updated)

        deleted = mcp.call_tool(
            'manage_email_account',
            action='delete',
            account_id=account_id,
        )
        assert deleted['ok'], deleted['text']

    def test_create_requires_a_password(self, mcp):
        refused = mcp.call_tool(
            'manage_email_account',
            action='create',
            email=self.EMAIL,
        )
        assert not refused['ok']
        assert 'password' in refused['text']

    def test_connection_tests_fail_cleanly_on_invalid_hosts(self, mcp):
        """No real mailbox in this lane: the reserved ``.invalid``
        domain fails DNS instantly, and the route answers a structured
        ``ok: false`` — never a crash, never the password."""
        account_id = self._create(mcp)

        tested = mcp.call_tool(
            'manage_email_account',
            action='test',
            email=self.EMAIL,
            imap_host='imap.invalid',
            password='mail-pass-1234',
        )
        assert tested['ok'], tested['text']
        assert tested['data']['ok'] is False
        assert 'mail-pass-1234' not in json.dumps(tested)

        smtp = mcp.call_tool(
            'manage_email_account',
            action='test_smtp',
            account_id=account_id,
        )
        assert smtp['ok'], smtp['text']
        assert smtp['data']['ok'] is False

        synced = mcp.call_tool(
            'manage_email_account',
            action='sync_now',
            account_email=self.EMAIL,
        )
        # The blocked sync surfaces the failure as a tool error naming
        # the account, not a hang.
        assert not synced['ok']
        assert 'sync failed' in synced['text'].lower()

    def test_links_link_unlink_and_info_by_store(self, mcp, api_client):
        account_id = self._create(mcp)
        store_id = create_store(api_client, 'MCP Ops Mail Store')['id']
        try:
            empty = mcp.call_tool(
                'manage_email_account',
                action='links',
                account_id=account_id,
            )
            assert empty['ok'], empty['text']
            assert empty['data']['links'] == []

            linked = mcp.call_tool(
                'manage_email_account',
                action='link',
                store_id=store_id,
                email_account_id=account_id,
            )
            assert linked['ok'], linked['text']
            assert linked['data']['linked']['store_id'] == store_id

            links = mcp.call_tool(
                'manage_email_account',
                action='links',
                account_id=account_id,
            )
            assert len(links['data']['links']) == 1

            info = mcp.call_tool(
                'manage_email_account',
                action='info_by_store',
                store_id=store_id,
            )
            assert info['ok'], info['text']
            assert info['data']['accounts'][0]['email'] == self.EMAIL

            unlinked = mcp.call_tool(
                'manage_email_account',
                action='unlink',
                store_id=store_id,
                link_id=links['data']['links'][0]['link_id'],
            )
            assert unlinked['ok'], unlinked['text']
        finally:
            mcp.call_tool(
                'manage_email_account',
                action='delete',
                account_id=account_id,
            )
            mcp.call_tool('manage_store', action='delete', store_id=store_id)


# ── manage_chat_bot ──────────────────────────────────


class TestManageChatBot:
    def test_platforms_list_get_delete_with_masked_webhook(self, mcp):
        platforms = mcp.call_tool('manage_chat_bot', action='platforms')
        assert platforms['ok'], platforms['text']
        assert 'wecom' in {p['name'] for p in platforms['data']['platforms']}

        created = mcp.call_tool(
            'manage_chat_bot',
            action='create',
            name='mcp-e2e-room',
            webhook_url=WECOM_WEBHOOK,
        )
        assert created['ok'], created['text']
        bot = created['data']['created']
        assert bot['webhook_url'] == '****'
        assert WECOM_WEBHOOK not in json.dumps(created)
        bot_id = bot['id']

        listed = mcp.call_tool('manage_chat_bot', action='list')
        assert listed['ok'], listed['text']
        entry = next(b for b in listed['data']['bots'] if b['id'] == bot_id)
        assert entry['webhook_url_masked'].startswith('https://')
        assert '00000000' not in entry['webhook_url_masked']

        got = mcp.call_tool('manage_chat_bot', action='get', bot_id=bot_id)
        assert got['ok'], got['text']
        # Even the edit-form route (raw URL over REST) is masked here.
        assert got['data']['bot']['webhook_url'] == '****'
        assert WECOM_WEBHOOK not in json.dumps(got)

        updated = mcp.call_tool(
            'manage_chat_bot',
            action='update',
            bot_id=bot_id,
            name='mcp-e2e-room-2',
        )
        assert updated['ok'], updated['text']
        assert updated['data']['updated']['name'] == 'mcp-e2e-room-2'

        deleted = mcp.call_tool(
            'manage_chat_bot', action='delete', bot_id=bot_id
        )
        assert deleted['ok'], deleted['text']

    def test_a_loopback_webhook_is_refused(self, mcp):
        """No real group in this lane — and the server refuses a
        loopback webhook outright (a webhook secret never goes to a
        local stub in production)."""
        refused = mcp.call_tool(
            'manage_chat_bot',
            action='create',
            name='loopback-bot',
            webhook_url='http://127.0.0.1:9999/webhook/send'
            '?key=00000000-0000-0000-0000-000000000000',
        )
        assert not refused['ok']
        assert 'webhook' in refused['text'].lower()

    def test_update_without_fields_is_rejected(self, mcp):
        refused = mcp.call_tool(
            'manage_chat_bot', action='update', bot_id='any'
        )
        assert not refused['ok']


# ── manage_ticktick ──────────────────────────────────


class TestManageTicktick:
    def test_status_when_not_connected(self, mcp):
        status = mcp.call_tool('manage_ticktick', action='status')
        assert status['ok'], status['text']
        assert 'connected' in status['data']

    def test_authorize_returns_a_url_for_a_human(self, mcp):
        authorized = mcp.call_tool(
            'manage_ticktick',
            action='authorize',
            client_id='cid-mcp-e2e',
            client_secret='csecret-mcp-e2e',
            service_type='ticktick',
        )
        assert authorized['ok'], authorized['text']
        data = authorized['data']
        assert data['auth_url'].startswith(
            'https://ticktick.com/oauth/authorize?'
        )
        assert 'state=' in data['auth_url']
        assert data['callback_url'].endswith('/api/dida365/callback')
        assert 'user' in data['note'].lower()
        # The client secret is write-only — never echoed.
        assert 'csecret-mcp-e2e' not in json.dumps(data)

        # A mask can only be an echo of the stored secret: re-running
        # authorize with the masked echo keeps working (the stored
        # credential is used), never writes '****' anywhere visible.
        again = mcp.call_tool(
            'manage_ticktick',
            action='authorize',
            client_id='cid-mcp-e2e',
            client_secret='****-e2e',
            service_type='ticktick',
        )
        assert again['ok'], again['text']
        assert again['data']['auth_url']

        disconnected = mcp.call_tool('manage_ticktick', action='disconnect')
        assert disconnected['ok'], disconnected['text']

    def test_configure_requires_a_project_id(self, mcp):
        refused = mcp.call_tool('manage_ticktick', action='configure')
        assert not refused['ok']
        assert 'project_id' in refused['text']
