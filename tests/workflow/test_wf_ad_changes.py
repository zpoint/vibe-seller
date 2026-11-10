"""set_task_result with the ad TSV contract, end to end through the API.

Pins how the two ad gates are enforced at the endpoint:
- declaring ``execute`` freezes CHANGES.tsv, as the user approved it, as
  the approval (an accepted audit freezes nothing);
- a coverage gap is refused once, then ships as a caveat;
- an execution that diverges from the approval is refused on every
  submit up to the cap, then ships banner-marked — never silently.
"""

import asyncio

import pytest

from app.ai.stop_gates import CONTRADICTION_MAX_DENIALS, ad_changes, ad_coverage
from app.ai.stop_gates.ad_tables import CHANGE_COLUMNS, read_table
import app.routers.tasks as tasks_router
from tests.unit.ad_fixtures import AdTask, change
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


async def test_narrowing_inside_the_turn_does_not_refreeze(
    admin_client, install_fake_agent, monkeypatch
):
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A6')
    task.changes([change(target_bid='1.35')])
    r = await _declare(admin_client, task, 'execute')
    assert r.status_code == 200, r.text
    task.changes([change(target_bid='9.99')])
    r = await _declare(
        admin_client, task, 'execute', {'campaigns': ['A1234567']}
    )
    assert r.status_code == 200, r.text
    assert _frozen_bids(task) == ['1.35']


async def test_execute_with_nothing_to_start_from_clears_an_old_freeze(
    admin_client, install_fake_agent, monkeypatch
):
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A7')
    task.changes([change(target_bid='1.35')])
    ad_changes.freeze_approved_changes(task.id)
    (task.dir / 'CHANGES.tsv').unlink()
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


async def test_diverging_execution_is_refused_until_the_cap(
    admin_client, install_fake_agent, monkeypatch
):
    task = await _running(admin_client, install_fake_agent, monkeypatch, 'A3')
    task.changes([change(target_bid='1.20')])
    ad_changes.freeze_approved_changes(task.id)
    (task.dir / 'CHANGES.tsv').unlink()
    task.declare('execute')
    task.execution([
        {
            'campaign_id': 'A1234567',
            'ad_group': 'g1',
            'target': 'widget',
            'match_type': 'exact',
            'action': 'raise',
            'from_bid': '1.00',
            'to_bid': '11.3',
            'applied_at': '2026-01-01T00:00:00+00:00',
        }
    ])
    refusals = 0
    for _ in range(CONTRADICTION_MAX_DENIALS + 3):
        r = await _submit(admin_client, task)
        if r.status_code == 200:
            break
        assert 'approved 1.20' in r.text
        refusals += 1
    assert refusals >= CONTRADICTION_MAX_DENIALS
    data = (await admin_client.get(f'/api/tasks/{task.id}')).json()
    assert 'approved 1.20' in (data.get('review_gaps') or '')


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
