"""Live, real-browser E2E for the multi-platform chat bot integration.

Each platform case (WeCom / DingTalk / Feishu / Slack) is SKIPPED
unless its env var carries a REAL webhook URL:

    E2E_WECOM_WEBHOOK        https://qyapi.weixin.qq.com/...?key=…
    E2E_DINGTALK_WEBHOOK     https://oapi.dingtalk.com/robot/send?access_token=…
    E2E_DINGTALK_SECRET      optional 加签 SEC… secret
    E2E_FEISHU_WEBHOOK       https://open.feishu.cn/open-apis/bot/v2/hook/…
    E2E_FEISHU_SECRET        optional 签名校验 secret
    E2E_SLACK_WEBHOOK        https://hooks.slack.com/services/…

Two tests per platform, so the UI leg can run WITHOUT an LLM provider:

  - ``test_chat_bot_ui_flow`` — UI config (Settings → Integrations →
    Add bot: paste the URL (+secret), the live platform badge shows
    the right platform, Save succeeds, the row shows the masked URL,
    and the RAW URL/secret never appears in the page text), the UI
    Test button (a REAL delivery; screenshot), then delete via the UI.
    No agent task.

  - ``test_chat_bot_keyword_ui_flow`` — keyword-security cases
    (DingTalk 自定义关键词 / Feishu 关键词): fill the keyword field,
    Save, Test → the platform accepts (the server appended the
    keyword). Driven by ``E2E_DINGTALK_KW_WEBHOOK`` +
    ``E2E_DINGTALK_KW_KEYWORD`` (Feishu: ``E2E_FEISHU_KW_WEBHOOK`` +
    ``E2E_FEISHU_KW_KEYWORD``).

  - ``test_chat_bot_agent_send`` — creates the bot via the API, runs
    a real send-message task created through the same API the
    frontend uses, verifies from the task's recorded tool_use
    messages that the MCP tool ``vibe_seller_send_chat_message`` was
    called with this bot's id, prints the nonce so a human can eyeball
    the message in the group, and deletes the bot in a ``finally``.
    SKIPS unless ``E2E_PROVIDER_MAP`` is set (the conftest assigns one
    LLM profile per xdist worker from it) — it spends provider credits.

Webhook URLs and secrets NEVER go to logs or screenshots of the edit
form (the form is closed before screenshots are taken), and never to
files under the repo. Screenshots go to ``E2E_CHATBOT_SHOT_DIR``
(default: a ``chatbot-e2e`` dir under the system temp).

Run (isolated server, port 7798 — never 7777). The worktree venv needs
pytest-playwright for the browser fixtures (CI installs it separately):

    uv pip install pytest-playwright
    VIBE_HOME=~/.vibe-seller-chatbot-e2e ./start.sh 7798 --dev

  # UI legs — no LLM provider needed:
    E2E_BASE_URL=http://127.0.0.1:7798 \\
    E2E_CHATBOT_SHOT_DIR=~/chatbot-e2e \\
    E2E_WECOM_WEBHOOK='https://qyapi.weixin.qq.com/…' \\
    E2E_SLACK_WEBHOOK='https://hooks.slack.com/services/…' \\
    E2E_DINGTALK_KW_WEBHOOK='https://oapi.dingtalk.com/…' \\
    E2E_DINGTALK_KW_KEYWORD='SALES' \\
    .venv/bin/python -m pytest --e2e tests/e2e/test_chat_bots_live.py -v \\
        -k ui_flow

  # Agent-send leg (spends credits — needs a provider profile):
    E2E_BASE_URL=http://127.0.0.1:7798 \\
    E2E_PROVIDER_MAP=minimax \\
    E2E_SLACK_WEBHOOK='https://hooks.slack.com/services/…' \\
    .venv/bin/python -m pytest --e2e tests/e2e/test_chat_bots_live.py -v \\
        -k agent_send

With no env vars set the module skips cleanly.
"""

import json
import logging
import os
import re
import tempfile
import uuid

import httpx
from playwright.sync_api import expect
import pytest

from tests.e2e.conftest import BASE_URL
import tests.e2e.e2e_helpers as e2e_helpers

logger = logging.getLogger('e2e.chatbots')

pytestmark = [pytest.mark.e2e]

# Skip the whole module when no server is up: without it the api_client
# fixture would ERROR (connection refused) instead of skipping, and a
# clean skip with no env vars set is part of this module's contract.
try:
    _probe = httpx.Client(timeout=3)
    _probe.get(f'{BASE_URL}/api/health').raise_for_status()
    _probe.close()
except Exception:
    pytest.skip(
        'chat-bot live e2e needs a running server '
        f'({BASE_URL}) and E2E_<PLATFORM>_WEBHOOK env vars',
        allow_module_level=True,
    )

SCREENSHOT_DIR = os.environ.get(
    'E2E_CHATBOT_SHOT_DIR',
    os.path.join(tempfile.gettempdir(), 'chatbot-e2e'),
)

PLATFORM_CASES = {
    'wecom': {
        'url_env': 'E2E_WECOM_WEBHOOK',
        'secret_env': None,
    },
    'dingtalk': {
        'url_env': 'E2E_DINGTALK_WEBHOOK',
        'secret_env': 'E2E_DINGTALK_SECRET',
    },
    'feishu': {
        'url_env': 'E2E_FEISHU_WEBHOOK',
        'secret_env': 'E2E_FEISHU_SECRET',
    },
    'slack': {
        'url_env': 'E2E_SLACK_WEBHOOK',
        'secret_env': None,
    },
}

# Keyword-security-only cases (自定义关键词): a UI case each for
# DingTalk and Feishu, driven by their own env pairs. The keyword
# itself is not a secret, but the webhook URL still is — never log it.
KEYWORD_CASES = {
    'dingtalk-keyword': {
        'platform': 'dingtalk',
        'url_env': 'E2E_DINGTALK_KW_WEBHOOK',
        'keyword_env': 'E2E_DINGTALK_KW_KEYWORD',
    },
    'feishu-keyword': {
        'platform': 'feishu',
        'url_env': 'E2E_FEISHU_KW_WEBHOOK',
        'keyword_env': 'E2E_FEISHU_KW_KEYWORD',
    },
}

# Label the badge must show per platform (from /api/chat-bots/platforms).
PLATFORM_LABELS = {
    'wecom': 'WeCom',
    'dingtalk': 'DingTalk',
    'feishu': 'Feishu / Lark',
    'slack': 'Slack',
}


def _case_params():
    """(platform, url, secret) for every platform whose env var is set."""
    for platform, spec in sorted(PLATFORM_CASES.items()):
        url = os.environ.get(spec['url_env'], '').strip()
        if not url:
            continue
        secret = ''
        if spec['secret_env']:
            secret = os.environ.get(spec['secret_env'], '').strip()
        yield platform, url, secret


def _live_cases():
    cases = list(_case_params())
    if not cases:
        return [
            pytest.param(
                None,
                None,
                None,
                marks=pytest.mark.skip(
                    'no E2E_<PLATFORM>_WEBHOOK env vars set — set one '
                    'of E2E_WECOM_WEBHOOK / E2E_DINGTALK_WEBHOOK / '
                    'E2E_FEISHU_WEBHOOK / E2E_SLACK_WEBHOOK to run live'
                ),
            )
        ]
    return [pytest.param(p, u, s, id=p) for p, u, s in cases]


def _keyword_cases():
    """UI-only keyword-security cases; skips when no env pair is set."""
    cases = []
    for case, spec in sorted(KEYWORD_CASES.items()):
        url = os.environ.get(spec['url_env'], '').strip()
        keyword = os.environ.get(spec['keyword_env'], '').strip()
        if url and keyword:
            cases.append(pytest.param(spec['platform'], url, keyword, id=case))
    if not cases:
        return [
            pytest.param(
                None,
                None,
                None,
                marks=pytest.mark.skip(
                    'no keyword-security env pairs set — set '
                    'E2E_DINGTALK_KW_WEBHOOK + E2E_DINGTALK_KW_KEYWORD '
                    'or E2E_FEISHU_KW_WEBHOOK + E2E_FEISHU_KW_KEYWORD'
                ),
            )
        ]
    return cases


def _open_integrations(page):
    page.reload()
    page.wait_for_selector('h1', timeout=10000)
    page.locator('[data-testid="nav-settings"]').click()
    page.locator('[data-testid="settings-tab-integrations"]').click()
    page.locator('[data-testid="chat-bot-section"]').wait_for(timeout=10000)


def _tool_use_messages(client: httpx.Client, task_id: str) -> list[dict]:
    """Parsed tool_use blocks recorded for a task."""
    out = []
    for m in e2e_helpers.get_messages(client, task_id):
        if m.get('role') != 'tool_use':
            continue
        try:
            out.append(json.loads(m.get('content') or '{}'))
        except ValueError:
            continue
    return out


@pytest.mark.parametrize('platform,webhook_url,secret', _live_cases())
def test_chat_bot_ui_flow(
    authenticated_page, api_client, platform, webhook_url, secret
):
    """UI leg — config, real test delivery, delete. No agent task."""
    # Guard against the URL leaking into assertion messages / traces:
    # never put webhook_url in an assert message or a log line.
    assert webhook_url.startswith('https://'), 'webhook must be https'
    nonce = uuid.uuid4().hex[:8]
    bot_name = f'e2e-{platform}-{nonce}'
    page = authenticated_page
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    # Registry endpoint sanity (the UI badge depends on it).
    plats = api_client.get(f'{BASE_URL}/api/chat-bots/platforms')
    assert plats.status_code == 200
    assert {p['name'] for p in plats.json()} == set(PLATFORM_CASES)

    # ── 1. UI config ────────────────────────────────────
    _open_integrations(page)
    page.locator('[data-testid="chat-bot-add"]').click()
    page.locator('[data-testid="chat-bot-name"]').fill(bot_name)
    page.locator('[data-testid="chat-bot-url"]').fill(webhook_url)

    # The badge derives the platform from the URL live.
    badge = page.locator('[data-testid="chat-bot-platform-badge"]')
    expect(badge).to_contain_text(PLATFORM_LABELS[platform], timeout=5000)

    if secret:
        secret_box = page.locator('[data-testid="chat-bot-secret"]')
        expect(secret_box).to_be_visible()
        secret_box.fill(secret)

    page.locator('[data-testid="chat-bot-save"]').click()

    row = page.locator('[data-testid^="chat-bot-row-"]', has_text=bot_name)
    row.wait_for(timeout=10000)
    # The row shows the platform badge + a masked URL; the RAW URL
    # and the secret must never appear in the page text.
    body_text = page.locator('body').inner_text()
    assert webhook_url not in body_text, 'raw webhook URL leaked into the page'
    if secret:
        assert secret not in body_text, 'signing secret leaked into the page'

    # Grab the bot id from the API (the per-row testids carry it).
    bots = api_client.get(f'{BASE_URL}/api/chat-bots').json()
    match = [b for b in bots if b['name'] == bot_name]
    assert match, f'bot {bot_name} not found via API'
    bot_id = match[0]['id']
    assert match[0]['platform'] == platform

    # ── 2. UI Test button (real delivery) ──────────────
    # The form is closed here, so the screenshot cannot contain
    # the URL or secret.
    page.locator(f'[data-testid="chat-bot-test-{bot_id}"]').click()
    toast = page.locator('[data-testid="chat-bot-toast"]')
    # Success is asserted by the toast's success styling, not its
    # text — the UI may be in either locale.
    expect(toast).to_have_class(re.compile(r'text-green'), timeout=30000)
    page.screenshot(
        path=f'{SCREENSHOT_DIR}/{platform}-test.png',
        full_page=True,
    )

    # ── 3. Cleanup via the UI ───────────────────────────
    page.once('dialog', lambda d: d.accept())
    row.locator('[data-testid^="chat-bot-delete-"]').click()
    expect(row).to_have_count(0, timeout=10000)
    assert bot_name not in page.locator('body').inner_text()


@pytest.mark.parametrize('platform,webhook_url,keyword', _keyword_cases())
def test_chat_bot_keyword_ui_flow(
    authenticated_page, api_client, platform, webhook_url, keyword
):
    """Keyword-security UI case (DingTalk 自定义关键词 / Feishu 关键词).

    A keyword-only bot rejects every message that doesn't contain the
    keyword — including the default test text. The server appends it,
    so filling the keyword field must make the Test button succeed
    against the REAL platform API.
    """
    assert webhook_url.startswith('https://'), 'webhook must be https'
    nonce = uuid.uuid4().hex[:8]
    bot_name = f'e2e-kw-{platform}-{nonce}'
    page = authenticated_page
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    _open_integrations(page)
    page.locator('[data-testid="chat-bot-add"]').click()
    page.locator('[data-testid="chat-bot-name"]').fill(bot_name)
    page.locator('[data-testid="chat-bot-url"]').fill(webhook_url)

    badge = page.locator('[data-testid="chat-bot-platform-badge"]')
    expect(badge).to_contain_text(PLATFORM_LABELS[platform], timeout=5000)

    # The keyword field appears only for keyword platforms; fill it.
    keyword_box = page.locator('[data-testid="chat-bot-keyword"]')
    expect(keyword_box).to_be_visible()
    keyword_box.fill(keyword)
    page.locator('[data-testid="chat-bot-save"]').click()

    row = page.locator('[data-testid^="chat-bot-row-"]', has_text=bot_name)
    row.wait_for(timeout=10000)
    # The row carries a keyword pill; the RAW URL never shows.
    pill = row.locator('[data-testid^="chat-bot-keyword-pill-"]')
    expect(pill).to_contain_text(keyword)
    body_text = page.locator('body').inner_text()
    assert webhook_url not in body_text, 'raw webhook URL leaked'

    bots = api_client.get(f'{BASE_URL}/api/chat-bots').json()
    match = [b for b in bots if b['name'] == bot_name]
    assert match, f'bot {bot_name} not found via API'
    bot_id = match[0]['id']
    assert match[0]['keyword'] == keyword

    # The Test button's default text has NO keyword — the server must
    # append it and the platform must accept the message.
    page.locator(f'[data-testid="chat-bot-test-{bot_id}"]').click()
    toast = page.locator('[data-testid="chat-bot-toast"]')
    expect(toast).to_have_class(re.compile(r'text-green'), timeout=30000)
    page.screenshot(
        path=f'{SCREENSHOT_DIR}/{platform}-keyword-test.png',
        full_page=True,
    )

    # Cleanup via the UI.
    page.once('dialog', lambda d: d.accept())
    row.locator('[data-testid^="chat-bot-delete-"]').click()
    expect(row).to_have_count(0, timeout=10000)


@pytest.mark.parametrize('platform,webhook_url,secret', _live_cases())
def test_chat_bot_agent_send(api_client, platform, webhook_url, secret):
    """Agent leg — a real LLM task sends via the MCP tool.

    SKIPS without E2E_PROVIDER_MAP: the conftest assigns one AI profile
    per xdist worker from it, and without a profile the task would run
    on whatever default the server happens to have — a credit-spending
    run on a model nobody chose.
    """
    if not os.environ.get('E2E_PROVIDER_MAP', '').strip():
        pytest.skip(
            'agent-send case needs an LLM provider profile — set '
            'E2E_PROVIDER_MAP=<provider>[,<provider>…] (see '
            'tests/e2e/conftest.py::_setup_worker_profile)'
        )
    assert webhook_url.startswith('https://'), 'webhook must be https'
    nonce = uuid.uuid4().hex[:8]
    bot_name = f'e2e-{platform}-agent-{nonce}'
    bot_id = None
    try:
        payload = {'name': bot_name, 'webhook_url': webhook_url}
        if secret:
            payload['secret'] = secret
        created = api_client.post(f'{BASE_URL}/api/chat-bots', json=payload)
        assert created.status_code == 200, (
            f'bot create failed: {created.status_code}'
        )
        bot_id = created.json()['id']
        assert created.json()['platform'] == platform

        description = (
            f'Use the chat bot named "{bot_name}" to send this exact '
            f'markdown message, then finish: "## Vibe Seller e2e '
            f'{nonce}\\n- platform: {platform}\\n- **bold** check"'
        )
        task = e2e_helpers.create_task(
            api_client,
            f'Chat bot e2e {platform} {nonce}',
            description=description,
            skip_reflection=True,
        )
        task_id = task['id']
        # A store-less task is ALWAYS plan mode (app/routers/tasks.py:
        # `if data.store_id is None: plan_mode = True`), so the agent
        # stops at 'planned' and waits for a human to approve — the
        # same flow the UI drives. Poll for 'planned' too, approve the
        # plan, then wait for the run to finish.
        final = e2e_helpers.poll_task_status(
            api_client,
            task_id,
            {'planned', 'completed', 'failed'},
            timeout=600,
        )
        if final['status'] == 'planned':
            approve = api_client.post(
                f'{BASE_URL}/api/tasks/{task_id}/execute-plan'
            )
            assert approve.status_code == 200, (
                f'execute-plan returned {approve.status_code}'
            )
            final = e2e_helpers.poll_task_status(
                api_client,
                task_id,
                {'completed', 'failed'},
                timeout=600,
            )
        assert final['status'] == 'completed', (
            f'task {task_id[:8]} ended {final["status"]}: '
            f'{(final.get("error") or "")[:300]}'
        )

        # The agent really called the MCP tool with THIS bot's id and
        # the call returned ok (recorded in the tool_use stream).
        calls = [
            c
            for c in _tool_use_messages(api_client, task_id)
            if c.get('tool') == 'vibe_seller_send_chat_message'
        ]
        assert calls, (
            'no vibe_seller_send_chat_message tool call recorded for '
            f'task {task_id[:8]}'
        )
        assert any(
            (c.get('input') or {}).get('bot_id') == bot_id for c in calls
        ), 'tool call used a different bot id'
        assert nonce in json.dumps(calls, ensure_ascii=False), (
            'the sent message did not carry the nonce'
        )
        print(f'\n[chat-bot-e2e] {platform}: nonce={nonce} bot={bot_name}')
        print(
            f'[chat-bot-e2e] eyeball the group for: "## Vibe Seller '
            f'e2e {nonce}"'
        )
    finally:
        if bot_id:
            try:
                api_client.delete(f'{BASE_URL}/api/chat-bots/{bot_id}')
            except httpx.HTTPError:
                logger.exception('cleanup delete failed for %s', bot_id)
