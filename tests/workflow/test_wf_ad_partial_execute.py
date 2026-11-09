"""The reviewed-audit round trip: submit a slice, then hold it next time.

This is the journey a seller actually runs:

1. An audit produces a report covering several campaigns.
2. The user reviews the report, keeps ONE bid change, drops everything
   else, and leaves another target in the same ad group on hold.
3. Reply — the agent must receive exactly that one change, and be told
   what was deliberately excluded so "absent" is never read as "missing".
4. Execution records what it applied onto the campaign's TSV.
5. The NEXT audit must not adjust that target again: it was moved days
   ago and there is not a week of data behind it yet.

Steps 2-3 are the user typing in chat. What is pinned HERE is the
server half — that the follow-up reaches the task, and that the cooldown
gate turns the second audit's "lower it again" (a CHANGES.tsv row)
into a refusal with a reason a reader can act on.

The live-store version of this journey (drive a real ad account, raise a
real bid by 0.1, re-audit a week later) cannot run in CI: it spends money
on a production advertising account and takes days of wall-clock. It is a
manual runbook, not a test — see the docstring at the bottom.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.ai.stop_gates import ad_tables
from app.ai.stop_gates.ad_changes import rule_violations
from app.ai.stop_gates.ad_rules import DEFAULT_RULES, resolve_rules
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow

SLUG = 'acme'
_TSV_HEADER = '\t'.join(ad_tables.TARGETING_COLUMNS) + '\n'


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(ad_tables, 'VIBE_SELLER_DIR', tmp_path)
    return tmp_path


def _write_history(home, *, target, action, days_ago, bid, prev):
    """A campaign TSV carrying an APPLIED change, as execution leaves it."""
    day = (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat()
    d = home / 'store-data' / SLUG / 'ads' / 'amazon' / 'SA'
    d.mkdir(parents=True, exist_ok=True)
    (d / '100000000001.tsv').write_text(
        _TSV_HEADER
        + (
            f'acme group\t{target}\tExact\tenabled\t{bid}\tSAR\t40\t80.00\t'
            f'4\t400.00\t20\t5.00\t{action}\t{day}\t{prev}\n'
        ),
        encoding='utf-8',
    )


def _change(target, action, target_bid='', reason=''):
    """One CHANGES.tsv row of the next audit."""
    return {
        'platform': 'amazon',
        'country': 'SA',
        'campaign_id': '100000000001',
        'ad_group': 'acme group',
        'target': target,
        'match_type': 'Exact',
        'action': action,
        'current_bid': '2.10',
        'target_bid': target_bid,
        'acos': '35',
        'roas': '2.9',
        'cpc': '1.00',
        'reason': reason,
    }


class TestFollowUpReachesTheTask:
    async def test_submitting_decisions_resumes_the_same_task(
        self, admin_client, install_fake_agent
    ):
        """The decisions land on the audit's own task, not a new one.

        That is what lets execution reuse the drilled data and cached
        TSVs instead of re-deriving a report it just produced.
        """
        r = await admin_client.post('/api/stores', json={'name': 'acme'})
        store_id = r.json()['id']
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'Audit the ads',
                'description': 'Review 30 days of ad performance',
                'store_id': store_id,
            },
        )
        task_id = r.json()['id']
        await wait_for_task(admin_client, task_id)

        # The human-reviewed decisions arrive as a follow-up message.
        follow_up = '广告审计已人工复核完毕。**只执行下面 JSON 里的决定**，其余一律不动。'
        r = await admin_client.post(
            f'/api/tasks/{task_id}/messages', json={'content': follow_up}
        )
        assert r.status_code == 200

        r = await admin_client.get(f'/api/tasks/{task_id}/messages')
        contents = [m['content'] for m in r.json()]
        assert any('只执行下面 JSON 里的决定' in c for c in contents), (
            'the reviewed decisions must reach the audit task itself'
        )


class TestCooldownOnTheNextAudit:
    """The second half: a target moved recently must come back as hold."""

    def test_a_fresh_change_blocks_another_move(self, home):
        # Execution raised this bid two days ago (2.00 -> 2.10).
        _write_history(
            home,
            target='widget red',
            action='raise',
            days_ago=2,
            bid='2.10',
            prev='2.00',
        )
        # The next audit wants to walk it back down.
        found = rule_violations(
            (_change('widget red', 'lower', '1.80'),), DEFAULT_RULES, SLUG
        )
        assert len(found) == 1
        # The refusal has to tell the agent what to write instead.
        assert 'hold' in found[0]
        assert 'cooldown' in found[0]
        assert 'widget red' in found[0]

    def test_the_hold_we_want_passes(self, home):
        _write_history(
            home,
            target='widget red',
            action='raise',
            days_ago=2,
            bid='2.10',
            prev='2.00',
        )
        hold = _change(
            'widget red', 'hold', reason='raised 2.00 -> 2.10 two days ago'
        )
        assert rule_violations((hold,), DEFAULT_RULES, SLUG) == []

    def test_a_target_we_did_not_touch_is_still_tunable(self, home):
        """The cooldown must not freeze the whole campaign."""
        _write_history(
            home,
            target='widget red',
            action='raise',
            days_ago=2,
            bid='2.10',
            prev='2.00',
        )
        pause = _change('widget blue', 'pause', reason='20 clicks, 0 orders')
        assert rule_violations((pause,), DEFAULT_RULES, SLUG) == []

    def test_once_the_window_passes_it_is_tunable_again(self, home):
        _write_history(
            home,
            target='widget red',
            action='raise',
            days_ago=9,
            bid='2.10',
            prev='2.00',
        )
        lower = _change('widget red', 'lower', '1.80')
        assert rule_violations((lower,), DEFAULT_RULES, SLUG) == []

    def test_a_seller_who_tunes_daily_can_shorten_the_window(self, home):
        """Cadence varies; the window is a per-store rule, not a constant."""
        _write_history(
            home,
            target='widget red',
            action='raise',
            days_ago=2,
            bid='2.10',
            prev='2.00',
        )
        lower = (_change('widget red', 'lower', '1.80'),)
        assert rule_violations(lower, DEFAULT_RULES, SLUG)
        fast = resolve_rules('change_cooldown_days: 1')
        assert rule_violations(lower, fast, SLUG) == []


# ── Live-store runbook (NOT a test) ──────────────────────────────────
#
# The journey above, run against a real advertising account, is a manual
# procedure. It is not in CI because it spends real money and needs days
# of wall-clock between the two halves:
#
#   1. Run the weekly ad-audit task for the store.
#   2. Open the console at /stores/<id>/tasks/<id>/audit.
#   3. Keep exactly one target, raise its bid by 0.1. Leave another
#      target in the SAME ad group on hold. Drop every other campaign.
#   4. Submit. Confirm the follow-up names one campaign and lists the
#      dropped ones as deliberate exclusions.
#   5. Confirm execution wrote applied_action / applied_at /
#      previous_bid onto that row of the campaign's TSV, and nothing
#      onto the untouched row.
#   6. Re-run the audit inside the cooldown window. The CHANGES.tsv row
#      for the raised target must be hold naming the change and its age;
#      the untouched target is judged on its data as usual.
