"""A real agent, a fake ad console, and one question: did the scope hold?

This closes the loop that unit tests cannot. The scoping contract has two
halves — the agent must keep to what it was asked for, and the report
must be bounded by what it declared — and only the first half depends on
a model reading a natural-language request. So this drives a real agent
against a stand-in ad console carrying TWO campaigns and asks about ONE.

The journey mirrors the one that produced the design:

1. "how much did we spend, what came back" → a look, not an audit.
2. Same task, new user turn: "review the bids on <one campaign>, leave
   the other alone" → a review, scoped to that campaign.

**What is asserted, and why that split.** The load-bearing assertion is
the console's own access log: once the agent has committed to a scope
(its declaration answering the follow-up — or, if it never declares, from
the follow-up on), the excluded campaign's detail page must never be
fetched. That is ground truth — a report can claim restraint it
did not exercise, but the server records what it was actually asked for.
An access-log assertion only carries meaning if the request *entails* the
fetch, so both tests ask for keyword bids, which exist nowhere but on a
campaign's own page. ``tests/unit/test_fake_ads_console.py`` pins that.

The ``vibe_seller_declare_ad_task`` call is checked only *if the agent
made one*, because the design does not promise one here. The declaration
requirement is carried by the ad skills, and the gate that refuses a
report lacking one lives in that same skill bundle. The store is an
Amazon US store (``StoreCreate`` defaults), so a model MAY load
``amazon-ads`` for a bid review — and then its completeness check holds
the report to a real audit, which is why the stub's figures must be ones
a real console could show (see ``fake_ads_console``; it deliberately
does not brand itself as Amazon, which invited a full audit for a
lookup). It may equally answer straight off the page without the skill. Observed in CI on ``glm-4.7``: a
correctly scoped review that named the asked-about campaign, explicitly
said it had not opened the other, and declared nothing. That is the
fail-safe direction — no declaration means no obligation is inferred, so
nothing over-wide is ever demanded — and failing the run for it would be
pinning a model's habits, not a contract. What must never happen is a
declaration WIDER than the request, and that is asserted whenever one
exists.

The bounding of a report by its declaration (an out-of-scope marketplace
is a gap) is deterministic Python, pinned separately in
``tests/unit/test_ad_declaration.py``. Do NOT read a pass here as
evidence those gates ran — they ride on the ad skills, and this test's
agent may never load one.

The failure this guards against is not hypothetical. Live, a task asked
to create two campaigns for one product produced a report covering five
marketplaces — because a gate inferred "audit" from a heading and
demanded them — and every one of them was offered up as an
actionable bid decision.

Needs no advertiser account: the console is served from this process.
"""

from datetime import datetime
import logging
import time

import pytest

from app.ai.stop_gates.ad_tables import CHANGE_COLUMNS, CHANGES_FILE, read_table
from app.config import VIBE_SELLER_DIR
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


# The business facts a bid review turns on (margin, goal) come from
# the user, as they would from a real seller. Left out, the agent asks,
# and the session auto-answer picks whichever option the MODEL listed
# first: a run that listed "30%" first got an over-break-even campaign,
# trims, a negation and a cross-SKU question; one that listed "40%" got
# a modest trim. Same test, 5x the reasoning, and a timeout. Stating
# the facts makes the job this test measures the same job every run.
SELLER_FACTS = (
    'For context: we make about 40% margin on everything we sell, and '
    'the goal right now is to grow sales, not to cut spend.'
)


@pytest.mark.e2e
class TestScopeSurvivesAFollowUp:
    @pytest.mark.long_budget(602)  # measured p50 over CI 201j + baseline
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
        # The exclusion starts HERE. Phase 1 ("how much did we spend")
        # excluded nothing, and an agent that broke the totals down by
        # campaign there opened both pages -- observed in CI, where the
        # whole-test log then failed a phase 2 that never touched the
        # excluded campaign. The scope assertion is about this turn.
        phase2_start = ads_console.mark()
        r = api_client.post(
            f'{BASE_URL}/api/tasks/{task_id}/messages',
            json={
                'content': (
                    f'Now review the keyword bids on the "{CAMPAIGN_A_NAME}" '
                    f'campaign only ({CAMPAIGN_A}). Leave '
                    f'"{CAMPAIGN_B_NAME}" alone — I do not want to touch it '
                    f'this week. {SELLER_FACTS}'
                )
            },
        )
        r.raise_for_status()
        # A bid review is an audit when the model follows the store's
        # ``amazon-ads`` skill — drill, TSVs, a report the completeness
        # check accepts — and that is the slower job, same as the wide
        # test below: observed at ~10 min on MiniMax for one campaign.
        # The answer-off-the-page path still finishes in about a minute.
        # The budget is for the honest job, not for a gate: the review
        # gate's re-drives are bounded by wall clock at the source
        # (``app/ai/turn_hold_budget.py``), and a follow-up on a completed
        # task is not reviewed at all.
        second = poll_task_status(
            api_client,
            task_id,
            {'completed', 'failed'},
            fail_statuses=set(),
            timeout=PIPELINE_TIMEOUT * 2,
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
        # The exclusion binds from the moment the agent COMMITTED to a
        # scope: its first declaration answering this turn. Before that
        # it may still be finding its way around the console — in CI an
        # agent probed every page for edit forms (one read of the
        # excluded page, nothing used, nothing written), then declared
        # just the asked-about campaign and kept to it. Opening the
        # excluded page AFTER declaring is the breach. An agent that never
        # declares never commits, so it is held to the whole turn.
        committed = [
            d for d in _declarations(api_client, task_id) if d['user_turn'] >= 2
        ]
        since = phase2_start
        if committed:
            declared_at = datetime.fromisoformat(committed[0]['created_at'])
            since = max(since, ads_console.mark_at(declared_at.timestamp()))
        assert not ads_console.fetched_campaign(CAMPAIGN_B, since=since), (
            f'the campaign the user explicitly excluded was opened after '
            f'{"the agent declared its scope" if committed else "the narrowing follow-up"}'
            f' — it did not bound the work: {served[since:]}'
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
        # fail-safe (no declaration → no widening obligation); WIDER
        # than asked is the regression this whole change exists to
        # prevent.
        decls = _declarations(api_client, task_id)
        if not decls:
            logger.info(
                'no declaration recorded. Scope was still held — '
                'verified against the ad-console access log.'
            )
            return
        logger.info('declarations: %s', decls)

        assert [d['seq'] for d in decls] == sorted(d['seq'] for d in decls)
        review = decls[-1]
        # The kind is held to what the phase delivered — the one rule the
        # server enforces about it (``ad_changes``). `audit` is the
        # expected reading of "review the bids", but a review that only
        # lays out the figures and recommends nothing is an honest
        # `investigate`. Observed in CI on MiniMax: exactly that, and the
        # test failed it for a choice the contract allows. What must not
        # happen is `investigate` handing out decisions.
        assert review['kind'] in ('audit', 'investigate'), review
        if review['kind'] == 'investigate':
            # Decisions live in CHANGES.tsv (the report prose is for
            # people); anything but `hold` there is a decision.
            changes = read_table(
                VIBE_SELLER_DIR / 'tasks' / task_id / CHANGES_FILE,
                CHANGE_COLUMNS,
            )
            moves = [
                r
                for r in (changes.rows if changes else ())
                if r['action'].lower() != 'hold'
            ]
            assert not moves, (
                f'declared investigate yet proposed {len(moves)} change(s); '
                f'decisions are an audit: {review}\n{moves}'
            )
        assert review['user_turn'] >= 2, (
            f'the review declaration was made on the opening turn, so it '
            f'cannot be a response to the narrowing follow-up: {decls}'
        )

        named = _scope_text(review)
        assert CAMPAIGN_A in named or CAMPAIGN_A_NAME in named, (
            f'the campaign the user asked about is missing from the '
            f'declared scope, so a report covering it would trip the '
            f'out-of-scope gap: {review}'
        )
        assert CAMPAIGN_B not in named and CAMPAIGN_B_NAME not in named, (
            f'the campaign the user explicitly excluded is inside the '
            f'declared scope — the scope is wider than asked: {review}'
        )
        # A scope that named no campaigns would silently mean "every
        # campaign in these markets" — narrow-looking, but not narrow.
        assert (review.get('scope') or {}).get('campaigns'), (
            f'scope named no campaigns, which means ALL of them: {review}'
        )
        # A declaration belongs to the phase whose work needs it. An
        # audit declared BEFORE turn 1's answer would cover the revenue
        # phase as well — one audit declaration spanning two phases.
        assert review['created_at'] > results[0]['created_at'], (
            "the audit declaration must post-date the first turn's "
            'answer, or it covers that answer too: '
            f'declared {review["created_at"]}, first result '
            f'{results[0]["created_at"]}'
        )


@pytest.mark.e2e
class TestAnUnscopedRequestStaysWide:
    """The protection must not cost us the whole-store audit."""

    @pytest.mark.long_budget(530)  # measured p50 over CI 201j + baseline
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
                'me a keyword-by-keyword bid recommendation for each one. '
                f'{SELLER_FACTS}'
            ),
        )
        # An unscoped audit is inherently the slower job — it drills
        # every campaign rather than a named few — so it needs more than
        # the shared pipeline budget.
        #
        # This override used to be doing a second, illegitimate job:
        # absorbing an UNBOUNDED turn hold. The hold's re-drive budget
        # was counted in attempts (5), each a full agent turn, so at
        # 100-150s/turn it could spend 500-750s on its own and push any
        # run past any deadline — the scoped test below hit the same
        # cliff once, which is how the real cause was found. That is
        # fixed at the source (``app/ai/turn_hold_budget.py`` bounds the
        # hold by wall clock), so what is left here is only the honest
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
            logger.info('no declaration recorded')
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
