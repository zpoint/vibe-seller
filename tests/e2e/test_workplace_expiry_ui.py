"""E2E: Feishu and DingTalk share WeCom's enabled-only expiry UX.

Each stand-in answers its real CLI's status command with an expired
sign-in. Before anything is enabled there is no banner and no task
button. After the first successful connection, an expired sign-in gives
one top-bar alert and one concise task failure with Re-authorize.
"""

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import sqlite3
import sys

import httpx
from playwright.sync_api import expect
import pytest

from tests.e2e import workplace_expiry_standin as standin
from tests.e2e.e2e_helpers import login_client, run_tag
from tests.e2e.evidence import Evidence
from tests.e2e.own_server import owned_agent_stack

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip('needs the mock CLI', allow_module_level=True)
if sys.platform == 'win32':
    pytest.skip('the stand-in is a shebang script', allow_module_level=True)

pytestmark = [pytest.mark.e2e]

EN = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / 'frontend/src/i18n/locales/en/translation.json'
    ).read_text(encoding='utf-8')
)
CLI = EN['integrations']['cli']


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    with owned_agent_stack(
        tmp_path_factory, 'workplace-expiry-ui', env={'MOCK_CLI': MOCK_CLI}
    ) as owned:
        yield owned


@pytest.fixture
def api(server):
    client = httpx.Client(timeout=60)
    login_client(client, server.base_url)
    yield client
    client.close()


def _failed_task(api, base: str, home: Path, title: str, error: str) -> str:
    """Create the UI precondition directly; no model is involved here."""
    r = api.post(
        f'{base}/api/tasks',
        json={'title': title, 'description': title, 'defer_start': True},
    )
    assert r.status_code == 200, r.text
    task = r.json()['id']
    with sqlite3.connect(home / 'data' / 'vibe_seller.db') as db:
        db.execute(
            "UPDATE tasks SET status='failed', error=?, "
            "error_category='agent_reported', updated_at=? WHERE id=?",
            (error, datetime.now(UTC).isoformat(), task),
        )
        db.commit()
    return task


@pytest.mark.parametrize('key', ['feishu', 'dingtalk'])
def test_expiry_appears_only_after_the_first_connection(
    key, server, api, authenticated_page
):
    page = authenticated_page
    base = server.base_url
    ev = Evidence(f'e2e-{key}-expiry', home=server.home)
    standin.reset(server.home)
    name = CLI['platform'][key]
    long_error = (
        f'{name} authorization has expired; a long explanation follows.'
    )
    task = _failed_task(
        api, base, server.home, f'{key} expiry {run_tag()}', long_error
    )

    # Never connected: neither the top bar nor the task offers re-auth.
    page.goto(f'{base}/tasks/{task}')
    expect(page.get_by_test_id('integration-expired-banner')).to_have_count(0)
    expect(
        page.get_by_test_id('task-error-settings-integrations')
    ).to_have_count(0)
    expect(page.get_by_test_id('task-error')).to_contain_text(long_error)
    ev.shot(page, '01-never-connected-no-prompt')

    # The platform was enabled once; now its official status says expired.
    standin.install(key, server.home, expired=True)
    attention = api.get(f'{base}/api/integrations/cli/attention').json()
    ev.write_json('attention.json', attention)
    assert [i['key'] for i in attention['items']] == [key]

    page.goto(f'{base}/tasks')
    bar = page.get_by_test_id('integration-expired-banner')
    expect(bar).to_contain_text(
        EN['integrationBanner']['message'].replace('{{name}}', name),
        timeout=20000,
    )
    ev.shot(page, '02-enabled-expired-banner')
    bar.click()
    expect(page).to_have_url(
        f'{base}/settings/integrations#cli-{key}', timeout=10000
    )
    expect(page.get_by_test_id(f'cli-relogin-{key}')).to_have_text(
        CLI['reauthorize'], timeout=20000
    )
    expect(page.get_by_test_id(f'cli-needs-signin-{key}')).to_be_visible()
    ev.shot(page, '03-integration-row-reauthorize')

    # The task's long agent explanation becomes one sentence and one action.
    page.goto(f'{base}/tasks/{task}')
    note = page.get_by_test_id('task-error')
    expect(note).to_contain_text(
        EN['integrationBanner']['taskError'].replace('{{name}}', name),
        timeout=20000,
    )
    expect(note).not_to_contain_text('a long explanation follows')
    button = page.get_by_test_id('task-error-settings-integrations')
    expect(button).to_be_visible()
    ev.shot(page, '04-task-concise-reauthorize')
    button.click()
    expect(page).to_have_url(
        f'{base}/settings/integrations#cli-{key}', timeout=10000
    )
    expect(page.get_by_test_id(f'cli-relogin-{key}')).to_have_text(
        CLI['reauthorize']
    )
