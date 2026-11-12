"""E2E: the first-run guide is per browser (localStorage), not per user.

``authenticated_page`` pre-writes the seen key (every other UI test
assumes an uncovered app), so this test drives its own fresh browser
contexts. It pins the whole storage story end to end: a fresh
context shows the guide; a reload BEFORE any dismissal shows it
again; Skip writes the key; a reload after that stays quiet; and a
NEW browser context (another browser) shows it again.
"""

import time

import httpx
from playwright.sync_api import Browser, Page, expect
import pytest

from tests.e2e import e2e_helpers
from tests.e2e.e2e_helpers import login

pytestmark = [pytest.mark.e2e]

SEEN_KEY = 'vibe-seller-guide-seen'


def _login(page: Page, base: str, identifier: str, password: str):
    page.goto(base)
    page.evaluate("() => localStorage.setItem('vibe-seller-language', 'en')")
    page.evaluate(
        """async (creds) => {
        const r = await fetch('/api/auth/login', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            credentials: 'include', body: JSON.stringify(creds)});
        if (!r.ok) throw new Error('Login failed: ' + r.status);
    }""",
        {'identifier': identifier, 'password': password},
    )
    page.goto(base)
    expect(page.locator('h1')).to_have_text('Vibe Seller')


def test_guide_seen_key_is_per_browser(page: Page, browser: Browser):
    base = e2e_helpers.BASE_URL
    username = f'e2e-guide-{int(time.time())}'
    password = 'guide123'
    user_id = None
    try:
        with httpx.Client(timeout=30) as c:
            login(c)
            r = c.post(
                f'{base}/api/users',
                json={
                    'username': username,
                    'password': password,
                    'role': 'member',
                },
            )
            assert r.status_code == 200, r.text
            user_id = r.json()['id']

        guide = page.get_by_test_id('onboarding-guide')
        _login(page, base, username, password)
        expect(guide).to_be_visible()
        expect(guide).to_contain_text('Step 1 of 3')

        # Showing alone writes nothing: a reload re-shows it.
        assert (
            page.evaluate(f'() => localStorage.getItem("{SEEN_KEY}")') is None
        )
        page.goto(base)
        expect(guide).to_be_visible()

        # Skip writes the key; after that a reload stays quiet.
        guide.get_by_role('button', name='Skip', exact=True).click()
        expect(guide).to_have_count(0)
        assert page.evaluate(f'() => localStorage.getItem("{SEEN_KEY}")')
        page.goto(base)
        expect(page.locator('h1')).to_have_text('Vibe Seller')
        expect(guide).to_have_count(0)

        # Another browser (new context) has never seen it.
        ctx2 = browser.new_context()
        try:
            page2 = ctx2.new_page()
            _login(page2, base, username, password)
            expect(page2.get_by_test_id('onboarding-guide')).to_be_visible()
        finally:
            ctx2.close()
    finally:
        if user_id:
            with httpx.Client(timeout=30) as c:
                login(c)
                client_delete = c.delete(f'{base}/api/users/{user_id}')
                assert client_delete.status_code == 200
