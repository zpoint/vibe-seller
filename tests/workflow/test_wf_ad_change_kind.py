"""A change the USER named is confirmed, not audited.

CI, PR #232: "Lower the bid on 'widget-006' to 1.00. I know it converts
well; I still want it lower." There was no kind for that, so the phase
declared ``audit`` — the only kind that owed coverage — and went and
computed a whole audit: enumerated the market, drilled campaigns, derived
the store's target ACOS and waste cutoff, then spent what was left of the
turn on whether ``acos_no_lower`` would refuse the number the user had
written themselves. 108k output tokens against the 27k the same task used
when it finished, and then the 1200 s limit.

``change`` is the kind that says the user decided. What it sheds is the
audit AROUND the row — coverage, drilling, the store-rule argument. What
it keeps is the money: the turn ends showing the change, only a reply
opens ``execute``, and execution is held to the approval that froze.

Each class here fails on the parent commit, where the kind does not exist
(the declaration is a 400) and a one-row instruction owes a market.
"""

import asyncio

import pytest

from app import question_cards
from app.ai.stop_gates import ad_changes, ad_coverage
import app.database as _db
from app.models.task_message import TaskMessage
import app.routers.tasks as tasks_router
from tests.unit.ad_fixtures import AdTask, change
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

# The campaign the user named, and nothing else.
_THEIR_CAMPAIGN = {
    'combos': [{'platform': 'amazon', 'country': 'SA'}],
    'campaigns': ['A1234567'],
}


@pytest.fixture(autouse=True)
def _ad_skill_loaded(monkeypatch):
    """Both ad gates armed, the way loading the skill arms them."""
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


async def _declare(admin_client, task, kind, scope=None):
    return await admin_client.post(
        f'/api/tasks/{task.id}/ad-declaration',
        json={'kind': kind, 'scope': scope or {}},
    )


async def _user_replies(task, content='Yes, apply it.'):
    """The user answering the change they were shown.

    The row is written directly, as the sibling ad tests do: what these
    tests care about is what the SERVER counts as an answer, and a gated
    fake agent must not be resumed to produce one.
    """
    async with _db.async_session() as db:
        db.add(TaskMessage(task_id=task.id, role='user', content=content))
        await db.commit()


async def _submit(admin_client, task, result='Shown to the user.'):
    return await admin_client.post(
        f'/api/tasks/{task.id}/result', json={'result': result}
    )


async def _shown(admin_client, task):
    """The frozen list the conversation was told about, latest first."""
    msgs = (await admin_client.get(f'/api/tasks/{task.id}/messages')).json()
    return [m['content'] for m in msgs if 'ad changes' in m['content']]


def their_value(**kw) -> dict:
    """The row the user wrote: ACOS 20 under the 25% no-lower floor, and
    1.00 under CPC 1.00 x 1.1. Every store rule here says no."""
    return change(**{
        'action': 'lower',
        'acos': '20',
        'roas': '5',
        'cpc': '1.00',
        'current_bid': '1.20',
        'target_bid': '1.00',
        'reason': 'the user asked for it',
        **kw,
    })


def _executed(**kw) -> dict:
    row = {
        'campaign_id': 'A1234567',
        'ad_group': 'g1',
        'target': 'widget',
        'match_type': 'exact',
        'action': 'lower',
        'from_bid': '1.20',
        'to_bid': '1.00',
        'applied_at': '2026-01-01T00:00:00+00:00',
    }
    row.update({k: str(v) for k, v in kw.items()})
    return row


class TestAChangeNeedsOnlyItsOwnRow:
    async def test_declaring_change_is_accepted(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K1'
        )
        r = await _declare(admin_client, task, 'change', _THEIR_CAMPAIGN)
        assert r.status_code == 200, r.text
        assert r.json()['kind'] == 'change'
        assert r.json()['scope']['campaigns'] == ['A1234567']

    async def test_one_row_and_no_enumeration_passes_both_gates(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """No ``CAMPAIGNS.tsv``, no per-campaign drill, no search-term
        reconciliation — and nothing refused for their absence."""
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K2'
        )
        assert (
            await _declare(admin_client, task, 'change', _THEIR_CAMPAIGN)
        ).status_code == 200
        task.changes([their_value()])
        assert not (task.dir / 'CAMPAIGNS.tsv').exists()

        first = await _submit(admin_client, task)
        assert first.status_code == 200, first.text
        data = (await admin_client.get(f'/api/tasks/{task.id}')).json()
        assert not data.get('review_gaps'), data.get('review_gaps')
        assert ad_coverage.check('', task.id) is None
        assert ad_changes.check('', task.id) is None

    async def test_the_store_rules_do_not_argue_with_their_number(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """The same row is a finding when it is the agent's recommendation
        and not one when the user wrote it."""
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K3'
        )
        assert (
            await _declare(admin_client, task, 'audit', _THEIR_CAMPAIGN)
        ).status_code == 200
        task.changes([their_value()])
        audit = ad_changes.check('', task.id)
        assert audit is not None, 'the control row no longer breaks a rule'
        assert any('under 25% ACOS' in g for g in audit.gaps), audit.gaps

        # Moving to `change` sheds a check, so within the turn it is
        # answered once and then recorded as the agent declares it —
        # one round, like every other check here.
        r = await _declare(admin_client, task, 'change', _THEIR_CAMPAIGN)
        assert r.status_code == 409, r.text
        r = await _declare(admin_client, task, 'change', _THEIR_CAMPAIGN)
        assert r.status_code == 200, r.text
        assert ad_changes.check('', task.id) is None

    async def test_an_audit_of_the_same_request_still_owes_the_market(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """The contrast, so a green `change` test cannot mean the coverage
        gate was simply never running."""
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K4'
        )
        assert (
            await _declare(admin_client, task, 'audit', _THEIR_CAMPAIGN)
        ).status_code == 200
        task.changes([their_value()])
        first = await _submit(admin_client, task)
        assert first.status_code == 400, first.text
        assert 'CAMPAIGNS.tsv' in first.text

    async def test_a_change_row_still_owes_the_typed_columns(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """The contract is not a store rule: a bid action with no bid is
        refused whoever wrote it, because it is the frozen approval that
        execution will be held to."""
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K5'
        )
        assert (
            await _declare(admin_client, task, 'change', _THEIR_CAMPAIGN)
        ).status_code == 200
        task.changes([their_value(target_bid='')])
        first = await _submit(admin_client, task)
        assert first.status_code == 400, first.text
        assert 'without a target_bid' in first.text, first.text


class TestTheirReplyOpensExecute:
    async def test_execute_after_a_reply_to_a_change_is_confirmed(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K6'
        )
        assert (
            await _declare(admin_client, task, 'change', _THEIR_CAMPAIGN)
        ).status_code == 200
        task.changes([their_value()])
        await _user_replies(task)

        r = await _declare(admin_client, task, 'execute')
        assert r.status_code == 200, r.text
        assert r.json()['approved'] == [
            'A1234567 / widget (exact): lower 1.20 -> 1.00'
        ], r.json()

        shown = await _shown(admin_client, task)
        assert shown and 'NOT confirmed' not in shown[-1], shown
        assert '1.00' in shown[-1], shown

    async def test_execute_without_a_reply_to_a_change_is_asked_about_once(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """Naming the value in the task is not having seen the change
        listed — the reply is still what opens execution."""
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K7'
        )
        assert (
            await _declare(admin_client, task, 'change', _THEIR_CAMPAIGN)
        ).status_code == 200
        task.changes([their_value()])

        r = await _declare(admin_client, task, 'execute')
        assert r.status_code == 409, r.text
        assert 'change' in r.json()['detail'], r.json()
        r = await _declare(admin_client, task, 'execute')
        assert r.status_code == 200, r.text
        shown = await _shown(admin_client, task)
        assert shown and 'NOT confirmed' in shown[-1], shown

    async def test_a_question_card_is_not_a_reply_to_a_change(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """The card is the agent's own multiple choice. Ending the turn
        with the change shown is the only path that reads as an answer."""
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K8'
        )
        assert (
            await _declare(admin_client, task, 'change', _THEIR_CAMPAIGN)
        ).status_code == 200
        task.changes([their_value()])
        await _user_replies(task)
        question_cards.note(task.id)  # the card answered after the message

        r = await _declare(admin_client, task, 'execute')
        assert r.status_code == 409, r.text
        assert 'question card' in r.text, r.text
        r = await _declare(admin_client, task, 'execute')
        assert r.status_code == 200, r.text
        shown = await _shown(admin_client, task)
        assert shown and 'NOT confirmed' in shown[-1], shown


class TestTheDeclarationSaysWhatTheKindOwes:
    """The declaration answer names the data contract to load when no ad
    skill is loaded yet. Telling a `change` about the audit's files sends
    it back to the enumeration that timed out."""

    @pytest.fixture(autouse=True)
    def _no_ad_skill(self, monkeypatch):
        monkeypatch.setattr(
            'app.routers.tasks_ad_declaration.agent_manager.'
            'loaded_skills_and_workspace',
            lambda _t: ({'browser-harness'}, None),
        )

    async def test_a_change_is_pointed_at_one_file(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K11'
        )
        r = await _declare(admin_client, task, 'change', _THEIR_CAMPAIGN)
        assert r.status_code == 200, r.text
        note = r.json()['load_skill']
        assert 'CHANGES.tsv' in note, note
        assert 'EXECUTION_LOG.tsv' not in note, note

    async def test_an_audit_still_owes_the_enumeration(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K12'
        )
        r = await _declare(admin_client, task, 'audit', _THEIR_CAMPAIGN)
        assert r.status_code == 200, r.text
        assert 'CAMPAIGNS.tsv' in r.json()['load_skill'], r.json()


class TestTheMoneyGuardIsIntact:
    async def test_execution_that_deviates_from_their_approval_is_caught(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K9'
        )
        assert (
            await _declare(admin_client, task, 'change', _THEIR_CAMPAIGN)
        ).status_code == 200
        task.changes([their_value()])
        await _user_replies(task)
        assert (
            await _declare(admin_client, task, 'execute')
        ).status_code == 200

        task.execution([_executed(to_bid='1.35')])
        first = await _submit(admin_client, task)
        assert first.status_code == 400, first.text
        assert 'approved 1.00' in first.text, first.text

    async def test_execution_of_their_value_is_clean(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        task = await _running(
            admin_client, install_fake_agent, monkeypatch, 'K10'
        )
        assert (
            await _declare(admin_client, task, 'change', _THEIR_CAMPAIGN)
        ).status_code == 200
        task.changes([their_value()])
        await _user_replies(task)
        assert (
            await _declare(admin_client, task, 'execute')
        ).status_code == 200

        task.execution([_executed()])
        r = await _submit(admin_client, task)
        assert r.status_code == 200, r.text
        data = (await admin_client.get(f'/api/tasks/{task.id}')).json()
        assert not data.get('review_gaps'), data.get('review_gaps')
        assert ad_changes.check('', task.id) is None
