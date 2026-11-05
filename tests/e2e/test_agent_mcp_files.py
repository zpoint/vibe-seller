"""E2E: the MCP task-adjacent operator tools — the debug log, the
output files, attachments and the image confirm gate.

Companion to ``test_agent_mcp.py``; same mock-CLI lane (no model
credits) and the same skip guard. A SEPARATE FILE on purpose: the
e2e-mock-cli CI job names its test files explicitly.

What these pin, against a real server:

* ``get_task_log`` — the conversation a real (mock-CLI) run persisted,
  paged and totalled honestly;
* ``task_files`` — a producing run's deliverables: list, read (inline
  text / URL + base64 blob) and the ZIP URL, fetched with the caller's
  own ``vst_`` bearer;
* attachments round-trip — a base64 file sent over MCP lands in the
  task workspace (create flow) and is promoted to a parked agent
  (chat flow), the same routes the UI's uploads use;
* ``image_request`` — the confirm gate under the server's fake vision
  mode (``VISION_FAKE=1``, as CI sets it): the card a generate call
  parks on, surfaced by ``wait_task`` and resolved by confirm/cancel.
"""

import base64
import logging
import os
import threading
import time

import httpx
import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import admin_credentials, create_store
from tests.e2e.mock_cli import (
    OUTPUT_FILES_MARKER,
    OUTPUT_PNG,
)
from tests.e2e.test_agent_mcp import McpClient, wait_for_terminal, wait_until

logger = logging.getLogger(__name__)

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — skipping MCP e2e tests. '
        'Run with: MOCK_CLI=tests/e2e/mock_cli.py ./start.sh 7777',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

# A deterministic tiny PNG (the same bytes the mock CLI writes).
_PNG = b'\x89PNG\r\n\x1a\n' + b'\x00\x01\x02fake-png-body' * 8


def _vision_fake() -> bool:
    """Whether the server runs fake vision mode (``VISION_FAKE=1``, as
    the e2e CI lanes set it). Without it the generate call would hit a
    real image API and the gate tests would hang, not fail."""
    try:
        probe = httpx.Client(timeout=10, trust_env=False)
        probe.post(f'{BASE_URL}/api/auth/login', json=admin_credentials())
        cfg = probe.get(f'{BASE_URL}/api/vision/config')
        probe.close()
        return bool(cfg.json().get('fake'))
    except Exception:  # noqa: BLE001 - a probe must never fail a run
        return False


requires_fake_vision = pytest.mark.skipif(
    not _vision_fake(),
    reason='the image gate needs a VISION_FAKE=1 server',
)


def _attachment(filename: str, mime: str, data: bytes) -> dict:
    return {
        'filename': filename,
        'mime_type': mime,
        'content_base64': base64.b64encode(data).decode('ascii'),
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
        json={'name': 'e2e-mcp-files', 'expires_in_days': 1},
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


@pytest.fixture(scope='module')
def bearer(api_token) -> httpx.Client:
    """A plain HTTP client carrying the ``vst_`` bearer, for the
    download URLs the file tools hand out."""
    client = httpx.Client(
        timeout=60,
        headers={'Authorization': f'Bearer {api_token["token"]}'},
        trust_env=False,
    )
    yield client
    client.close()


@pytest.fixture(scope='module')
def store_id(api_client) -> str:
    """A store: an auto-mode task needs one (no-store tasks plan)."""
    return create_store(api_client, 'MCP Files E2E Store')['id']


def _completed_task(mcp: McpClient, store_id: str, description: str) -> str:
    """Create an auto-mode task and return its id once it finishes."""
    created = mcp.call_tool(
        'create_task',
        description=description,
        store_id=store_id,
        plan_mode=False,
    )
    assert created['ok'], created['text']
    task_id = created['data']['task_id']
    waited = wait_for_terminal(mcp, task_id)
    assert waited['status'] == 'completed', waited
    return task_id


# ── get_task_log ─────────────────────────────────────


class TestGetTaskLog:
    def test_log_carries_the_real_conversation(self, mcp, store_id):
        task_id = _completed_task(mcp, store_id, 'Summarise the catalog.')

        log = mcp.call_tool('get_task_log', task_id=task_id)
        assert log['ok'], log['text']
        data = log['data']
        assert data['task_id'] == task_id
        assert data['status'] == 'completed'
        assert data['error'] is None
        # The mock CLI's turn: the user prompt, its thinking/tool_use
        # bookkeeping, the assistant text and the result. The exact
        # count is the messages route's own view — never fewer than a
        # full turn.
        assert data['messages_total'] >= 3, data['messages_total']
        roles = [m['role'] for m in data['messages']]
        assert 'user' in roles and 'result' in roles
        # The most recent messages ride along; the flag says whether
        # there were more.
        assert isinstance(data['messages_truncated'], bool)

        # steps_total is honest (nothing records browser steps in this
        # lane) and an offset past the end is an EMPTY page, not an
        # error and not a wrapped-around one.
        assert data['steps_total'] >= 0
        paged = mcp.call_tool(
            'get_task_log',
            task_id=task_id,
            offset=data['steps_total'],
            limit=10,
        )
        assert paged['ok'], paged['text']
        assert paged['data']['steps'] == []
        assert paged['data']['steps_truncated'] is False

    def test_log_for_a_missing_task_names_it(self, mcp):
        missing = mcp.call_tool('get_task_log', task_id='no-such-task')
        assert not missing['ok']
        assert 'not found' in missing['text'].lower()


# ── task_files ───────────────────────────────────────


class TestTaskFiles:
    def test_list_read_and_zip_of_a_producing_run(self, mcp, bearer, store_id):
        """The mock CLI writes two deliverables into the task workspace
        (its cwd); the tool must list, read and zip them exactly as it
        would a real agent's output."""
        task_id = _completed_task(
            mcp,
            store_id,
            f'Pull the numbers together. {OUTPUT_FILES_MARKER}',
        )

        listed = mcp.call_tool('task_files', task_id=task_id, action='list')
        assert listed['ok'], listed['text']
        names = {f['name'] for f in listed['data']['files']}
        assert {'report.txt', 'chart.png'} <= names, names

        text_read = mcp.call_tool(
            'task_files', task_id=task_id, action='read', path='report.txt'
        )
        assert text_read['ok'], text_read['text']
        assert text_read['data']['encoding'] == 'text'
        assert text_read['data']['content'].startswith('quarterly numbers')

        # A binary answers with a URL and a base64 blob, not mojibake.
        bin_read = mcp.call_tool(
            'task_files', task_id=task_id, action='read', path='chart.png'
        )
        assert bin_read['ok'], bin_read['text']
        assert bin_read['data']['encoding'] == 'url'
        assert '/api/tasks/' in bin_read['data']['url']
        decoded = base64.b64decode(bin_read['data']['blob'])
        assert decoded == OUTPUT_PNG

        # The URL is fetchable with the caller's own vst_ bearer.
        fetched = bearer.get(bin_read['data']['url'])
        assert fetched.status_code == 200, fetched.text
        assert fetched.content == OUTPUT_PNG

        zipped = mcp.call_tool('task_files', task_id=task_id, action='zip_url')
        assert zipped['ok'], zipped['text']
        assert zipped['data']['url'].endswith(f'/api/tasks/{task_id}/files-zip')
        zip_resp = bearer.get(zipped['data']['url'])
        assert zip_resp.status_code == 200
        assert zip_resp.headers['content-type'] == 'application/zip'

    def test_read_refuses_escapes(self, mcp, store_id):
        task_id = _completed_task(mcp, store_id, 'Anything at all.')
        for bad in ('../other/report.txt', '/etc/passwd', 'a\\b.png'):
            refused = mcp.call_tool(
                'task_files', task_id=task_id, action='read', path=bad
            )
            assert not refused['ok'], bad

        bare = mcp.call_tool('task_files', task_id=task_id, action='read')
        assert not bare['ok']
        assert 'path' in bare['text']

    def test_unknown_action_is_rejected(self, mcp):
        refused = mcp.call_tool('task_files', task_id='any', action='rename')
        assert not refused['ok']
        assert 'rename' in refused['text']


# ── attachments ──────────────────────────────────────


class TestAttachments:
    def test_create_attachment_lands_in_the_workspace(self, mcp, store_id):
        created = mcp.call_tool(
            'create_task',
            description='Describe the attached image.',
            store_id=store_id,
            plan_mode=False,
            attachments=[_attachment('widget.png', 'image/png', _PNG)],
        )
        assert created['ok'], created['text']
        data = created['data']
        assert data['attachments'][0]['file_name'] == 'widget.png'
        task_id = data['task_id']

        waited = wait_for_terminal(mcp, task_id)
        assert waited['status'] == 'completed', waited

        # The deferred-create flow lands the file BEFORE the agent
        # starts — the workspace listing is the independent check.
        listed = mcp.call_tool('task_files', task_id=task_id, action='list')
        names = {f['name'] for f in listed['data']['files']}
        assert 'uploads/widget.png' in names, names

    def test_disallowed_type_is_refused_before_the_task_exists(
        self, mcp, store_id
    ):
        refused = mcp.call_tool(
            'create_task',
            description='Run this.',
            store_id=store_id,
            plan_mode=False,
            attachments=[
                _attachment('payload.exe', 'application/x-msdownload', b'MZ...')
            ],
        )
        assert not refused['ok']
        assert 'not allowed' in refused['text'].lower()

    def test_chat_attachment_is_promoted_to_the_live_agent(self, mcp, store_id):
        """send_message with attachments: the mock CLI parks a plan-mode
        task with its process alive, so the staged file must be
        promoted into the workspace while the agent waits."""
        created = mcp.call_tool(
            'create_task', description='Plan the work, then do it.'
        )
        assert created['ok'], created['text']
        task_id = created['data']['task_id']
        try:
            wait_until(
                mcp, task_id, lambda w: w['status'] == 'planned', 'a plan'
            )

            sent = mcp.call_tool(
                'send_message',
                task_id=task_id,
                message='Also use the attached reference.',
                attachments=[_attachment('ref.png', 'image/png', _PNG)],
            )
            assert sent['ok'], sent['text']
            assert sent['data']['attachments'], sent['data']
            assert sent['data']['attachments'][0]['filename'] == 'ref.png'

            listed = mcp.call_tool('task_files', task_id=task_id, action='list')
            names = {f['name'] for f in listed['data']['files']}
            assert 'uploads/ref.png' in names, names
        finally:
            # NEVER leave a task parked on a plan: it holds an agent
            # slot. Stop it, then delete it.
            mcp.call_tool('stop_task', task_id=task_id)
            mcp.call_tool('delete_task', task_id=task_id)


# ── the image confirm gate (fake vision) ─────────────


@requires_fake_vision
class TestImageConfirmGate:
    """The gate end to end: a generate call parks on a card, wait_task
    surfaces it, and confirm resolves it with the operator's edit.

    The generate POST is what the task agent's in-task MCP tool calls;
    driving it directly is the only no-credits way to park a card. It
    BLOCKS until the decision arrives, so it runs on a side thread.
    """

    def _generate_async(self, api_client, task_id: str, body: dict):
        response: dict = {}

        def _post():
            try:
                r = api_client.post(
                    f'{BASE_URL}/api/tasks/{task_id}/image/generate',
                    json=body,
                    timeout=120,
                )
                response['status'] = r.status_code
                response['body'] = r.json()
            except Exception as exc:  # noqa: BLE001 - reported below
                response['error'] = repr(exc)

        thread = threading.Thread(target=_post, daemon=True)
        thread.start()
        return thread, response

    def _wait_for_card(self, mcp, task_id: str) -> dict:
        deadline = time.time() + 30
        card = None
        while time.time() < deadline:
            got = mcp.call_tool('image_request', task_id=task_id, action='get')
            assert got['ok'], got['text']
            if got['data'].get('pending'):
                card = got['data']
                break
            time.sleep(1)
        assert card, 'the generate call never parked its card'
        return card

    def test_confirm_card_flow(self, mcp, api_client):
        created = mcp.call_tool(
            'create_task', description='Plan the work, then do it.'
        )
        assert created['ok'], created['text']
        task_id = created['data']['task_id']
        thread, response = None, {}
        try:
            wait_until(
                mcp, task_id, lambda w: w['status'] == 'planned', 'a plan'
            )
            thread, response = self._generate_async(
                api_client,
                task_id,
                {
                    'prompt': 'a hero banner for widget-006',
                    'model': 'nano-banana-pro-2k',
                    'output_name': 'banner.png',
                },
            )
            card = self._wait_for_card(mcp, task_id)
            assert card['prompt'] == 'a hero banner for widget-006'
            assert card['request_id']
            assert card['models']

            # wait_task wakes on the CARD (the task itself is still
            # parked on its plan — the card is the operator flag it
            # reports first).
            waited = mcp.call_tool(
                'wait_task', task_id=task_id, timeout_seconds=5
            )
            assert waited['ok'], waited['text']
            assert waited['data']['reason'] == 'needs_operator'
            pending = waited['data']['pending']
            assert pending['kind'] == 'image_confirm', pending
            assert pending['prompt'] == 'a hero banner for widget-006'
            assert 'image_request' in waited['data']['next_actions']

            # Confirm with the user's edited prompt.
            confirmed = mcp.call_tool(
                'image_request',
                task_id=task_id,
                action='confirm',
                prompt='a hero banner for widget-006, white background',
            )
            assert confirmed['ok'], confirmed['text']

            thread.join(timeout=60)
            assert not thread.is_alive(), 'the generate call never returned'
            assert response.get('status') == 200, response
            assert response['body']['status'] == 'ok', response['body']
            assert response['body']['prompt'] == (
                'a hero banner for widget-006, white background'
            )

            # The image landed in the task workspace — through the
            # tool, not by trusting the response.
            listed = mcp.call_tool('task_files', task_id=task_id, action='list')
            names = {f['name'] for f in listed['data']['files']}
            assert 'generated_images/banner.png' in names, names
        finally:
            if thread and thread.is_alive():
                mcp.call_tool('image_request', task_id=task_id, action='cancel')
                thread.join(timeout=10)
            mcp.call_tool('stop_task', task_id=task_id)
            mcp.call_tool('delete_task', task_id=task_id)

    def test_cancel_refuses_the_proposal(self, mcp, api_client):
        created = mcp.call_tool(
            'create_task', description='Plan the work, then do it.'
        )
        assert created['ok'], created['text']
        task_id = created['data']['task_id']
        thread, response = None, {}
        try:
            wait_until(
                mcp, task_id, lambda w: w['status'] == 'planned', 'a plan'
            )
            thread, response = self._generate_async(
                api_client, task_id, {'prompt': 'an unwanted image'}
            )
            self._wait_for_card(mcp, task_id)

            cancelled = mcp.call_tool(
                'image_request', task_id=task_id, action='cancel'
            )
            assert cancelled['ok'], cancelled['text']
            assert cancelled['data']['action'] == 'cancel'

            thread.join(timeout=60)
            assert response.get('body', {}).get('status') == 'cancelled'
            listed = mcp.call_tool('task_files', task_id=task_id, action='list')
            names = {f['name'] for f in listed['data']['files']}
            assert not any(
                name.startswith('generated_images/') for name in names
            ), names
        finally:
            if thread and thread.is_alive():
                mcp.call_tool('image_request', task_id=task_id, action='cancel')
                thread.join(timeout=10)
            mcp.call_tool('stop_task', task_id=task_id)
            mcp.call_tool('delete_task', task_id=task_id)

    def test_confirm_without_a_card_is_an_error(self, mcp):
        refused = mcp.call_tool(
            'image_request', task_id='no-such-task-id', action='confirm'
        )
        assert not refused['ok']
        assert 'pending image request' in refused['text'].lower()

    def test_get_without_a_card_says_so(self, mcp, store_id):
        task_id = _completed_task(mcp, store_id, 'Nothing image-shaped.')
        empty = mcp.call_tool('image_request', task_id=task_id, action='get')
        assert empty['ok'], empty['text']
        assert empty['data']['pending'] is False
