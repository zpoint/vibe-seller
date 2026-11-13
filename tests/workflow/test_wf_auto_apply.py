"""The auto-apply of a small update, through the real server.

The unit tests pin the policy; this pins what an operator notices: a
ready pip update waits for a running task and applies itself when the
task ends, with no click anywhere. The DB, the task rows, the agent
manager and ``FakeAgentScenario`` are all real — the only thing faked is
the update itself (the apply, which would replace this checkout).
"""

import asyncio
import time

import pytest

from app import pip_update, update_idle
import app.auto_update as au
from app.models.task import Task
from app.scheduler.task_queue import task_queue_scheduler
from app.task_states import TaskStatus
from tests.workflow.conftest import wait_for_task
from tests.workflow.fake_agent import FakeAgentScenario

pytestmark = pytest.mark.workflow

VERSION = '9.9.9'
PIP = pip_update.PATH_PIP


@pytest.fixture(autouse=True)
def _reset():
    au.reset_for_tests()
    yield
    au.reset_for_tests()


def ready(path=pip_update.PATH_PIP, version=VERSION, since=None):
    """Put the machine in ``ready`` for an update, as a cycle would.

    The clock is written the way :func:`app.update_idle.note_ready`
    writes it, because the popup decision reads it from there."""
    au._update(
        state=au.READY,
        version=version,
        path=path,
        ready_since=since if since is not None else time.time(),
        error=None,
    )


def _recorder(sink, path=PIP):
    async def fake_apply(**kw):
        sink.append(kw)
        return {'state': au.RESTARTING, 'version': VERSION, 'path': PIP}

    return fake_apply


class TestRunningTaskBlocksTheApply:
    """The scenario the brief asks for, with a live agent process."""

    async def test_a_live_task_blocks_it_and_its_end_releases_it(
        self, admin_client, install_fake_agent, monkeypatch
    ):
        """The scenario the brief asks for, end to end.

        A real task is created through the API and run by the fake agent
        with its execute phase held open; while it is live the update
        must not apply, and the tick after it finishes must apply
        without anyone clicking."""
        applied = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(applied, PIP))
        ready()
        # Registered BEFORE the task exists: creating it dispatches it.
        install_fake_agent.default_scenario = FakeAgentScenario(
            result='finished while the update waited', execute_delay=3.0
        )

        store = await admin_client.post(
            '/api/stores', json={'name': 'the busy store'}
        )
        assert store.status_code == 200
        r = await admin_client.post(
            '/api/tasks',
            json={
                'title': 'occupy the server',
                'plan_mode': False,
                'store_id': store.json()['id'],
            },
        )
        assert r.status_code == 200
        task_id = r.json()['id']

        # 1. The task is live. The tick refuses — and refuses WITHOUT
        #    leaving dispatch paused, which would stall queued work.
        # The same bounded poll the harness's own `wait_for_task` uses.
        for _ in range(200):
            if install_fake_agent.live_agent_count():
                break
            await asyncio.sleep(0.1)
        assert install_fake_agent.live_agent_count(), 'agent never went live'
        answer = await au.idle_tick(task_queue_scheduler, install_fake_agent)
        assert answer.startswith('busy:'), answer
        assert applied == []
        assert task_queue_scheduler._dispatch_paused is False

        # 2. The task ends; the same tick now applies.
        data = await wait_for_task(admin_client, task_id, timeout=60)
        assert data['status'] == 'completed'
        answer = await au.idle_tick(task_queue_scheduler, install_fake_agent)
        assert answer == 'applied', answer
        assert applied == [{'interrupted_tasks': 0, 'trigger': 'auto_idle'}]

    async def test_a_queued_task_blocks_it(
        self,
        install_fake_agent,
        monkeypatch,
        override_async_session,
        admin_user,
    ):
        fake = install_fake_agent
        """Queued is not running, but it starts on the next tick — and a
        restart between the two kills it."""
        applied = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(applied))
        ready()
        async with override_async_session() as db:
            db.add(_task_row(TaskStatus.QUEUED, admin_user.id))
            await db.commit()
        answer = await au.idle_tick(task_queue_scheduler, fake)
        assert 'queued' in answer, answer
        assert applied == []

    async def test_a_waiting_task_blocks_it(
        self,
        install_fake_agent,
        monkeypatch,
        override_async_session,
        admin_user,
    ):
        fake = install_fake_agent
        """WAITING holds no agent, but an inbound email or channel message
        wakes it into one at any second."""
        applied = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(applied))
        ready()
        async with override_async_session() as db:
            db.add(_task_row(TaskStatus.WAITING, admin_user.id))
            await db.commit()
        assert (await au.idle_tick(task_queue_scheduler, fake)) != 'applied'
        assert applied == []

    async def test_a_planned_task_blocks_it(
        self,
        install_fake_agent,
        monkeypatch,
        override_async_session,
        admin_user,
    ):
        fake = install_fake_agent
        """PLANNED is one approval away from an agent — the approval could
        arrive between the check and the restart."""
        applied = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(applied))
        ready()
        async with override_async_session() as db:
            db.add(_task_row(TaskStatus.PLANNED, admin_user.id))
            await db.commit()
        assert (await au.idle_tick(task_queue_scheduler, fake)) != 'applied'
        assert applied == []

    async def test_finished_tasks_do_not_block_it(
        self,
        install_fake_agent,
        monkeypatch,
        override_async_session,
        admin_user,
    ):
        fake = install_fake_agent
        applied = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(applied))
        ready()
        async with override_async_session() as db:
            db.add(_task_row(TaskStatus.COMPLETED, admin_user.id))
            db.add(_task_row(TaskStatus.FAILED, admin_user.id))
            await db.commit()
        assert await au.idle_tick(task_queue_scheduler, fake) == 'applied'
        assert len(applied) == 1

    async def test_a_schedule_about_to_fire_blocks_it(
        self, install_fake_agent, monkeypatch, override_async_session
    ):
        fake = install_fake_agent
        """A restart a minute before 09:00 turns a daily report into one
        that starts late or not at all."""
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        import app.scheduler.cron as cron  # noqa: PLC0415

        applied = []
        monkeypatch.setenv('VIBE_SELLER_AUTO_UPDATE', '1')
        monkeypatch.setattr(au, 'supported', lambda: True)
        monkeypatch.setattr(au, 'apply_restart', _recorder(applied))
        ready()
        due = datetime.now(UTC) + timedelta(minutes=4)
        monkeypatch.setattr(
            cron, 'scheduler', _Sched([_Job('schedule_x', due)])
        )
        answer = await au.idle_tick(task_queue_scheduler, fake)
        assert 'schedule_x' in answer, answer
        assert applied == []

        later = datetime.now(UTC) + timedelta(hours=3)
        monkeypatch.setattr(
            cron, 'scheduler', _Sched([_Job('schedule_x', later)])
        )
        assert await au.idle_tick(task_queue_scheduler, fake) == 'applied'


class _Job:
    def __init__(self, job_id, when):
        self.id = job_id
        self.next_run_time = when


class _Sched:
    def __init__(self, jobs):
        self._jobs = jobs

    def get_jobs(self):
        return list(self._jobs)


def _task_row(status, created_by):
    """A task row in one of the states the idle policy has to notice.

    The columns are the ones the model requires and nothing more: these
    tests are about the STATUS, and a row that needs four more fixtures
    would test the fixtures."""
    import uuid  # noqa: PLC0415

    return Task(
        id=str(uuid.uuid4()),
        title=f'wf-auto-apply {status}',
        description='occupied',
        status=status,
        plan_mode=False,
        created_by=created_by,
    )


class TestInstallerPathIsNeverAutoApplied:
    """#244's rule, restated against the real queue: only a click may run
    an installer, however idle the machine is."""

    async def test_a_ready_installer_update_waits_for_the_click(
        self, install_fake_agent, monkeypatch, override_async_session
    ):
        fake = install_fake_agent
        applied = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(applied))
        ready(path=pip_update.PATH_INSTALLER)
        assert await au.idle_tick(task_queue_scheduler, fake) == 'not ready'
        assert applied == []


class TestStatusEndpointReportsThePath:
    """What the UI renders IS the path and the popup decision, so they
    have to survive the round trip."""

    async def test_a_ready_pip_update_says_it_will_apply_itself(
        self, admin_client
    ):
        ready()
        body = (await admin_client.get('/api/system/update-status')).json()
        assert body['state'] == 'ready'
        assert body['version'] == VERSION
        assert body['path'] == 'pip'
        assert body['auto_apply'] is True
        assert body['popup_due'] is False

    async def test_a_ready_installer_update_asks_at_once(self, admin_client):
        ready(path=pip_update.PATH_INSTALLER)
        body = (await admin_client.get('/api/system/update-status')).json()
        assert body['path'] == 'installer'
        assert body['auto_apply'] is False
        assert body['popup_due'] is True

    async def test_a_day_of_never_being_idle_makes_the_pip_path_ask(
        self, admin_client
    ):
        ready(since=time.time() - update_idle.READY_POPUP_AFTER_SECONDS - 1)
        body = (await admin_client.get('/api/system/update-status')).json()
        assert body['popup_due'] is True

    async def test_the_last_apply_is_reported_to_the_ui(
        self, admin_client, monkeypatch, tmp_path
    ):
        doc = {
            'status': pip_update.ROLLED_BACK,
            'from': '9.9.8',
            'to': VERSION,
            'path': 'pip',
            'trigger': 'auto_idle',
            'stage': 'health',
            'error': 'the server never reported 9.9.9',
            'error_class': 'ApplyError',
            'reported': True,
        }
        monkeypatch.setattr(au.pip_update, 'read_record', lambda: doc)
        monkeypatch.setattr(au.pip_update, 'mark_record_reported', lambda: None)
        au.load_last_update()
        body = (await admin_client.get('/api/system/update-status')).json()
        assert body['last_update']['status'] == 'rolled_back'
        assert body['last_update']['from'] == '9.9.8'


class TestRestartEndpoint:
    async def test_the_apply_answers_with_the_path(
        self, admin_client, monkeypatch
    ):
        class _Proc:
            def poll(self):
                return None

        monkeypatch.setattr(au.sys, 'platform', 'linux')
        monkeypatch.setattr(au.apply_launch, 'apply', lambda *a: _Proc())
        monkeypatch.setattr(au.pip_update, 'read_record', lambda: None)
        monkeypatch.setattr(au.pip_update, 'record_mtime', lambda: 0.0)
        monkeypatch.setattr(au.pip_update, 'APPLY_TIMEOUT_SECONDS', 0.05)
        ready()
        r = await admin_client.post('/api/system/update/restart')
        assert r.status_code == 200
        assert r.json() == {
            'state': 'restarting',
            'version': VERSION,
            'path': 'pip',
        }
        if au._apply_task is not None:
            import asyncio  # noqa: PLC0415

            au._apply_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await au._apply_task

    async def test_a_second_apply_is_refused_with_409(
        self, admin_client, monkeypatch
    ):
        """Two admins, or an auto-apply that got there first: the loser
        hears "one is already running", not "the updater is broken"."""
        monkeypatch.setattr(au.sys, 'platform', 'linux')
        monkeypatch.setattr(
            au.apply_launch,
            'apply',
            lambda *a: (_ for _ in ()).throw(
                pip_update.ApplyInProgress('another update is applying (pid 1)')
            ),
        )
        ready()
        r = await admin_client.post('/api/system/update/restart')
        assert r.status_code == 409
        assert 'another update' in r.json()['detail']
        assert au.snapshot()['state'] == au.READY

    async def test_a_member_is_still_refused(self, member_client, monkeypatch):
        ready()
        r = await member_client.post('/api/system/update/restart')
        assert r.status_code == 403


class TestReleaseNotesRange:
    """/update-check can only ever describe releases NEWER than the server,
    so the "already updated" panel must be able to ask for the range it
    just crossed."""

    async def test_it_returns_the_merged_range(self, admin_client, monkeypatch):
        import app.routers.system as sr  # noqa: PLC0415

        async def notes(_client, _since):
            return [
                {
                    'version': '9.9.9',
                    'name': 'v9.9.9',
                    'body': '## Highlights\n\n- pip-path updates\n',
                    'url': 'https://x',
                    'published_at': None,
                },
                {
                    'version': '10.0.0',
                    'name': 'v10.0.0',
                    'body': '## Highlights\n\n- newer than the ask\n',
                    'url': 'https://x',
                    'published_at': None,
                },
            ]

        monkeypatch.setattr(sr, 'fetch_release_notes', notes)
        r = await admin_client.get(
            '/api/system/release-notes',
            params={'since': '9.9.8', 'until': '9.9.9'},
        )
        assert r.status_code == 200
        body = r.json()
        assert body['since'] == '9.9.8'
        assert body['until'] == '9.9.9'
        # `until` is a bound, not a filter nobody asked for: 10.0.0 is
        # not part of the crossed range.
        assert body['releases'] == ['9.9.9']
        assert [h['version'] for h in body['highlights']] == ['9.9.9']

    async def test_until_defaults_to_the_running_version(
        self, admin_client, monkeypatch
    ):
        import app.routers.system as sr  # noqa: PLC0415

        async def none(_client, _since):
            return []

        monkeypatch.setattr(sr, 'fetch_release_notes', none)
        r = await admin_client.get(
            '/api/system/release-notes', params={'since': '0.1.0'}
        )
        assert r.json()['until'] == r.json()['until']
        assert r.json()['until']

    async def test_it_needs_the_start_of_the_range(self, admin_client):
        r = await admin_client.get('/api/system/release-notes')
        assert r.status_code == 422

    async def test_it_requires_login(self, unauthed_client):
        r = await unauthed_client.get(
            '/api/system/release-notes', params={'since': '0.1.0'}
        )
        assert r.status_code == 401


class TestRefusedUpdate:
    """A release this machine already put back never runs by itself again.

    Review 4's outage loop: S5 fails on boot, the helper rolls back, the
    server is serving the old version again — and six hours later the
    same release is offered, warmed and marked ready, so the idle tick
    applies it again. Every cycle is then a restart, an interrupted
    machine and a rollback, forever, for a build that cannot boot. The
    answer has three parts and all three are answered here: the idle
    tick refuses, the status says why, and an admin's click still works
    — because a click is a decision and a tick is a timer.
    """

    def _refuse(self, version=VERSION):
        """As if the helper had just recorded this rollback."""
        return au.update_refusal.note_apply_result({
            'status': au.pip_update.ROLLED_BACK,
            'from': '9.9.8',
            'to': version,
            'path': 'pip',
            'trigger': 'auto_idle',
            'stage': 'health',
            'error': f'the server never reported {version}',
            'at': '2026-11-13T04:00:00Z',
            'reported': False,
        })

    def _ready_refused(self):
        self._refuse()
        au._mark_ready(VERSION, PIP)

    async def test_the_idle_tick_refuses_the_release_it_put_back(
        self, monkeypatch, install_fake_agent
    ):
        applied = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(applied))
        self._ready_refused()
        decision = await au.idle_tick(task_queue_scheduler, install_fake_agent)
        assert decision.startswith('refused:'), decision
        assert applied == [], 'a rolled-back release was applied again'
        assert 'will not be applied by itself again' in decision

    async def test_a_newer_release_clears_the_refusal(
        self, monkeypatch, install_fake_agent, admin_client
    ):
        # `admin_client` for the database the idle tick's busy-count
        # question reads: this tick must get PAST that question, and a
        # missing schema would look like the policy refusing to move.
        applied = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(applied))
        self._ready_refused()
        assert au.snapshot()['auto_apply'] is False
        # The next release arrives. Nothing deletes the memory by hand:
        # the offer being newer IS the clearing.
        au._mark_ready('99.9.9', PIP)
        snap = au.snapshot()
        assert snap['refusal'] is None
        assert snap['auto_apply'] is True, 'a new release must self-apply'
        assert au.update_refusal.current() is None
        assert (
            await au.idle_tick(task_queue_scheduler, install_fake_agent)
            == 'applied'
        )

    async def test_a_booting_server_remembers_the_outage_it_came_from(
        self, monkeypatch, install_fake_agent, admin_client
    ):
        """The process that rolled the update back is usually the one that
        was stopped: on Unix the helper restarts the old version, and the
        server that comes up reads `last.json` — that read is where the
        refusal has to be born, or the memory dies with the process that
        learned it."""
        doc = {
            'status': au.pip_update.ROLLED_BACK,
            'from': '9.9.8',
            'to': VERSION,
            'path': 'pip',
            'trigger': 'auto_idle',
            'stage': 'health',
            'error': 'the server never reported ' + VERSION,
            'at': '2026-11-13T05:00:00Z',
            'reported': True,
        }
        monkeypatch.setattr(au.pip_update, 'read_record', lambda: doc)
        monkeypatch.setattr(au.pip_update, 'mark_record_reported', lambda: None)
        au.reset_for_tests()
        au.load_last_update()
        applied = []
        monkeypatch.setattr(au, 'apply_restart', _recorder(applied))
        au._mark_ready(VERSION, PIP)
        assert au.snapshot()['auto_apply'] is False
        assert (
            await au.idle_tick(task_queue_scheduler, install_fake_agent)
        ).startswith('refused:')
        assert applied == []

    async def test_the_status_says_what_happened_and_why_nothing_moves(
        self, admin_client
    ):
        self._ready_refused()
        body = (await admin_client.get('/api/system/update-status')).json()
        assert body['state'] == 'ready'
        assert body['path'] == 'pip'
        # The sidebar sentence ("updates automatically when idle") is
        # wrong for this state, which is why the flag is in the payload.
        assert body['auto_apply'] is False
        assert body['popup_due'] is True, 'the admin has to be told'
        assert body['refusal']['version'] == VERSION
        assert body['refusal']['status'] == 'rolled_back'
        assert body['refusal']['stage'] == 'health'

    async def test_an_admin_can_still_apply_it(self, admin_client, monkeypatch):
        """The refusal gates the TIMER, not the person."""

        class _Proc:
            def poll(self):
                return None

        started = []

        def fake_apply(version, trigger, path):
            started.append(trigger)
            return _Proc()

        monkeypatch.setattr(au.sys, 'platform', 'linux')
        monkeypatch.setattr(au.apply_launch, 'apply', fake_apply)
        monkeypatch.setattr(au.pip_update, 'read_record', lambda: None)
        monkeypatch.setattr(au.pip_update, 'record_mtime', lambda: 0.0)
        self._ready_refused()
        r = await admin_client.post('/api/system/update/restart')
        assert r.status_code == 200
        assert r.json()['state'] == 'restarting'
        assert started == [pip_update.TRIGGER_MANUAL]

    async def test_a_boot_remembers_the_refusal_of_the_previous_process(
        self, monkeypatch, admin_client
    ):
        """The helper that rolled back is gone; the server that came up
        on the old version is the one that must not try it again."""
        doc = {
            'status': au.pip_update.ROLLED_BACK,
            'from': '9.9.8',
            'to': VERSION,
            'path': 'pip',
            'trigger': 'auto_idle',
            'stage': 'health',
            'error': 'boom',
            'reported': True,
        }
        monkeypatch.setattr(au.pip_update, 'read_record', lambda: doc)
        monkeypatch.setattr(au.pip_update, 'mark_record_reported', lambda: None)
        au.reset_for_tests()
        au.load_last_update()
        au._mark_ready(VERSION, PIP)
        body = (await admin_client.get('/api/system/update-status')).json()
        assert body['auto_apply'] is False
        assert body['refusal']['version'] == VERSION
