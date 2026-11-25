"""Settings → Integrations → Custom MCP: external MCP servers for tasks.

Every signed-in user can SEE the list (which tools their tasks get);
only an admin adds, toggles, tests or removes one — a server reaches
every task of every user, and a local (stdio) one is a command this
machine runs. API tokens cannot add one at all (``TOKEN_REFUSALS``).

A server is saved only after it connects (the add route runs the
Test handshake first). Credentials are write-only: every response is
:func:`public_view`.
There is no partial edit — changing a token means pasting the
service's config again with ``replace`` — so a masked value can never
round-trip into storage (``normalize_server`` refuses one anyway).

Changes apply to the NEXT launch of a task (new task, follow-up,
retry, schedule fire): the server list is read from the DB on every
launch, never cached in a shared file.
"""

from datetime import UTC, datetime
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import telemetry
from app.auth import get_current_user, require_admin
from app.custom_mcp import (
    McpConfigError,
    display_label,
    normalize_server,
    parse_pasted,
    seal,
    unseal,
)
from app.custom_mcp_probe import ProbeError, probe
from app.custom_mcp_view import public_view
from app.database import get_db
from app.models.custom_mcp_server import CustomMcpServer
from app.models.user import User
from app.telemetry_events import TelemetryEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/custom-mcp-servers', tags=['custom-mcp'])


class CustomMcpCreate(BaseModel):
    # What the user pasted: a vendor's JSON blurb (text), the parsed
    # object, or a bare server URL.
    config: str | dict
    # Names a server the paste leaves unnamed (a bare URL / object),
    # or renames the single server in it.
    name: str | None = None
    # Overwrite servers that already exist under the same names.
    replace: bool = False


class CustomMcpUpdate(BaseModel):
    enabled: bool


def _config_error(e: McpConfigError) -> dict:
    """A 400 body the panel can say in the user's language."""
    return {'code': f'config_{e.code}', 'message': str(e), **e.params}


def _view(row: CustomMcpServer) -> dict:
    try:
        config = unseal(row.config_enc)
        view = public_view(config)
        valid, problem = True, None
        normalize_server(row.name, config)
    except McpConfigError as e:
        view = {'type': 'unknown'}
        valid, problem = False, str(e)
    return {
        'id': row.id,
        'name': row.name,
        # What people call it; the key above is what tool names use.
        'label': row.label or row.name,
        **view,
        'enabled': row.enabled,
        # A stored row that no longer passes is skipped at launch —
        # say so instead of showing it as working.
        'valid': valid,
        'problem': problem,
        'created_at': row.created_at,
        'updated_at': row.updated_at,
    }


async def _get_or_404(db: AsyncSession, server_id: str) -> CustomMcpServer:
    row = await db.get(CustomMcpServer, server_id)
    if row is None:
        raise HTTPException(status_code=404, detail='MCP server not found')
    return row


@router.get('')
async def list_servers(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    rows = await db.execute(
        select(CustomMcpServer).order_by(CustomMcpServer.name)
    )
    return [_view(r) for r in rows.scalars().all()]


@router.post('', status_code=201)
async def add_servers(
    data: CustomMcpCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_admin),
):
    """Add every server in the paste — all or nothing, and only once
    each one CONNECTS.

    The same handshake as the Test button runs before anything is
    written (like the AI-profile save probe): a wrong token or a dead
    URL is answered here, on the settings page, not by a task that
    quietly runs without the tools. A failure is a ``400`` with
    ``detail = {code: 'connect_failed', name, error}`` and nothing is
    stored. The answer carries each server's probe result as ``test``.
    """
    typed = (data.name or '').strip() or None
    try:
        servers = parse_pasted(data.config, typed)
        # The typed name is what people see; it names the one server a
        # paste usually holds.
        label = display_label(typed) if len(servers) == 1 else None
    except McpConfigError as e:
        raise HTTPException(status_code=400, detail=_config_error(e)) from e
    existing = {
        r.name: r
        for r in (
            await db.execute(
                select(CustomMcpServer).where(
                    CustomMcpServer.name.in_(list(servers))
                )
            )
        )
        .scalars()
        .all()
    }
    if existing and not data.replace:
        raise HTTPException(
            status_code=409,
            detail={
                'message': 'A server with this name already exists',
                'names': sorted(existing),
                'labels': sorted(r.label or r.name for r in existing.values()),
            },
        )
    tests: dict[str, dict] = {}
    for name, config in servers.items():
        try:
            tests[name] = {'ok': True, **await probe(config)}
        except ProbeError as e:
            logger.info('Custom MCP %s not saved: %s', name, e)
            raise HTTPException(
                status_code=400,
                detail={
                    'code': 'connect_failed',
                    'name': name,
                    'label': label or name,
                    'error': str(e),
                    'reason': e.reason,
                },
            ) from e
    now = datetime.now(UTC).isoformat()
    saved = []
    for name, config in servers.items():
        row = existing.get(name)
        if row is None:
            row = CustomMcpServer(
                name=name,
                label=label,
                config_enc=seal(config),
                enabled=True,
                created_by=user.id,
            )
            db.add(row)
        else:
            row.config_enc = seal(config)
            row.label = label
            row.updated_at = now
        saved.append(row)
    await db.commit()
    for row in saved:
        await db.refresh(row)
        telemetry.send(
            TelemetryEvent.CUSTOM_MCP_ADDED,
            {
                'transport': servers[row.name].get('type'),
                'replaced': row.name in existing,
            },
        )
    return [{**_view(r), 'test': tests[r.name]} for r in saved]


@router.patch('/{server_id}')
async def update_server(
    server_id: str,
    data: CustomMcpUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    row = await _get_or_404(db, server_id)
    changed = row.enabled != data.enabled
    row.enabled = data.enabled
    row.updated_at = datetime.now(UTC).isoformat()
    await db.commit()
    await db.refresh(row)
    if changed:
        telemetry.send(
            TelemetryEvent.CUSTOM_MCP_TOGGLED, {'enabled': data.enabled}
        )
    return _view(row)


@router.delete('/{server_id}')
async def delete_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    row = await _get_or_404(db, server_id)
    try:
        transport = unseal(row.config_enc).get('type')
    except McpConfigError:
        transport = None
    await db.delete(row)
    await db.commit()
    telemetry.send(TelemetryEvent.CUSTOM_MCP_REMOVED, {'transport': transport})
    return {'ok': True}


@router.post('/{server_id}/test')
async def test_server(
    server_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_admin),
):
    """Connect the way a task would and list the server's tools.

    Always 200: ``ok`` false carries the reason, which the panel
    shows next to the server (a failed test is an answer, not an
    error of this route).
    """
    row = await _get_or_404(db, server_id)
    try:
        config = normalize_server(row.name, unseal(row.config_enc))
    except McpConfigError as e:
        return {'ok': False, 'error': str(e)}
    try:
        result = await probe(config)
    except ProbeError as e:
        logger.info('Custom MCP %s test failed: %s', row.name, e)
        outcome = {'ok': False, 'error': str(e), 'reason': e.reason}
    else:
        outcome = {'ok': True, **result}
    telemetry.send(
        TelemetryEvent.CUSTOM_MCP_TESTED,
        {
            'transport': config.get('type'),
            'ok': outcome['ok'],
            'tool_count': telemetry.count_bucket(outcome.get('tool_count', 0)),
        },
    )
    return outcome
