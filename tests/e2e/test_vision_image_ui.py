"""E2E browser test for the Vision image-generation UI.

Verifies the interactive path the user cares about:
  - Settings → AI → Vision renders and saves a key
  - when the image tool runs, a confirm card pops up with the prompt +
    model; the user can edit the prompt and pick the model
  - after Confirm, the generated image renders INLINE in the stream

The agent's tool call is simulated by POSTing the same endpoint the MCP
tool hits (``/api/tasks/{id}/image/generate``) from a background thread;
that call blocks awaiting the user's confirmation, exactly as in
production. The server must run with ``VISION_FAKE=1`` so generation is
deterministic and offline.

**This module owns its server**, booted with ``VISION_FAKE=1`` and a
dedicated ``VIBE_HOME``: what it writes is SERVER-GLOBAL — the Settings
save writes the one kie.ai key the whole server generates with, and it is
never restored — and the vision config is read by other modules (the MCP
image gates, the catalog) while it holds a test key. Owning the server
keeps the test key and the fake mode in this module, where they belong.

Run:
  .venv/bin/python -m pytest --e2e tests/e2e/test_vision_image_ui.py
"""

import threading
import time

import httpx
from playwright.sync_api import expect
import pytest

from tests.e2e.own_server import owned_agent_stack

pytestmark = [pytest.mark.e2e]

# Set by the module fixture (own_stack) to this module's own server.
BASE_URL = ''


@pytest.fixture(scope='module', autouse=True)
def own_stack(tmp_path_factory):
    """A server of this module's own, in fake-vision mode.

    The fake mode is no longer something the module probes and hopes the
    shared server was started with — it starts its own with it, so the
    gate below is a check on what it built, not a race with a stranger.
    """
    global BASE_URL
    with owned_agent_stack(
        tmp_path_factory, 'vision-ui', env={'VISION_FAKE': '1'}
    ) as server:
        BASE_URL = server.base_url
        # Skip the whole module unless the server carries the vision
        # route AND runs in VISION_FAKE mode. The fake-mode gate is the
        # contract, not a convenience: against a real image API these
        # tests would (a) hit it with a bogus key — the confirm flow then
        # never emits image_generated and times out (seen live on CI),
        # and (b) the settings test would overwrite a real user's
        # configured key.
        probe = _login_api()
        try:
            resp = probe.get(f'{BASE_URL}/api/vision/config')
            cfg = resp.json() if resp.status_code == 200 else {}
        finally:
            probe.close()
        if not cfg.get('fake'):
            pytest.skip(
                'vision e2e requires a VISION_FAKE=1 server (offline, '
                'deterministic, and safe to write test keys) — this '
                f'one at {BASE_URL} reports {cfg!r}'
            )
        yield server


def _login_api() -> httpx.Client:
    """Login as the seeded admin — the only account on this home."""
    client = httpx.Client(timeout=30)
    client.post(
        f'{BASE_URL}/api/auth/login',
        json={'identifier': 'admin@vibe-seller.local', 'password': 'admin'},
    )
    return client


def _create_task(client: httpx.Client, title: str) -> str:
    r = client.post(
        f'{BASE_URL}/api/tasks',
        json={'title': title},
    )
    r.raise_for_status()
    return r.json()['id']


def _select_task(page, title: str):
    page.reload()
    page.wait_for_selector('h1', timeout=10000)
    all_stores = page.locator('button', has_text='All Stores')
    if all_stores.count() > 0:
        all_stores.first.click()
    task_btn = page.locator('button', has_text=title).first
    task_btn.wait_for(timeout=10000)
    task_btn.click()
    page.locator('h2', has_text=title).wait_for(timeout=10000)


def test_vision_settings_panel_saves_key(authenticated_page):
    page = authenticated_page
    page.reload()
    page.wait_for_selector('h1', timeout=10000)
    # Open Settings, then the AI tab (via stable testids, locale-agnostic).
    page.locator('[data-testid="nav-settings"]').click()
    page.locator('[data-testid="settings-tab-aiAgent"]').click()
    panel = page.locator('[data-testid="vision-panel"]')
    panel.wait_for(timeout=10000)
    page.locator('[data-testid="vision-key-input"]').fill('sk-e2e-test-4242')
    page.locator('[data-testid="vision-key-save"]').click()
    status = page.locator('[data-testid="vision-key-status"]')
    expect(status).to_contain_text('4242', timeout=10000)


def test_confirm_card_edit_and_inline_image(authenticated_page):
    page = authenticated_page
    api = _login_api()
    try:
        title = f'Vision image e2e {int(time.time())}'
        task_id = _create_task(api, title)
        _select_task(page, title)

        # Simulate the agent's MCP tool call — it blocks awaiting confirm.
        result: dict = {}

        def _fire():
            c = _login_api()
            try:
                resp = c.post(
                    f'{BASE_URL}/api/tasks/{task_id}/image/generate',
                    json={
                        'prompt': '主图：白色棉袜，纯白背景',
                        'model': 'nano-banana-pro-2k',
                        'output_name': 'main.png',
                        'kind': 'main',
                    },
                    timeout=120,
                )
                result['body'] = resp.json()
            finally:
                c.close()

        t = threading.Thread(target=_fire, daemon=True)
        t.start()

        # The confirm card pops up with the proposed prompt.
        card = page.locator('[data-testid="image-request-card"]')
        card.wait_for(timeout=15000)
        prompt_box = page.locator('[data-testid="image-prompt-input"]')
        expect(prompt_box).to_have_value(
            '主图：白色棉袜，纯白背景', timeout=5000
        )

        # User edits the prompt and switches the model+tier. The model
        # picker is a left→right cascade (custom popover, not a <select>):
        # click the trigger, pick the provider column, then the model
        # option (data-model-id is a model-tier id like "nano-banana-2-1-2k").
        prompt_box.fill('主图：白色棉袜，纯白背景，产品占85%')
        page.locator('[data-testid="image-model-select"]').click()
        page.locator('[data-testid="image-provider-Google"]').click()
        page.locator('[data-model-id="nano-banana-2-1-2k"]').click()
        page.locator('[data-testid="image-confirm-btn"]').click()

        # The generated image renders inline.
        img = page.locator('[data-testid="generated-image"]')
        img.wait_for(timeout=20000)
        src = img.get_attribute('src')
        assert src and '/files/generated_images/' in src

        t.join(timeout=20)
        assert result.get('body', {}).get('status') == 'ok'
        # The user's edits won.
        assert result['body']['prompt'] == '主图：白色棉袜，纯白背景，产品占85%'
        assert result['body']['model'] == 'nano-banana-2-1-2k'
    finally:
        api.close()
