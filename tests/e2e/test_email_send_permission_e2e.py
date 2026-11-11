"""Live e2e for the per-mailbox send permission (``allow_send``).

A real agent, a real configured mailbox, a real SMTP send — so this is
opt-in twice over and SKIPS unless all of these are set:

    E2E_PROVIDER_MAP          LLM provider(s) — the conftest assigns one
                              AI profile per xdist worker from it; this
                              test spends provider credits.
    VIBE_E2E_EMAIL_ACCOUNT    address of a mailbox ALREADY configured
                              (IMAP + SMTP) on the server under test.
    VIBE_E2E_EMAIL_TO         recipient of the one real test email —
                              use an inbox you own.

Server and login come from the usual e2e env (``E2E_BASE_URL``,
``E2E_ADMIN_IDENTIFIER`` / ``E2E_ADMIN_PASSWORD``). Nothing about the
mailbox or recipient is hardcoded; neither is ever written to a file.

Two legs, in order, against the same mailbox:

1. ``allow_send`` ON (the default) → a task to send an email whose
   subject carries a unique token. Asserts the tool call carried the
   token and returned ``ok: true`` (one real email lands in
   ``VIBE_E2E_EMAIL_TO``).
2. The user turns ``allow_send`` OFF → a task asking the agent to send
   a short test email. Asserts no ``vibe_seller_send_email`` call
   returned ok, any call that was made came back with the permission
   refusal, and the task's result tells the user sending is turned off.

The mailbox's original ``allow_send`` is restored afterwards.

Run (isolated server — never the 7777 one you use day to day):

    E2E_BASE_URL=http://127.0.0.1:7798 E2E_PROVIDER_MAP=<provider> \\
    VIBE_E2E_EMAIL_ACCOUNT=<configured mailbox> \\
    VIBE_E2E_EMAIL_TO=<your inbox> \\
    pytest --e2e tests/e2e/test_email_send_permission_e2e.py -s
"""

from __future__ import annotations

import json
import logging
import os
import uuid

import httpx
import pytest

import tests.e2e.e2e_helpers as e2e_helpers
from tests.e2e.e2e_helpers import BASE_URL

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e]

SEND_TOOL = 'vibe_seller_send_email'
# The refusal text the server returns (app/email/sender.py).
REFUSAL_MARKER = 'turned sending off'
# How a task result may tell the user sending is off — any language
# the agent answers in.
REPORT_MARKERS = (
    'settings',
    'turned off',
    'disabled',
    'not enabled',
    'not allowed',
    'permission',
    '设置',
    '关闭',
    '未开启',
    '权限',
)


@pytest.fixture(scope='module')
def mailbox(api_client: httpx.Client):
    """The configured mailbox under test; restores ``allow_send``."""
    if not os.environ.get('E2E_PROVIDER_MAP', '').strip():
        pytest.skip('needs an LLM provider profile — set E2E_PROVIDER_MAP')
    account_email = os.environ.get('VIBE_E2E_EMAIL_ACCOUNT', '').strip()
    recipient = os.environ.get('VIBE_E2E_EMAIL_TO', '').strip()
    if not account_email or not recipient:
        pytest.skip(
            'set VIBE_E2E_EMAIL_ACCOUNT (a configured mailbox) and '
            'VIBE_E2E_EMAIL_TO (an inbox you own) to run'
        )
    accounts = api_client.get(f'{BASE_URL}/api/email-accounts').json()
    account = next((a for a in accounts if a['email'] == account_email), None)
    if account is None:
        pytest.skip('VIBE_E2E_EMAIL_ACCOUNT is not configured on the server')
    if not account.get('smtp_host'):
        pytest.skip('the mailbox under test has no SMTP server configured')
    original = bool(account.get('allow_send'))
    yield {'id': account['id'], 'email': account_email, 'to': recipient}
    _set_allow_send(api_client, account['id'], original)


def _set_allow_send(client: httpx.Client, account_id: str, value: bool):
    r = client.put(
        # The interactive-only route: api_client logs in like the web
        # UI does (session cookie), so it is allowed here.
        f'{BASE_URL}/api/email-accounts/{account_id}/send-permission',
        json={'allow_send': value},
    )
    r.raise_for_status()
    assert r.json()['allow_send'] is value


def _run_task(client: httpx.Client, title: str, description: str) -> dict:
    """Create a store-less task and drive it to a terminal state.

    A store-less task is always plan mode, so approve the plan the way
    the UI's confirm button does, then wait for the run to finish.
    """
    task = e2e_helpers.create_task(client, title, description=description)
    final = e2e_helpers.poll_task_status(
        client, task['id'], {'planned', 'completed', 'failed'}, timeout=600
    )
    if final['status'] == 'planned':
        r = client.post(f'{BASE_URL}/api/tasks/{task["id"]}/execute-plan')
        assert r.status_code == 200, f'execute-plan: {r.status_code}'
        final = e2e_helpers.poll_task_status(
            client, task['id'], {'completed', 'failed'}, timeout=600
        )
    return final


def _send_calls(client: httpx.Client, task_id: str) -> list[tuple[dict, str]]:
    """Every send-tool call of the task, with its tool_result text."""
    messages = e2e_helpers.get_messages(client, task_id)
    calls = []
    for idx, m in enumerate(messages):
        if m.get('role') != 'tool_use':
            continue
        try:
            payload = json.loads(m.get('content') or '{}')
        except ValueError:
            continue
        if not str(payload.get('tool', '')).endswith(SEND_TOOL):
            continue
        calls.append((payload, _result_after(messages, idx, payload)))
    return calls


def _result_after(messages: list[dict], idx: int, call: dict) -> str:
    """The text of the tool_result that answers the call at *idx*."""
    call_id = call.get('tool_use_id')
    for m in messages[idx + 1 :]:
        if m.get('role') != 'agent_event':
            continue
        try:
            event = json.loads(m.get('content') or '{}')
        except ValueError:
            continue
        blocks = (event.get('message') or {}).get('content') or []
        for block in blocks if isinstance(blocks, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get('type') != 'tool_result':
                continue
            if call_id and block.get('tool_use_id') != call_id:
                continue
            content = block.get('content')
            if isinstance(content, str):
                return content
            return '\n'.join(
                part.get('text') or ''
                for part in content or []
                if isinstance(part, dict) and part.get('type') == 'text'
            )
    return ''


def _returned_ok(text: str) -> bool:
    try:
        return json.loads(text).get('ok') is True
    except (ValueError, AttributeError):
        return '"ok": true' in text or '"ok":true' in text


def test_send_by_default_then_refused_when_off(
    api_client: httpx.Client, mailbox
):
    nonce = uuid.uuid4().hex[:8]

    # ── Leg 1: the default — sending ON ─────────────────────────
    # Set explicitly in case this server's mailbox was turned off
    # earlier; the fixture restores whatever it was.
    _set_allow_send(api_client, mailbox['id'], True)
    token = f'vibe-e2e-{nonce}'
    final = _run_task(
        api_client,
        f'Send a test email ({token})',
        f'Please send a short test email from {mailbox["email"]} to '
        f'{mailbox["to"]} with the subject "Vibe Seller send check '
        f'{token}" and a one-line body saying this is a test.',
    )
    assert final['status'] == 'completed', (
        f'leg 1 ended {final["status"]}: {(final.get("error") or "")[:300]}'
    )
    calls = _send_calls(api_client, final['id'])
    sent = [
        (payload, text)
        for payload, text in calls
        if token in json.dumps(payload.get('input') or {}, ensure_ascii=False)
        and _returned_ok(text)
    ]
    assert sent, 'no send call with the token returned ok: ' + '; '.join(
        text[:200] for _, text in calls
    )
    print(f'\n[email-send-e2e] sent; look for subject token {token}')

    # ── Leg 2: the user turns sending OFF ───────────────────────
    _set_allow_send(api_client, mailbox['id'], False)
    refused_subject = f'Vibe Seller refused-send check {nonce}'
    final = _run_task(
        api_client,
        f'Send a test email ({nonce})',
        f'Please send a short test email from {mailbox["email"]} to '
        f'{mailbox["to"]} with the subject "{refused_subject}" and a '
        'one-line body saying this is a test. Then tell me whether it '
        'was sent.',
    )
    assert final['status'] == 'completed', (
        f'leg 2 ended {final["status"]}: {(final.get("error") or "")[:300]}'
    )
    calls = _send_calls(api_client, final['id'])
    assert not any(_returned_ok(text) for _, text in calls), (
        'a send succeeded while allow_send was off'
    )
    for _, text in calls:
        assert REFUSAL_MARKER in text, f'unexpected send result: {text[:200]}'
    report = (final.get('result') or '').lower()
    assert any(marker in report for marker in REPORT_MARKERS), (
        f'the task did not tell the user sending is off: {report[:300]}'
    )
