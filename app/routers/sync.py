"""Sync status + manual runs for the central sync scheduler.

Replaces the per-module ``/api/workspace/{skills,knowledge}/sync`` and
``/sync-meta`` endpoints: one place to see when every sync job last
ran, what upstream version it holds, whether a synced change is
waiting for a restart — and to make one run now.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_user
from app.models.user import User
from app.sync import state as sync_state
from app.sync.scheduler import REASON_MANUAL, sync_scheduler

router = APIRouter(prefix='/api/sync', tags=['sync'])


async def _job_view(name: str) -> dict:
    """One job's registration + stored state, as the UI reads it."""
    job = sync_scheduler.jobs[name]
    stored = await sync_state.get_dict(name) or {
        'name': name,
        'last_run_at': None,
        'last_ok_at': None,
        'version': None,
        'etag': None,
        'status': None,
        'error': None,
        'needs_restart': False,
        'detail': {},
    }
    stored['cadence'] = int(
        sync_scheduler.cadence_of(name),
    )
    stored['enabled'] = await sync_state.read_toggle(job.toggle_key)
    stored['running'] = sync_scheduler.is_running(name)
    stored['next_run_at'] = _iso(await sync_scheduler.next_run_time(name))
    return stored


def _iso(value) -> str | None:
    return value.isoformat() if value else None


@router.get('/status')
async def sync_status(_user: User = Depends(get_current_user)) -> dict:
    """Every registered sync job, with its stored state.

    ``needs_restart`` (top level and per job) is the signal that a
    synced skill changed gate code, which is only loaded at boot.
    """
    jobs = [await _job_view(name) for name in sync_scheduler.names()]
    return {
        'jobs': jobs,
        'needs_restart': any(job['needs_restart'] for job in jobs),
    }


@router.post('/{name}/run')
async def run_sync(name: str, _user: User = Depends(get_current_user)):
    """Run one sync job now.

    Manual runs bypass the job's auto-sync toggle (pressing Sync is an
    explicit user action) and force real work rather than a probe that
    says "nothing changed".
    """
    if not sync_scheduler.has(name):
        raise HTTPException(status_code=404, detail=f'Unknown sync job: {name}')
    try:
        result = await sync_scheduler.trigger(
            name, reason=REASON_MANUAL, force=True
        )
    except Exception as exc:  # noqa: BLE001 — reported, not raised
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    state = await sync_state.get_dict(name) or {}
    return {
        'name': name,
        'status': result.status,
        'version': result.version,
        'error': result.error,
        'skip_reason': result.skip_reason,
        'detail': result.detail,
        'state': state,
    }
