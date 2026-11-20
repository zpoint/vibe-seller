"""E2E: a new user with no AI set up is asked to set it up first.

Fresh install, a user whose AI is the built-in Claude, and Claude Code
on that machine not logged in: "New Task" shows the set-up-AI dialog
instead of the form; "Set up AI" lands on the AI settings tab with
"Add Model" highlighted, and clicking it opens the add-model form; the
tutorial links point at the docs page for the UI language; and once the
user has a model of their own as default, "New Task" opens the form and
the task is created with no dialog.

**This module owns its server** (no provider model on it), and that
server's ``claude`` is a stub
that reports "not logged in" (``claude auth status --json`` →
``loggedIn: false``, exit 1). The stub sits where the server looks
first (``<VIBE_HOME>/node_modules/.bin/claude``), so the result does
not depend on whether the machine running the tests is logged in, and
the run never touches a real login or provider.
"""

from collections.abc import Iterator
from contextlib import contextmanager
import stat
import sys
import time

import httpx
from playwright.sync_api import Page, expect
import pytest

from tests.e2e.e2e_helpers import admin_credentials
from tests.e2e.own_server import AGENT_SERVER_ENV, OwnServer, pick_free_port

pytestmark = [pytest.mark.e2e]

ZH_DOCS = 'https://docs.vibe-sellers.com/llm-keys/'
EN_DOCS = 'https://docs.vibe-sellers.com/en/llm-keys/'

#: A ``claude`` that was never logged in. Anything else it is asked to
#: do fails at once, so a task that does start ends quickly and calls
#: nothing.
STUB = (
    f'#!{sys.executable}\n'
    'import sys\n'
    "if sys.argv[1:3] == ['auth', 'status']:\n"
    '    print(\'{"loggedIn": false, "authMethod": "none"}\')\n'
    'sys.exit(1)\n'
)

#: Blank every credential the CLI would honour, so a developer's own
#: key in the test environment cannot make the default model usable.
NO_LOGIN_ENV = {
    'ANTHROPIC_API_KEY': '',
    'ANTHROPIC_AUTH_TOKEN': '',
    'CLAUDE_CODE_OAUTH_TOKEN': '',
    'CLAUDE_CODE_USE_BEDROCK': '',
    'CLAUDE_CODE_USE_VERTEX': '',
    # The readiness check answers "ready" under the mock CLI (tasks
    # never spawn claude there); this server must run the real rule.
    'MOCK_CLI': '',
}

BASE = ''


@pytest.fixture(scope='module', autouse=True)
def own_stack(tmp_path_factory):
    """Own server, stub CLI in place BEFORE start (the check runs once,
    at boot), and no provider model: any added AI would count as set
    up, so the plain ``OwnServer`` is used, not ``owned_agent_stack``."""
    global BASE
    home = tmp_path_factory.mktemp('ai-setup')
    cli = home / 'node_modules' / '.bin' / 'claude'
    cli.parent.mkdir(parents=True, exist_ok=True)
    cli.write_text(STUB)
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    server = OwnServer(
        port=pick_free_port(),
        home=home,
        env={**AGENT_SERVER_ENV, **NO_LOGIN_ENV},
    )
    server.start()
    try:
        BASE = server.base_url
        yield server
    finally:
        server.stop()


@contextmanager
def _admin() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=BASE, timeout=30) as c:
        c.post('/api/auth/login', json=admin_credentials()).raise_for_status()
        yield c


@pytest.fixture
def new_user():
    """A brand-new user: default AI is the built-in Claude."""
    name = f'e2e-ai-setup-{int(time.time() * 1000)}'
    with _admin() as c:
        r = c.post(
            '/api/users',
            json={'username': name, 'password': 'setup123', 'role': 'member'},
        )
        assert r.status_code == 200, r.text
        user = r.json()
    yield name, 'setup123'
    with _admin() as c:
        c.delete(f'/api/users/{user["id"]}')


def _open_app(page: Page, user: tuple[str, str], lang: str = 'en'):
    page.goto(BASE)
    page.evaluate(
        """(lang) => {
        localStorage.setItem('vibe-seller-language', lang)
        localStorage.setItem('vibe-seller-guide-seen', 'e2e')
    }""",
        lang,
    )
    page.evaluate(
        """async ([identifier, password]) => {
        const r = await fetch('/api/auth/login', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            credentials: 'include',
            body: JSON.stringify({identifier, password})});
        if (!r.ok) throw new Error('login failed ' + r.status);
    }""",
        list(user),
    )
    page.goto(f'{BASE}/tasks')
    expect(page.locator('h1')).to_have_text('Vibe Seller')


def _create_task(page: Page, title: str):
    page.get_by_role('button', name='New Task').first.click()
    page.locator('#task-prompt').fill(title)
    page.get_by_role('button', name='Create', exact=True).click()


def _task_titles(user: tuple[str, str]) -> list[str]:
    with httpx.Client(base_url=BASE, timeout=30) as c:
        c.post(
            '/api/auth/login',
            json={'identifier': user[0], 'password': user[1]},
        ).raise_for_status()
        return [t['title'] for t in c.get('/api/tasks').json()]


def test_new_task_shows_dialog_then_highlight_on_ai_page(page: Page, new_user):
    _open_app(page, new_user)
    page.get_by_role('button', name='New Task').first.click()

    dialog = page.get_by_test_id('ai-setup-prompt')
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text('Set up AI first')
    expect(page.locator('#task-prompt')).to_have_count(0)  # no form

    dialog.get_by_role('button', name='Set up AI').click()
    expect(page).to_have_url(f'{BASE}/settings/ai')
    guide = page.get_by_test_id('ai-setup-guide')
    expect(guide).to_be_visible()
    expect(guide).to_contain_text('Click here to add an AI model')

    # The highlighted button itself is clickable through the dim.
    page.locator('[data-tour="add-model"]').click()
    expect(guide).to_have_count(0)
    expect(page.get_by_text('Add AI Model').first).to_be_visible()


def test_docs_links_target_the_setup_page(page: Page, new_user):
    _open_app(page, new_user, lang='en')
    page.get_by_role('button', name='New Task').first.click()
    dialog = page.get_by_test_id('ai-setup-prompt')
    link = dialog.get_by_role('link', name='Read the setup guide')
    expect(link).to_have_attribute('href', EN_DOCS)
    expect(link).to_have_attribute('target', '_blank')

    page.evaluate("() => localStorage.setItem('vibe-seller-language', 'zh')")
    page.goto(f'{BASE}/settings/ai')
    guide = page.get_by_test_id('ai-setup-guide')
    expect(guide).to_be_visible()  # first visit, AI not set up
    expect(guide.get_by_role('link', name='还没有 API Key')).to_have_attribute(
        'href', ZH_DOCS
    )


def test_with_a_model_as_default_no_dialog(page: Page, new_user):
    # The user adds a model with a key and keeps it as default (what
    # the guide walks them through). Its endpoint is a dead local port:
    # the task is created and then fails fast, calling nothing.
    _open_app(page, new_user)
    profile_id = f'e2e-model-{int(time.time() * 1000)}'
    page.evaluate(
        """async (id) => {
        const post = await fetch('/api/profiles', {
            method: 'POST', credentials: 'include',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({id, name: 'Test model', env: {
                ANTHROPIC_BASE_URL: 'http://127.0.0.1:9/anthropic',
                ANTHROPIC_AUTH_TOKEN: 'sk-e2e',
                ANTHROPIC_MODEL: 'test-model'}})});
        if (!post.ok) throw new Error('create ' + post.status);
        const def = await fetch(`/api/profiles/${id}/set-default`, {
            method: 'PATCH', credentials: 'include'});
        if (!def.ok) throw new Error('default ' + def.status);
    }""",
        profile_id,
    )
    try:
        page.goto(f'{BASE}/tasks')
        title = f'Configured user task {int(time.time())}'
        _create_task(page, title)
        expect(page.get_by_text(title).first).to_be_visible()
        expect(page.get_by_test_id('ai-setup-prompt')).to_have_count(0)
        assert title in _task_titles(new_user)

        # And the AI page has no guide for a user who is set up.
        page.goto(f'{BASE}/settings/ai')
        expect(page.get_by_text('AI Models').first).to_be_visible()
        page.wait_for_timeout(1500)
        expect(page.get_by_test_id('ai-setup-guide')).to_have_count(0)
    finally:
        with _admin() as c:
            c.delete(f'/api/profiles/{profile_id}')
