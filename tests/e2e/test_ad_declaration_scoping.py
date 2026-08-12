"""A real agent, a fake ad console, and one question: did the scope hold?

This closes the loop that unit tests cannot. The scoping contract has two
halves — the agent must keep to what it was asked for, and the console
must be bounded by what it declared — and only the first half depends on
a model reading a natural-language request. So this drives a real agent
against a stand-in ad console carrying TWO campaigns and asks about ONE.

The journey mirrors the one that produced the design:

1. "how much did we spend, what came back" → a look, not an audit.
2. Same task, new user turn: "review the bids on <one campaign>, leave
   the other alone" → a review, scoped to that campaign.

**What is asserted, and why that split.** The load-bearing assertion is
the console's own access log: the excluded campaign's detail page must
never be fetched. That is ground truth — a report can claim restraint it
did not exercise, but the server records what it was actually asked for.
An access-log assertion only carries meaning if the request *entails* the
fetch, so both tests ask for keyword bids, which exist nowhere but on a
campaign's own page. ``tests/unit/test_fake_ads_console.py`` pins that.

The ``vibe_seller_declare_ad_task`` call is checked only *if the agent
made one*, because the design does not promise one here. The declaration
requirement is carried by the ad skills, and the gate that refuses a
report lacking one lives in that same skill bundle and only bites a
report presenting per-marketplace coverage. A localhost stub is neither
Amazon nor noon, so the skill need not load and the report carries no
combo sections. Observed in CI on ``glm-4.7``: a correctly scoped review
that named the asked-about campaign, explicitly said it had not opened
the other, and declared nothing. That is the fail-safe direction — no
declaration means no console, so nothing over-wide is ever offered — and
failing the run for it would be pinning a model's habits, not a
contract. What must never happen is a declaration WIDER than the
request, and that is asserted whenever one exists.

The console's own filtering (``scope ∩ report``) is deterministic
TypeScript, pinned separately in
``frontend/src/__tests__/adAuditDeclaration.test.ts`` and verified in a
real browser against a multi-campaign report. Do NOT read a pass here as
evidence the console rendered anything.

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
    PIPELINE_TIMEOUT,
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


@pytest.fixture
def ads_console():
    """A fresh console per test — the access log IS the assertion.

    Deliberately not module-scoped: the wide test opens both campaigns,
    and sharing one server would leave that visit in the log for the
    scoped test to trip over (whenever xdist lands them on the same
    worker, so it would fail only sometimes). A stdlib HTTP server on an
    ephemeral port is cheap; a cross-test-contaminated assertion is not.
    """
    console = serve()
    yield console
    console.shutdown()
    console.server_close()


def _declarations(client, task_id: str) -> list[dict]:
    r = client.get(f'{BASE_URL}/api/tasks/{task_id}/ad-declarations')
    r.raise_for_status()
    return r.json()


def _messages(client, task_id: str) -> list[dict]:
    r = client.get(f'{BASE_URL}/api/tasks/{task_id}/messages')
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
    def test_a_narrow_follow_up_reviews_only_what_was_asked(
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
                f'Our ad console is at {ads_console.base} — open it with '
                'the browser-use CLI and read the last-30-days summary. '
                'Just tell me the numbers.'
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

        # ── The whole point: narrow in, narrow out ───────────────────
        #
        # Ground truth, straight off the console: which campaign pages
        # were actually opened. Independent of how the report is worded
        # and of whether any skill happened to load.
        served = ads_console.served()
        assert ads_console.fetched_campaign(CAMPAIGN_A), (
            f'the campaign the user asked about was never opened, so the '
            f'review cannot have been grounded in its data: {served}'
        )
        assert not ads_console.fetched_campaign(CAMPAIGN_B), (
            f'the campaign the user explicitly excluded was opened — the '
            f'narrowing follow-up did not bound the work: {served}'
        )

        # ── Each turn leaves its own answer ──────────────────────────
        #
        # The rebuild used to promote only the FIRST result, so turn 2's
        # answer took turn 1's slot and rendered above the question that
        # asked for it. A unit test cannot see this; two real turns can.
        msgs = _messages(api_client, task_id)
        results = [m for m in msgs if m['role'] == 'result']
        assert len(results) >= 2, (
            f'each turn must leave its own result; got {len(results)}: '
            f'{[m["role"] for m in msgs]}'
        )

        # ── If it declared, the declaration must be narrow ───────────
        #
        # Conditional by design — see the module docstring. Absent is
        # fail-safe (no declaration → no console); WIDER than asked is
        # the regression this whole change exists to prevent.
        decls = _declarations(api_client, task_id)
        if not decls:
            logger.info(
                'no declaration recorded; console stays closed. Scope was '
                'still held — verified against the console access log.'
            )
            return
        logger.info('declarations: %s', decls)

        assert [d['seq'] for d in decls] == sorted(d['seq'] for d in decls)
        review = decls[-1]
        assert review['kind'] == 'audit', (
            f'a bid review is an audit — it is what opens the console: {review}'
        )
        assert review['user_turn'] >= 2, (
            f'the review declaration was made on the opening turn, so it '
            f'cannot be a response to the narrowing follow-up: {decls}'
        )

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
        # The console binds each result to the declaration in force when
        # that result landed. An audit declared BEFORE turn 1's answer
        # would attach the console to the revenue answer as well, showing
        # two consoles for one review.
        assert review['created_at'] > results[0]['created_at'], (
            "the audit declaration must post-date the first turn's "
            'answer, or the console binds to that answer too: '
            f'declared {review["created_at"]}, first result '
            f'{results[0]["created_at"]}'
        )


@pytest.mark.e2e
class TestAnUnscopedRequestStaysWide:
    """The protection must not cost us the whole-store audit."""

    def test_reviewing_everything_looks_at_everything(
        self, api_client, ads_console
    ):
        ts = int(time.time())
        store = create_store(api_client, f'e2e-adwide-{ts}')
        # Ask for what only the campaign PAGES hold. The assertion below
        # is the console access log — "was each campaign's detail page
        # opened?" — so the request has to be one that cannot be answered
        # without opening them, or the test measures browsing habits
        # instead of scope.
        #
        # It did, once. "Look at every campaign and give me bid
        # recommendations" is answerable straight off /campaigns, which
        # already carries per-campaign spend/revenue/orders/ROAS. Opening
        # a campaign was then optional work, and whether the agent
        # bothered was a coin flip: the same model passed one CI run and
        # failed the next, both times having read BOTH campaigns off the
        # list — never the subset-clip this test exists to catch. Bids
        # live ONLY in each campaign's keyword table, so asking for them
        # makes the click load-bearing, exactly as the scoped test above
        # does with its one campaign. Keep it that way: if you reword
        # this, check the new wording is unanswerable from the list.
        task = create_task(
            api_client,
            'Review all our ad campaigns and tell me what to change',
            store_id=store['id'],
            description=(
                f'Our ad console is at {ads_console.base} — open it with '
                'the browser-use CLI, go through every campaign, and give '
                'me a keyword-by-keyword bid recommendation for each one.'
            ),
        )
        # An unscoped audit is inherently the slower job — it drills
        # every campaign rather than a named few — so it needs more than
        # the shared pipeline budget.
        #
        # This override used to be doing a second, illegitimate job:
        # absorbing an UNBOUNDED review gate. The gate's re-drive budget
        # was counted in attempts (5), each a full agent turn, so at
        # 100-150s/turn it could spend 500-750s on its own and push any
        # run past any deadline — the scoped test below hit the same
        # cliff once, which is how the real cause was found. That is
        # fixed at the source (``app/ai/review_redrive.py`` bounds the
        # gate by wall clock), so what is left here is only the honest
        # statement that drilling every campaign takes longer than
        # drilling one. If this needs doubling AGAIN, do not: something
        # has become unbounded, and the budget is the place to look.
        result = poll_task_status(
            api_client,
            task['id'],
            {'completed', 'failed'},
            timeout=PIPELINE_TIMEOUT * 2,
        )
        assert result['status'] == 'completed', (
            f'task failed: {result.get("error")}'
        )

        # "Every campaign" means every campaign. The narrowing machinery
        # must not quietly clip an unscoped request down to a subset.
        served = ads_console.served()
        assert ads_console.fetched_campaign(CAMPAIGN_A), (
            f'an "audit everything" request never opened {CAMPAIGN_A}: {served}'
        )
        assert ads_console.fetched_campaign(CAMPAIGN_B), (
            f'an "audit everything" request never opened {CAMPAIGN_B} — '
            f'one campaign stood in for the whole account: {served}'
        )

        decls = _declarations(api_client, task['id'])
        if not decls:
            logger.info('no declaration recorded; console stays closed')
            return
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
