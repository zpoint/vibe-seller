"""A task's purpose can change — but only the user may change it.

The journey this pins is the one that broke the first version of this
design. A seller writes "create a listing for widget-006"; the task
finishes; then, in the SAME conversation with the same workspace, they
write "now audit the ads for the listing you just made". The second turn
is genuinely an audit and must open a review console. The first was not
and must not.

Modelled as one flag per task, that conversation is unrepresentable. So
declarations belong to a RUN and accumulate. What must NOT be possible
is an agent re-declaring on its own — that would hand it the escape
hatch from any gate it dislikes. The rule enforced here is therefore:
append-only, and a new declaration requires a new USER message.
"""

import json

import pytest

from app.ai import ad_declaration
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow


async def _make_task(admin_client, title='Create a listing for widget-006'):
    r = await admin_client.post('/api/stores', json={'name': 'acme'})
    store_id = r.json()['id']
    r = await admin_client.post(
        '/api/tasks',
        json={
            'title': title,
            'description': 'placeholder',
            'store_id': store_id,
        },
    )
    task_id = r.json()['id']
    await wait_for_task(admin_client, task_id)
    return task_id


async def _declare(admin_client, task_id, kind, scope=None):
    return await admin_client.post(
        f'/api/tasks/{task_id}/ad-declaration',
        json={'kind': kind, 'scope': scope or {}},
    )


_ONE_MARKET = {
    'combos': [{'platform': 'amazon', 'country': 'AE'}],
    'campaigns': ['A1234567'],
    'products': ['WIDGET-006'],
}


class TestDeclaringAPhase:
    async def test_first_declaration_is_accepted_and_readable(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        r = await _declare(admin_client, task_id, 'create', _ONE_MARKET)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['seq'] == 1
        assert body['kind'] == 'create'
        assert body['scope']['campaigns'] == ['A1234567']

        # The frontend reads the sequence to decide which result item
        # gets a console; it must be able to.
        r = await admin_client.get(f'/api/tasks/{task_id}/ad-declarations')
        assert r.status_code == 200
        assert [d['kind'] for d in r.json()] == ['create']

    async def test_unknown_kind_is_rejected(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        r = await _declare(admin_client, task_id, 'sweep')
        assert r.status_code == 400

    async def test_edit_is_not_a_kind_and_the_error_says_why(
        self, admin_client, install_fake_agent
    ):
        # Deliberately absent: "change these three bids" is a one-campaign
        # AUDIT. Two kinds meaning nearly the same thing drift apart, and
        # the console would need two code paths.
        task_id = await _make_task(admin_client)
        r = await _declare(admin_client, task_id, 'edit')
        assert r.status_code == 400
        assert 'audit' in r.json()['detail']


class TestOnlyTheUserOpensANewPhase:
    async def test_redeclaring_in_the_same_turn_is_refused(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        assert (
            await _declare(admin_client, task_id, 'create', _ONE_MARKET)
        ).status_code == 200
        # The agent, having been denied by a gate, tries to widen its
        # own remit. This is the hole the whole design closes.
        r = await _declare(admin_client, task_id, 'audit', {})
        assert r.status_code == 409
        assert 'cannot be changed' in r.json()['detail']

    async def test_a_user_follow_up_opens_a_new_phase(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        await _declare(admin_client, task_id, 'create', _ONE_MARKET)

        # The seller comes back: "now audit the ads for what you made."
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages',
            json={'content': '把刚创建的广告复核一下'},
        )
        assert r.status_code == 200

        r = await _declare(admin_client, task_id, 'audit', _ONE_MARKET)
        assert r.status_code == 200, r.text
        assert r.json()['seq'] == 2
        assert r.json()['kind'] == 'audit'

        r = await admin_client.get(f'/api/tasks/{task_id}/ad-declarations')
        # Both phases survive: the create result keeps no console, the
        # audit result gets one.
        assert [d['kind'] for d in r.json()] == ['create', 'audit']

    async def test_the_earlier_declaration_is_never_rewritten(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        await _declare(admin_client, task_id, 'create', _ONE_MARKET)
        await admin_client.post(
            f'/api/tasks/{task_id}/messages', json={'content': '再看看'}
        )
        await _declare(admin_client, task_id, 'audit', {})

        r = await admin_client.get(f'/api/tasks/{task_id}/ad-declarations')
        first = r.json()[0]
        assert first['kind'] == 'create'
        assert first['scope']['campaigns'] == ['A1234567']


class TestTheGateSeesWhatWasAccepted:
    async def test_accepted_declaration_is_mirrored_for_the_gates(
        self, admin_client, install_fake_agent, monkeypatch, tmp_path
    ):
        # Stop gates run synchronously with no DB session, so they read a
        # file. The row stays the authority — the file is written only
        # AFTER validation, which is what stops an agent editing its way
        # to a different obligation.
        monkeypatch.setattr(ad_declaration, 'VIBE_SELLER_DIR', tmp_path)
        task_id = await _make_task(admin_client)
        (tmp_path / 'tasks' / task_id).mkdir(parents=True, exist_ok=True)

        await _declare(admin_client, task_id, 'audit', _ONE_MARKET)

        path = tmp_path / 'tasks' / task_id / 'AD_TASK.json'
        assert path.exists()
        written = json.loads(path.read_text(encoding='utf-8'))
        assert written['kind'] == 'audit'
        assert written['scope']['combos'] == [
            {'platform': 'amazon', 'country': 'AE'}
        ]

        loaded = ad_declaration.load_declaration(task_id)
        assert loaded is not None
        assert not ad_declaration.owes_marketplace_coverage(loaded), (
            'a scoped audit must not be told it owes every marketplace'
        )
