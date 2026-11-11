"""
E2E test: Feature 1 - "All" store selection for store-independent tasks.
"""

import time

import httpx
from playwright.sync_api import Page, expect
import pytest

from tests.e2e.e2e_helpers import BASE_URL, login

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def created_titles():
    """Stop and delete the tasks this test created through the UI.

    The UI creates a real task, and its agent runs on after the test's
    assertions. Observed in CI: such an agent, left running for minutes,
    searched the shared workspace for its title's number, found another
    test's store and opened that test's fake console -- the very access
    log that test asserts on. A test leaves no work running behind it.
    """
    titles: list[str] = []
    yield titles
    with httpx.Client(timeout=30) as client:
        login(client)
        tasks = client.get(f'{BASE_URL}/api/tasks').json()
        for task in tasks if isinstance(tasks, list) else []:
            if task.get('title') in titles:
                client.post(f'{BASE_URL}/api/tasks/{task["id"]}/agent/stop')
                for _ in range(30):
                    r = client.delete(f'{BASE_URL}/api/tasks/{task["id"]}')
                    if r.status_code != 409:
                        break
                    time.sleep(1)


def test_all_tasks_button_visible(authenticated_page: Page):
    """The 'All Stores' option should be visible in the sidebar."""
    page = authenticated_page
    all_btn = page.locator('button', has_text='All Stores')
    expect(all_btn).to_be_visible()


def test_create_store_independent_task(
    authenticated_page: Page, created_titles: list[str]
):
    """Click 'All Stores' -> create a task via modal -> task appears with no store."""
    page = authenticated_page
    task_title = f'Process reports {int(time.time())}'
    created_titles.append(task_title)

    # Click "All Stores"
    page.locator('button', has_text='All Stores').click()

    # Header should show "All Stores"
    expect(page.locator('h2', has_text='All Stores')).to_be_visible()

    # Click "+ New Task" — opens modal
    page.get_by_role('button', name='+ New Task').click()

    # Modal header should say "New Task" (not "New Task for ...")
    expect(page.locator('h3', has_text='New Task')).to_be_visible()

    # Modal subtitle should describe what the AI will do
    expect(page.locator('text=What should the AI do?')).to_be_visible()

    # Placeholder should match the actual UI (different for 'All Stores' view)
    task_input = page.get_by_placeholder('e.g. Process billing files')
    expect(task_input).to_be_visible()

    # Create a task
    task_input.fill(task_title)
    page.get_by_role('button', name='Create', exact=True).click()

    # Task should appear in the list
    task_item = page.locator('button', has_text=task_title).first
    expect(task_item).to_be_visible(timeout=5000)

    # Click the task to see its details
    task_item.click()

    # Task detail should show the task title
    expect(page.locator('h2', has_text=task_title)).to_be_visible()
