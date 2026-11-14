"""Tests for the idle policy and the 24 h ready clock.

The pip path's promise is "this restarts itself, and never on top of
somebody's work", so the two questions these pin are: what counts as
busy, and when does a machine that is never idle fall back to asking.
"""

import time

import pytest

from app import update_idle
from app.task_states import TaskStatus

pytestmark = pytest.mark.unit

DAY = update_idle.READY_POPUP_AFTER_SECONDS


class FakeAgents:
    def __init__(self, live=0):
        self.live = live

    def live_agent_count(self):
        return self.live


def _busy(count):
    async def fake():
        return count

    return fake


# No fixture hides the schedule check: an un-started APScheduler reports
# no jobs, so `evaluate` asks the real question in every test here.


@pytest.fixture(name='no_tasks')
def _no_tasks(monkeypatch):
    monkeypatch.setattr(update_idle, 'busy_task_count', _busy(0))


class TestBusySet:
    def test_only_finished_tasks_are_not_busy(self):
        """Every state that holds an agent, or starts one without
        anyone first having to be idle, counts as busy — a restart that
        lands between a plan approval and its agent is the bug."""
        assert set(TaskStatus) - set(update_idle.BUSY_TASK_STATUSES) == {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
        }


class TestEvaluate:
    async def test_idle_when_nothing_is_running(self, no_tasks):
        verdict = await update_idle.evaluate(FakeAgents(0))
        assert verdict.idle is True
        assert verdict.reason is None

    async def test_a_live_agent_blocks(self, no_tasks):
        verdict = await update_idle.evaluate(FakeAgents(2))
        assert verdict.idle is False
        assert '2 task agent(s) running' == verdict.reason

    async def test_a_queued_task_blocks(self, monkeypatch):
        monkeypatch.setattr(update_idle, 'busy_task_count', _busy(1))
        verdict = await update_idle.evaluate(FakeAgents(0))
        assert verdict.idle is False
        assert '1 task(s) queued' in verdict.reason

    async def test_a_schedule_about_to_fire_blocks(self, monkeypatch):
        monkeypatch.setattr(update_idle, 'busy_task_count', _busy(0))
        monkeypatch.setattr(
            update_idle,
            'schedule_due_within',
            lambda s, now=None: 'schedule_abc',
        )
        verdict = await update_idle.evaluate(FakeAgents(0))
        assert verdict.idle is False
        assert 'schedule_abc' in verdict.reason

    async def test_the_agent_is_checked_first(self, monkeypatch):
        """The cheapest question is asked first: a live agent is a
        one-line answer and should not cost a DB round trip."""
        called = []

        async def boom():
            called.append(1)
            return 0

        monkeypatch.setattr(update_idle, 'busy_task_count', boom)
        await update_idle.evaluate(FakeAgents(1))
        assert called == []

    async def test_is_idle_is_evaluate_s_answer(self, no_tasks):
        assert await update_idle.is_idle(FakeAgents(0)) is True


class TestScheduleWindow:
    """The window is read off APScheduler's own job list, which is the
    only place a schedule's next fire exists."""

    class _Job:
        def __init__(self, job_id, when):
            self.id = job_id
            self.next_run_time = when

    class _Scheduler:
        def __init__(self, jobs):
            self._jobs = jobs

        def get_jobs(self):
            return list(self._jobs)

    def _patch(self, monkeypatch, jobs):
        import app.scheduler.cron as cron  # noqa: PLC0415

        monkeypatch.setattr(cron, 'scheduler', self._Scheduler(jobs))

    def test_a_schedule_due_inside_the_window_counts(self, monkeypatch):
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        now = datetime.now(UTC)
        self._patch(
            monkeypatch, [self._Job('schedule_x', now + timedelta(minutes=5))]
        )
        assert (
            update_idle.schedule_due_within(
                update_idle.SCHEDULE_LOOKAHEAD_SECONDS
            )
            == 'schedule_x'
        )

    def test_one_due_later_does_not(self, monkeypatch):
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        now = datetime.now(UTC)
        self._patch(
            monkeypatch, [self._Job('schedule_x', now + timedelta(hours=3))]
        )
        assert (
            update_idle.schedule_due_within(
                update_idle.SCHEDULE_LOOKAHEAD_SECONDS
            )
            is None
        )

    def test_housekeeping_jobs_do_not_hold_an_update(self, monkeypatch):
        """Email sync and the reapers run every minute; treating them as
        "busy" would mean the server is never idle."""
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        now = datetime.now(UTC)
        self._patch(
            monkeypatch,
            [
                self._Job('email_sync', now),
                self._Job('stall_reaper', now),
                self._Job('schedule_x', now + timedelta(hours=6)),
            ],
        )
        assert (
            update_idle.schedule_due_within(
                update_idle.SCHEDULE_LOOKAHEAD_SECONDS
            )
            is None
        )

    def test_a_paused_schedule_is_not_due(self, monkeypatch):
        self._patch(monkeypatch, [self._Job('schedule_x', None)])
        assert update_idle.schedule_due_within(600) is None


class TestReadyClock:
    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            update_idle,
            'ready_file',
            lambda: tmp_path / 'updates' / 'update-ready.json',
        )

    def test_note_ready_starts_a_clock(self):
        rec = update_idle.note_ready('0.3.2', 'pip', now=1000.0)
        assert rec['since'] == 1000.0

    def test_the_same_version_and_path_keep_the_clock(self):
        """The update cycle runs every six hours; if each one restarted
        the clock, a busy server would never reach the ask."""
        update_idle.note_ready('0.3.2', 'pip', now=1000.0)
        assert update_idle.note_ready('0.3.2', 'pip', now=2000.0)['since'] == (
            1000.0
        )

    def test_a_new_version_resets_it(self):
        update_idle.note_ready('0.3.2', 'pip', now=1000.0)
        assert update_idle.note_ready('0.3.3', 'pip', now=2000.0)['since'] == (
            2000.0
        )

    def test_a_change_of_path_resets_it(self):
        """pip → installer is a different decision with different
        consequences (only one of them applies itself)."""
        update_idle.note_ready('0.3.2', 'pip', now=1000.0)
        assert (
            update_idle.note_ready('0.3.2', 'installer', now=2000.0)['since']
            == 2000.0
        )

    def test_the_clock_survives_a_restart(self):
        """A server that restarts for an unrelated reason at hour 23 must
        not silently reset the timer and never ask."""
        update_idle.note_ready('0.3.2', 'pip', now=1000.0)
        assert update_idle.note_ready('0.3.2', 'pip')['since'] == 1000.0

    def test_clear_ready_forgets_it(self):
        update_idle.note_ready('0.3.2', 'pip', now=1000.0)
        update_idle.clear_ready()
        now = time.time()
        assert update_idle.note_ready('0.3.2', 'pip')['since'] >= now - 2

    def test_clearing_without_a_record_is_not_an_error(self):
        update_idle.clear_ready()


class TestPopup:
    def test_the_installer_path_asks_as_soon_as_it_is_ready(self):
        """Nothing applies it by itself, so this is the only way it ever
        happens. #244's behaviour, unchanged."""
        assert update_idle.popup_due('installer', 1000.0, now=1001.0) is True

    def test_the_pip_path_stays_quiet_for_a_day(self):
        assert (
            update_idle.popup_due('pip', 1000.0, now=1000.0 + DAY - 1) is False
        )

    def test_the_pip_path_asks_after_a_day_of_never_being_idle(self):
        assert update_idle.popup_due('pip', 1000.0, now=1000.0 + DAY) is True

    def test_a_pip_update_with_no_clock_cannot_ask(self):
        """No start time means no day has passed — the flag must not
        default to "bother the admin"."""
        assert update_idle.popup_due('pip', None, now=10.0**9) is False
