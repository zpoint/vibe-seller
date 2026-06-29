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

    def exec_driver_sql(self, sql):
        return self._con.execute(sql)


def test_ensure_added_columns_is_idempotent(tmp_path):
    db = tmp_path / 'old.db'
    con = sqlite3.connect(db)
    # Simulate a pre-feature DB: tables without the new columns.
    con.execute('CREATE TABLE schedules (id TEXT PRIMARY KEY)')
    con.execute('CREATE TABLE tasks (id TEXT PRIMARY KEY)')
    con.commit()
    shim = _ConnShim(con)

    _ensure_added_columns(shim)  # first run adds columns
    _ensure_added_columns(shim)  # second run must be a no-op (no error)

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
