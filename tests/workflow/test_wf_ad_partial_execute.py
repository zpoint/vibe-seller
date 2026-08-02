"""The reviewed-audit round trip: submit a slice, then hold it next time.

This is the journey a seller actually runs:

1. An audit produces a report covering several campaigns.
2. In the console the reviewer keeps ONE bid change, drops everything
   else, and leaves another target in the same ad group on 维持.
3. Submit — the agent must receive exactly that one change, and be told
   what was deliberately excluded so "absent" is never read as "missing".
4. Execution records what it applied onto the campaign's TSV.
5. The NEXT audit must not adjust that target again: it was moved days
   ago and there is not a week of data behind it yet.

Steps 2-3 are covered end-to-end in the frontend suite (the console
builds the submission, the handler posts it). What is pinned HERE is the
server half — that the follow-up reaches the task, and that the cooldown
gate turns the second report's "lower it again" into a refusal with a
reason a reader can act on.

The live-store version of this journey (drive a real ad account, raise a
real bid by 0.1, re-audit a week later) cannot run in CI: it spends money
on a production advertising account and takes days of wall-clock. It is a
manual runbook, not a test — see the docstring at the bottom.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.ai.stop_gates import ad_change_cooldown as cooldown
from app.ai.stop_gates.ad_rules import DEFAULT_RULES, resolve_rules
from tests.workflow.conftest import wait_for_task

pytestmark = pytest.mark.workflow

_TSV_HEADER = (
    'ad_group\ttarget\tmatch_type\tstate\tbid\tcurrency\tclicks\tspend\t'
    'orders\tsales\tacos\troas\tsuggestion\tapplied_action\tapplied_at\t'
    'previous_bid\n'
)


def _write_history(ads_root, *, target, action, days_ago, bid, prev):
    """A campaign TSV carrying an APPLIED change, as execution leaves it."""
    day = (datetime.now(UTC).date() - timedelta(days=days_ago)).isoformat()
    d = ads_root / 'amazon' / 'SA'
    d.mkdir(parents=True, exist_ok=True)
    (d / '100000000001.tsv').write_text(
        _TSV_HEADER
        + (
            f'acme group\t{target}\tExact\tenabled\t{bid}\tSAR\t40\t80.00\t'
            f'4\t400.00\t20%\t5.00\t维持\t{action}\t{day}\t{prev}\n'
        ),
        encoding='utf-8',
    )


def _report(rows: str) -> str:
    return (
        '# 广告优化建议 — acme — 2026-08-10\n\n## amazon SA\n'
        '**进度**: drilled 1/1 active (1 total, 1 pages)\n\n'
        '| 定向词 | 匹配 | 出价 | 点击 | 花费 | 订单 | ROAS | 建议 |\n'
        '|---|---|---|---|---|---|---|---|\n' + rows
    )


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

        # The console posts the reviewed decisions as a follow-up.
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
    """The second half: a target moved recently must come back as 维持."""

    def test_a_fresh_change_blocks_another_move(self, tmp_path):
        # Execution raised this bid two days ago (2.00 -> 2.10).
        _write_history(
            tmp_path,
            target='widget red',
            action='提高出价',
            days_ago=2,
            bid='2.10',
            prev='2.00',
        )
        # The next audit wants to walk it back down.
        report = _report(
            '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | '
            '下调至 1.80（ROAS 偏低） |\n'
        )
        deny = cooldown.check(report, ads_root=tmp_path)
        assert deny is not None
        # The refusal has to tell the agent what to write instead.
        assert '维持' in deny.reason
        assert '冷却期' in deny.reason
        assert 'widget red' in deny.reason

    def test_the_hold_we_want_passes(self, tmp_path):
        _write_history(
            tmp_path,
            target='widget red',
            action='提高出价',
            days_ago=2,
            bid='2.10',
            prev='2.00',
        )
        report = _report(
            '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | '
            '维持（2 天前刚从 2.00 提到 2.10，冷却期未满，等满一周数据再判断） |\n'
        )
        assert cooldown.check(report, ads_root=tmp_path) is None

    def test_a_target_we_did_not_touch_is_still_tunable(self, tmp_path):
        """The cooldown must not freeze the whole campaign."""
        _write_history(
            tmp_path,
            target='widget red',
            action='提高出价',
            days_ago=2,
            bid='2.10',
            prev='2.00',
        )
        report = _report(
            '| widget blue | Exact | 1.00 | 20 | 30.00 | 0 | — | '
            '暂停（20 点击零单） |\n'
        )
        assert cooldown.check(report, ads_root=tmp_path) is None

    def test_once_the_window_passes_it_is_tunable_again(self, tmp_path):
        _write_history(
            tmp_path,
            target='widget red',
            action='提高出价',
            days_ago=9,
            bid='2.10',
            prev='2.00',
        )
        report = _report(
            '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | '
            '下调至 1.80 |\n'
        )
        assert cooldown.check(report, ads_root=tmp_path) is None

    def test_a_seller_who_tunes_daily_can_shorten_the_window(self, tmp_path):
        """Cadence varies; the window is a per-store rule, not a constant."""
        _write_history(
            tmp_path,
            target='widget red',
            action='提高出价',
            days_ago=2,
            bid='2.10',
            prev='2.00',
        )
        report = _report(
            '| widget red | Exact | 2.10 | 40 | 80.00 | 4 | 5.00 | '
            '下调至 1.80 |\n'
        )
        assert cooldown.check(report, None, DEFAULT_RULES, ads_root=tmp_path)
        fast = resolve_rules('change_cooldown_days: 1')
        assert cooldown.check(report, None, fast, ads_root=tmp_path) is None


# ── Live-store runbook (NOT a test) ──────────────────────────────────
#
# The journey above, run against a real advertising account, is a manual
# procedure. It is not in CI because it spends real money and needs days
# of wall-clock between the two halves:
#
#   1. Run the weekly ad-audit task for the store.
#   2. Open the console at /stores/<id>/tasks/<id>/audit.
#   3. Keep exactly one target, raise its bid by 0.1. Leave another
#      target in the SAME ad group on 维持. Drop every other campaign.
#   4. Submit. Confirm the follow-up names one campaign and lists the
#      dropped ones as deliberate exclusions.
#   5. Confirm execution wrote applied_action / applied_at /
#      previous_bid onto that row of the campaign's TSV, and nothing
#      onto the untouched row.
#   6. Re-run the audit inside the cooldown window. The recommendation
#      for the raised target must be 维持 naming the change and its age;
#      the untouched target is judged on its data as usual.
