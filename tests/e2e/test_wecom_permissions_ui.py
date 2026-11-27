"""E2E: an expired WeCom permission, on a real server in a real browser.

WeCom grants its bot each permission for 7 days, and its CLI keeps
saying "signed in" after one expires. The server checks each permission
with one read-only call; the page shows it. Here the agent is the mock
CLI (no model) and WeCom is the stand-in ``wecom-cli``
(``tests/e2e/fake_wecom_cli.py``, laid down by ``wecom_standin``) —
connected and enabled the way an admin's scan leaves it.

* ``test_row_lists_each_permission`` — Settings → Integrations → WeCom
  lists every permission. Two expiring turn the status amber, give each
  its own Renew link and make "Re-authorize" lead the row; the row holds
  its layout (every permission inside it, no sideways scroll) and the
  page logs no error.
* ``test_bar_shows_expiry_last_and_opens_integrations`` — ONE bar: a plan
  to review holds it first; once handled, the expired permission takes
  it; clicking it opens Settings → Integrations. A platform never
  connected never shows up; a new scan clears the bar.

Everything a reviewer needs (screenshots, the attention answers) is
written by ``Evidence``.
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
    pytest.skip(
        'MOCK_CLI not set — the WeCom permission UI e2e needs the mock CLI',
        allow_module_level=True,
    )
if sys.platform == 'win32':
    pytest.skip(
        'the stand-in wecom-cli is a shebang script', allow_module_level=True
    )

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
    """Our own server: enabling WeCom reaches every agent on it."""
    with owned_agent_stack(
        tmp_path_factory, 'wecom-permissions-ui', env={'MOCK_CLI': MOCK_CLI}
    ) as owned:
        yield owned


@pytest.fixture
def api(server):
    client = httpx.Client(timeout=60)
    login_client(client, server.base_url)
    yield client
    client.close()


def _permissions(api, base) -> dict[str, str]:
    r = api.get(f'{base}/api/integrations/cli/wecom/permissions')
    assert r.status_code == 200, r.text
    return {p['key']: p['state'] for p in r.json()['permissions'] or []}


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


def test_row_lists_each_permission(server, api, authenticated_page):
    page = authenticated_page
    base = server.base_url
    ev = Evidence('e2e-wecom-permissions-row', home=server.home)
    errors = _console_errors(page)
    standin.install(server.home)

    page.goto(f'{base}/settings/integrations')
    row = page.get_by_test_id('cli-row-wecom')
    expect(page.get_by_test_id('cli-permissions-wecom')).to_be_visible(
        timeout=30000
    )
    for key in standin.PERMISSIONS:
        expect(
            page.get_by_test_id(f'cli-permission-wecom-{key}')
        ).to_have_attribute('data-state', 'ok')
    expect(page.get_by_test_id('cli-relogin-wecom')).to_have_text(
        CLI['relogin']
    )
    expect(page.get_by_test_id('cli-permissions-hint-wecom')).to_have_count(0)
    ev.shot(page, '01-every-permission-works')

    standin.set_expired(server.home, ['mail', 'contact'])
    page.reload()
    expect(page.get_by_test_id('cli-permission-wecom-mail')).to_have_attribute(
        'data-state', 'expired', timeout=30000
    )
    expect(
        page.get_by_test_id('cli-permission-wecom-contact')
    ).to_have_attribute('data-state', 'expired')
    expect(page.get_by_test_id('cli-permission-wecom-doc')).to_have_attribute(
        'data-state', 'ok'
    )
    # Each expired permission links its own renewal, in a new tab.
    for key, kind in (('mail', 7), ('contact', 4)):
        link = page.get_by_test_id(f'cli-permission-renew-wecom-{key}')
        assert f'type={kind}' in (link.get_attribute('href') or ''), key
        assert link.get_attribute('target') == '_blank'
    expect(page.get_by_test_id('cli-permission-renew-wecom-doc')).to_have_count(
        0
    )
    expect(page.get_by_test_id('cli-permissions-expired-wecom')).to_have_text(
        CLI['permissions']['someExpired']
    )
    button = page.get_by_test_id('cli-relogin-wecom')
    expect(button).to_have_text(CLI['reauthorize'])
    expect(button).to_have_class(re.compile(r'\bbg-indigo-600\b'))
    expect(page.get_by_test_id('cli-permissions-hint-wecom')).to_contain_text(
        CLI['permissions']['expiredHint']
    )

    # The row holds its layout: every permission inside it, nothing
    # pushing the page sideways.
    box = row.bounding_box()
    assert box is not None
    for key in standin.PERMISSIONS:
        pill = page.get_by_test_id(f'cli-permission-wecom-{key}').bounding_box()
        assert pill is not None and pill['width'] > 0, key
        assert box['x'] <= pill['x'], key
        assert pill['x'] + pill['width'] <= box['x'] + box['width'] + 0.5, key
    assert page.evaluate(
        'document.documentElement.scrollWidth'
        ' <= document.documentElement.clientWidth'
    )
    ev.shot(page, '02-two-expired')
    row.screenshot(path=str(ev.dir / '02b-wecom-row.png'))
    assert errors == [], errors


def test_bar_shows_expiry_last_and_opens_integrations(
    server, api, authenticated_page
):
    page = authenticated_page
    base = server.base_url
    ev = Evidence('e2e-wecom-permissions-bar', home=server.home)
    errors = _console_errors(page)
    tag = run_tag()
    standin.install(server.home)
    # Mail has worked here (the Settings card checked it while it did)…
    assert set(_permissions(api, base).values()) == {'ok'}
    # …and then expired.
    standin.set_expired(server.home, ['mail'])
    assert _permissions(api, base)['mail'] == 'expired'
    attention = api.get(f'{base}/api/integrations/cli/attention').json()
    ev.write_json('attention.json', attention)
    # Only WeCom: Feishu and DingTalk were never connected here.
    assert attention['items'] == [
        {'key': 'wecom', 'title': 'WeCom (企业微信)', 'expired': ['mail']}
    ]

    # A plan to review holds the bar first.
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

    # Then the expired permission takes the bar — and leads to the fix.
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
    expect(page.get_by_test_id('cli-relogin-wecom')).to_have_text(
        CLI['reauthorize'], timeout=30000
    )
    # It lands on WeCom's row, not on the top of a long page.
    expect(page.get_by_test_id('cli-row-wecom')).to_be_in_viewport()
    ev.shot(page, '03-opened-integrations')

    # A new scan renews every permission: the bar goes.
    standin.renew(server.home)
    page.goto(f'{base}/tasks')
    for b in BARS:
        expect(page.get_by_test_id(b)).to_have_count(0, timeout=20000)
    assert api.get(f'{base}/api/integrations/cli/attention').json() == {
        'items': []
    }
    ev.shot(page, '04-renewed-no-bar')
    assert errors == [], errors
