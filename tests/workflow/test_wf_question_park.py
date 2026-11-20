"""An unanswered AskUserQuestion parks the task and frees its slot.

Observed live: a scheduled task asked a question nobody was there to
answer and waited for it — holding one of the two agent slots for more
than a day, while every scheduled task behind it sat queued.

Driven through the real routers, orchestrators and slot limiter with
the FakeAgent, whose question uses the product's own bounded wait
(``app.ai.question_wait``): past ``VIBE_QUESTION_ANSWER_TIMEOUT_S`` the
agent exits like a stopped CLI and the orchestrator parks the task in
WAITING; answering resumes it to COMPLETED.
"""

import json
import logging

import pytest

from app.models.task import Task
from app.scheduler.stall_reaper import reap_stalled_running_tasks
from app.scheduler.task_queue import TaskQueueScheduler
from tests.workflow.conftest import real_sleep, wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario
from tests.workflow.test_wf_agent_mcp import call_tool

pytestmark = pytest.mark.workflow

evidence = logging.getLogger('evidence')

QUESTION = {
    'request_id': 'rid-ship',
    'questions': [
        {
            'question': 'Which shipping option?',
            'header': 'Shipping',
            'options': [
                {'label': 'Standard', 'description': '5 days'},
                {'label': 'Express', 'description': '2 days'},
            ],
            'multiSelect': False,
        }
    ],
}
ANSWER = {'Which shipping option?': 'Express'}


@pytest.fixture
async def with_queue(monkeypatch, override_async_session, mock_browser_wf):
    """A real TaskQueueScheduler behind every launch path, as in
    production (``test_wf_resume_retry.with_queue``)."""
    scheduler = TaskQueueScheduler()
    for mod in (
        'app.routers.tasks',
        'app.routers.tasks_conversation',
        'app.routers.schedules',
        'app.task_launch',
    ):
        monkeypatch.setattr(f'{mod}.task_queue_scheduler', scheduler)
    await scheduler.start()
    yield scheduler
    await scheduler.stop()


@pytest.fixture(autouse=True)
def short_question_timeout(monkeypatch):
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '0.3')


def _asking(**kw) -> FakeAgentScenario:
    return FakeAgentScenario(
        ask_question=QUESTION, result_from_answer=True, **kw
    )


async def _store(client, name='Question Store'):
    r = await client.post('/api/stores', json={'name': name})
    assert r.status_code in (200, 201), r.text
    return r.json()['id']


async def _create(client, fake, store_id, title, scenario, **body):
    fake.default_scenario = scenario
    r = await client.post(
        '/api/tasks', json={'title': title, 'store_id': store_id, **body}
    )
    assert r.status_code in (200, 201), r.text
    task_id = r.json()['id']
    # Pin it, so a resumed run of this task keeps its scenario.
    fake.scenarios[task_id] = scenario
    return task_id


async def _task(client, task_id) -> dict:
    data = (await client.get(f'/api/tasks/{task_id}')).json()
    evidence.info(
        'task %s %s',
        task_id[:8],
        json.dumps(
            {
                k: data.get(k)
                for k in (
                    'status',
                    'result',
                    'error',
                    'error_category',
                    'wait_condition',
                    'session_id',
                )
            },
            ensure_ascii=False,
        ),
    )
    return data


async def _pending(client, task_id) -> dict:
    r = await client.get(f'/api/tasks/{task_id}/questions/pending')
    assert r.status_code == 200, r.text
    evidence.info('pending %s %s', task_id[:8], r.json())
    return r.json()


async def _answer(client, task_id, request_id='rid-ship', answers=ANSWER):
    r = await client.post(
        f'/api/tasks/{task_id}/questions/answer',
        json={'request_id': request_id, 'answers': answers},
    )
    evidence.info('answer %s -> %s %s', task_id[:8], r.status_code, r.text)
    return r


async def _until(predicate, timeout=5.0):
    for _ in range(int(timeout / 0.02)):
        if await predicate():
            return
        await real_sleep(0.02)
    raise AssertionError('condition never became true')


async def _wait_question(client, task_id):
    async def asked():
        return (await _pending(client, task_id)).get('pending')

    await _until(asked)


async def _park(client, fake, title='Ship ORD-1001'):
    store_id = await _store(client)
    task_id = await _create(client, fake, store_id, title, _asking())
    parked = await _wait_status(client, task_id, 'waiting')
    return store_id, task_id, parked


async def _wait_status(client, task_id, status, timeout=10.0):
    """``wait_for_task`` also returns on FAILED: insist on *status*."""
    data = await wait_for_task(client, task_id, target=status, timeout=timeout)
    assert data['status'] == status, (
        f'{task_id[:8]} ended {data["status"]!r} (error={data.get("error")!r})'
        f', expected {status!r}'
    )
    return data


async def test_unanswered_question_parks_task_in_waiting(
    admin_client, install_fake_agent
):
    fake = install_fake_agent
    _, task_id, parked = await _park(admin_client, fake)
    await _task(admin_client, task_id)
    cond = json.loads(parked['wait_condition'])
    assert cond['pending_question']['request_id'] == 'rid-ship'
    assert cond['pending_question']['questions'] == QUESTION['questions']
    assert cond['parked_reason'] == 'answer_timeout'
    assert 'paused after waiting' in cond['reason']
    assert parked['error'] is None
    pending = await _pending(admin_client, task_id)
    assert pending['pending'] is True
    assert pending['request_id'] == 'rid-ship'
    assert pending['source'] == 'parked'
    assert not fake.is_running(task_id)
    assert fake._slots.in_flight == 0


async def test_parked_question_frees_the_slot_for_a_queued_task(
    admin_client, install_fake_agent, monkeypatch
):
    fake = install_fake_agent
    # Long enough to see B queued behind A's live question.
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '1.0')
    r = await admin_client.put(
        '/api/settings', json={'max_agent_concurrency': '1'}
    )
    assert r.status_code == 200, r.text
    store_id = await _store(admin_client)
    a = await _create(admin_client, fake, store_id, 'A asks', _asking())
    await _wait_question(admin_client, a)
    b = await _create(
        admin_client, fake, store_id, 'B works', FakeAgentScenario(result='B')
    )
    await real_sleep(0.3)
    assert (await _task(admin_client, a))['status'] == 'running'
    assert (await _task(admin_client, b))['status'] in ('pending', 'queued')
    assert not fake.is_running(b)

    await _wait_status(admin_client, a, 'waiting')
    done_b = await _wait_status(admin_client, b, 'completed')
    assert done_b['status'] == 'completed'
    assert done_b['result'] == 'B'
    assert (await _task(admin_client, a))['status'] == 'waiting'
    assert fake._slots.in_flight == 0


async def test_answer_resumes_parked_task_to_completed(
    admin_client, install_fake_agent
):
    fake = install_fake_agent
    _, task_id, _ = await _park(admin_client, fake)
    r = await _answer(admin_client, task_id)
    assert r.status_code == 200
    assert r.json() == {'ok': True, 'resumed': True}
    done = await _wait_status(admin_client, task_id, 'completed')
    await _task(admin_client, task_id)
    assert done['result'] == 'Chose: Express'
    assert done['wait_condition'] is None
    runs = fake.get_calls(task_id, 'run')
    assert len(runs) == 2
    assert '→ Express' in runs[1].prompt
    assert 'Do not start the task over' in runs[1].prompt
    assert 'Original task, already in progress' in runs[1].prompt
    assert (await _pending(admin_client, task_id))['pending'] is False
    msgs = (await admin_client.get(f'/api/tasks/{task_id}/messages')).json()
    users = [m['content'] for m in msgs if m['role'] == 'user']
    # The opening prompt once, then the answer as the user's turn.
    assert users[-1] == 'Which shipping option? → Express'
    assert not any('earlier AskUserQuestion' in u for u in users)


async def test_answer_before_timeout_keeps_in_process_path(
    admin_client, install_fake_agent, monkeypatch
):
    fake = install_fake_agent
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '5')
    store_id = await _store(admin_client)
    task_id = await _create(admin_client, fake, store_id, 'Ask', _asking())
    await _wait_question(admin_client, task_id)
    r = await _answer(admin_client, task_id)
    assert r.json() == {'ok': True}
    done = await _wait_status(admin_client, task_id, 'completed')
    assert done['result'] == 'Chose: Express'
    assert len(fake.get_calls(task_id, 'run')) == 1


async def test_answer_during_parking_window_resumes(
    admin_client, install_fake_agent
):
    fake = install_fake_agent
    store_id = await _store(admin_client)
    task_id = await _create(
        admin_client,
        fake,
        store_id,
        'Ask',
        _asking(park_exit_delay=0.5),
    )

    async def parking():
        s = fake.get_session(task_id)
        return s is not None and s._question_park is not None

    await _until(parking)
    assert (await _pending(admin_client, task_id))['pending'] is True
    r = await _answer(admin_client, task_id)
    assert r.status_code == 200, r.text
    assert r.json() == {'ok': True, 'resumed': True}
    done = await _wait_status(admin_client, task_id, 'completed')
    assert done['result'] == 'Chose: Express'


async def test_stop_during_question_wait_fails_not_parks(
    admin_client, install_fake_agent, monkeypatch
):
    fake = install_fake_agent
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '5')
    store_id = await _store(admin_client)
    task_id = await _create(admin_client, fake, store_id, 'Ask', _asking())
    await _wait_question(admin_client, task_id)
    await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
    failed = await _wait_status(admin_client, task_id, 'failed')
    await _task(admin_client, task_id)
    assert failed['error_category'] == 'stopped_by_user'
    assert (await _pending(admin_client, task_id))['pending'] is False


async def test_stop_of_parked_task_hides_question(
    admin_client, install_fake_agent
):
    fake = install_fake_agent
    _, task_id, _ = await _park(admin_client, fake)
    await admin_client.post(f'/api/tasks/{task_id}/agent/stop')
    await _wait_status(admin_client, task_id, 'failed')
    assert (await _pending(admin_client, task_id))['pending'] is False
    assert (await _answer(admin_client, task_id)).status_code == 409


async def test_parked_task_survives_restart(
    admin_client, install_fake_agent, override_async_session
):
    fake = install_fake_agent
    _, task_id, _ = await _park(admin_client, fake)
    # The process is gone after a restart; the row is all that is left.
    fake._sessions.pop(task_id, None)
    await TaskQueueScheduler()._recover_from_db()
    async with override_async_session() as db:
        assert (await db.get(Task, task_id)).status == 'waiting'
    pending = await _pending(admin_client, task_id)
    assert pending['pending'] is True
    assert pending['source'] == 'parked'
    assert (await _answer(admin_client, task_id)).json()['resumed'] is True
    done = await _wait_status(admin_client, task_id, 'completed')
    assert done['result'] == 'Chose: Express'


async def test_planned_task_parks_and_resumes_with_answers(
    admin_client, install_fake_agent
):
    fake = install_fake_agent
    store_id = await _store(admin_client)
    task_id = await _create(
        admin_client,
        fake,
        store_id,
        'Plan then ask',
        _asking(),
        plan_mode=True,
    )
    await _wait_status(admin_client, task_id, 'planned')
    r = await admin_client.post(f'/api/tasks/{task_id}/execute-plan')
    assert r.status_code == 200, r.text
    await _wait_status(admin_client, task_id, 'waiting')
    await _task(admin_client, task_id)
    assert (await _answer(admin_client, task_id)).json()['resumed'] is True
    done = await _wait_status(admin_client, task_id, 'completed')
    await _task(admin_client, task_id)
    assert done['result'] == 'Chose: Express'
    assert done['wait_condition'] is None


async def test_second_question_after_resume_parks_again_without_restart(
    admin_client, install_fake_agent
):
    fake = install_fake_agent
    store_id = await _store(admin_client)
    task_id = await _create(
        admin_client,
        fake,
        store_id,
        'Ask twice',
        _asking(ask_again_after_answer=True),
    )
    await _wait_status(admin_client, task_id, 'waiting')
    await _answer(admin_client, task_id)
    parked = await _wait_status(admin_client, task_id, 'waiting')
    cond = json.loads(parked['wait_condition'])
    assert cond['pending_question']['request_id'] == 'rid-ship-again'
    assert fake.get_calls(task_id, 'retry_without_resume') == []
    assert len(fake.get_calls(task_id, 'run')) == 2


async def test_mcp_tools_report_parked_question(
    admin_client, install_fake_agent
):
    fake = install_fake_agent
    _, task_id, _ = await _park(admin_client, fake)
    data, _ = await call_tool(
        admin_client, 'get_pending_questions', {'task_id': task_id}
    )
    assert data['pending'] is True
    assert data['source'] == 'parked'
    assert data['request_id'] == 'rid-ship'
    answered, _ = await call_tool(
        admin_client,
        'answer_question',
        {'task_id': task_id, 'answers': ANSWER},
    )
    evidence.info('mcp answer %s', answered)
    done = await _wait_status(admin_client, task_id, 'completed')
    assert done['result'] == 'Chose: Express'


async def test_stall_reaper_skips_task_being_parked(
    admin_client, install_fake_agent, override_async_session
):
    fake = install_fake_agent
    store_id = await _store(admin_client)
    task_id = await _create(
        admin_client,
        fake,
        store_id,
        'Ask',
        _asking(park_exit_delay=1.0),
    )

    async def parking():
        s = fake.get_session(task_id)
        return s is not None and s._question_park is not None

    await _until(parking)
    # Age the row far past any stall threshold.
    async with override_async_session() as db:
        task = await db.get(Task, task_id)
        task.updated_at = '2000-01-01T00:00:00+00:00'
        await db.commit()
    await reap_stalled_running_tasks()
    assert (await _task(admin_client, task_id))['status'] == 'running'
    await _wait_status(admin_client, task_id, 'waiting')


async def test_followup_session_question_parks(
    admin_client, install_fake_agent
):
    fake = install_fake_agent
    store_id = await _store(admin_client)
    task_id = await _create(
        admin_client, fake, store_id, 'Plain', FakeAgentScenario(result='ok')
    )
    await _wait_status(admin_client, task_id, 'completed')
    # The follow-up's session asks and nobody answers.
    fake.scenarios[task_id] = _asking()
    r = await admin_client.post(
        f'/api/tasks/{task_id}/messages',
        json={'content': 'Now book the shipment for ORD-1001'},
    )
    assert r.status_code == 200, r.text
    parked = await _wait_status(admin_client, task_id, 'waiting')
    await _task(admin_client, task_id)
    cond = json.loads(parked['wait_condition'])
    assert cond['pending_question']['request_id'] == 'rid-ship'
    assert (await _answer(admin_client, task_id)).json()['resumed'] is True
    done = await _wait_status(admin_client, task_id, 'completed')
    assert done['result'] == 'Chose: Express'


# ── C: a scheduled run never waits on a person ─────────────────────


async def _ready_schedule(client, fake, store_id):
    # The plan-authoring run plans; the fake never asks while planning.
    fake.default_scenario = FakeAgentScenario(plan='## Plan\n1. Ship')
    r = await client.post(
        '/api/schedules',
        json={
            'title': 'Nightly shipping check',
            'description': 'Ship ORD-1001; ask me which option if unsure.',
            'store_id': store_id,
            'schedule_type': 'days',
            'schedule_time': '03:00',
            'plan_mode': True,
        },
    )
    assert r.status_code == 201, r.text
    sched_id = r.json()['id']

    async def ready():
        s = (await client.get(f'/api/schedules/{sched_id}')).json()
        return s.get('plan_status') == 'ready'

    await _until(ready)
    return sched_id


async def test_scheduled_fire_never_waits_on_a_question(
    admin_client, install_fake_agent, monkeypatch, with_queue
):
    fake = install_fake_agent
    # Only C can let this finish fast: nobody answers, the bound is long.
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '60')
    store_id = await _store(admin_client)
    sched_id = await _ready_schedule(admin_client, fake, store_id)
    fake.default_scenario = _asking(result='Shipped with Standard')
    r = await admin_client.post(f'/api/schedules/{sched_id}/trigger')
    assert r.status_code == 200, r.text

    children: list = []

    async def child_created():
        rows = (
            await admin_client.get(f'/api/schedules/{sched_id}/tasks')
        ).json()
        children[:] = rows if isinstance(rows, list) else rows.get('items', [])
        return bool(children)

    await _until(child_created)
    child = children[0]['id']
    seen: list[str] = []
    for _ in range(250):
        data = await _task(admin_client, child)
        if not seen or seen[-1] != data['status']:
            seen.append(data['status'])
        assert (await _pending(admin_client, child))['pending'] is False
        if data['status'] in ('completed', 'failed'):
            break
        await real_sleep(0.02)
    evidence.info('child %s statuses %s', child[:8], seen)
    assert data['status'] == 'completed', data
    assert 'waiting' not in seen
    assert data['result'] == 'Shipped with Standard (question declined)'
    assert data['schedule_id'] == sched_id


async def test_non_scheduled_task_unaffected_by_scheduled_policy(
    admin_client, install_fake_agent, monkeypatch
):
    fake = install_fake_agent
    monkeypatch.setenv('VIBE_QUESTION_ANSWER_TIMEOUT_S', '60')
    store_id = await _store(admin_client)
    task_id = await _create(admin_client, fake, store_id, 'Ask', _asking())
    await _wait_question(admin_client, task_id)
    assert (await _task(admin_client, task_id))['status'] == 'running'
    await _answer(admin_client, task_id)
    done = await _wait_status(admin_client, task_id, 'completed')
    assert done['result'] == 'Chose: Express'


async def test_answer_resumes_through_the_store_queue(
    admin_client, install_fake_agent, with_queue
):
    """Production dispatch: the answer re-queues the parked task on its
    store's queue, whose dispatcher picks the executor."""
    fake = install_fake_agent
    _, task_id, _ = await _park(admin_client, fake)
    r = await _answer(admin_client, task_id)
    assert r.json() == {'ok': True, 'resumed': True}
    done = await _wait_status(admin_client, task_id, 'completed')
    assert done['result'] == 'Chose: Express'
    assert len(fake.get_calls(task_id, 'run')) == 2


async def _fire(client, fake, store_id, scenario):
    sched_id = await _ready_schedule(client, fake, store_id)
    fake.default_scenario = scenario
    r = await client.post(f'/api/schedules/{sched_id}/trigger')
    assert r.status_code == 200, r.text
    children: list = []

    async def child_created():
        rows = (await client.get(f'/api/schedules/{sched_id}/tasks')).json()
        children[:] = rows if isinstance(rows, list) else rows.get('items', [])
        return bool(children)

    await _until(child_created)
    return children[0]['id']


MANUAL_WAIT_RESULT = (
    'Nothing selected.\n\n## Open questions\n- Which shipping option?\n\n'
    '```wait-condition\nreason: waiting for your choice\n'
    'check_strategy: manual\n```'
)


async def test_scheduled_fire_does_not_park_waiting_for_a_reply(
    admin_client, install_fake_agent, with_queue
):
    """Observed live: the question was declined, then the agent ended
    with a wait-condition 'waiting for your choice' and the scheduled
    run parked anyway. A scheduled fire completes with its result."""
    fake = install_fake_agent
    store_id = await _store(admin_client)
    child = await _fire(
        admin_client,
        fake,
        store_id,
        FakeAgentScenario(result=MANUAL_WAIT_RESULT),
    )
    done = await _wait_status(admin_client, child, 'completed')
    await _task(admin_client, child)
    assert done['wait_condition'] is None
    assert 'Open questions' in done['result']


async def test_scheduled_fire_still_waits_on_an_outside_event(
    admin_client, install_fake_agent, with_queue
):
    fake = install_fake_agent
    store_id = await _store(admin_client)
    child = await _fire(
        admin_client,
        fake,
        store_id,
        FakeAgentScenario(
            result=(
                'Asked the carrier.\n\n```wait-condition\nreason: carrier '
                'reply\ncheck_strategy: email\nkeywords: ORD-1001\n```'
            )
        ),
    )
    parked = await _wait_status(admin_client, child, 'waiting')
    assert json.loads(parked['wait_condition'])['check_strategy'] == 'email'


async def test_attended_task_still_parks_waiting_for_a_reply(
    admin_client, install_fake_agent
):
    fake = install_fake_agent
    store_id = await _store(admin_client)
    task_id = await _create(
        admin_client,
        fake,
        store_id,
        'Plain',
        FakeAgentScenario(result=MANUAL_WAIT_RESULT),
    )
    parked = await _wait_status(admin_client, task_id, 'waiting')
    assert json.loads(parked['wait_condition'])['check_strategy'] == 'manual'
