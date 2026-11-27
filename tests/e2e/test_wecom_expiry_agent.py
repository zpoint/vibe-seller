"""E2E (real model): an agent that hits an expired WeCom authorization sends
the user to re-authorize — and only then.

WeCom is the stand-in ``wecom-cli`` (``tests/e2e/fake_wecom_cli.py``),
enabled with WeCom's real official skills, with its mail permission
expired. The agent reads the umbrella rules this release writes
(``app/cli_integrations/skills.py``).

* ``test_an_expired_permission_sends_the_user_to_re_authorize`` — asked
  for the latest mail, the agent runs the CLI, gets 850003, stops, and
  tells the user — with the link that opens WeCom's row in Settings →
  Integrations; it never signs in and never passes on the renewal link.
  In the browser that link is a button that lands on the row.
* ``test_a_task_without_wecom_says_nothing_about_it`` — a task that does
  not need WeCom never mentions it and never runs its CLI.

Spends model credits; the mock-agent half is
``test_wecom_permissions_ui.py``.
"""

import os
import sys

import httpx
from playwright.sync_api import expect
import pytest

from app.cli_integrations import manager
from app.cli_integrations.registry import INTEGRATIONS
from tests.e2e import e2e_helpers, wecom_standin as standin
from tests.e2e.e2e_helpers import login_client
from tests.e2e.evidence import Evidence
from tests.e2e.own_server import owned_agent_stack

if os.environ.get('MOCK_CLI'):
    pytest.skip(
        'needs a real model (the mock CLI never reads the skills)',
        allow_module_level=True,
    )
if sys.platform == 'win32':
    pytest.skip(
        'the stand-in wecom-cli is a shebang script', allow_module_level=True
    )

pytestmark = [pytest.mark.e2e]

DONE = {'completed', 'failed', 'waiting'}
LINK = '](#integrations-wecom)'


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    """Our own server: enabling WeCom reaches every agent on it."""
    with owned_agent_stack(tmp_path_factory, 'wecom-permissions-agent') as own:
        integ = INTEGRATIONS['wecom']
        subs = manager._fetch_skills_blocking(integ, standin.VERSION)
        standin.install(own.home, subs=subs)
        standin.set_expired(own.home, True)
        yield own


@pytest.fixture
def api(server):
    client = httpx.Client(timeout=60)
    login_client(client, server.base_url)
    yield client
    client.close()


def _said(api, base, task: dict) -> str:
    """Everything the agent told the user: its messages, result, error
    and any question it asked."""
    r = api.get(f'{base}/api/tasks/{task["id"]}/messages')
    body = r.json()
    msgs = body if isinstance(body, list) else body.get('messages', [])
    parts = [task.get('result') or '', task.get('error') or '']
    parts += [
        m['content']
        for m in msgs
        if m.get('role') in ('assistant', 'result')
        and isinstance(m.get('content'), str)
    ]
    q = api.get(f'{base}/api/tasks/{task["id"]}/questions/pending').json()
    parts.append(repr(q.get('questions') or ''))
    return '\n'.join(parts)


def _run(api, title: str, description: str) -> dict:
    task = e2e_helpers.create_task(
        api, title=title, description=description, plan_mode=False
    )
    return e2e_helpers.poll_task_status(
        api, task['id'], DONE, fail_statuses=set()
    )


def test_an_expired_permission_sends_the_user_to_re_authorize(
    server, api, authenticated_page
):
    base = server.base_url
    ev = Evidence('e2e-wecom-expired-agent', home=server.home)
    before = len(standin.calls(server.home))
    task = _run(
        api,
        'WeCom latest mail',
        '用企业微信帮我看一下邮箱里最近一封邮件的标题',
    )
    said = _said(api, base, task)
    calls = standin.calls(server.home)[before:]
    ev.write_json('task.json', task)
    ev.write_json('cli-calls.json', calls)
    (ev.dir / 'said.txt').write_text(said, encoding='utf-8')

    # It used the CLI and met the expired permission…
    assert any(c[:1] == ['mail'] for c in calls), calls
    # …never tried to sign in or renew it itself…
    assert not any(c[:2] == ['auth', 'init'] for c in calls), calls
    assert 'authorizationList' not in said
    # …and sent the user to the row that renews it.
    assert LINK in said, said[-2000:]
    assert '重新授权' in said or 'Re-authorize' in said

    page = authenticated_page
    page.goto(f'{base}/tasks/{task["id"]}')
    button = page.get_by_role('button', name='Settings → Integrations').or_(
        page.get_by_test_id('task-error-settings-integrations')
    )
    expect(button.first).to_be_visible(timeout=20000)
    ev.shot(page, '01-task-says-re-authorize')
    button.first.click()
    expect(page).to_have_url(
        f'{base}/settings/integrations#cli-wecom', timeout=10000
    )
    expect(page.get_by_test_id('cli-row-wecom')).to_be_in_viewport(
        timeout=20000
    )
    expect(page.get_by_test_id('cli-expired-wecom')).to_have_attribute(
        'data-state', 'expired', timeout=30000
    )
    ev.shot(page, '02-landed-on-wecom-row')


def test_a_task_without_wecom_says_nothing_about_it(server, api):
    base = server.base_url
    before = len(standin.calls(server.home))
    task = _run(
        api,
        'Plain arithmetic',
        'What is 17 times 23? Reply with the number only.',
    )
    said = _said(api, base, task)
    assert task['status'] == 'completed', task.get('error')
    assert '391' in said
    for word in (
        '#integrations',
        'Re-authorize',
        '重新授权',
        'WeCom',
        '企业微信',
    ):
        assert word not in said, word
    assert standin.calls(server.home)[before:] == []
