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
        # The refusal has to name the one legal in-turn move, or an agent
        # that genuinely needs to record enumerated campaign ids has no
        # way to learn it may.
        detail = r.json()['detail']
        assert 'NARROW' in detail
        assert 'USER' in detail

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


class TestNarrowingWithinATurn:
    """Campaign ids can only be learned by looking, so they may be added.

    The first version of this rule could not be obeyed: declare BEFORE
    opening a browser, yet name campaign ids that only exist once you
    have enumerated the account. Observed live, an agent asked to review
    one SKU family declared the market with no campaigns — which means
    every campaign in it — and the audit ended up owing all of Amazon SA.

    So a refinement is allowed within the turn, but only ever inward.
    """

    async def test_naming_campaigns_after_enumerating_is_allowed(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        market_only = {'combos': [{'platform': 'amazon', 'country': 'SA'}]}
        assert (
            await _declare(admin_client, task_id, 'audit', market_only)
        ).status_code == 200

        # …now it has enumerated and knows which campaigns carry the SKU.
        narrowed = dict(market_only, campaigns=['A1234567', 'A7654321'])
        r = await _declare(admin_client, task_id, 'audit', narrowed)
        assert r.status_code == 200, r.text
        assert r.json()['scope']['campaigns'] == ['A1234567', 'A7654321']
        # Append-only holds: the first declaration is still on the record.
        r = await admin_client.get(f'/api/tasks/{task_id}/ad-declarations')
        assert [d['seq'] for d in r.json()] == [1, 2]

    async def test_narrowing_again_is_allowed(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        market = {'combos': [{'platform': 'amazon', 'country': 'SA'}]}
        await _declare(
            admin_client,
            task_id,
            'audit',
            dict(market, campaigns=['A1234567', 'A7654321']),
        )
        r = await _declare(
            admin_client, task_id, 'audit', dict(market, campaigns=['A1234567'])
        )
        assert r.status_code == 200, r.text

    async def test_widening_the_campaign_list_is_refused(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        market = {'combos': [{'platform': 'amazon', 'country': 'SA'}]}
        await _declare(
            admin_client, task_id, 'audit', dict(market, campaigns=['A1234567'])
        )
        r = await _declare(
            admin_client,
            task_id,
            'audit',
            dict(market, campaigns=['A1234567', 'A7654321']),
        )
        assert r.status_code == 409, r.text

    async def test_dropping_the_campaign_list_is_refused(
        self, admin_client, install_fake_agent
    ):
        # Going back to "every campaign in this market" is widening.
        task_id = await _make_task(admin_client)
        market = {'combos': [{'platform': 'amazon', 'country': 'SA'}]}
        await _declare(
            admin_client, task_id, 'audit', dict(market, campaigns=['A1234567'])
        )
        r = await _declare(admin_client, task_id, 'audit', market)
        assert r.status_code == 409, r.text

    async def test_adding_a_marketplace_is_refused(
        self, admin_client, install_fake_agent
    ):
        # The move the whole design exists to prevent.
        task_id = await _make_task(admin_client)
        r = await _declare(
            admin_client,
            task_id,
            'audit',
            {'combos': [{'platform': 'amazon', 'country': 'SA'}]},
        )
        assert r.status_code == 200
        r = await _declare(
            admin_client,
            task_id,
            'audit',
            {
                'combos': [
                    {'platform': 'amazon', 'country': 'SA'},
                    {'platform': 'noon', 'country': 'AE'},
                ],
                'campaigns': ['A1234567'],
            },
        )
        assert r.status_code == 409, r.text

    async def test_shedding_an_obligation_by_changing_the_kind_is_refused(
        self, admin_client, install_fake_agent
    ):
        # `investigate` owes no marketplace coverage and opens no
        # console. Reaching it from `audit` mid-turn is how an agent
        # would drop both, so it is the move that has to stay shut.
        task_id = await _make_task(admin_client)
        market = {'combos': [{'platform': 'amazon', 'country': 'SA'}]}
        await _declare(admin_client, task_id, 'audit', market)
        r = await _declare(admin_client, task_id, 'investigate', market)
        assert r.status_code == 409, r.text

    async def test_an_unrelated_kind_change_is_refused(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        market = {'combos': [{'platform': 'amazon', 'country': 'SA'}]}
        await _declare(admin_client, task_id, 'create', market)
        r = await _declare(
            admin_client, task_id, 'audit', dict(market, campaigns=['A1234567'])
        )
        assert r.status_code == 409, r.text


class TestCorrectingAnUnderDeclaredPhase:
    """The one kind change that is legal, and why it does not open a hole.

    An agent that declared `investigate` ("just read me the numbers") and
    then produced a table of bid changes has under-declared what it owes.
    Before this, the ratchet refused every kind change, so its only way
    to comply was to DELETE the recommendations — the opposite of what
    the person asked for, and the state a CI run reached: a bid review
    declared `investigate`, so the user got recommendations and no
    console to approve them on.

    `investigate` → `audit` is safe to allow precisely because it is not
    an escape: it ADDS the coverage obligation and opens the console. The
    reach checks are what stop it smuggling in a wider scope.
    """

    _MARKET = {'combos': [{'platform': 'amazon', 'country': 'SA'}]}

    async def test_investigate_may_be_corrected_to_audit(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        assert (
            await _declare(admin_client, task_id, 'investigate', self._MARKET)
        ).status_code == 200
        r = await _declare(admin_client, task_id, 'audit', self._MARKET)
        assert r.status_code == 200, r.text
        assert r.json()['kind'] == 'audit'
        assert r.json()['seq'] == 2

        # Append-only holds — the correction is a new row, not a rewrite,
        # so the record still shows what the phase originally claimed.
        r = await admin_client.get(f'/api/tasks/{task_id}/ad-declarations')
        assert [d['kind'] for d in r.json()] == ['investigate', 'audit']

    async def test_the_correction_may_narrow_at_the_same_time(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        await _declare(admin_client, task_id, 'investigate', self._MARKET)
        r = await _declare(
            admin_client,
            task_id,
            'audit',
            dict(self._MARKET, campaigns=['A1234567']),
        )
        assert r.status_code == 200, r.text

    async def test_an_unscoped_investigate_may_name_markets_as_it_upgrades(
        self, admin_client, install_fake_agent
    ):
        # Without this, correcting a whole-store `investigate` would
        # commit the phase to auditing every marketplace the store sells
        # on — a bill big enough that the agent would rationally choose
        # to delete its findings instead.
        task_id = await _make_task(admin_client)
        await _declare(admin_client, task_id, 'investigate', {})
        r = await _declare(admin_client, task_id, 'audit', self._MARKET)
        assert r.status_code == 200, r.text

    async def test_the_correction_may_not_widen_the_markets(
        self, admin_client, install_fake_agent
    ):
        task_id = await _make_task(admin_client)
        await _declare(admin_client, task_id, 'investigate', self._MARKET)
        r = await _declare(
            admin_client,
            task_id,
            'audit',
            {
                'combos': [
                    {'platform': 'amazon', 'country': 'SA'},
                    {'platform': 'noon', 'country': 'AE'},
                ]
            },
        )
        assert r.status_code == 409, r.text

    async def test_the_correction_may_not_become_whole_store(
        self, admin_client, install_fake_agent
    ):
        # Dropping `combos` means EVERY marketplace — the widest value
        # there is, not an unchanged one.
        task_id = await _make_task(admin_client)
        await _declare(admin_client, task_id, 'investigate', self._MARKET)
        r = await _declare(admin_client, task_id, 'audit', {})
        assert r.status_code == 409, r.text

    async def test_the_refusal_teaches_the_correction(
        self, admin_client, install_fake_agent
    ):
        # An agent that cannot learn the legal move from the refusal will
        # satisfy the gate the other way — by deleting its findings.
        task_id = await _make_task(admin_client)
        await _declare(admin_client, task_id, 'create', self._MARKET)
        r = await _declare(admin_client, task_id, 'audit', self._MARKET)
        assert r.status_code == 409
        detail = r.json()['detail']
        assert 'investigate' in detail and 'audit' in detail
        assert 'USER' in detail


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
        store = [
            {'platform': 'amazon', 'country': 'AE'},
            {'platform': 'noon', 'country': 'SA'},
        ]
        assert ad_declaration.owed_combos(loaded, store) == [
            {'platform': 'amazon', 'country': 'AE'}
        ], 'a scoped audit owes what it named, not every marketplace'
