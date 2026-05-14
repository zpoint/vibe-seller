import logging
import os
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.ai.claude_backend_manager import agent_manager
from app.auth import get_current_user, is_auth_required
from app.browser.manager import kill_aux_daemons
from app.database import get_db
from app.env_options import Options
from app.models.app_settings import AppSettings
from app.models.schedule_constants import USER_SELECTABLE_PHASE_MODES
from app.models.user import User
from app.scheduler.task_cleanup import (
    DEFAULT_TASK_RETENTION_DAYS,
    TASK_RETENTION_KEY,
)
from app.telemetry_events import TelemetryEvent
from app.utils.timezone import get_server_timezone
from app.workspace import gws_integration
from app.workspace.skills_sync import skills_sync

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/settings', tags=['settings'])

# Keys that can be read/written via the generic GET/PUT /api/settings.
# `google_workspace_enabled` is intentionally NOT here — flipping it
# via plain PUT would leave the DB flag out of sync with the actual
# filesystem (e.g., flag=true but .claude/skills/gws/ missing). It's
# managed exclusively via the dedicated enable/disable endpoints
# below, which run the full install/uninstall pipeline.
ALLOWED_KEYS = {
    'auth_required',
    'browser_headless',
    'max_agent_concurrency',
    'default_schedule_phase_mode',
    'default_schedule_timezone',
    'telemetry_enabled',
    TASK_RETENTION_KEY,
}

MAX_CONCURRENCY_LIMIT = 10


def _bucket_setting_value(key: str, value: str):
    """Turn a setting value into a privacy-safe summary for telemetry."""
    if key in {'auth_required', 'telemetry_enabled', 'browser_headless'}:
        return value == 'true'
    if key == 'max_agent_concurrency':
        try:
            return int(value)
        except (ValueError, TypeError):
            return None
    if key == 'default_schedule_phase_mode':
        return value
    if key == 'default_schedule_timezone':
        return telemetry.tz_continent_bucket(value)
    if key == TASK_RETENTION_KEY:
        try:
            return telemetry.retention_days_bucket(int(value))
        except (ValueError, TypeError):
            return None
    return None


# 0 disables the daily cleanup; 3650 (10y) is the upper bound the
# UI offers, plenty for users who effectively want to keep forever.
MAX_TASK_RETENTION_DAYS = 3650


@router.get('')
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Return app settings. Public when auth is off."""
    auth_on = await is_auth_required(db)
    if auth_on:
        # When auth is on, only return to authenticated users
        # (but we can't use Depends here for conditional auth,
        # so we just return settings publicly — they're not
        # sensitive. The PUT is admin-gated.)
        pass

    result = await db.execute(
        select(AppSettings).where(AppSettings.key.in_(ALLOWED_KEYS))
    )
    settings: dict[str, str] = {s.key: s.value for s in result.scalars()}
    # Return env-var default for concurrency if not in DB
    if 'max_agent_concurrency' not in settings:
        settings['max_agent_concurrency'] = Options.MAX_AGENT_CONCURRENCY.get()
    # Return server's local timezone as the default when unset
    if 'default_schedule_timezone' not in settings:
        settings['default_schedule_timezone'] = get_server_timezone()
    if TASK_RETENTION_KEY not in settings:
        settings[TASK_RETENTION_KEY] = str(DEFAULT_TASK_RETENTION_DAYS)
    if 'telemetry_enabled' not in settings:
        settings['telemetry_enabled'] = 'true'
    if 'browser_headless' not in settings:
        # Default: headless (works in every environment). Flip to
        # 'false' in Settings → General to make agent browsers
        # visible on a workstation.
        settings['browser_headless'] = 'true'
    # Reflect env-var opt-out in the value the frontend reads, so it
    # doesn't init PostHog while the backend stays silent.
    if telemetry._disabled_via_env():
        settings['telemetry_enabled'] = 'false'
    install_id = telemetry.install_id()
    if install_id:
        settings['install_id'] = install_id
    return settings


@router.put('')
async def update_settings(
    body: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Update app settings. Admin only."""
    if current_user.role != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')

    # Keys that store boolean values — normalize to 'true'/'false'
    bool_keys = {'auth_required', 'telemetry_enabled', 'browser_headless'}
    int_keys = {'max_agent_concurrency', TASK_RETENTION_KEY}
    # (key, lo, hi) — clamp ranges per int key.
    int_ranges: dict[str, tuple[int, int]] = {
        'max_agent_concurrency': (1, MAX_CONCURRENCY_LIMIT),
        TASK_RETENTION_KEY: (0, MAX_TASK_RETENTION_DAYS),
    }
    enum_keys = {
        'default_schedule_phase_mode': USER_SELECTABLE_PHASE_MODES,
    }
    tz_keys = {'default_schedule_timezone'}
    accepted_ints: dict[str, int] = {}

    accepted_settings: dict[str, str] = {}
    for key, value in body.items():
        if key not in ALLOWED_KEYS:
            continue
        # Normalize booleans
        if key in bool_keys:
            value = (
                'true'
                if str(value).strip().lower() in ('true', '1')
                else 'false'
            )
        elif key in int_keys:
            try:
                int_val = int(value)
            except (ValueError, TypeError):
                continue
            lo, hi = int_ranges[key]
            int_val = max(lo, min(hi, int_val))
            accepted_ints[key] = int_val
            value = str(int_val)
        elif key in enum_keys:
            value = str(value)
            if value not in enum_keys[key]:
                continue  # silently skip invalid enum values
        elif key in tz_keys:
            tz = str(value)
            try:
                ZoneInfo(tz)
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail=f'Invalid timezone: {tz}',
                )
            value = tz
        else:
            value = str(value)
        accepted_settings[key] = value
        existing = await db.get(AppSettings, key)
        if existing:
            existing.value = value
        else:
            db.add(AppSettings(key=key, value=value))

    await db.commit()

    # Live-update agent concurrency if successfully validated
    if 'max_agent_concurrency' in accepted_ints:
        agent_manager.set_max_concurrent(accepted_ints['max_agent_concurrency'])

    # Update the telemetry opt-out cache BEFORE firing setting_changed
    # so a re-enable transition (false → true) actually emits — without
    # this, ``send`` no-ops on the very event recording the toggle.
    if 'telemetry_enabled' in body:
        row = await db.get(AppSettings, 'telemetry_enabled')
        telemetry.set_db_disabled(bool(row and row.value == 'false'))

    # Project browser_headless onto BROWSER_USE_HEADLESS env so the
    # next agent / browser-use subprocess inherits the new value
    # without requiring a server restart. See main.py
    # `_apply_persisted_browser_headless` for the full story.
    if 'browser_headless' in body:
        row = await db.get(AppSettings, 'browser_headless')
        os.environ['BROWSER_USE_HEADLESS'] = (
            'true' if row and row.value == 'true' else 'false'
        )
        # Kill aux daemons so the next agent call respawns one with
        # the new wrapper flags (--headed). The UI warns the user
        # before this fires.
        try:
            await kill_aux_daemons()
        except Exception:
            logger.exception('kill_aux_daemons failed')

    # Telemetry — one event per accepted setting, with bucketed value.
    for key, value in accepted_settings.items():
        try:
            telemetry.send(
                TelemetryEvent.SETTING_CHANGED,
                {'key': key, 'to_value': _bucket_setting_value(key, value)},
            )
        except Exception:
            pass

    return {'ok': True}


# ── Google Workspace integration ───────────────────────


async def _read_gws_enabled(db: AsyncSession) -> bool:
    """Read google_workspace_enabled from AppSettings (default false)."""
    row = await db.get(AppSettings, 'google_workspace_enabled')
    return bool(row and row.value == 'true')


async def _write_gws_enabled(db: AsyncSession, enabled: bool) -> None:
    """Persist google_workspace_enabled flag."""
    value = 'true' if enabled else 'false'
    row = await db.get(AppSettings, 'google_workspace_enabled')
    if row:
        row.value = value
    else:
        db.add(AppSettings(key='google_workspace_enabled', value=value))
    await db.commit()


@router.get('/google-workspace/status')
async def gws_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Report prereqs (gws binary + auth) and current enabled flag.

    Requires auth: shells out to `gws --version` + `gws auth status`,
    which are cheap but spawn subprocesses — we don't want anonymous
    callers able to trigger that when `auth_required=true`. When auth
    is off, `get_current_user` returns the default admin so the UI
    still works out of the box. Any authenticated role is fine — the
    response doesn't leak secrets.
    """
    status = await gws_integration.check_status()
    enabled = await _read_gws_enabled(db)
    return {
        'binary': status['binary'],
        'auth': status['auth'],
        'auth_reason': status.get('auth_reason'),
        'version': status['version'],
        'detail': status.get('detail', {}),
        'enabled': enabled,
        'installed': gws_integration.is_installed(),
    }


@router.post('/google-workspace/enable')
async def gws_enable(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Validate prereqs, install skills, set flag. Admin only."""
    if current_user.role != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')

    status = await gws_integration.check_status()
    if not status['binary']:
        raise HTTPException(
            status_code=400,
            detail=(
                'gws binary not found on PATH. Install from '
                'https://github.com/googleworkspace/cli/releases'
            ),
        )
    if not status['auth']:
        raise HTTPException(
            status_code=400,
            detail=(
                'gws is installed but not authenticated. '
                "Run 'gws auth login' in a terminal."
            ),
        )

    try:
        result = await gws_integration.install_skills()
    except Exception as e:
        logger.exception('gws install failed')
        raise HTTPException(
            status_code=500, detail=f'gws install failed: {e}'
        ) from e

    skills_sync.mark_gws_installed(True)
    await _write_gws_enabled(db, True)
    telemetry.send(
        TelemetryEvent.INTEGRATION_GWS_ENABLED,
        {
            'success': bool(result.get('installed', True)),
            'skill_count_bucket': telemetry.count_bucket(
                int(result.get('count') or 0)
            ),
        },
    )
    return {
        'ok': True,
        'installed': result.get('installed', True),
        'count': result.get('count', 0),
        'subskills': result.get('subskills', []),
    }


@router.post('/google-workspace/disable')
async def gws_disable(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Remove gws/ umbrella and clear flag. Admin only. Idempotent."""
    if current_user.role != 'admin':
        raise HTTPException(status_code=403, detail='Admin access required')

    result = await gws_integration.uninstall_skills()
    skills_sync.mark_gws_installed(False)
    await _write_gws_enabled(db, False)
    telemetry.send(TelemetryEvent.INTEGRATION_GWS_DISABLED, {})
    return {'ok': True, 'removed': result.get('removed', False)}
