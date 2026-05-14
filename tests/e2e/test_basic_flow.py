"""
E2E test: verify basic flow - create store, create task, task appears in list.
Uses Playwright to drive the browser against the running app at localhost:7777.
"""

import re
import time

from playwright.sync_api import Page, expect
import pytest

pytestmark = [pytest.mark.e2e]


def test_create_store_and_task(authenticated_page: Page):
    """Full flow: create a store, create a task via modal, verify it appears."""
    page = authenticated_page
    store_name = f'e2e-test-{int(time.time())}'

    # 1. Click "+ New Store"
    page.get_by_role('button', name='+ New Store').click()

    # 2. Type store name and create
    store_input = page.get_by_placeholder('Store name...')
    expect(store_input).to_be_visible()
    store_input.fill(store_name)
    page.get_by_role('button', name='Create', exact=True).click()

    # 3. Store should appear in sidebar — click it (use .first to avoid strict mode)
    store_btn = page.get_by_role('button', name=re.compile(store_name)).first
    expect(store_btn).to_be_visible(timeout=5000)
    store_btn.click()

    # 4. Verify we see the store header and task count
    expect(page.locator('h2', has_text=store_name)).to_be_visible()

    # 5. Click "+ New Task" button — opens modal
    page.get_by_role('button', name='+ New Task').click()

    # 6. Modal should appear with title input
    modal_title = page.get_by_placeholder('e.g. Navigate to google.com')
    expect(modal_title).to_be_visible()

    # 7. Type a task title and submit
    task_title = f'Navigate to google.com {int(time.time())}'
    modal_title.fill(task_title)
    page.get_by_role('button', name='Create & Run').click()

    # 8. Task should appear in the task list
    task_item = page.locator('button', has_text=task_title).first
    expect(task_item).to_be_visible(timeout=5000)

    # 9. Task detail panel should show the task title
    expect(page.locator('h2', has_text=task_title)).to_be_visible()


def test_empty_store_shows_create_first_task_button(authenticated_page: Page):
    """When a store has no tasks, the empty state shows a 'Create First Task' button."""
    page = authenticated_page
    store_name = f'e2e-empty-{int(time.time())}'

    # Create a new store
    page.get_by_role('button', name='+ New Store').click()
    page.get_by_placeholder('Store name...').fill(store_name)
    page.get_by_role('button', name='Create', exact=True).click()

    # Click the new store
    store_btn = page.get_by_role('button', name=re.compile(store_name)).first
    expect(store_btn).to_be_visible(timeout=5000)
    store_btn.click()

    # Should see "Create your first task to get started" button in empty state
    first_task_btn = page.get_by_role(
        'button', name='Create your first task to get started'
    )
    expect(first_task_btn).to_be_visible()

    # Click it — should open the modal with task title input
    first_task_btn.click()
    expect(
        page.get_by_placeholder('e.g. Navigate to google.com')
    ).to_be_visible()


def test_cancel_task_creation(authenticated_page: Page):
    """Clicking Cancel closes the task creation modal without creating a task."""
    page = authenticated_page
    store_name = f'e2e-cancel-{int(time.time())}'

    # Create a store to use
    page.get_by_role('button', name='+ New Store').click()
    page.get_by_placeholder('Store name...').fill(store_name)
    page.get_by_role('button', name='Create', exact=True).click()

    # Click the store
    store_btn = page.get_by_role('button', name=re.compile(store_name)).first
    expect(store_btn).to_be_visible(timeout=5000)
    store_btn.click()

    # Open task creation modal
    page.get_by_role('button', name='+ New Task').click()
    expect(
        page.get_by_placeholder('e.g. Navigate to google.com')
    ).to_be_visible()

    # Cancel
    page.get_by_role('button', name='Cancel').last.click()

    # Modal should be closed — title input hidden
    expect(
        page.get_by_placeholder('e.g. Navigate to google.com')
    ).to_be_hidden()
