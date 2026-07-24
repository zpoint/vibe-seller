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

Run:
  VISION_FAKE=1 MOCK_CLI=tests/e2e/mock_cli.py PORT=7799 \
    VIBE_HOME=~/.vibe-seller-vision-e2e ./start.sh --dev
  E2E_BASE_URL=http://127.0.0.1:7799 \
    .venv/bin/python -m pytest --e2e tests/e2e/test_vision_image_ui.py
"""

import threading
import time

import httpx
from playwright.sync_api import expect
import pytest

from tests.e2e.conftest import BASE_URL

pytestmark = [pytest.mark.e2e]


def _login_api() -> httpx.Client:
    client = httpx.Client(timeout=30)
    client.post(
        f'{BASE_URL}/api/auth/login',
        json={'identifier': 'admin@vibe-seller.local', 'password': 'admin'},
    )
    return client


# Skip the whole module unless the server is up, carries the vision
# route, AND runs in VISION_FAKE mode. The fake-mode gate is the
# contract, not a convenience: against a real server these tests would
# (a) hit the real image API with a bogus key — the confirm flow then
# never emits image_generated and times out (seen live on CI), and
# (b) the settings test would overwrite a real user's configured key.
try:
    _probe = _login_api()
    _resp = _probe.get(f'{BASE_URL}/api/vision/config')
    _cfg = _resp.json() if _resp.status_code == 200 else {}
    _probe.close()
except Exception:
    _cfg = {}
if not _cfg.get('fake'):
    pytest.skip(
        'vision e2e requires a VISION_FAKE=1 server (offline, '
        'deterministic, and safe to write test keys) — start it with '
        'VISION_FAKE=1 ./start.sh',
        allow_module_level=True,
    )


def _create_task(client: httpx.Client, title: str) -> str:
    r = client.post(f'{BASE_URL}/api/tasks', json={'title': title})
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

        # User edits the prompt and switches the model+tier (the model
        # dropdown is grouped by provider via <optgroup>; option values
        # are model-tier ids like "nano-banana-2-2k").
        prompt_box.fill('主图：白色棉袜，纯白背景，产品占85%')
        page.locator('[data-testid="image-model-select"]').select_option(
            'nano-banana-2-2k'
        )
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
        assert result['body']['model'] == 'nano-banana-2-2k'
    finally:
        api.close()
