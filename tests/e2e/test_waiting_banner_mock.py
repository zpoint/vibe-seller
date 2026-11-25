"""What waits for the user: ONE bar at the top, answers first; deadlines.

On a real server with the mock CLI as the agent and a real browser.

Walk: every kind of task that waits on the user is waiting at once — a
no-store task's question, a store task's question, a sub-task's question
(its parent waiting on it), a plan-mode task's plan — and a review
schedule's plan. The bar shows one kind at a time: replies first (oldest
first, whatever the task), then the task's plan, then the schedule's.
Each click opens the next item; resolving it (in the UI) moves the bar
on, until it is gone. Every task's status badge reads right on the way.

Deadline: a parked question's wait passes its 7 days; the checker (run
every 2 s here) stops the task with a message that says how to go on,
and it leaves the bar.

Everything a reviewer needs is written by ``Evidence``.
"""

from datetime import UTC, datetime, timedelta
import json
import os
import re
import sqlite3
import time

import httpx
from playwright.sync_api import Page, expect
import pytest

from tests.e2e.e2e_helpers import create_store, login_client, run_tag
from tests.e2e.evidence import Evidence
from tests.e2e.own_server import owned_agent_stack
from tests.e2e.question_park_flow import create_task_in_ui, pending, poll

MOCK_CLI = os.environ.get('MOCK_CLI', '')
if not MOCK_CLI:
    pytest.skip(
        'MOCK_CLI not set — the waiting-banner e2e needs the mock CLI',
        allow_module_level=True,
    )

pytestmark = [pytest.mark.e2e]

TIMEOUT_S = 5
BARS = (
    'waiting-task-banner',
    'planner-question-banner',
    'task-plan-banner',
    'plan-approval-banner',
)


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    with owned_agent_stack(
        tmp_path_factory,
        'waiting-banner-mock',
        env={
            'MOCK_CLI': MOCK_CLI,
            'MAX_AGENT_CONCURRENCY': '4',
            'VIBE_QUESTION_ANSWER_TIMEOUT_S': str(TIMEOUT_S),
            'VIBE_WAITING_CHECK_INTERVAL_S': '2',
        },
        auto_answer=False,
    ) as owned:
        yield owned


@pytest.fixture
def api(server):
    client = httpx.Client(timeout=30)
    login_client(client, server.base_url)
    yield client
    client.close()


def _attention(api, base) -> list[dict]:
    return api.get(f'{base}/api/tasks/attention').json()['waiting_tasks']


def _only_bar(page: Page, test_id: str, text: str):
    """Exactly one bar is up — *test_id* — and it says *text*."""
    bar = page.get_by_test_id(test_id)
    expect(bar).to_contain_text(text, timeout=15000)
    for other in BARS:
        if other != test_id:
            expect(page.get_by_test_id(other)).to_have_count(0)
    return bar


def _no_bar(page: Page):
    for bar in BARS:
        expect(page.get_by_test_id(bar)).to_have_count(0, timeout=15000)


def _create_no_store_in_ui(page: Page, base: str, prompt: str) -> dict:
    page.goto(f'{base}/tasks')
    page.locator('button', has_text='All Stores').first.click()
    page.get_by_role('button', name='+ New Task').click()
    page.get_by_placeholder('e.g. Process billing files').fill(prompt)
    with page.expect_response(
        lambda r: r.url.endswith('/api/tasks') and r.request.method == 'POST'
    ) as created:
        page.get_by_role('button', name='Create', exact=True).click()
    assert created.value.ok, created.value.text()
    return created.value.json()


def _open_store(page: Page, base: str, name: str) -> None:
    page.goto(f'{base}/tasks')
    page.get_by_role('button', name=re.compile(re.escape(name))).first.click()
    expect(page.locator('h2', has_text=name)).to_be_visible(timeout=10000)


def _row(page: Page, title: str):
    return page.locator('button', has_text=title).first


def _answer_in_ui(page: Page, label: str = 'Express') -> None:
    submit = page.get_by_role('button', name='Submit Answers')
    expect(submit).to_be_visible(timeout=15000)
    page.get_by_role('button', name=label, exact=True).click()
    submit.click()
    expect(submit).to_have_count(0, timeout=20000)


def _schedule_status(api, base, schedule_id, target, timeout=60) -> dict:
    deadline = time.time() + timeout
    s: dict = {}
    while time.time() < deadline:
        s = api.get(f'{base}/api/schedules/{schedule_id}').json()
        if s['plan_status'] == target:
            return s
        time.sleep(0.5)
    raise AssertionError(f'schedule stuck at {s.get("plan_status")!r}')


def _move_wait_start(home, task_id: str, hours: float) -> str:
    """Make the task's wait have started *hours* ago (its own row)."""
    db = sqlite3.connect(home / 'data' / 'vibe_seller.db')
    try:
        (raw,) = db.execute(
            'select wait_condition from tasks where id = ?', (task_id,)
        ).fetchone()
        cond = json.loads(raw)
        cond['waiting_since'] = (
            datetime.now(UTC) - timedelta(hours=hours)
        ).isoformat()
        db.execute(
            'update tasks set wait_condition = ? where id = ?',
            (json.dumps(cond), task_id),
        )
        db.commit()
    finally:
        db.close()
    return cond['waiting_since']


def test_one_bar_walks_every_kind_until_nothing_waits(
    server, api, authenticated_page
):
    page = authenticated_page
    base = server.base_url
    ev = Evidence('e2e-mock-attention-walk', home=server.home)
    ev.start_sse(api, base)
    tag = run_tag()
    store = create_store(api, f'walk-{tag}')

    # A review schedule whose plan comes back for approval.
    r = api.post(
        f'{base}/api/schedules',
        json={
            'title': f'Weekly review {tag}',
            'description': 'Open the dashboard and report anything odd.',
            'store_id': store['id'],
            'schedule_type': 'weekly',
            'schedule_time': '09:00',
            'schedule_day': 1,
            'plan_mode': True,
        },
    )
    assert r.status_code == 201, r.text
    schedule_id = r.json()['id']
    _schedule_status(api, base, schedule_id, 'pending_approval')

    # Replies: no-store, then store, then a sub-task (created in order,
    # so they park in order). Plan: a plan-mode store task.
    a = _create_no_store_in_ui(page, base, f'[[MOCK_ASK_PARK]] A {tag}')['id']
    poll(api, base, a, ev, 'A', lambda t: t['status'] == 'waiting', 60, 0.5)
    _open_store(page, base, store['name'])
    b = create_task_in_ui(page, f'[[MOCK_ASK_PARK]] B {tag}')['id']
    poll(api, base, b, ev, 'B', lambda t: t['status'] == 'waiting', 60, 0.5)
    o = _create_no_store_in_ui(
        page,
        base,
        f'[[MOCK_SPAWN_SUBTASKS]] [[MOCK_CHILD_ASKS]] O {tag} '
        f'stores: {store["id"]}',
    )['id']
    poll(api, base, o, ev, 'O', lambda t: t['status'] == 'waiting', 60, 0.5)
    [child] = api.get(f'{base}/api/tasks?parent_task_id={o}').json()
    c = child['id']
    poll(api, base, c, ev, 'C', lambda t: t['status'] == 'waiting', 60, 0.5)
    r = api.post(
        f'{base}/api/tasks',
        json={
            'title': f'Plan first {tag}',
            'store_id': store['id'],
            'plan_mode': True,
        },
    )
    assert r.status_code == 200, r.text
    p = r.json()['id']
    poll(api, base, p, ev, 'P', lambda t: t['status'] == 'planned', 60, 0.5)
    for task_id, label in ((a, 'A'), (b, 'B'), (o, 'O'), (c, 'C'), (p, 'P')):
        ev.task_row(task_id, f'{label}-00-waiting')
    kinds = [(i['task_id'], i['kind']) for i in _attention(api, base)]
    ev.write_json('attention-start.json', kinds)
    assert kinds == [
        (a, 'question'),
        (b, 'question'),
        (c, 'question'),
        (p, 'plan'),
    ]

    # Status badges: the All Stores list and the store's list.
    page.goto(f'{base}/tasks')
    page.locator('button', has_text='All Stores').first.click()
    expect(_row(page, current_title(api, base, a))).to_contain_text('Waiting')
    expect(_row(page, current_title(api, base, o))).to_contain_text('Subtasks')
    ev.shot(page, '01-all-stores-list-one-bar-replies-first')
    _only_bar(page, 'waiting-task-banner', '3 tasks are waiting for your reply')
    _open_store(page, base, store['name'])
    expect(_row(page, current_title(api, base, b))).to_contain_text('Waiting')
    # A plan waiting for review reads like a schedule plan waiting for it.
    expect(_row(page, current_title(api, base, p))).to_contain_text(
        'Needs your approval'
    )
    ev.shot(page, '02-store-list-badges')

    # 1-3. Replies, oldest first: A (no store), B (store), C (sub-task).
    sid = store['id']
    replies = (
        (a, f'{base}/tasks/{a}', 2),
        # Store tasks open inside their store: the list beside them matches.
        (b, f'{base}/stores/{sid}/tasks/{b}', 1),
        (c, f'{base}/stores/{sid}/tasks/{c}', 0),
    )
    for n, (task_id, url, left) in enumerate(replies, start=3):
        bar = _only_bar(page, 'waiting-task-banner', 'waiting for your reply')
        bar.click()
        expect(page).to_have_url(url, timeout=10000)
        expect(page.get_by_role('button', name='Submit Answers')).to_be_visible(
            timeout=15000
        )
        ev.shot(page, f'{n:02d}-bar-opened-{task_id[:8]}')
        _answer_in_ui(page)
        poll(
            api,
            base,
            task_id,
            ev,
            task_id[:8],
            lambda t: t['status'] == 'completed',
            60,
            0.5,
        )
        if left:
            _only_bar(
                page,
                'waiting-task-banner',
                f'{left} task{"s are" if left > 1 else " is"} waiting',
            )
    # The sub-task's answer completed its parent too.
    poll(api, base, o, ev, 'O', lambda t: t['status'] == 'completed', 30, 0.5)

    # 4. Then the task's plan to review.
    bar = _only_bar(
        page, 'task-plan-banner', '1 task has a plan waiting for your review'
    )
    ev.shot(page, '06-replies-done-task-plan-next')
    bar.click()
    expect(page).to_have_url(f'{base}/stores/{sid}/tasks/{p}', timeout=10000)
    page.get_by_role('button', name='Confirm & Execute').click()
    poll(api, base, p, ev, 'P', lambda t: t['status'] == 'completed', 60, 0.5)

    # 5. Then the schedule's plan to approve.
    bar = _only_bar(page, 'plan-approval-banner', 'waiting for your approval')
    ev.shot(page, '07-task-plan-done-schedule-plan-next')
    bar.click()
    page.get_by_test_id('plan-approve').click()
    expect(page.get_by_test_id('schedule-plan-status')).to_have_text('Ready')
    _no_bar(page)
    ev.shot(page, '08-everything-handled-no-bar')
    assert _attention(api, base) == []
    for task_id, label in ((a, 'A'), (b, 'B'), (o, 'O'), (c, 'C'), (p, 'P')):
        ev.task_row(task_id, f'{label}-99-done')
    page.goto(f'{base}/tasks')
    page.locator('button', has_text='All Stores').first.click()
    expect(_row(page, current_title(api, base, o))).to_contain_text('Completed')
    ev.shot(page, '09-all-stores-list-all-completed')


def current_title(api, base, task_id) -> str:
    return api.get(f'{base}/api/tasks/{task_id}').json()['title']


def test_parked_question_stops_at_its_deadline(server, api, authenticated_page):
    page = authenticated_page
    base = server.base_url
    ev = Evidence('e2e-mock-wait-deadline', home=server.home)
    ev.start_sse(api, base)
    tag = run_tag()
    store = create_store(api, f'deadline-{tag}')
    _open_store(page, base, store['name'])
    d = create_task_in_ui(page, f'[[MOCK_ASK_PARK]] D {tag}')['id']
    poll(api, base, d, ev, 'D', lambda t: t['status'] == 'waiting', 60, 0.5)
    assert d in {i['task_id'] for i in _attention(api, base)}
    page.goto(f'{base}/tasks/{d}')
    expect(page.get_by_test_id('waiting-deadline')).to_contain_text(
        '7d remaining', timeout=15000
    )
    expect(page.get_by_test_id('waiting-task-banner')).to_be_visible()
    ev.shot(page, '01-parked-7-days-left')

    since = _move_wait_start(server.home, d, 7 * 24 + 1 / 60)
    ev.mark('wait_moved', waiting_since=since)
    stopped, _ = poll(
        api, base, d, ev, 'D', lambda t: t['status'] != 'waiting', 30, 0.5
    )
    ev.task_row(d, 'D-02-stopped')
    assert stopped['status'] == 'failed', stopped
    assert stopped['error_category'] == 'wait_timeout', stopped
    assert pending(api, base, d)['pending'] is False
    assert d not in {i['task_id'] for i in _attention(api, base)}
    expect(
        page.get_by_text('No reply came in time, so the task stopped')
    ).to_be_visible(timeout=15000)
    # The page was open through the stop: the dead answer card is gone
    # (answering a stopped task is a 409), the question stays readable.
    expect(page.get_by_role('button', name='Submit Answers')).to_have_count(
        0, timeout=15000
    )
    # The question nobody answered stays readable, for the reply.
    expect(page.get_by_test_id('unanswered-question')).to_contain_text(
        'Which shipping option?'
    )
    ev.shot(page, '02-stopped-at-the-deadline')
    log = server.log.read_text(errors='replace')
    assert f'Timed out waiting task {d}' in log
