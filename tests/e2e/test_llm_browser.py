"""
Real E2E test: LLM + Browser + App GUI.

Spins up a local 2-page static site:
  - /index.html: company homepage with heading + "Contact Us" link
  - /contact.html: contact page with email + phone

Tests:
  1. API-level: create tasks via API, agent executes, verify result
  2. GUI-level: create task via Playwright UI, verify agent chat output

All tests go through the full stack (API/UI -> task -> agent -> result).
Requires: provider credentials matching E2E_PROVIDER_MAP.
"""

import http.server
import json
import logging
import re
import threading
import time
import unicodedata

from playwright.sync_api import Page, expect
import pytest

from tests.e2e.e2e_helpers import (
    BASE_URL,
    PIPELINE_TIMEOUT,
    create_store,
    create_task,
    get_messages,
    poll_task_status,
)

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e]

# -- Static site content --

INDEX_HTML = """\
<!DOCTYPE html>
<html><head><title>Acme Corp</title></head>
<body>
  <h1>Welcome to Acme Corp</h1>
  <p>We build the best widgets in the world.</p>
  <p>Founded in 2020, Acme Corp serves customers in 50 countries.</p>
  <nav>
    <a href="/contact.html">Contact Us</a>
  </nav>
</body></html>
"""

CONTACT_HTML = """\
<!DOCTYPE html>
<html><head><title>Contact - Acme Corp</title></head>
<body>
  <h1>Contact Us</h1>
  <p>Get in touch with our team:</p>
  <ul>
    <li>Email: <a href="mailto:hello@acme-corp.test">\
hello@acme-corp.test</a></li>
    <li>Phone: +1-555-0123</li>
    <li>Address: 123 Widget Lane, San Francisco, CA 94105</li>
  </ul>
  <a href="/index.html">Back to Home</a>
</body></html>
"""

PAGES = {
    '/': INDEX_HTML,
    '/index.html': INDEX_HTML,
    '/contact.html': CONTACT_HTML,
}


class _TestHandler(http.server.BaseHTTPRequestHandler):
    """Serve static HTML pages for tests."""

    def do_GET(self):
        content = PAGES.get(self.path)
        if content:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.write(content)
        else:
            self.send_error(404)

    def write(self, content: str):
        self.wfile.write(content.encode())

    def log_message(self, format, *args):
        pass  # Suppress request logging


@pytest.fixture(scope='module')
def test_site():
    """Start a local HTTP server serving the 2-page site."""
    server = http.server.HTTPServer(('127.0.0.1', 0), _TestHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{port}'
    server.shutdown()
    server.server_close()


@pytest.mark.e2e
class TestLLMConnectivity:
    """Verify LLM responds through the full task pipeline."""

    def test_llm_responds(self, api_client, test_site):
        """Create a store task, agent completes it."""
        ts = int(time.time())
        store = create_store(api_client, f'e2e-ping-{ts}')
        data = create_task(
            api_client,
            'Reply with exactly: OK',
            store_id=store['id'],
        )
        result = poll_task_status(
            api_client,
            data['id'],
            {'completed', 'failed'},
        )
        assert result['status'] == 'completed', (
            f'Task failed: {result.get("error")}'
        )

    def test_llm_follows_instructions(self, api_client, test_site):
        """Agent can follow structured instructions."""
        ts = int(time.time())
        store = create_store(api_client, f'e2e-instr-{ts}')
        data = create_task(
            api_client,
            'What is the capital of France? '
            'Reply with ONLY the city name, nothing else.',
            store_id=store['id'],
        )
        result = poll_task_status(
            api_client,
            data['id'],
            {'completed', 'failed'},
        )
        assert result['status'] == 'completed', (
            f'Task failed: {result.get("error")}'
        )
        # Check messages for content (result field is
        # agent-controlled summary)
        msgs = get_messages(api_client, data['id'])
        all_text = ' '.join(
            m['content'] for m in msgs if m['role'] in ('assistant', 'result')
        ).lower()
        assert 'paris' in all_text, (
            f'Expected "paris" in messages: {all_text[:500]}'
        )


@pytest.mark.e2e
class TestLLMBrowserAPI:
    """API-level E2E: task + agent + browser against static test site."""

    def test_agent_reads_homepage(self, api_client, test_site):
        """Agent navigates test site and identifies company name."""
        ts = int(time.time())
        store = create_store(api_client, f'e2e-llm-{ts}')

        data = create_task(
            api_client,
            f'Navigate to {test_site} using browser-use CLI. '
            f'Read the page content and tell me the company '
            f'name. Include the company name in your result.',
            store_id=store['id'],
        )
        result = poll_task_status(
            api_client,
            data['id'],
            {'completed', 'failed'},
        )
        assert result['status'] == 'completed', (
            f'Task failed: {result.get("error")}'
        )
        result_text = (result.get('result') or '').lower()
        assert 'acme' in result_text, (
            f'Expected "acme" in task result: {result_text[:500]}'
        )

    def test_agent_extracts_contact_details(self, api_client, test_site):
        """Agent navigates to contact page and extracts details."""
        ts = int(time.time())
        store = create_store(api_client, f'e2e-contact-{ts}')

        data = create_task(
            api_client,
            f'Use browser-use CLI to navigate to {test_site} '
            f'and find the contact page. Extract all contact '
            f'details (email, phone, address) and report them.',
            store_id=store['id'],
        )
        result = poll_task_status(
            api_client,
            data['id'],
            {'completed', 'failed'},
        )
        assert result['status'] == 'completed', (
            f'Task failed: {result.get("error")}'
        )
        msgs = get_messages(api_client, data['id'])
        all_text = ' '.join(
            m['content'] for m in msgs if m['role'] in ('assistant', 'result')
        )
        normalized = unicodedata.normalize('NFKC', all_text)
        norm_lower = normalized.lower()
        assert 'hello@acme-corp.test' in norm_lower, (
            f'Email not found in messages: {all_text[:500]}'
        )
        norm_hyphens = (
            normalized.replace('\u2010', '-')
            .replace('\u2011', '-')
            .replace('\u2012', '-')
            .replace('\u2013', '-')
        )
        assert '555-0123' in norm_hyphens, (
            f'Phone not found in messages: {all_text[:500]}'
        )

        # Stop hook should have forced the agent to reflect. For a
        # local test server the agent may correctly decide "no
        # reusable knowledge" — so we verify that the reflection
        # PHASE happened, not that files were written.
        #
        # Reflection happens between the agent finishing its work
        # and the agent session actually exiting (the Stop hook
        # injects the reflection prompt on first stop, then
        # approves on retry). `auto_run_task` only marks the task
        # COMPLETED after the session exits, so reflection always
        # precedes completion — but the structured ``reflection_*``
        # agent_events take a moment to flush; poll with a bounded
        # cap.
        #
        # Earlier versions of this test scanned ``thinking`` /
        # ``assistant`` content for keywords like ``learn`` /
        # ``knowledge``. That coupled the test to model vocabulary
        # and broke whenever a model phrased its reflection
        # without those words (e.g. "Nothing to write" / "no
        # gotchas"). The server now emits explicit
        # ``reflection_started`` and ``reflection_completed``
        # agent_events from the Stop-hook code path — these are a
        # structural contract independent of model wording.
        deadline = time.time() + 30
        msgs_last: list[dict] = msgs
        reflection_events: list[dict] = []
        while time.time() < deadline:
            msgs_last = get_messages(api_client, data['id'])
            reflection_events = []
            for m in msgs_last:
                if m['role'] != 'agent_event':
                    continue
                try:
                    payload = json.loads(m['content'])
                except (ValueError, TypeError):
                    continue
                ev = payload.get('event') if isinstance(payload, dict) else None
                if ev in ('reflection_started', 'reflection_completed'):
                    reflection_events.append({'role': m['role'], 'event': ev})
            # Wait for the full bracket: reflection_started pins that the
            # Stop-hook block fired, reflection_completed pins the retry
            # approve. Breaking on either alone would let the test pass
            # on a half-emitted run and weaken what the contract proves.
            event_names = [e['event'] for e in reflection_events]
            if (
                'reflection_started' in event_names
                and 'reflection_completed' in event_names
            ):
                break
            time.sleep(1)

        logger.info(
            'Reflection events: %d (%s)',
            len(reflection_events),
            [e['event'] for e in reflection_events],
        )
        event_names = [e['event'] for e in reflection_events]
        assert 'reflection_started' in event_names, (
            'Stop hook should emit a reflection_started agent_event '
            'within 30s of task completion. '
            f'Got events: {event_names}. '
            f'Roles seen: {[m["role"] for m in msgs_last]}'
        )
        assert 'reflection_completed' in event_names, (
            'Stop hook should emit reflection_completed after the '
            'retry approves; got only '
            f'{event_names}.'
        )


@pytest.mark.e2e
class TestLLMWebBrowserNoStore:
    """No-store (orchestrator) task drives the store-less ``web``
    browser against the static test site — the mirror of
    ``TestLLMBrowserAPI`` but with ``store_id=None``.

    Proves the whole store-less browser path is wired end to end:
    the ``browser-use`` skill is present in the no-store workspace,
    the ``bin/_web`` wrapper is on PATH, its auto-start route brings
    up Chrome + the CDP proxy, and the agent extracts real content.
    """

    def _run_no_store_task(self, api_client, prompt: str) -> dict:
        """Create a no-store task and drive it to a terminal state.

        Non-store tasks are forced into plan mode, so accept the
        ``planned`` → execute-plan → ``completed`` path (and the
        plan-skip shortcut where the agent completes directly). See
        ``test_task_execution.test_task_without_store_completes``.
        """
        data = create_task(api_client, prompt)
        assert data['store_id'] is None
        assert data['plan_mode'] is True
        result = poll_task_status(
            api_client,
            data['id'],
            target_statuses={'planned', 'completed', 'waiting'},
            fail_statuses={'failed'},
            timeout=PIPELINE_TIMEOUT,
        )
        if result['status'] == 'planned':
            r = api_client.post(
                f'{BASE_URL}/api/tasks/{data["id"]}/execute-plan'
            )
            assert r.status_code == 200
            result = poll_task_status(
                api_client,
                data['id'],
                target_statuses={'completed', 'waiting'},
                fail_statuses={'failed'},
                timeout=PIPELINE_TIMEOUT,
            )
        assert result['status'] in ('completed', 'waiting'), (
            f'Task failed: {result.get("error")}'
        )
        return data

    def test_web_browser_reads_homepage(self, api_client, test_site):
        """Agent uses the web browser to identify the company name."""
        data = self._run_no_store_task(
            api_client,
            f'Use the browser-use CLI (your general web browser) to '
            f'navigate to {test_site}. Read the page and report the '
            f'company name. Include the company name in your result.',
        )
        msgs = get_messages(api_client, data['id'])
        all_text = ' '.join(
            m['content'] for m in msgs if m['role'] in ('assistant', 'result')
        ).lower()
        assert 'acme' in all_text, (
            f'Expected "acme" in messages: {all_text[:500]}'
        )

    def test_web_browser_extracts_contact_details(self, api_client, test_site):
        """Agent uses the web browser to reach the contact page and
        extract the email + phone."""
        data = self._run_no_store_task(
            api_client,
            f'Use the browser-use CLI (your general web browser) to '
            f'navigate to {test_site} and find the contact page. '
            f'Extract the contact details (email, phone, address) '
            f'and report them.',
        )
        msgs = get_messages(api_client, data['id'])
        all_text = ' '.join(
            m['content'] for m in msgs if m['role'] in ('assistant', 'result')
        )
        normalized = unicodedata.normalize('NFKC', all_text)
        norm_lower = normalized.lower()
        assert 'hello@acme-corp.test' in norm_lower, (
            f'Email not found in messages: {all_text[:500]}'
        )
        norm_hyphens = (
            normalized.replace('‐', '-')
            .replace('‑', '-')
            .replace('‒', '-')
            .replace('–', '-')
        )
        assert '555-0123' in norm_hyphens, (
            f'Phone not found in messages: {all_text[:500]}'
        )


@pytest.mark.e2e
class TestGUITaskExecution:
    """
    True E2E: Create a task through the app GUI, AI agent navigates
    the static test site via browser automation, verify results
    appear in the agent chat dialog.

    Flow:
    1. Start static test site on random port
    2. Login to app, create store + task via UI
    3. Task instructs AI to navigate test site and extract contact info
    4. Wait for task status: pending -> designing -> running -> completed
    5. Read agent chat messages from DOM
    6. Verify contact details in chat output
    """

    def _create_store(self, page: Page, store_name: str):
        """Create a store via the UI and select it."""
        page.get_by_role('button', name='+ New Store').click()
        page.get_by_placeholder('Store name...').fill(store_name)
        page.get_by_role('button', name='Create', exact=True).click()

        store_btn = page.get_by_role(
            'button', name=re.compile(store_name)
        ).first
        expect(store_btn).to_be_visible(timeout=5000)
        store_btn.click()

    def _create_task(self, page: Page, task_title: str):
        """Create a task via the UI and wait for it to appear."""
        page.get_by_role('button', name='+ New Task').click()
        page.get_by_placeholder('e.g. Navigate to google.com').fill(task_title)
        page.get_by_role('button', name='Create & Run').click()

        task_item = page.locator('button', has_text=task_title).first
        expect(task_item).to_be_visible(timeout=5000)

    def _wait_for_terminal_status(self, page: Page, max_wait: int = 600) -> str:
        """Poll until task reaches a terminal status."""
        start = time.time()
        last_status = ''

        while time.time() - start < max_wait:
            status_el = page.locator(
                'span.rounded-full.text-xs.font-medium'
            ).first
            if status_el.count() > 0:
                status_text = status_el.inner_text()
                if status_text != last_status:
                    logger.info('Task status: %s', status_text)
                    last_status = status_text

                status_lower = status_text.lower()
                if 'completed' in status_lower or 'failed' in status_lower:
                    return status_text

            time.sleep(5)

        pytest.fail(
            f'Task did not reach terminal status within {max_wait}s '
            f'(last: {last_status})'
        )

    def _get_agent_chat_text(self, page: Page) -> str:
        """Extract all visible text from the task detail panel."""
        # Select by stable test id, not incidental utility classes —
        # the conversation panel's styling (padding, width) changes
        # with the design; the contract "this is the panel" should not.
        detail_panel = page.locator(
            '[data-testid="conversation-scroll"]'
        ).last
        if detail_panel.count() == 0:
            return ''

        start = time.time()
        while time.time() - start < 30:
            text = detail_panel.inner_text()
            # Case-insensitive: the result label may render uppercase
            # via CSS text-transform, which innerText reflects.
            if 'result' in text.lower() and len(text) > 500:
                return text
            time.sleep(2)

        return detail_panel.inner_text()

    def test_ai_navigates_site_via_gui(
        self, authenticated_page: Page, test_site: str
    ):
        """Full GUI E2E: create task -> AI agent -> verify chat."""
        page = authenticated_page
        ts = int(time.time())
        store_name = f'acme-e2e-{ts}'

        self._create_store(page, store_name)

        task_prompt = (
            f'Use the browser-use CLI to navigate to {test_site} '
            f'and find all contact details. '
            f'The homepage has a "Contact Us" link that leads to '
            f'a page with email, phone, and address. '
            f'Click that link and extract all contact information.'
        )
        self._create_task(page, task_prompt)

        logger.info('Waiting for AI agent to complete task...')
        final_status = self._wait_for_terminal_status(page)
        logger.info('Task finished with status: %s', final_status)

        chat_text = self._get_agent_chat_text(page)
        logger.info('Agent chat text length: %d', len(chat_text))
        logger.info('Agent chat (first 1000 chars): %s', chat_text[:1000])

        assert chat_text, 'No agent chat output found'

        normalized = unicodedata.normalize('NFKC', chat_text)
        norm_lower = normalized.lower()
        assert 'hello@acme-corp.test' in norm_lower, (
            f'Email not found in agent chat: {chat_text[:500]}'
        )
        norm_hyphens = (
            normalized.replace('\u2010', '-')
            .replace('\u2011', '-')
            .replace('\u2012', '-')
            .replace('\u2013', '-')
        )
        assert '555-0123' in norm_hyphens, (
            f'Phone not found in agent chat: {chat_text[:500]}'
        )
        assert 'san francisco' in norm_lower, (
            f'Address not found in agent chat: {chat_text[:500]}'
        )
