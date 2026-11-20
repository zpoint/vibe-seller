"""The UI journeys behind the question-park e2e (mock and live).

B: a task asks a question nobody answers. It must park in WAITING after
``VIBE_QUESTION_ANSWER_TIMEOUT_S`` and free its agent slot (the server
runs with ONE slot, so a task created behind it can only run once it
parks); the question stays on screen, and answering it in the UI
resumes the task to COMPLETED.

C: a scheduled run whose prompt makes it ask never waits: the question
is declined at once and the run completes with its own choice.

Everything a reviewer needs is written by ``Evidence``.
"""

from datetime import datetime
import re
import time

import httpx
from playwright.sync_api import Page, expect

from tests.e2e.evidence import Evidence


def _ts(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def poll(api, base_url, task_id, ev, label, until, timeout, every=1.0):
    """Poll the task until ``until(task)``; every distinct status lands
    in the timeline. Returns ``(task, statuses_seen)``."""
    deadline = time.time() + timeout
    seen: list[str] = []
    task: dict = {}
    while time.time() < deadline:
        task = api.get(f'{base_url}/api/tasks/{task_id}').json()
        status = task.get('status')
        if not seen or seen[-1] != status:
            seen.append(status)
            ev.mark('status', task=label, status=status)
        if until(task):
            return task, seen
        time.sleep(every)
    raise AssertionError(
        f'{label} ({task_id[:8]}) never reached the expected state in '
        f'{timeout}s; statuses seen: {seen}; last: {task.get("status")}'
    )


def pending(api, base_url, task_id) -> dict:
    return api.get(f'{base_url}/api/tasks/{task_id}/questions/pending').json()


def open_store(page: Page, store_name: str) -> None:
    page.reload()
    page.wait_for_selector('h1', timeout=15000)
    page.get_by_role(
        'button', name=re.compile(re.escape(store_name))
    ).first.click()
    expect(page.locator('h2', has_text=store_name)).to_be_visible(timeout=10000)


def create_task_in_ui(page: Page, prompt: str) -> dict:
    """Type *prompt* into the New Task dialog's one box and submit.

    The dialog has no title field: the server derives the title (and
    may later replace it with an AI-written one), so tasks are tracked
    by the id the create call returns, never by a title.
    """
    page.get_by_role('button', name='+ New Task').click()
    page.get_by_placeholder('e.g. Navigate to google.com').fill(prompt)
    with page.expect_response(
        lambda r: r.url.endswith('/api/tasks') and r.request.method == 'POST'
    ) as created:
        page.get_by_role('button', name='Create & Run').click()
    assert created.value.ok, created.value.text()
    return created.value.json()


def current_title(api, base_url, task_id) -> str:
    return api.get(f'{base_url}/api/tasks/{task_id}').json()['title']


def select_task(page: Page, api, base_url, task_id: str) -> None:
    """Open the task by clicking its row — found by its title right now."""
    title = current_title(api, base_url, task_id)
    page.locator('button', has_text=title).first.click()
    expect(page.locator('h2', has_text=title)).to_be_visible(timeout=10000)


def banner(page: Page):
    return page.get_by_role('button', name='Submit Answers')


def run_park_flow(
    *,
    page: Page,
    api: httpx.Client,
    base_url: str,
    home,
    log_path,
    ev: Evidence,
    store_name: str,
    store_id: str,
    a_prompt: str,
    b_prompt: str,
    timeout_s: float,
    b_budget_s: float,
    a_budget_s: float,
    check_a_result,
    check_b_result,
    answer_label: str = 'Express',
) -> dict:
    ids: dict[str, str] = {}
    ev.start_sse(api, base_url)
    try:
        # 1. Task A asks the question; it shows in the UI.
        open_store(page, store_name)
        a = ids['A'] = create_task_in_ui(page, a_prompt)['id']
        ev.mark('created', task='A', id=a)
        _, _ = poll(
            api,
            base_url,
            a,
            ev,
            'A',
            lambda t: pending(api, base_url, a).get('pending'),
            timeout=a_budget_s,
        )
        asked_at = time.time()
        # A real model words the question itself: read it off the server.
        asked = pending(api, base_url, a)
        question_text = asked['questions'][0]['question']
        ev.mark('question_pending', task='A', question=question_text)
        ev.write_json('question-asked.json', asked)
        expect(banner(page)).to_be_visible(timeout=20000)
        expect(page.get_by_text(question_text).first).to_be_visible()
        ev.shot(page, '01-a-question-shown')
        row_a = ev.task_row(a, 'A-01-asking')
        assert row_a['status'] == 'running', row_a

        # 2. Task B is created while A holds the only slot: it queues.
        b = ids['B'] = create_task_in_ui(page, b_prompt)['id']
        ev.mark('created', task='B', id=b)
        time.sleep(min(10.0, timeout_s / 3))
        b_now = api.get(f'{base_url}/api/tasks/{b}').json()
        a_now = api.get(f'{base_url}/api/tasks/{a}').json()
        ev.mark('step2', a=a_now['status'], b=b_now['status'])
        select_task(page, api, base_url, b)
        ev.shot(page, '02-b-queued-a-waiting-for-answer')
        ev.task_row(b, 'B-02-queued')
        assert a_now['status'] == 'running', a_now['status']
        assert b_now['status'] in ('pending', 'queued'), b_now['status']

        # 3. A parks in WAITING after the timeout, question still shown.
        a_parked, a_seen = poll(
            api,
            base_url,
            a,
            ev,
            'A',
            lambda t: t.get('status') == 'waiting',
            timeout=timeout_s + 60,
            every=0.5,
        )
        parked_after = time.time() - asked_at
        ev.mark('a_parked', after_s=round(parked_after, 1), seen=a_seen)
        assert parked_after >= timeout_s - 2, parked_after
        select_task(page, api, base_url, a)
        expect(banner(page)).to_be_visible(timeout=20000)
        ev.shot(page, '03-a-parked-question-still-shown')
        row_a = ev.task_row(a, 'A-03-parked')
        assert 'pending_question' in (row_a['wait_condition'] or '')

        # 4. B leaves the queue only now, and completes.
        b_done, b_seen = poll(
            api,
            base_url,
            b,
            ev,
            'B',
            lambda t: t.get('status') in ('completed', 'failed'),
            timeout=b_budget_s,
        )
        assert b_done['status'] == 'completed', b_done
        check_b_result(b_done['result'] or '')
        select_task(page, api, base_url, b)
        ev.shot(page, '04-b-completed')
        ev.task_row(b, 'B-04-completed')
        b_running = [
            e
            for e in ev.sse_events
            if e.get('type') == 'task_update'
            and e.get('task_id') == b
            and e.get('status') == 'running'
        ]
        a_asked = [
            e
            for e in ev.sse_events
            if e.get('type') == 'task_questions' and e.get('task_id') == a
        ]
        a_agent_ended = [
            e
            for e in ev.sse_events
            if e.get('type') == 'agent_done' and e.get('task_id') == a
        ]
        assert b_running and a_asked and a_agent_ended, (
            b_running,
            a_asked,
            a_agent_ended,
        )
        # The slot is freed when A's agent exits (its WAITING row is
        # written right after, by the orchestrator): B starts only once
        # A's agent is gone, and never while A waited for its answer.
        b_start = b_running[0]['_received_at']
        assert b_start >= a_agent_ended[0]['_received_at'], b_start
        waited = (
            _ts(b_start) - _ts(a_asked[0]['_received_at'])
        ).total_seconds()
        ev.mark('b_started_after_question_s', seconds=round(waited, 2))
        assert waited >= timeout_s - 2, waited

        # 5. Reload: the parked question is served from the row.
        open_store(page, store_name)
        select_task(page, api, base_url, a)
        expect(banner(page)).to_be_visible(timeout=20000)
        ev.shot(page, '05-a-reloaded-question-shown')
        assert pending(api, base_url, a)['source'] == 'parked'

        # 6. Answer in the UI.
        # The option chip inside the question card — the transcript can
        # mention the same word (a real agent describes the page).
        card = page.locator(
            'div', has=banner(page), has_text=question_text
        ).last
        card.get_by_role('button', name=answer_label, exact=True).click()
        expect(banner(page)).to_be_enabled(timeout=5000)
        banner(page).click()
        expect(banner(page)).to_have_count(0, timeout=20000)
        ev.mark('answered', task='A', label=answer_label)
        time.sleep(1)
        ev.shot(page, '06-a-resumed-running')

        # 7. A resumes and completes with the chosen option.
        a_done, _ = poll(
            api,
            base_url,
            a,
            ev,
            'A',
            lambda t: t.get('status') in ('completed', 'failed'),
            timeout=a_budget_s,
        )
        assert a_done['status'] == 'completed', a_done
        check_a_result(a_done['result'] or '')
        select_task(page, api, base_url, a)
        ev.shot(page, '07-a-completed-result')
        ev.task_row(a, 'A-07-completed')
        ev.messages(api, base_url, a, 'A')
        ev.messages(api, base_url, b, 'B')
        return {'a': a_done, 'b': b_done, 'a_parked': a_parked, 'ids': ids}
    finally:
        ev.stop_sse()
        ev.server_log(log_path, ids)


def create_ready_schedule(api, base_url, store_id, title, prompt, ev, timeout):
    """A store schedule with its plan authored and approved — the real
    path: create → plan-only run → plan ready. Questions are answered
    ONLY for that plan-authoring run (someone is reviewing it); the
    scheduled fire itself is never answered."""
    r = api.post(
        f'{base_url}/api/schedules',
        json={
            'title': title,
            'description': prompt,
            'store_id': store_id,
            'schedule_type': 'days',
            'schedule_time': '03:00',
            'timezone': 'UTC',
            'plan_mode': True,
        },
    )
    assert r.status_code in (200, 201), r.text
    sched = r.json()
    ev.write_json('schedule-created.json', sched)
    planning_ids: set[str] = set()
    deadline = time.time() + timeout
    s: dict = {}
    while time.time() < deadline:
        s = api.get(f'{base_url}/api/schedules/{sched["id"]}').json()
        if s.get('plan_status') == 'ready':
            ev.write_json('schedule-ready.json', s)
            return s, planning_ids
        planning = s.get('current_planning_task_id')
        if planning:
            planning_ids.add(planning)
            q = pending(api, base_url, planning)
            if q.get('pending'):
                answers = {
                    item['question']: (
                        item.get('options') or [{'label': 'Proceed'}]
                    )[0]['label']
                    for item in q['questions']
                }
                ev.mark('answered_plan_question', task=planning[:8])
                api.post(
                    f'{base_url}/api/tasks/{planning}/questions/answer',
                    json={'request_id': q['request_id'], 'answers': answers},
                )
        time.sleep(1)
    raise AssertionError(f'schedule plan never became ready: {s}')


def run_decline_flow(
    *,
    page: Page,
    api: httpx.Client,
    base_url: str,
    log_path,
    ev: Evidence,
    store_name: str,
    store_id: str,
    title: str,
    prompt: str,
    plan_budget_s: float,
    run_budget_s: float,
    check_result,
) -> dict:
    """C: a scheduled run whose prompt makes it ask never waits."""
    ids: dict[str, str] = {}
    ev.start_sse(api, base_url)
    try:
        sched, planning_ids = create_ready_schedule(
            api, base_url, store_id, title, prompt, ev, plan_budget_s
        )
        for n, pid in enumerate(sorted(planning_ids)):
            ids[f'plan{n}'] = pid
        open_store(page, store_name)
        page.get_by_role('button', name='Scheduled').first.click()
        page.locator('button', has_text=title).first.click()
        run_now = page.get_by_role('button', name='Run Now')
        expect(run_now).to_be_enabled(timeout=15000)
        ev.shot(page, '01-schedule-plan-ready')
        run_now.click()
        ev.mark('run_now_clicked')

        deadline = time.time() + 30
        child = None
        while time.time() < deadline and not child:
            rows = api.get(
                f'{base_url}/api/schedules/{sched["id"]}/tasks'
            ).json()
            rows = rows if isinstance(rows, list) else rows.get('items', [])
            child = rows[0]['id'] if rows else None
            time.sleep(0.5)
        assert child, 'Run Now created no task'
        ids['child'] = child
        ev.mark('child_created', id=child)

        seen: list[str] = []
        shot_running = False
        deadline = time.time() + run_budget_s
        task: dict = {}
        while time.time() < deadline:
            task = api.get(f'{base_url}/api/tasks/{child}').json()
            if not seen or seen[-1] != task['status']:
                seen.append(task['status'])
                ev.mark('status', task='child', status=task['status'])
            assert not pending(api, base_url, child).get('pending'), (
                'a scheduled run is waiting on a question'
            )
            if task['status'] == 'running' and not shot_running:
                page.locator('button', has_text=title).first.click()
                expect(banner(page)).to_have_count(0)
                ev.shot(page, '02-child-running-no-question')
                if ev.home:
                    ev.task_row(child, 'child-02-running')
                shot_running = True
            if task['status'] in ('completed', 'failed'):
                break
            time.sleep(1)
        assert task.get('status') == 'completed', (task.get('status'), seen)
        assert 'waiting' not in seen, seen
        check_result(task.get('result') or '')
        asked = [
            e
            for e in ev.sse_events
            if e.get('type') == 'task_questions' and e.get('task_id') == child
        ]
        assert asked == [], asked
        page.reload()
        page.wait_for_selector('h1', timeout=15000)
        open_store(page, store_name)
        page.get_by_role('button', name='Scheduled').first.click()
        page.locator('button', has_text=title).first.click()
        time.sleep(1)
        expect(banner(page)).to_have_count(0)
        ev.shot(page, '03-schedule-run-completed')
        # Open the run itself: its result, and no question card.
        page.locator('h3, div', has_text=re.compile(r'^Runs')).first.wait_for()
        page.get_by_text('Completed', exact=True).last.click()
        time.sleep(1)
        expect(banner(page)).to_have_count(0)
        ev.shot(page, '04-child-result')
        if ev.home:
            ev.task_row(child, 'child-03-completed')
        ev.messages(api, base_url, child, 'child')
        return {'child': task, 'seen': seen, 'ids': ids}
    finally:
        ev.stop_sse()
        ev.server_log(log_path, ids)
