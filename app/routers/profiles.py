"""API endpoints for AI profile management."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.ai.profiles import ProfileManager, profile_kind
from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.telemetry_events import TelemetryEvent

router = APIRouter(prefix='/api/profiles', tags=['profiles'])


@router.get('')
async def list_profiles(_user: User = Depends(get_current_user)):
    """List all available AI profiles."""
    return {'profiles': ProfileManager.list_profiles()}


@router.post('')
async def create_profile(
    data: dict,
    _user: User = Depends(get_current_user),
):
    """Create a new AI profile."""
    try:
        existing_count = len(ProfileManager.list_profiles())
        profile = ProfileManager.create_profile(
            profile_id=data['id'],
            name=data['name'],
            env=data.get('env', {}),
            description=data.get('description', ''),
            load_global_mcp=data.get('load_global_mcp', False),
        )
        telemetry.send(
            TelemetryEvent.AI_PROFILE_CREATED,
            {
                'provider_kind': profile_kind(profile),
                'is_first_profile': existing_count <= 1,
                'has_global_mcp': bool(profile.get('load_global_mcp')),
            },
        )
        return profile
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.put('/{profile_id}')
async def update_profile(
    profile_id: str,
    data: dict,
    _user: User = Depends(get_current_user),
):
    """Update an AI profile."""
    try:
        profile = ProfileManager.update_profile(profile_id, data)
        return profile
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete('/{profile_id}')
async def delete_profile(
    profile_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an AI profile."""
    try:
        kind = profile_kind(ProfileManager.get_profile(profile_id))
        ProfileManager.delete_profile(profile_id)
        # Reset user default if they were using the deleted profile
        user = await db.get(User, current_user.id)
        if user and user.default_profile_id == profile_id:
            user.default_profile_id = 'default'
            user.updated_at = datetime.now(UTC).isoformat()
            await db.commit()
        telemetry.send(
            TelemetryEvent.AI_PROFILE_DELETED, {'provider_kind': kind}
        )
        return {'ok': True}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.patch('/{profile_id}/set-default')
async def set_default_profile(
    profile_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Set a profile as the user's default."""
    # Verify the profile exists
    profile = ProfileManager.get_profile(profile_id)
    if not profile:
        raise HTTPException(status_code=404, detail='Profile not found')
    user = await db.get(User, current_user.id)
    if not user:
        raise HTTPException(status_code=404, detail='User not found')
    user.default_profile_id = profile_id
    user.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    telemetry.send(
        TelemetryEvent.AI_PROFILE_DEFAULT_SET,
        {'provider_kind': profile_kind(profile)},
    )
    return {'ok': True, 'default_profile_id': profile_id}


@router.get('/presets')
async def get_provider_presets(
    _user: User = Depends(get_current_user),
):
    """Get hardcoded provider presets (Kimi, MiniMax, GLM, etc.)."""
    return {'presets': ProfileManager.get_provider_presets()}
