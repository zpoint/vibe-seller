"""A schedule plan frozen under an older harness contract must re-plan.

Every fire replays the frozen plan verbatim. Observed on an upgrade: a
weekly ad-audit plan frozen under the previous harness spelled out the
old mechanics step by step — TSVs under the old store path, "if last
week's TSV exists, skip the browser and copy it", a server reviewer
that no longer exists — and would have kept driving them. The boot
migration sends such plans back to STALE, which the existing fire gate
refuses and the UI already offers to re-plan.
"""

import sqlite3

import pytest
from sqlalchemy import create_engine, insert

import app.database as _database  # noqa: F401 — also registers all models
from app.models.base import Base
from app.models.schedule import Schedule
from app.plan_states import PLAN_CONTRACT, allows_fire

pytestmark = pytest.mark.unit

_ensure_added_columns = _database._ensure_added_columns  # noqa: SLF001


class _ConnShim:
    def __init__(self, con):
        self._con = con

    def exec_driver_sql(self, sql, params=()):
        cur = self._con.execute(sql, params)
        self._con.commit()
        return cur


def _row(id_, **kw):
    base = dict(
        id=id_,
        title=f'sched {id_}',
        schedule_type='weekly',
        schedule_time='03:50',
        created_by='u1',
        plan_mode=True,
        plan_status='ready',
        plan='## Steps\n1. old mechanics',
    )
    base.update(kw)
    return Schedule(**base)


def _db(tmp_path, rows, *, drop_contract_column):
    db = tmp_path / 'vs.db'
    engine = create_engine(f'sqlite:///{db}')
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for row in rows:
            conn.execute(
                insert(Schedule).values(**{
                    c: getattr(row, c)
                    for c in Schedule.__mapper__.columns.keys()
                    if getattr(row, c) is not None
                })
            )
    engine.dispose()
    con = sqlite3.connect(db)
    if drop_contract_column:
        con.execute('ALTER TABLE schedules DROP COLUMN plan_contract')
        con.commit()
    return con, _ConnShim(con)


def _status(con):
    return dict(con.execute('SELECT id, plan_status FROM schedules'))


def test_upgrade_sends_pre_contract_plans_back_to_replan(tmp_path):
    """The DB from before the stamp existed: every ready user plan was
    written under the old contract and must stop firing."""
    con, shim = _db(
        tmp_path,
        [
            _row('weekly-ads'),
            _row('system', is_system=True),
            _row('no-plan-mode', plan_mode=False, plan_status='none'),
            _row('planning', plan_status='planning', plan=None),
        ],
        drop_contract_column=True,
    )
    _ensure_added_columns(shim)
    got = _status(con)
    assert got['weekly-ads'] == 'stale'
    assert not allows_fire(got['weekly-ads'])
    # Untouched: system schedules carry no user plan; the others hold
    # no frozen plan to be wrong.
    assert got['system'] == 'ready'
    assert got['no-plan-mode'] == 'none'
    assert got['planning'] == 'planning'
    con.close()


def test_a_plan_frozen_under_the_current_contract_keeps_firing(tmp_path):
    con, shim = _db(
        tmp_path,
        [
            _row('current', plan_contract=PLAN_CONTRACT),
            _row('older', plan_contract=PLAN_CONTRACT - 1),
        ],
        drop_contract_column=False,
    )
    _ensure_added_columns(shim)
    _ensure_added_columns(shim)  # every later boot: same answer
    got = _status(con)
    assert got['current'] == 'ready'
    assert got['older'] == 'stale'
    con.close()


def test_the_boot_reports_how_many_plans_it_staled(tmp_path):
    con, shim = _db(
        tmp_path,
        [_row('a'), _row('b'), _row('current', plan_contract=PLAN_CONTRACT)],
        drop_contract_column=False,
    )
    assert _ensure_added_columns(shim) == 2
    assert _ensure_added_columns(shim) == 0
    con.close()
