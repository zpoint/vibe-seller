"""E2E browser tests for conversation stream UI.

Tests the full pipeline: mock CLI subprocess → _handle_event →
SSE → frontend renders tool calls, thinking, plan, replan.

Requires:
  MOCK_CLI=tests/e2e/mock_cli.py ./start.sh 7777
  E2E_BASE_URL=http://localhost:7777 pytest tests/e2e/test_conversation_ui.py

The mock CLI script outputs stream-json events that the real
ClaudeCodeBackend processes through the full pipeline.

Marked @pytest.mark.e2e — skipped unless MOCK_CLI env is set.
"""

import logging
import os
import time

import httpx
from playwright.sync_api import expect
import pytest

from tests.e2e.conftest import BASE_URL

logger = logging.getLogger(__name__)

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — skipping conversation UI tests. '
        'Run with: MOCK_CLI=tests/e2e/mock_cli.py ./start.sh 7777',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]


def _login_api() -> httpx.Client:
    """Login via API, return authenticated client."""
    client = httpx.Client(timeout=30)
    client.post(
        f'{BASE_URL}/api/auth/login',
        json={
            'identifier': 'admin@vibe-seller.local',
            'password': 'admin',
        },
    )
    return client


def _create_task_api(
    client: httpx.Client,
    title: str,
    plan_mode: bool | None = None,
) -> str:
    """Create a task via API, return task_id."""
    payload: dict = {'title': title}
    if plan_mode is not None:
        payload['plan_mode'] = plan_mode
    resp = client.post(
        f'{BASE_URL}/api/tasks',
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()['id']


def _poll_status(
    client: httpx.Client,
    task_id: str,
    target: str = 'completed',
    timeout: int = 60,
) -> dict:
    """Poll until task reaches target status."""
    deadline = time.time() + timeout
    data: dict = {}
    while time.time() < deadline:
        data = client.get(f'{BASE_URL}/api/tasks/{task_id}').json()
        if data.get('status') == target:
            return data
        if data.get('status') == 'failed' and target != 'failed':
            return data
        time.sleep(1)
    raise TimeoutError(
        f'Task {task_id[:8]} did not reach {target} '
        f'within {timeout}s (last={data.get("status")})'
    )


class TestConversationStreamUI:
    """Test the conversation stream renders tool calls, thinking,
    plans, and results from the mock CLI pipeline.

    Tasks are created via API (fast, reliable) and verified
    in the browser via Playwright.
    """

    @pytest.fixture(autouse=True)
    def _api_client(self):
        self.api = _login_api()
        yield
        self.api.close()

    def _select_task_in_browser(self, page, title: str):
        """Navigate to All Stores, select task, wait for detail to load."""
        page.reload()
        page.wait_for_selector('h1', timeout=10000)
        # Switch to All Stores view (tasks without store_id)
        all_stores = page.locator('button', has_text='All Stores')
        if all_stores.count() > 0:
            all_stores.first.click()
        # Wait for task list to render, then click the task
        task_btn = page.locator('button', has_text=title).first
        task_btn.wait_for(timeout=10000)
        task_btn.click()
        # Wait for task detail header to confirm selection loaded
        page.locator('h2', has_text=title).wait_for(timeout=10000)

    def test_tool_calls_visible_after_completion(self, authenticated_page):
        """Tool call cards appear in conversation stream."""
        page = authenticated_page
        tag = int(time.time())
        title = f'Tool call test {tag}'
        task_id = _create_task_api(self.api, title)

        # Non-store task → plan mode → approve then complete
        _poll_status(self.api, task_id, target='planned')
        self.api.post(f'{BASE_URL}/api/tasks/{task_id}/execute-plan')
        _poll_status(self.api, task_id)

        # Select task in browser
        self._select_task_in_browser(page, title)

        # Verify tool calls appear in conversation
        page.wait_for_selector('text=/tool call/', timeout=15000)

    def test_thinking_visible_after_completion(self, authenticated_page):
        """Thinking blocks appear in conversation stream."""
        page = authenticated_page
        tag = int(time.time())
        title = f'Thinking test {tag}'
        task_id = _create_task_api(self.api, title)
        # Non-store task → plan mode → approve then complete
        _poll_status(self.api, task_id, target='planned')
        self.api.post(f'{BASE_URL}/api/tasks/{task_id}/execute-plan')
        _poll_status(self.api, task_id)

        self._select_task_in_browser(page, title)

        page.wait_for_selector('text=/Thinking/', timeout=15000)

    def test_plan_and_result_visible(self, authenticated_page):
        """Plan card and result card appear after task completes."""
        page = authenticated_page
        tag = int(time.time())
        title = f'Plan result test {tag}'
        # Plan mode: plan card is only rendered when agent produces
        # a plan via ExitPlanMode (requires plan_then_execute mode).
        task_id = _create_task_api(self.api, title, plan_mode=True)
        _poll_status(self.api, task_id, target='planned')
        self.api.post(f'{BASE_URL}/api/tasks/{task_id}/execute-plan')
        _poll_status(self.api, task_id)

        self._select_task_in_browser(page, title)

        # Verify plan card and result
        page.wait_for_selector('text=/Step one/', timeout=15000)
        expect(
            page.locator('text=/completed successfully/').first
        ).to_be_visible(timeout=5000)

    def test_free_text_answer_reaches_agent(self, authenticated_page):
        """Issue #211: full click-through — agent asks a question, the
        operator uses 'Type freely instead', and the free text must
        reach the agent (not arrive empty).

        The mock CLI (gated by a prompt marker) asks one
        AskUserQuestion, then echoes back the answer it received from
        the backend's control_response. The whole stack runs:
        QuestionBanner → POST /questions/answer → submit_answer →
        _handle_ask_user_question (the fix) → control_response → mock
        renders by question text → conversation. Before the fix the
        echo is empty; after it, the typed text comes back.
        """
        page = authenticated_page
        tag = int(time.time())
        # Marker tells the mock CLI to run the AskUserQuestion flow.
        # Keep the marker out of the free text so the echo assertion
        # below can't accidentally match the title.
        title = f'[[MOCK_ASK_FREE_TEXT]] free text test {tag}'
        free_text = f'Audit US only and skip everything else {tag}'
        task_id = _create_task_api(self.api, title)

        # Wait until the agent has emitted the question server-side.
        deadline = time.time() + 30
        while time.time() < deadline:
            pend = self.api.get(
                f'{BASE_URL}/api/tasks/{task_id}/questions/pending'
            ).json()
            if pend.get('pending'):
                break
            time.sleep(1)
        else:
            raise TimeoutError('agent never asked the question')

        # Open the task; the QuestionBanner renders from pending Qs.
        self._select_task_in_browser(page, title)

        # Switch to free-text mode, type, submit.
        page.get_by_role('button', name='Type freely instead').click(
            timeout=10000
        )
        textarea = page.get_by_placeholder('Type your response...')
        textarea.wait_for(timeout=10000)
        textarea.fill(free_text)
        page.get_by_role('button', name='Submit Answers').click()

        # The banner clears once the answer is submitted.
        expect(page.get_by_placeholder('Type your response...')).to_have_count(
            0, timeout=10000
        )

        # The mock echoes the answer it received as
        # '<question> => <free text>'. The ' => ' separator only ever
        # appears in the agent's rendered echo — never in the task
        # title or the (now-cleared) textarea — so this assertion fails
        # exactly when the sentinel arrives untranslated (the empty
        # 'You answered: .' render from issue #211).
        expect(page.locator(f'text=/=> {free_text}/').first).to_be_visible(
            timeout=20000
        )

    def test_page_refresh_preserves_conversation(self, authenticated_page):
        """After refresh, conversation shows tool calls + plan."""
        page = authenticated_page
        tag = int(time.time())
        title = f'Refresh test {tag}'
        # Plan mode so mock CLI produces plan text ("Step one").
        task_id = _create_task_api(self.api, title, plan_mode=True)
        _poll_status(self.api, task_id, target='planned')
        self.api.post(f'{BASE_URL}/api/tasks/{task_id}/execute-plan')
        _poll_status(self.api, task_id)

        # Load task
        self._select_task_in_browser(page, title)
        page.wait_for_selector('text=/tool call/', timeout=10000)

        # Refresh again
        self._select_task_in_browser(page, title)

        # Verify conversation survives refresh
        page.wait_for_selector('text=/tool call/', timeout=10000)
        expect(page.locator('text=/Step one/').first).to_be_visible(
            timeout=5000
        )
