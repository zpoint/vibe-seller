"""``set_task_result`` enforces the ads reviewer, not just the Stop hook.

The all-ads slip-through: a backend that finishes by calling
``vibe_seller_set_task_result`` (rather than emitting a Stop event) used
to complete an ads report having run only the deterministic coverage
floor — the active ``ads-report-review`` reviewer was gated ONLY in the
Stop hook, so it was never required on this path. A shallow-but-covering
report sailed through.

These tests pin the fix: for a task BOUND to an ads skill that produced
an ``AD_AUDIT_*.md``, ``POST /api/tasks/{id}/result`` is denied until a
``*REVIEW*.md`` with ``Status: ok`` exists, and after the bounded
stall-cap it fails OPEN but marks the result UNVERIFIED (never a silent
"done"). A task with no ads-skill binding is unaffected.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

import app.ai.stop_gates as sg
from app.ai.stop_gates import record_skill_load, report_reviewer, reset_attempts
from app.models.task import Task
import app.routers.tasks as tasks_router
from app.task_states import TaskStatus

pytestmark = pytest.mark.workflow


async def _seed_running_task(db_maker, store_id=None) -> str:
    async with db_maker() as db:
        task = Task(
            title='review ads',
            created_by='00000000-0000-0000-0000-000000000001',
            status=TaskStatus.RUNNING,
            priority=1,
            plan_mode=False,
            store_id=store_id,
            created_at=datetime.now(UTC).isoformat(),
            updated_at=datetime.now(UTC).isoformat(),
        )
        db.add(task)
        await db.commit()
        return task.id


def _wire(monkeypatch, tmp_path, task_id, *, skill='amazon-ads'):
    """Point the endpoint at an isolated workspace + bind an ads skill."""
    # task_root = VIBE_SELLER_DIR / 'tasks' / task_id — isolate it.
    monkeypatch.setattr(tasks_router, 'VIBE_SELLER_DIR', tmp_path)
    monkeypatch.setattr(sg, 'GATE_BINDINGS_DIR', tmp_path / 'gate_bindings')
    # The session declared no skill gates → the floor doesn't run here;
    # this test targets the reviewer block specifically. The durable
    # binding is what the reviewer trigger keys on.
    monkeypatch.setattr(
        tasks_router.agent_manager,
        'loaded_skills_and_workspace',
        lambda tid: (frozenset(), None),
        raising=False,
    )
    if skill:
        record_skill_load(task_id, skill)
    task_dir = tmp_path / 'tasks' / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    return task_dir


class TestSetResultReviewerGate:
    async def test_denied_without_reviewer(
        self, admin_client, override_async_session, monkeypatch, tmp_path
    ):
        task_id = await _seed_running_task(override_async_session)
        task_dir = _wire(monkeypatch, tmp_path, task_id)
        (task_dir / 'AD_AUDIT_2026-07-09.md').write_text(
            '# 广告优化建议\n\n## Amazon SA\n报告内容\n', encoding='utf-8'
        )
        reset_attempts(task_id)

        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': './AD_AUDIT_2026-07-09.md'},
        )
        assert r.status_code == 400
        assert 'ads-report-review' in r.json()['detail']

    async def test_passes_with_reviewer_ok(
        self, admin_client, override_async_session, monkeypatch, tmp_path
    ):
        task_id = await _seed_running_task(override_async_session)
        task_dir = _wire(monkeypatch, tmp_path, task_id)
        (task_dir / 'AD_AUDIT_2026-07-09.md').write_text(
            '# 广告优化建议\n\n## Amazon SA\n报告内容\n', encoding='utf-8'
        )
        (task_dir / 'REVIEW_2026-07-09_iter1.md').write_text(
            '# Review\nStatus: ok\n', encoding='utf-8'
        )
        reset_attempts(task_id)

        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': './AD_AUDIT_2026-07-09.md'},
        )
        assert r.status_code == 200

        async with override_async_session() as db:
            task = (
                await db.execute(select(Task).where(Task.id == task_id))
            ).scalar_one()
        assert '广告优化建议' in task.result
        assert 'UNVERIFIED' not in task.result

    async def test_fails_open_marks_unverified_after_stall(
        self, admin_client, override_async_session, monkeypatch, tmp_path
    ):
        task_id = await _seed_running_task(override_async_session)
        task_dir = _wire(monkeypatch, tmp_path, task_id)
        (task_dir / 'AD_AUDIT_2026-07-09.md').write_text(
            '# 广告优化建议\n\n## Amazon SA\n报告内容\n', encoding='utf-8'
        )
        reset_attempts(task_id)
        # Exhaust the stall cap: every attempt lacks a passing reviewer.
        for _ in range(report_reviewer.REVIEWER_STALL_CAP):
            r = await admin_client.post(
                f'/api/tasks/{task_id}/result',
                json={'result': './AD_AUDIT_2026-07-09.md'},
            )
            assert r.status_code == 400

        # Past the cap → fails open, but the persisted result is banner-
        # marked UNVERIFIED rather than silently accepted.
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': './AD_AUDIT_2026-07-09.md'},
        )
        assert r.status_code == 200
        async with override_async_session() as db:
            task = (
                await db.execute(select(Task).where(Task.id == task_id))
            ).scalar_one()
        assert 'UNVERIFIED' in task.result

    async def test_ad_skill_lookup_requires_reviewer_then_passes(
        self, admin_client, override_async_session, monkeypatch, tmp_path
    ):
        # Always-require: even a lookup (ads skill bound, no AD_AUDIT
        # report) must route to the reviewer — the server never
        # pre-judges lookup vs report. First attempt is denied (reviewer
        # never ran); once the reviewer signs off fast ("nothing to
        # verify" → Status: ok), the result is accepted.
        task_id = await _seed_running_task(override_async_session)
        task_dir = _wire(monkeypatch, tmp_path, task_id)  # binds amazon-ads
        reset_attempts(task_id)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': 'Current ACOS is 25 percent; no action needed.'},
        )
        assert r.status_code == 400
        assert 'ads-report-review' in r.json()['detail']

        # Reviewer runs and signs off fast — nothing substantive to check.
        (task_dir / 'REVIEW_2026-07-09_iter1.md').write_text(
            '# Review\nStatus: ok\nNo report to verify — informational.\n',
            encoding='utf-8',
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': 'Current ACOS is 25 percent; no action needed.'},
        )
        assert r.status_code == 200
        async with override_async_session() as db:
            task = (
                await db.execute(select(Task).where(Task.id == task_id))
            ).scalar_one()
        assert 'UNVERIFIED' not in task.result
        assert 'ACOS' in task.result

    async def test_general_review_skill_gated_at_set_result(
        self, admin_client, override_async_session, monkeypatch, tmp_path
    ):
        # Phase 2: a non-ad skill declaring a review: block is gated on the
        # set_task_result path too (not just the Stop hook).
        task_id = await _seed_running_task(override_async_session)
        task_dir = _wire(monkeypatch, tmp_path, task_id, skill='amazon-listing')
        skdir = task_dir / '.claude' / 'skills' / 'amazon-listing'
        skdir.mkdir(parents=True, exist_ok=True)
        (skdir / 'SKILL.md').write_text(
            '---\nname: amazon-listing\nreview:\n'
            '  criteria: |\n    - Every SKU is live.\n---\n\n# body\n',
            encoding='utf-8',
        )
        reset_attempts(task_id)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': 'Created SKU WIDGET-TEST-1; see report.'},
        )
        assert r.status_code == 400
        assert 'ads-report-review' in r.json()['detail']

        (task_dir / 'REVIEW_2026-07-09_iter1.md').write_text(
            '# Review\nStatus: ok\n', encoding='utf-8'
        )
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': 'Created SKU WIDGET-TEST-1; see report.'},
        )
        assert r.status_code == 200

    async def test_non_ad_task_unaffected(
        self, admin_client, override_async_session, monkeypatch, tmp_path
    ):
        # No ads-skill binding → the reviewer block never fires even with
        # an AD_AUDIT-shaped file present.
        task_id = await _seed_running_task(override_async_session)
        task_dir = _wire(monkeypatch, tmp_path, task_id, skill=None)
        (task_dir / 'AD_AUDIT_2026-07-09.md').write_text(
            '# report\n', encoding='utf-8'
        )
        reset_attempts(task_id)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': './AD_AUDIT_2026-07-09.md'},
        )
        assert r.status_code == 200


class TestFollowUpTurnRollover:
    """A follow-up turn must be reviewed on its OWN merits — it cannot
    inherit the prior turn's verdict. Regression for the AE relist that
    completed on the SA turn's stale ``iter5=incomplete``."""

    async def test_followup_cannot_ride_prior_turn_incomplete(
        self, admin_client, override_async_session, monkeypatch, tmp_path
    ):
        task_id = await _seed_running_task(override_async_session)
        task_dir = _wire(monkeypatch, tmp_path, task_id)
        (task_dir / 'AD_AUDIT_2026-07-16.md').write_text(
            '# 广告优化建议\n\n## Amazon SA\n报告内容\n', encoding='utf-8'
        )
        # Turn 1 finished with a terminal incomplete verdict → accepted.
        (
            task_dir
            / f'REVIEW_2026-07-16_iter{report_reviewer.REVIEW_MAX_ITERS}.md'
        ).write_text('Status: incomplete\n', encoding='utf-8')
        reset_attempts(task_id)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': './AD_AUDIT_2026-07-16.md'},
        )
        assert r.status_code == 200  # turn 1 completes

        # A NEW turn begins (the follow-up) — the server rolls the prior
        # turn's review verdict aside, exactly as the init event does.
        report_reviewer.rollover_reviews(task_dir)
        assert not (
            task_dir
            / f'REVIEW_2026-07-16_iter{report_reviewer.REVIEW_MAX_ITERS}.md'
        ).exists()

        # The follow-up's completion is now DENIED until it runs its own
        # reviewer — it can no longer ride the prior turn's incomplete.
        reset_attempts(task_id)
        r = await admin_client.post(
            f'/api/tasks/{task_id}/result',
            json={'result': './AD_AUDIT_2026-07-16.md'},
        )
        assert r.status_code == 400
        assert 'ads-report-review' in r.json()['detail']
