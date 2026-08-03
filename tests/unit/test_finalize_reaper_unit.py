"""Unit coverage for finalize-reaper pure logic + the startup ALTER."""

import sqlite3
import types

import pytest

import app.database as _database
import app.scheduler.finalize_reaper as _fr
from app.task_states import TaskStatus

# Unit tests intentionally exercise module internals.
_ensure_added_columns = _database._ensure_added_columns  # noqa: SLF001
_child_record = _fr._child_record  # noqa: SLF001
_RESULTS_POINTER = _fr._RESULTS_POINTER  # noqa: SLF001
_within_era = _fr._batch_is_within_finalize_era  # noqa: SLF001


def _child(created_at):
    return types.SimpleNamespace(created_at=created_at)


def _sched(enabled_at):
    return types.SimpleNamespace(finalize_enabled_at=enabled_at)


class TestOnlyBatchesFromTheFinalizeEra:
    """Turning a finalize step ON must not finalize the schedule's past.

    The eligibility test was "children all terminal AND no finalize task
    for this batch". Before the prompt was ever set, no batch had a
    finalize task — so the first PUT that set it satisfied both halves
    for every batch the schedule had ever run. Observed: ten finalize
    tasks stamped the same second, four of them still running 27 minutes
    later over month-old batches whose workspaces were long gone, with a
    real task starved in PENDING behind them for twenty minutes.
    """

    def test_a_batch_from_before_the_step_existed_is_skipped(self):
        assert not _within_era(
            [_child('2026-07-06T10:00:00+00:00')],
            _sched('2026-08-03T17:54:00+00:00'),
        )

    def test_a_batch_started_after_is_finalized(self):
        assert _within_era(
            [_child('2026-08-03T18:10:00+00:00')],
            _sched('2026-08-03T17:54:00+00:00'),
        )

    def test_a_batch_started_exactly_at_the_stamp_counts(self):
        # The fanout that the PUT itself armed must not fall in a crack.
        assert _within_era(
            [_child('2026-08-03T17:54:00+00:00')],
            _sched('2026-08-03T17:54:00+00:00'),
        )

    def test_the_earliest_child_decides_not_the_latest(self):
        # A retry child appended to an old batch must not drag the whole
        # batch into the era — batch start is when the batch started.
        assert not _within_era(
            [
                _child('2026-07-06T10:00:00+00:00'),
                _child('2026-08-03T18:10:00+00:00'),
            ],
            _sched('2026-08-03T17:54:00+00:00'),
        )

    def test_an_unarmed_schedule_finalizes_nothing(self):
        assert not _within_era(
            [_child('2026-08-03T18:10:00+00:00')], _sched(None)
        )

    def test_missing_stamps_refuse_rather_than_guess(self):
        # A batch that never finalizes is a visible gap someone chases;
        # a wrongly-fired one burns an agent slot on garbage.
        assert not _within_era(
            [_child(None)], _sched('2026-08-03T17:54:00+00:00')
        )


def test_backfill_arms_existing_schedules_from_now_not_forever(tmp_path):
    """An upgrade must keep working finalize schedules working.

    NULL-by-default would have been safe against the incident and wrong
    for everyone already using the feature — their next batch would
    silently stop finalizing. Stamping the upgrade instant keeps them
    working AND leaves every past batch outside the era.
    """
    db = tmp_path / 'upgrade.db'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE schedules (id TEXT PRIMARY KEY)')
    for t_ in ('tasks', 'users', 'stores'):
        con.execute(f'CREATE TABLE {t_} (id TEXT PRIMARY KEY)')
    con.commit()
    shim = _ConnShim(con)
    _ensure_added_columns(shim)

    con.execute(
        'INSERT INTO schedules (id, finalize_description) VALUES '
        "('armed', 'combine the reports'), ('blank', NULL), ('ws', '   ')"
    )
    con.commit()
    _ensure_added_columns(shim)  # the boot after the prompt existed

    got = dict(
        con.execute('SELECT id, finalize_enabled_at FROM schedules').fetchall()
    )
    assert got['armed'], 'a schedule with a prompt must stay armed'
    assert got['blank'] is None
    assert got['ws'] is None, 'whitespace is not a finalize prompt'

    stamp = got['armed']
    _ensure_added_columns(shim)  # every later boot must not re-stamp
    again = con.execute(
        "SELECT finalize_enabled_at FROM schedules WHERE id='armed'"
    ).fetchone()[0]
    assert again == stamp
    con.close()


pytestmark = pytest.mark.unit


def test_child_record_shape():
    task = types.SimpleNamespace(
        id='t1',
        store_id='s1',
        status=TaskStatus.COMPLETED,
        result='done',
        error=None,
        started_at='a',
        completed_at='b',
    )
    rec = _child_record(task, 'my-store', '/tasks/t1')
    assert rec == {
        'task_id': 't1',
        'store_id': 's1',
        'store_slug': 'my-store',
        'status': TaskStatus.COMPLETED,
        'result': 'done',
        'error': None,
        'task_dir': '/tasks/t1',
        'started_at': 'a',
        'completed_at': 'b',
    }


def test_results_pointer_names_the_file():
    assert 'batch_results.json' in _RESULTS_POINTER


class _ConnShim:
    """Adapt a raw sqlite3 connection to the .exec_driver_sql API."""

    def __init__(self, con):
        self._con = con

    def exec_driver_sql(self, sql, params=None):
        return self._con.execute(sql, params or ())


def test_ensure_added_columns_is_idempotent(tmp_path):
    db = tmp_path / 'old.db'
    con = sqlite3.connect(db)
    # Simulate a pre-feature DB: tables without the new columns.
    con.execute('CREATE TABLE schedules (id TEXT PRIMARY KEY)')
    con.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY)')
    con.execute('CREATE TABLE users (id TEXT PRIMARY KEY)')
    con.execute('CREATE TABLE stores (id TEXT PRIMARY KEY)')
    con.commit()
    shim = _ConnShim(con)

    _ensure_added_columns(shim)  # first run adds columns
    _ensure_added_columns(shim)  # second run must be a no-op (no error)

    user_cols = {r[1] for r in con.execute('PRAGMA table_info(users)')}
    assert 'sync_profile_to_schedules' in user_cols
    sched_cols = {r[1] for r in con.execute('PRAGMA table_info(schedules)')}
    task_cols = {r[1] for r in con.execute('PRAGMA table_info(tasks)')}
    assert 'finalize_description' in sched_cols
    assert 'is_finalize' in task_cols
    # Default must let existing rows be non-finalize.
    con.execute("INSERT INTO tasks (id) VALUES ('x')")
    con.commit()
    assert (
        con.execute("SELECT is_finalize FROM tasks WHERE id='x'").fetchone()[0]
        == 0
    )
    con.close()
