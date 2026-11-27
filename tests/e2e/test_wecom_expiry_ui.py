"""E2E: one WeCom authorization-expired state, in a real browser.

The agent is the mock CLI and WeCom is a stand-in CLI, so CI needs no
account. The row shows one expired warning and Re-authorize; the top
bar puts a plan to review first, then the expiry, and opens WeCom's
row. A fresh scan clears it. No individual capabilities are exposed.
"""

import json
import os
from pathlib import Path
import re
import sys

import httpx
from playwright.sync_api import Page, expect
import pytest

from tests.e2e import wecom_standin as standin
from tests.e2e.e2e_helpers import create_store, login_client, run_tag
from tests.e2e.evidence import Evidence
from tests.e2e.own_server import owned_agent_stack
from tests.e2e.question_park_flow import poll

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
BARS = (
    'waiting-task-banner',
    'planner-question-banner',
    'task-plan-banner',
    'plan-approval-banner',
    'integration-expired-banner',
)


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    with owned_agent_stack(
        tmp_path_factory, 'wecom-expiry-ui', env={'MOCK_CLI': MOCK_CLI}
    ) as owned:
        yield owned


@pytest.fixture
def api(server):
    client = httpx.Client(timeout=60)
    login_client(client, server.base_url)
    yield client
    client.close()


def _open_integrations(page: Page, base: str) -> None:
    with page.expect_response('**/api/integrations/cli/wecom/expiry') as seen:
        page.goto(f'{base}/settings/integrations')
    assert seen.value.ok, seen.value.text()
    expect(page.get_by_test_id('cli-expiry-checking-wecom')).to_have_count(
        0, timeout=30000
    )


def _only_bar(page: Page, test_id: str, text: str):
    bar = page.get_by_test_id(test_id)
    expect(bar).to_contain_text(text, timeout=20000)
    for other in BARS:
        if other != test_id:
            expect(page.get_by_test_id(other)).to_have_count(0)
    return bar


def _console_errors(page: Page) -> list[str]:
    errors: list[str] = []
    page.on(
        'console',
        lambda m: errors.append(m.text) if m.type == 'error' else None,
    )
    page.on('pageerror', lambda e: errors.append(str(e)))
    return errors


def test_row_shows_one_expired_warning(server, authenticated_page):
    page = authenticated_page
    base = server.base_url
    ev = Evidence('e2e-wecom-expiry-row', home=server.home)
    errors = _console_errors(page)
    standin.install(server.home)

    _open_integrations(page, base)
    expect(page.get_by_test_id('cli-relogin-wecom')).to_have_text(
        CLI['relogin']
    )
    expect(page.get_by_test_id('cli-expired-wecom')).to_have_count(0)
    expect(page.get_by_test_id('cli-expiry-hint-wecom')).to_have_count(0)
    ev.shot(page, '01-authorization-valid')

    standin.set_expired(server.home, True)
    _open_integrations(page, base)
    row = page.get_by_test_id('cli-row-wecom')
    expect(page.get_by_test_id('cli-expired-wecom')).to_have_text(
        CLI['expired']
    )
    expect(page.get_by_test_id('cli-expiry-hint-wecom')).to_contain_text(
        CLI['expiredHint']
    )
    button = page.get_by_test_id('cli-relogin-wecom')
    expect(button).to_have_text(CLI['reauthorize'])
    expect(button).to_have_class(re.compile(r'\bbg-indigo-600\b'))
    # The former per-capability labels and renewal links are gone.
    expect(page.get_by_test_id('cli-permissions-wecom')).to_have_count(0)
    box = row.bounding_box()
    assert box is not None
    assert (
        page.get_by_test_id('cli-expiry-hint-wecom').bounding_box() is not None
    )
    assert page.evaluate(
        'document.documentElement.scrollWidth <= document.documentElement.clientWidth'
    )
    ev.shot(page, '02-authorization-expired')
    row.screenshot(path=str(ev.dir / '02b-wecom-row.png'))
    assert errors == [], errors


def test_bar_shows_expiry_last_and_opens_wecom_row(
    server, api, authenticated_page
):
    page = authenticated_page
    base = server.base_url
    ev = Evidence('e2e-wecom-expiry-bar', home=server.home)
    errors = _console_errors(page)
    tag = run_tag()
    standin.install(server.home)
    standin.set_expired(server.home, True)
    assert api.get(f'{base}/api/integrations/cli/wecom/expiry').json() == {
        'expired': True
    }
    attention = api.get(f'{base}/api/integrations/cli/attention').json()
    ev.write_json('attention.json', attention)
    assert attention['items'] == [{'key': 'wecom', 'title': 'WeCom (企业微信)'}]

    store = create_store(api, f'wecom-bar-{tag}')
    r = api.post(
        f'{base}/api/tasks',
        json={
            'title': f'Plan first {tag}',
            'store_id': store['id'],
            'plan_mode': True,
        },
    )
    assert r.status_code == 200, r.text
    task = r.json()['id']
    poll(api, base, task, ev, 'P', lambda t: t['status'] == 'planned', 60, 0.5)
    page.goto(f'{base}/tasks')
    bar = _only_bar(
        page, 'task-plan-banner', '1 task has a plan waiting for your review'
    )
    ev.shot(page, '01-plan-to-review-first')
    bar.click()
    page.get_by_role('button', name='Confirm & Execute').click()
    poll(
        api, base, task, ev, 'P', lambda t: t['status'] == 'completed', 60, 0.5
    )

    name = CLI['platform']['wecom']
    bar = _only_bar(
        page,
        'integration-expired-banner',
        EN['integrationBanner']['message'].replace('{{name}}', name),
    )
    expect(bar).to_contain_text(EN['integrationBanner']['action'])
    ev.shot(page, '02-wecom-expired-next')
    bar.click()
    expect(page).to_have_url(
        f'{base}/settings/integrations#cli-wecom', timeout=10000
    )
    expect(page.get_by_test_id('cli-row-wecom')).to_be_in_viewport(
        timeout=20000
    )
    expect(page.get_by_test_id('cli-relogin-wecom')).to_have_text(
        CLI['reauthorize'], timeout=30000
    )
    ev.shot(page, '03-opened-wecom-row')

    standin.renew(server.home)
    page.goto(f'{base}/tasks')
    for item in BARS:
        expect(page.get_by_test_id(item)).to_have_count(0, timeout=20000)
    assert api.get(f'{base}/api/integrations/cli/attention').json() == {
        'items': []
    }
    ev.shot(page, '04-renewed-no-bar')
    assert errors == [], errors
