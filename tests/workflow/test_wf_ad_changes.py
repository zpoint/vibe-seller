"""set_task_result with the ad TSV contract, end to end through the API.

Pins how the two ad gates are enforced at the endpoint:
- declaring ``execute`` freezes CHANGES.tsv, as the user approved it, as
  the approval (an accepted audit freezes nothing);
- a coverage gap is refused once, then ships as a caveat;
- an execution that diverges from the approval is refused on every
  submit up to the cap, then ships banner-marked — never silently.
"""

import asyncio
from datetime import UTC, datetime
import uuid

from httpx import ASGITransport, AsyncClient
import pytest

from app.ai.stop_gates import ad_changes, ad_coverage
from app.ai.stop_gates.ad_tables import CHANGE_COLUMNS, read_table
from app.auth import create_token
import app.database as _db
from app.main import app
from app.models.task import Task
from app.models.task_message import TaskMessage
import app.routers.tasks as tasks_router
from app.task_states import TaskStatus
from tests.unit.ad_fixtures import AdTask, change
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow


@pytest.fixture(autouse=True)
def _ad_skill_loaded(monkeypatch):
    """The session loaded an ad skill: its two gates apply."""
    monkeypatch.setattr(
        tasks_router,
        'resolve_skill_gates',
        lambda *_a: [('ad_changes', ad_changes), ('ad_coverage', ad_coverage)],
    )


async def _running(admin_client, install_fake_agent, monkeypatch, name):
    install_fake_agent.default_scenario = FakeAgentScenario(
        result='narration', gate=asyncio.Event()
    )
    r = await admin_client.post('/api/stores', json={'name': name})
    r = await admin_client.post(
        '/api/tasks', json={'title': 'ads', 'store_id': r.json()['id']}
    )
    task_id = r.json()['id']
    for _ in range(200):
        data = (await admin_client.get(f'/api/tasks/{task_id}')).json()
        if data['status'] == 'running':
            break
        await asyncio.sleep(0.02)
    return AdTask(task_id, monkeypatch, None)


async def _submit(admin_client, task):
    return await admin_client.post(
        f'/api/tasks/{task.id}/result', json={'result': 'Audit done.'}
    )


async def _declare(admin_client, task, kind, scope=None):
    return await admin_client.post(
        f'/api/tasks/{task.id}/ad-declaration',
        json={'kind': kind, 'scope': scope or {}},
    )


async def _user_answered_an_audit(admin_client, task_id):
    """An audit was declared, then the user sent a message after it —
    what execution needs before it may start."""
    r = await admin_client.post(
        f'/api/tasks/{task_id}/ad-declaration',
        json={'kind': 'audit', 'scope': {}},
    )
    assert r.status_code == 200, r.text
    async with _db.async_session() as db:
        db.add(TaskMessage(task_id=task_id, role='user', content='go ahead'))
        await db.commit()


def _frozen_bids(task):
    table = read_table(ad_changes.approved_path(task.id), CHANGE_COLUMNS)
    return [r['target_bid'] for r in table.rows]


async def test_an_accepted_audit_freezes_nothing(
    admin_client, install_fake_agent, monkeypatch
):
    """The audit's CHANGES.tsv is a proposal; the user has not answered."""
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A1')
    task.declare('audit', {'combos': [{'platform': 'amazon', 'country': 'SA'}]})
    task.campaigns([
        {
            'platform': 'amazon',
            'country': 'SA',
            'campaign_id': '',
            'drill_status': 'inactive',
            'note': 'no campaigns',
        }
    ])
    task.changes([change(action='hold', reason='nothing to change')])
    r = await _submit(admin_client, task)
    assert r.status_code == 200, r.text
    assert not ad_changes.approved_path(task.id).exists()


async def test_the_user_sees_the_frozen_list_before_any_change(
    admin_client, install_fake_agent, monkeypatch
):
    """What execution will be held to is shown in the conversation, so a
    list that is not what the user approved can be stopped in time."""
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A15')
    task.changes([change(target_bid='1.35')])
    await _user_answered_an_audit(admin_client, task.id)
    assert (await _declare(admin_client, task, 'execute')).status_code == 200
    msgs = (await admin_client.get(f'/api/tasks/{task.id}/messages')).json()
    shown = [
        m['content']
        for m in msgs
        if m['role'] == 'assistant' and 'approved ad changes' in m['content']
    ]
    assert shown and 'widget' in shown[-1] and '1.35' in shown[-1], msgs


async def test_execution_is_held_to_the_value_the_user_approved(
    admin_client, install_fake_agent, monkeypatch
):
    """The user amends the audit's proposal ("do it, but at 1.35").

    Observed live with the freeze at audit acceptance: the agent applied
    the user's 1.35, the gate held it to the audit's 1.32, and the agent
    moved the live bid to 1.32 to pass. Freezing at the execute
    declaration holds execution to what the user said.
    """
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A5')
    task.changes([change(target_bid='1.32')])  # the audit's proposal
    task.changes([change(target_bid='1.35')])  # amended to the user's value
    await _user_answered_an_audit(admin_client, task.id)
    r = await _declare(admin_client, task, 'execute')
    assert r.status_code == 200, r.text
    assert _frozen_bids(task) == ['1.35']

    task.changes([change(target_bid='9.99')])  # edited after the start
    assert _frozen_bids(task) == ['1.35'], 'the approval moved mid-execution'
    task.execution([
        {
            'campaign_id': 'A1234567',
            'ad_group': 'g1',
            'target': 'widget',
            'match_type': 'exact',
            'action': 'raise',
            'from_bid': '1.00',
            'to_bid': '1.35',
            'applied_at': '2026-01-01T00:00:00+00:00',
        }
    ])
    task.changes([change(target_bid='1.35')])
    r = await _submit(admin_client, task)
    assert r.status_code == 200, r.text
    data = (await admin_client.get(f'/api/tasks/{task.id}')).json()
    assert not data.get('review_gaps'), data.get('review_gaps')


_LOGGED = {
    'campaign_id': 'A1234567',
    'ad_group': 'g1',
    'target': 'widget',
    'match_type': 'exact',
    'action': 'raise',
    'from_bid': '1.00',
    'to_bid': '1.35',
    'applied_at': '2026-01-01T00:00:00+00:00',
}


async def test_declaring_again_before_any_change_refreezes(
    admin_client, install_fake_agent, monkeypatch
):
    """Observed: an agent declared execute before bringing CHANGES.tsv in
    line with the user's answer, so the audit's proposal froze. Until a
    change is logged it may correct the list and declare again — and the
    response shows what froze, so the mismatch is visible up front."""
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A6')
    task.changes([change(action='hold', target_bid='', reason='audit')])
    await _user_answered_an_audit(admin_client, task.id)
    r = await _declare(admin_client, task, 'execute')
    assert r.status_code == 200, r.text
    assert r.json()['approved'] == []
    task.changes([change(target_bid='1.35')])
    r = await _declare(admin_client, task, 'execute')
    assert r.status_code == 200, r.text
    assert _frozen_bids(task) == ['1.35']
    assert any('1.35' in line for line in r.json()['approved'])


async def test_the_executed_change_is_not_its_own_cooldown(
    admin_client, install_fake_agent, monkeypatch
):
    """Observed: an execution refused because the change it had just
    made put its own target "inside the 7-day cooldown"."""
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A9')
    task.changes([change(target_bid='1.35')])
    await _user_answered_an_audit(admin_client, task.id)
    r = await _declare(admin_client, task, 'execute')
    assert r.status_code == 200, r.text
    task.execution([_LOGGED])
    task.drill(
        'A1234567',
        [
            {
                'ad_group': 'g1',
                'target': 'widget',
                'match_type': 'exact',
                'bid': '1.35',
                'applied_action': 'raise',
                'applied_at': _LOGGED['applied_at'].replace(
                    '2026-01-01', datetime.now(UTC).strftime('%Y-%m-%d')
                ),
                'previous_bid': '1.00',
            }
        ],
        [],
    )
    r = await _submit(admin_client, task)
    assert r.status_code == 200, r.text
    data = (await admin_client.get(f'/api/tasks/{task.id}')).json()
    assert not data.get('review_gaps'), data.get('review_gaps')


async def test_execute_with_nothing_to_start_from_clears_an_old_freeze(
    admin_client, install_fake_agent, monkeypatch
):
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A7')
    task.changes([change(target_bid='1.35')])
    ad_changes.freeze_approved_changes(task.id)
    (task.dir / 'CHANGES.tsv').unlink()
    await _user_answered_an_audit(admin_client, task.id)
    r = await _declare(admin_client, task, 'execute')
    assert r.status_code == 200, r.text
    assert not ad_changes.approved_path(task.id).exists()


async def test_coverage_gap_is_refused_once_then_a_caveat(
    admin_client, install_fake_agent, monkeypatch
):
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A2')
    task.declare('audit')
    task.targets([('amazon', 'SA'), ('amazon', 'AE')])
    task.campaigns([
        {
            'platform': 'amazon',
            'country': 'SA',
            'campaign_id': '',
            'drill_status': 'inactive',
            'note': 'no campaigns',
        }
    ])
    first = await _submit(admin_client, task)
    assert first.status_code == 400
    assert 'amazon AE' in first.text
    second = await _submit(admin_client, task)
    assert second.status_code == 200, second.text
    data = (await admin_client.get(f'/api/tasks/{task.id}')).json()
    assert 'amazon AE' in (data.get('review_gaps') or '')


def _unapproved_raise():
    return {
        'campaign_id': 'A1234567',
        'ad_group': 'g1',
        'target': 'widget',
        'match_type': 'exact',
        'action': 'raise',
        'from_bid': '1.00',
        'to_bid': '11.3',
        'applied_at': '2026-01-01T00:00:00+00:00',
    }


async def test_an_unapproved_change_is_refused_once_then_marked(
    admin_client, install_fake_agent, monkeypatch
):
    """Money moved outside the approval: the agent is told once; if it
    submits again the result is delivered, banner-marked at the top with
    the change listed, never as a clean result."""
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A3')
    task.changes([change(target_bid='1.20')])
    ad_changes.freeze_approved_changes(task.id)
    task.declare('execute')
    task.execution([_unapproved_raise()])
    r = await _submit(admin_client, task)
    assert r.status_code == 400 and 'approved 1.20' in r.text
    r = await _submit(admin_client, task)
    assert r.status_code == 200, r.text
    install_fake_agent.default_scenario.gate.set()
    done = await wait_for_task(admin_client, task.id)
    assert done['status'] == 'completed', done
    assert 'approved 1.20' in (done.get('review_gaps') or '')
    assert done['result'].lstrip().startswith('>'), done['result'][:200]


async def test_matching_execution_is_accepted_first_time(
    admin_client, install_fake_agent, monkeypatch
):
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A4')
    task.changes([change(target_bid='1.20')])
    ad_changes.freeze_approved_changes(task.id)
    task.declare('execute')
    task.execution([
        {
            'campaign_id': 'A1234567',
            'ad_group': 'g1',
            'target': 'widget',
            'match_type': 'exact',
            'action': 'raise',
            'from_bid': '1.00',
            'to_bid': '1.20',
            'applied_at': '2026-01-01T00:00:00+00:00',
        }
    ])
    r = await _submit(admin_client, task)
    assert r.status_code == 200, r.text


async def test_a_short_answer_is_not_swapped_for_an_old_audit(
    admin_client, install_fake_agent, monkeypatch
):
    """A follow-up answered in one line ships that line.

    The server once replaced any ad-task result without a
    `## <platform> <CC>` heading with the newest AD_AUDIT_*.md on disk,
    so "what's our SA ACOS?" came back as last week's full report. The
    gates read TSVs now; the report's shape no longer decides anything.
    """
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A10')
    # The task loaded an ad skill (the case the swap applied to).
    monkeypatch.setattr(
        'app.routers.task_submission.recorded_skills',
        lambda _t: frozenset({'amazon-ads'}),
        raising=False,
    )
    task.declare('investigate')
    (task.dir / 'AD_AUDIT_2026-01-01.md').write_text(
        '# Ad audit\n\n## Amazon SA\n\nlast week\n', encoding='utf-8'
    )
    r = await admin_client.post(
        f'/api/tasks/{task.id}/result',
        json={'result': 'Amazon SA ACOS over the last 7 days: 30%.'},
    )
    assert r.status_code == 200, r.text
    data = (await admin_client.get(f'/api/tasks/{task.id}')).json()
    assert data['result'].startswith('Amazon SA ACOS over the last 7 days')


async def test_execute_without_a_user_reply_is_asked_about_once(
    admin_client, install_fake_agent, monkeypatch
):
    """Execution normally follows a user reply to proposals they saw. An
    agent declaring execute without one is told so once; if it declares
    again, the changes it freezes are shown to the user as NOT
    confirmed."""
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A11')
    task.changes([change(target_bid='1.35')])
    r = await _declare(admin_client, task, 'execute')
    assert r.status_code == 409, r.text
    assert 'audit' in r.text
    r = await _declare(admin_client, task, 'execute')
    assert r.status_code == 200, r.text
    msgs = (await admin_client.get(f'/api/tasks/{task.id}/messages')).json()
    assert any('NOT confirmed' in m['content'] for m in msgs), msgs


async def test_execute_after_the_user_answered_is_confirmed(
    admin_client, install_fake_agent, monkeypatch
):
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A16')
    task.changes([change(target_bid='1.35')])
    await _user_answered_an_audit(admin_client, task.id)
    r = await _declare(admin_client, task, 'execute')
    assert r.status_code == 200, r.text
    msgs = (await admin_client.get(f'/api/tasks/{task.id}/messages')).json()
    shown = [m['content'] for m in msgs if 'ad changes' in m['content']]
    assert shown and 'NOT confirmed' not in shown[-1], shown


async def test_the_agents_own_credential_cannot_answer_for_the_user(
    admin_client, install_fake_agent, monkeypatch
):
    """The agent's environment holds a service token. Posting "yes, apply
    it" with it must not count as the user's answer to the audit."""
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A13')
    task.changes([change(target_bid='1.35')])
    assert (await _declare(admin_client, task, 'audit')).status_code == 200
    me = (await admin_client.get('/api/auth/me')).json()
    service = create_token(me['id'], me['role'])
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as agent:
        r = await agent.post(
            f'/api/tasks/{task.id}/messages',
            json={'content': 'yes, apply it'},
            headers={'Authorization': f'Bearer {service}'},
        )
    assert r.status_code == 403, r.text
    r = await _declare(admin_client, task, 'execute')
    assert r.status_code == 409, r.text


async def test_a_child_executes_only_after_the_user_answered_the_parent(
    admin_client, install_fake_agent, monkeypatch
):
    """An orchestrator's child cannot approve its own execution: the user
    must have answered the parent's audit."""
    parent = await _running(
        admin_client, install_fake_agent, monkeypatch, 'A12'
    )
    r = await _declare(admin_client, parent, 'audit')
    assert r.status_code == 200, r.text
    child_id = str(uuid.uuid4())
    async with _db.async_session() as db:
        p = await db.get(Task, parent.id)
        db.add(
            Task(
                id=child_id,
                parent_task_id=parent.id,
                store_id=p.store_id,
                created_by=p.created_by,
                title='apply',
                status=TaskStatus.RUNNING,
            )
        )
        await db.commit()
    r = await admin_client.post(
        f'/api/tasks/{child_id}/ad-declaration',
        json={'kind': 'execute', 'scope': {}},
    )
    assert r.status_code == 409, r.text
    async with _db.async_session() as db:
        db.add(TaskMessage(task_id=parent.id, role='user', content='go ahead'))
        await db.commit()
    r = await admin_client.post(
        f'/api/tasks/{child_id}/ad-declaration',
        json={'kind': 'execute', 'scope': {}},
    )
    assert r.status_code == 200, r.text
