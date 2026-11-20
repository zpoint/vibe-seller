"""E2E, live: a person signs each platform in, then a real agent uses it.

Runs only when ``E2E_CLI_LIVE`` names the platforms to test
(``wecom,feishu,dingtalk``). The server it owns runs on a PERSISTENT home
(``E2E_CLI_LIVE_HOME``, default ``~/.vibe-seller-cli-live``) so a sign-in
survives re-runs. For each platform:

1. Connect through the product API (install, then a login with
   ``enable_after``). The test prints what the person must do — the
   link + code, or where the WeCom QR is — and waits up to
   ``E2E_CLI_LIVE_WAIT`` seconds (default 600) for the approval. The
   same login shows on the owned server's Settings → Integrations page.
2. Once signed in, the server enables the platform by itself.
3. A real agent task asks, read-only, "who are you on <platform>?". The
   task must have run the platform's CLI through Bash and its answer
   must name the identity the Settings card shows.

Run (any OS; a person at hand to scan / approve):

    E2E_CLI_LIVE=wecom,feishu,dingtalk E2E_PROVIDER_MAP=... \\
      pytest --e2e -s tests/e2e/test_workplace_cli_live.py
"""

import json
import os
from pathlib import Path
import time

import pytest

from tests.e2e import e2e_helpers
from tests.e2e.own_server import AGENT_SERVER_ENV, OwnServer, pick_free_port

pytestmark = [pytest.mark.e2e]

LIVE = [k for k in os.environ.get('E2E_CLI_LIVE', '').split(',') if k]
if not LIVE:
    pytest.skip(
        'set E2E_CLI_LIVE=wecom,feishu,dingtalk (needs a person to sign in)',
        allow_module_level=True,
    )

HOME = Path(
    os.environ.get('E2E_CLI_LIVE_HOME') or Path.home() / '.vibe-seller-cli-live'
).expanduser()
WAIT_S = int(os.environ.get('E2E_CLI_LIVE_WAIT', '600'))

ASK = {
    'wecom': (
        'Using the wecom skill (wecom-cli), find out which WeCom identity '
        'you act as — read-only, change nothing.'
    ),
    'feishu': (
        'Using the feishu skill (lark-cli), find out which Feishu user you '
        'act as (your user name) — read-only, change nothing.'
    ),
    'dingtalk': (
        'Using the dingtalk skill (dws), find out which DingTalk user and '
        'organization you act as — read-only, change nothing.'
    ),
}
BINARY = {'wecom': 'wecom-cli', 'feishu': 'lark-cli', 'dingtalk': 'dws'}


@pytest.fixture(scope='module', autouse=True)
def own_live_server():
    server = OwnServer(port=pick_free_port(), home=HOME, env=AGENT_SERVER_ENV)
    server.start()
    try:
        with e2e_helpers.point_at_server(server.base_url):
            yield server
    finally:
        server.stop()


def _base() -> str:
    return f'{e2e_helpers.BASE_URL}/api/integrations/cli'


def _card(client, key):
    r = client.get(_base())
    r.raise_for_status()
    return {c['key']: c for c in r.json()['integrations']}[key]


def _sign_in(client, key) -> dict:
    card = _card(client, key)
    if not card['installed']:
        r = client.post(f'{_base()}/{key}/install', timeout=600)
        assert r.status_code == 200, r.text
        card = _card(client, key)
    if card['enabled'] and card['auth']['usable']:
        return card
    r = client.post(f'{_base()}/{key}/login', json={'enable_after': True})
    assert r.status_code == 200, r.text
    shown = None
    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        view = client.get(f'{_base()}/{key}/login').json()
        what = (view.get('step'), view.get('url'), view.get('user_code'))
        if view.get('url') and what != shown:
            shown = what
            qr = HOME / 'tools' / key / 'login' / 'qr.png'
            print(
                f'\n>>> {key} sign-in, step {view["step"]}/{view["steps"]}: '
                f'open {view["url"]}'
                + (f'  code {view["user_code"]}' if view['user_code'] else '')
                + (f'  (QR image: {qr})' if view.get('qr') else '')
                + f'\n    or Settings → Integrations on {e2e_helpers.BASE_URL}',
                flush=True,
            )
        if view['state'] in ('failed', 'cancelled'):
            pytest.fail(f'{key} sign-in {view["state"]}: {view["message"]}')
        if view['state'] == 'succeeded':
            break
        time.sleep(2)
    else:
        pytest.fail(f'{key}: nobody approved the sign-in within {WAIT_S}s')
    for _ in range(60):
        card = _card(client, key)
        if card['enabled']:
            return card
        time.sleep(1)
    pytest.fail(f'{key} signed in but never enabled: {card}')


def _ran_cli(messages: list[dict], binary: str) -> bool:
    """A recorded Bash tool_use (``{tool, input}`` JSON) ran *binary*."""
    for m in messages:
        if m.get('role') != 'tool_use':
            continue
        try:
            payload = json.loads(m.get('content') or '{}')
        except ValueError:
            continue
        cmd = str((payload.get('input') or {}).get('command', ''))
        if payload.get('tool') == 'Bash' and binary in cmd:
            return True
    return False


@pytest.mark.parametrize('key', LIVE)
def test_signed_in_platform_works_for_a_real_agent(api_client, key):
    card = _sign_in(api_client, key)
    identity = card['auth']['identity'] or ''
    store = e2e_helpers.create_store(
        api_client,
        f'cli-live-{key}-{int(time.time())}',
        browser_backend='chrome',
    )
    task = e2e_helpers.create_task(
        api_client,
        title=f'Who am I on {key}?',
        store_id=store['id'],
        description=ASK[key]
        + ' Report the identity in your result. No need to open a browser.',
        plan_mode=False,
    )
    done = e2e_helpers.poll_task_status(
        api_client, task['id'], {'completed'}, fail_statuses={'failed'}
    )
    assert done['status'] == 'completed', done.get('error')
    messages = e2e_helpers.get_messages(api_client, task['id'])
    assert _ran_cli(messages, BINARY[key]), f'{key}: the agent never ran it'
    # The card's identity ("Bot <id>" / user name / "user · corp"): the
    # agent must have reached the same account.
    names = [p.strip() for p in identity.replace('Bot ', '').split('·')]
    result = done.get('result') or ''
    assert any(n and n in result for n in names), (identity, result)
