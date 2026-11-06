"""An in-memory stand-in for :mod:`app.sync.state` (unit tests).

Unit tests exercise the scheduler's logic — cadence, single-flight,
toggles, skip reasons — and none of that needs SQLite. This fake keeps
the SAME semantics as the real store (``etag=''`` clears the validator,
``version=None`` leaves it alone, ``needs_restart`` is sticky, only
``updated``/``unchanged`` move ``last_ok_at``), so a unit test that
passes here means the same thing against the real table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json

from app.sync.state import OK_STATUSES


@dataclass
class Row:
    """One job's state (mirrors app.models.sync_state.SyncState)."""

    job_name: str
    last_run_at: str | None = None
    last_ok_at: str | None = None
    version: str | None = None
    etag: str | None = None
    status: str | None = None
    error: str | None = None
    needs_restart: bool = False
    detail: str | None = None
    updated_at: str = ''

    def to_dict(self) -> dict:
        return {
            'name': self.job_name,
            'last_run_at': self.last_run_at,
            'last_ok_at': self.last_ok_at,
            'version': self.version,
            'etag': self.etag,
            'status': self.status,
            'error': self.error,
            'needs_restart': self.needs_restart,
            'detail': json.loads(self.detail) if self.detail else {},
        }


@dataclass
class FakeState:
    """In-memory sync_state + toggles, with the real store's semantics."""

    rows: dict[str, Row] = field(default_factory=dict)
    toggles: dict[str, bool] = field(default_factory=dict)
    #: Every record() call, for assertions.
    calls: list[dict] = field(default_factory=list)

    # ── the app.sync.state surface ────────────────────────────

    async def get_row(self, job_name: str):
        return self.rows.get(job_name)

    async def get_dict(self, job_name: str):
        row = self.rows.get(job_name)
        return row.to_dict() if row else None

    async def get_all(self):
        return {name: row.to_dict() for name, row in self.rows.items()}

    async def record(
        self,
        job_name: str,
        *,
        status: str,
        error: str | None = None,
        version: str | None = None,
        etag: str | None = None,
        needs_restart: bool | None = None,
        detail: dict | None = None,
    ) -> dict:
        self.calls.append({
            'job_name': job_name,
            'status': status,
            'error': error,
            'version': version,
            'etag': etag,
            'needs_restart': needs_restart,
            'detail': detail,
        })
        row = self.rows.setdefault(job_name, Row(job_name=job_name))
        now = datetime.now(UTC).isoformat()
        row.status = status
        row.error = error
        row.last_run_at = now
        if status in OK_STATUSES:
            row.last_ok_at = now
        if version is not None:
            row.version = version
        if etag is not None:
            row.etag = etag or None
        if needs_restart:
            row.needs_restart = True
        if detail is not None:
            row.detail = json.dumps(detail)
        row.updated_at = now
        return row.to_dict()

    async def record_import(
        self,
        job_name: str,
        *,
        last_ok_at: str | None = None,
        last_run_at: str | None = None,
        status: str | None = None,
        error: str | None = None,
        version: str | None = None,
        etag: str | None = None,
        detail: dict | None = None,
    ) -> dict:
        if job_name in self.rows:
            return self.rows[job_name].to_dict()
        row = Row(
            job_name=job_name,
            last_ok_at=last_ok_at,
            last_run_at=last_run_at or last_ok_at,
            status=status,
            error=error,
            version=version,
            etag=etag or None,
            detail=json.dumps(detail) if detail else None,
        )
        self.rows[job_name] = row
        return row.to_dict()

    async def clear_restart_flags(self) -> None:
        for row in self.rows.values():
            row.needs_restart = False

    async def load_bookkeeping(self, job_name: str) -> dict:
        row = self.rows.get(job_name)
        return json.loads(row.detail) if row and row.detail else {}

    async def save_bookkeeping(self, job_name: str, **fields) -> dict:
        row = self.rows.setdefault(job_name, Row(job_name=job_name))
        merged = json.loads(row.detail) if row.detail else {}
        merged.update(fields)
        row.detail = json.dumps(merged)
        return merged

    async def read_toggle(self, key: str | None) -> bool:
        if not key:
            return True
        return self.toggles.get(key, True)

    # ── wiring ────────────────────────────────────────────────

    def install(self, monkeypatch) -> FakeState:
        """Point ``app.sync.state`` at this fake."""
        for name in (
            'get_row',
            'get_dict',
            'get_all',
            'record',
            'record_import',
            'clear_restart_flags',
            'load_bookkeeping',
            'save_bookkeeping',
            'read_toggle',
        ):
            monkeypatch.setattr(f'app.sync.state.{name}', getattr(self, name))
        return self

    def seed(self, job_name: str, **fields) -> Row:
        row = self.rows.setdefault(job_name, Row(job_name=job_name))
        for key, value in fields.items():
            setattr(row, key, value)
        return row
