"""A real agent, a fake ad console, and one question: did the scope hold?

This closes the loop that unit tests cannot. The scoping contract has two
halves — the agent must DECLARE what it was asked for, and the console
must be bounded by that declaration — and only the first half depends on
a model reading a natural-language request. So this drives a real agent
against a stand-in ad console carrying TWO campaigns and asks about ONE.

The journey mirrors the one that produced the design:

1. "how much did we spend, what came back" → a look, not an audit.
2. Same task, new user turn: "review the bids on <one campaign>, leave
   the other alone" → an audit, scoped to that campaign.

What must be true afterwards: two declarations, the second an ``audit``
naming the asked-about campaign and NOT the other. Because the console
renders ``scope ∩ report`` (pinned in
``frontend/src/__tests__/adAuditDeclaration.test.ts``), a scope holding
one campaign is a console holding one campaign.

The failure this guards against is not hypothetical. Live, a task asked
to create two campaigns for one product produced a report covering five
marketplaces — because a gate inferred "audit" from a heading and
demanded them — and the console offered every one of them as an
actionable bid decision.

Needs no advertiser account: the console is served from this process.
"""

import logging
import time

import pytest

from tests.e2e.conftest import BASE_URL
from tests.e2e.e2e_helpers import (
    create_store,
    create_task,
    poll_task_status,
)
from tests.e2e.fake_ads_console import (
    CAMPAIGN_A,
    CAMPAIGN_A_NAME,
    CAMPAIGN_B,
    CAMPAIGN_B_NAME,
    serve,
)

logger = logging.getLogger(__name__)
logging.getLogger('httpx').setLevel(logging.WARNING)

pytestmark = [pytest.mark.e2e]


@pytest.fixture(scope='module')
def ads_console():
    base, server = serve()
    yield base
    server.shutdown()
    server.server_close()


def _declarations(client, task_id: str) -> list[dict]:
    r = client.get(f'{BASE_URL}/api/tasks/{task_id}/ad-declarations')
    r.raise_for_status()
    return r.json()


def _scope_text(decl: dict) -> str:
    """Everything the scope names, flattened — for substring assertions."""
    scope = decl.get('scope') or {}
    parts = list(scope.get('campaigns') or [])
    parts += list(scope.get('products') or [])
    parts += [
        f'{c.get("platform")} {c.get("country")}'
        for c in scope.get('combos') or []
    ]
    return ' | '.join(str(p) for p in parts)


@pytest.mark.e2e
class TestScopeSurvivesAFollowUp:
    def test_a_narrow_follow_up_declares_a_narrow_audit(
        self, api_client, ads_console
    ):
        ts = int(time.time())
        store = create_store(api_client, f'e2e-adscope-{ts}')

        # ── Phase 1: a look, not an audit ────────────────────────────
        task = create_task(
            api_client,
            'How much did we spend on ads and what came back?',
            store_id=store['id'],
            description=(
                f'Our ad console is at {ads_console} — open it with the '
                'browser-use CLI and read the last-30-days summary. Just '
                'tell me the numbers.'
            ),
        )
        task_id = task['id']
        first = poll_task_status(api_client, task_id, {'completed', 'failed'})
        assert first['status'] == 'completed', (
            f'phase 1 failed: {first.get("error")}'
        )

        # ── Phase 2: same task, new turn, one campaign ───────────────
        r = api_client.post(
            f'{BASE_URL}/api/tasks/{task_id}/messages',
            json={
                'content': (
                    f'Now review the keyword bids on the "{CAMPAIGN_A_NAME}" '
                    f'campaign only ({CAMPAIGN_A}). Leave '
                    f'"{CAMPAIGN_B_NAME}" alone — I do not want to touch it '
                    'this week.'
                )
            },
        )
        r.raise_for_status()
        second = poll_task_status(
            api_client,
            task_id,
            {'completed', 'failed'},
            fail_statuses=set(),
        )
        assert second['status'] == 'completed', (
            f'phase 2 failed: {second.get("error")}'
        )

        decls = _declarations(api_client, task_id)
        assert decls, (
            'no declaration was recorded — the ad skill is gated on one, '
            'so a run that never declares is denied with no way forward'
        )
        logger.info('declarations: %s', decls)

        # The follow-up opened a NEW phase rather than editing the first.
        assert len(decls) >= 2, (
            f'expected a second declaration for the follow-up turn: {decls}'
        )
        assert [d['seq'] for d in decls] == sorted(d['seq'] for d in decls)

        review = decls[-1]
        assert review['kind'] == 'audit', (
            f'a bid review is an audit — it is what opens the console: {review}'
        )

        # ── The whole point: narrow in, narrow out ───────────────────
        named = _scope_text(review)
        assert CAMPAIGN_A in named or CAMPAIGN_A_NAME in named, (
            f'the campaign the user asked about is missing from the '
            f'declared scope, so the console would not show it: {review}'
        )
        assert CAMPAIGN_B not in named and CAMPAIGN_B_NAME not in named, (
            f'the campaign the user explicitly excluded is inside the '
            f'declared scope, so the console would offer it as an '
            f'actionable decision: {review}'
        )
        # A scope that named no campaigns would silently mean "every
        # campaign in these markets" — narrow-looking, but not narrow.
        assert (review.get('scope') or {}).get('campaigns'), (
            f'scope named no campaigns, which means ALL of them: {review}'
        )


@pytest.mark.e2e
class TestAnUnscopedRequestStaysWide:
    """The protection must not cost us the whole-store audit."""

    def test_reviewing_everything_declares_everything(
        self, api_client, ads_console
    ):
        ts = int(time.time())
        store = create_store(api_client, f'e2e-adwide-{ts}')
        task = create_task(
            api_client,
            'Review all our ad campaigns and tell me what to change',
            store_id=store['id'],
            description=(
                f'Our ad console is at {ads_console} — open it with the '
                'browser-use CLI, look at every campaign, and give me bid '
                'recommendations.'
            ),
        )
        result = poll_task_status(
            api_client, task['id'], {'completed', 'failed'}
        )
        assert result['status'] == 'completed', (
            f'task failed: {result.get("error")}'
        )

        decls = _declarations(api_client, task['id'])
        assert decls, 'an ad task must declare'
        review = decls[-1]
        assert review['kind'] == 'audit', review
        # Either spelling of "everything" is fine — no campaign list, or
        # a list naming both. What must NOT happen is one campaign
        # quietly standing in for the account.
        named = _scope_text(review)
        campaigns = (review.get('scope') or {}).get('campaigns') or []
        if campaigns:
            assert CAMPAIGN_A in named and CAMPAIGN_B in named, (
                f'an "audit everything" request narrowed to a subset: {review}'
            )
