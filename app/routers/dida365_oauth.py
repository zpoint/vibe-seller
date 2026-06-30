"""OAuth2 router for Dida365 / TickTick integration."""

import asyncio
import logging
import secrets
import time
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
import httpx
from pydantic import BaseModel

from app.auth import get_current_user
from app.browser.manager import atomic_write_json, read_mcp_config
from app.events_system.syncer import load_backend_config, save_backend_config
from app.platform import safe_chmod, venv_python
from app.utils.crypto import decrypt_password, encrypt_password
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/dida365', tags=['dida365'])

# Single-use state tokens: {state: {'expires': float}}
_pending_states: dict[str, dict] = {}

# Auto-managed ticktick-mcp installation
TICKTICK_MCP_REPO = 'https://github.com/jacepark12/ticktick-mcp.git'
TICKTICK_MCP_DIR = VIBE_SELLER_DIR / 'ticktick-mcp'

# URL mapping per service type
_SERVICE_URLS: dict[str, dict[str, str]] = {
    'dida365': {
        'auth_url': 'https://dida365.com/oauth/authorize',
        'token_url': 'https://dida365.com/oauth/token',
        'base_url': 'https://api.dida365.com/open/v1',
    },
    'ticktick': {
        'auth_url': 'https://ticktick.com/oauth/authorize',
        'token_url': 'https://ticktick.com/oauth/token',
        'base_url': 'https://api.ticktick.com/open/v1',
    },
}


class AuthorizeRequest(BaseModel):
    client_id: str
    client_secret: str
    service_type: str = 'dida365'


class ConfigureRequest(BaseModel):
    project_id: str


def _prune_expired_states() -> None:
    """Remove expired state tokens."""
    now = time.time()
    expired = [k for k, v in _pending_states.items() if v['expires'] < now]
    for k in expired:
        del _pending_states[k]


async def ensure_ticktick_mcp() -> str:
    """Clone and install ticktick-mcp if not present.

    Returns the path to the ticktick-mcp directory.
    """
    mcp_dir = str(TICKTICK_MCP_DIR)
    if TICKTICK_MCP_DIR.is_dir():
        return mcp_dir

    logger.info('Cloning ticktick-mcp to %s', mcp_dir)
    proc = await asyncio.create_subprocess_exec(
        'git',
        'clone',
        TICKTICK_MCP_REPO,
        mcp_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f'git clone failed: {stderr.decode().strip()}')

    logger.info('Installing ticktick-mcp dependencies')
    proc = await asyncio.create_subprocess_exec(
        'uv',
        'venv',
        str(TICKTICK_MCP_DIR / '.venv'),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await proc.communicate()

    proc = await asyncio.create_subprocess_exec(
        'uv',
        'pip',
        'install',
        '-e',
        mcp_dir,
        '--python',
        str(venv_python(TICKTICK_MCP_DIR / '.venv')),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(
            'uv pip install failed (will try uv run): %s',
            stderr.decode().strip(),
        )

    return mcp_dir


def _register_ticktick_mcp(config: dict) -> None:
    """Register ticktick MCP server in .mcp.json."""
    mcp_path = str(TICKTICK_MCP_DIR)
    if not TICKTICK_MCP_DIR.is_dir():
        return

    access_token = decrypt_password(config['access_token_enc'])

    env = {'TICKTICK_ACCESS_TOKEN': access_token}
    if config.get('service_type') == 'dida365':
        env['TICKTICK_BASE_URL'] = 'https://api.dida365.com/open/v1'

    mcp_data = read_mcp_config()
    mcp_data['mcpServers']['ticktick'] = {
        'command': 'uv',
        'args': [
            'run',
            '--directory',
            mcp_path,
            '-m',
            'ticktick_mcp.cli',
            'run',
        ],
        'env': env,
    }

    mcp_json_path = VIBE_SELLER_DIR / '.mcp.json'
    atomic_write_json(mcp_json_path, mcp_data)
    try:
        safe_chmod(mcp_json_path, 0o600)
    except OSError:
        pass


async def refresh_token_if_needed() -> bool:
    """Refresh access token if expiring within 5 minutes.

    Returns True if the token was refreshed.
    """
    config = load_backend_config('dida365')
    if not config.get('access_token_enc'):
        return False

    expires_at = config.get('expires_at', 0)
    if time.time() < expires_at - 300:
        return False

    refresh_tok = config.get('refresh_token_enc')
    if not refresh_tok:
        return False

    try:
        client_id = config['client_id']
        client_secret = decrypt_password(config['client_secret_enc'])
        token_url = config['token_url']
        refresh_token = decrypt_password(refresh_tok)
    except Exception as e:
        logger.warning('Failed to decrypt dida365 creds: %s', e)
        return False

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                token_url,
                data={
                    'grant_type': 'refresh_token',
                    'refresh_token': refresh_token,
                },
                auth=(client_id, client_secret),
            )
            resp.raise_for_status()
            tokens = resp.json()
    except Exception as e:
        logger.warning('Dida365 token refresh failed: %s', e)
        return False

    config['access_token_enc'] = encrypt_password(tokens['access_token'])
    if tokens.get('refresh_token'):
        config['refresh_token_enc'] = encrypt_password(tokens['refresh_token'])
    config['expires_at'] = time.time() + tokens.get('expires_in', 3600)
    save_backend_config('dida365', config)

    # Re-register MCP with new token
    _register_ticktick_mcp(config)

    logger.info('Dida365 access token refreshed')
    return True


@router.get('/status')
async def get_status(
    _user=Depends(get_current_user),
):
    """Check Dida365/TickTick connection status."""
    config = load_backend_config('dida365')
    return {
        'connected': bool(config.get('access_token_enc')),
        'service_type': config.get('service_type', ''),
        'project_id': config.get('project_id', ''),
    }


@router.post('/setup-mcp')
async def setup_mcp(
    _user=Depends(get_current_user),
):
    """Clone and install ticktick-mcp if not present."""
    already_installed = TICKTICK_MCP_DIR.is_dir()
    try:
        await ensure_ticktick_mcp()
    except RuntimeError as e:
        raise HTTPException(
            status_code=500,
            detail=str(e),
        ) from e
    return {
        'ok': True,
        'already_installed': already_installed,
        'path': str(TICKTICK_MCP_DIR),
    }


@router.post('/authorize')
async def authorize(
    body: AuthorizeRequest,
    request: Request,
    _user=Depends(get_current_user),
):
    """Start OAuth2 flow for Dida365/TickTick."""
    svc = body.service_type
    if svc not in _SERVICE_URLS:
        raise HTTPException(
            status_code=400,
            detail=f'Invalid service_type: {svc}',
        )

    urls = _SERVICE_URLS[svc]

    # Save client credentials
    config = load_backend_config('dida365')
    config['service_type'] = svc
    config['client_id'] = body.client_id
    config['client_secret_enc'] = encrypt_password(body.client_secret)
    config['base_url'] = urls['base_url']
    config['token_url'] = urls['token_url']
    config['auth_url'] = urls['auth_url']
    save_backend_config('dida365', config)

    # Generate single-use state and store callback URL for reuse
    # in /callback (ensures exact redirect_uri match).
    _prune_expired_states()
    state = secrets.token_urlsafe(32)
    callback_url = f'{request.base_url}api/dida365/callback'
    _pending_states[state] = {
        'expires': time.time() + 300,
        'redirect_uri': callback_url,
    }
    params = urlencode(
        {
            'client_id': body.client_id,
            'response_type': 'code',
            'redirect_uri': callback_url,
            'scope': 'tasks:read tasks:write',
            'state': state,
        },
        quote_via=quote,
    )
    auth_url = f'{urls["auth_url"]}?{params}'

    return {
        'auth_url': auth_url,
        'callback_url': callback_url,
    }


@router.get('/callback', response_class=HTMLResponse)
async def oauth_callback(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    """OAuth2 callback — browser redirect, no auth."""
    if error:
        return HTMLResponse(
            '<html><body>'
            '<h2>Authorization Failed</h2>'
            f'<p>Error: {error}</p>'
            '<p>You can close this window.</p>'
            '</body></html>',
            status_code=400,
        )

    if not code or not state:
        return HTMLResponse(
            '<html><body>'
            '<h2>Invalid Request</h2>'
            '<p>Missing code or state parameter.</p>'
            '</body></html>',
            status_code=400,
        )

    # Validate state (single-use) and retrieve stored redirect_uri
    _prune_expired_states()
    if state not in _pending_states:
        return HTMLResponse(
            '<html><body>'
            '<h2>Invalid or Expired State</h2>'
            '<p>Please try connecting again.</p>'
            '</body></html>',
            status_code=400,
        )
    state_data = _pending_states.pop(state)
    callback_url = state_data.get('redirect_uri', '')

    config = load_backend_config('dida365')
    if not config.get('client_id'):
        return HTMLResponse(
            '<html><body>'
            '<h2>Configuration Error</h2>'
            '<p>Client credentials not found.</p>'
            '</body></html>',
            status_code=500,
        )

    client_id = config['client_id']
    try:
        client_secret = decrypt_password(config['client_secret_enc'])
    except Exception:
        return HTMLResponse(
            '<html><body>'
            '<h2>Configuration Error</h2>'
            '<p>Failed to decrypt client secret.</p>'
            '</body></html>',
            status_code=500,
        )

    token_url = config['token_url']

    # Exchange code for tokens via Basic Auth
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                token_url,
                data={
                    'grant_type': 'authorization_code',
                    'code': code,
                    'redirect_uri': callback_url,
                },
                auth=(client_id, client_secret),
            )
            resp.raise_for_status()
            tokens = resp.json()
    except Exception as e:
        logger.error('Dida365 token exchange failed: %s', e)
        return HTMLResponse(
            '<html><body>'
            '<h2>Token Exchange Failed</h2>'
            f'<p>{e}</p>'
            '<p>You can close this window.</p>'
            '</body></html>',
            status_code=500,
        )

    config['access_token_enc'] = encrypt_password(tokens['access_token'])
    if tokens.get('refresh_token'):
        config['refresh_token_enc'] = encrypt_password(tokens['refresh_token'])
    config['expires_at'] = time.time() + tokens.get('expires_in', 3600)
    save_backend_config('dida365', config)

    # Register MCP server
    _register_ticktick_mcp(config)

    return HTMLResponse(
        '<html><body>'
        '<h2>Connected Successfully!</h2>'
        '<p>You can close this window and return to '
        'Vibe Seller.</p>'
        '<script>window.close()</script>'
        '</body></html>'
    )


@router.get('/projects')
async def list_projects(
    _user=Depends(get_current_user),
):
    """List TickTick/Dida365 projects."""
    config = load_backend_config('dida365')
    if not config.get('access_token_enc'):
        raise HTTPException(status_code=400, detail='Not connected')

    # Try refresh first
    await refresh_token_if_needed()
    config = load_backend_config('dida365')

    access_token = decrypt_password(config['access_token_enc'])
    base_url = config.get('base_url', 'https://api.ticktick.com/open/v1')

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f'{base_url}/project',
                headers={'Authorization': f'Bearer {access_token}'},
            )
            if resp.status_code == 401:
                # Token might be stale, try refresh
                refreshed = await refresh_token_if_needed()
                if refreshed:
                    config = load_backend_config('dida365')
                    access_token = decrypt_password(config['access_token_enc'])
                    resp = await client.get(
                        f'{base_url}/project',
                        headers={'Authorization': (f'Bearer {access_token}')},
                    )
                if resp.status_code == 401:
                    raise HTTPException(
                        status_code=401,
                        detail='Token expired, reconnect',
                    )
            resp.raise_for_status()
            projects = resp.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f'Failed to list projects: {e}',
        ) from e

    return [
        {'id': p.get('id', ''), 'name': p.get('name', '')} for p in projects
    ]


@router.post('/configure')
async def configure(
    body: ConfigureRequest,
    _user=Depends(get_current_user),
):
    """Save project ID configuration."""
    config = load_backend_config('dida365')
    config['project_id'] = body.project_id
    save_backend_config('dida365', config)

    # Re-register MCP with updated config
    if config.get('access_token_enc'):
        _register_ticktick_mcp(config)

    return {'ok': True}


@router.delete('/disconnect')
async def disconnect(
    _user=Depends(get_current_user),
):
    """Disconnect Dida365/TickTick integration."""
    config = load_backend_config('dida365')

    # Clear tokens
    for key in [
        'access_token_enc',
        'refresh_token_enc',
        'expires_at',
    ]:
        config.pop(key, None)
    save_backend_config('dida365', config)

    # Remove ticktick from .mcp.json
    mcp_data = read_mcp_config()
    if 'ticktick' in mcp_data.get('mcpServers', {}):
        del mcp_data['mcpServers']['ticktick']
        mcp_json_path = VIBE_SELLER_DIR / '.mcp.json'
        atomic_write_json(mcp_json_path, mcp_data)
        try:
            safe_chmod(mcp_json_path, 0o600)
        except OSError:
            pass

    return {'ok': True}
