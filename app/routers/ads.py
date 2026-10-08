"""Binding this deployment to an external Amazon Ads service.

Four routes, all of them about the binding rather than about advertising:
the advertising itself is the service's job and reaches the agent through
one MCP tool.

The authorization hand-off is deliberately one-way. We mint nothing and
receive nothing back: the client asks the service for a consent URL, shows
it to the person, and later notices that a store became authorized. The
whole OAuth round trip happens on the service's own domain, so this
deployment never needs a public address and there is no return URL of ours
for anyone to point somewhere else.
"""

from __future__ import annotations

from datetime import UTC, datetime
import logging
from pathlib import Path
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from app import ads_client, ads_skill
from app.auth import get_current_user, require_admin
from app.config import VIBE_SELLER_DIR
from app.database import get_db
from app.models.store import Store
from app.models.task import Task
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/ads', tags=['ads'])


class ConfigIn(BaseModel):
    #: Empty unbinds. The service itself is not configurable here.
    api_key: str | None = None


@router.get('/config')
async def read_config(
    db=Depends(get_db), _user: User = Depends(get_current_user)
) -> dict:
    """Is a service bound, and which one. The key is never returned."""
    return await ads_client.get_config(db)


@router.put('/config')
async def write_config(
    payload: ConfigIn,
    db=Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    """Bind, rebind, or unbind. An empty key unbinds. Admin only, like
    every deployment-wide setting.

    The key is verified immediately by asking the service who we are —
    a binding that only fails later, inside an agent's task, costs a
    whole run to diagnose.
    """
    await ads_client.set_config(db, payload.api_key)
    if not payload.api_key:
        ads_skill.remove()
        return {'configured': False}

    try:
        identity = await ads_client.call(db, '/me')
    except ads_client.AdsServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # A verified binding pulls the skill immediately. There is nothing
    # for a person to decide here: a bound deployment always wants the
    # current bundle, and a button they have to find is a button they
    # will forget, leaving agents on instructions older than the service.
    skill = await ads_skill.refresh(db)
    return {'configured': True, 'service': identity, 'skill': skill}


@router.post('/stores/sync')
async def sync_stores(
    db=Depends(get_db), _user: User = Depends(get_current_user)
) -> dict:
    """Upload our stores, record which are authorized.

    Also the polling endpoint: the consent finishes on the service's
    domain and nothing calls back here, so this is how a deployment finds
    out that a store became authorized.
    """
    try:
        rows = await ads_client.sync_stores(db)
    except ads_client.AdsServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {'stores': rows}


@router.get('/stores/{store_id}/auth-url')
async def store_auth_url(
    store_id: str,
    region: str = 'EU',
    db=Depends(get_db),
    _user: User = Depends(require_admin),
) -> dict:
    """A consent link for one store, plus how to open it.

    **Where the link is opened matters more than the link.** The consent
    screen has to see the seller's own Amazon session, and for a store on
    an anti-detect backend that session lives inside that browser's store
    window — not in the operator's everyday browser. Getting this wrong
    produces a successful authorization of the wrong Amazon account, which
    is worse than a failure.
    """
    store = (
        await db.execute(select(Store).where(Store.id == store_id))
    ).scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail='Store not found')

    try:
        body = await ads_client.auth_url(db, store_id, region=region)
    except ads_client.AdsServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    needs_antidetect = store.browser_backend == 'ziniao'
    body['open_in'] = 'ziniao' if needs_antidetect else 'browser'
    body['instruction_key'] = (
        'ads.authorize.openInZiniao'
        if needs_antidetect
        else 'ads.authorize.openHere'
    )
    return body


class CallIn(BaseModel):
    path: str
    method: str = 'GET'
    params: dict | None = None
    body: dict | None = None
    #: A store name or id. Resolved to the service's ``store_key`` here,
    #: so the agent never learns the service's identifiers and the key
    #: never enters its context.
    store: str | None = None
    marketplace: str | None = None
    #: The calling task. A large answer arrives as a file, saved into this
    #: task's folder so the agent can open it.
    task_id: str | None = None


@router.post('/call')
async def agent_call(
    payload: CallIn,
    db=Depends(get_db),
    _user: User = Depends(get_current_user),
) -> dict:
    """The agent's only door to the ads service.

    Generic on purpose. A typed route per service endpoint would make
    every new endpoint a vibe-seller release that every deployment has to
    take before it can be used; a pass-through makes it a skill update.
    What constrains the agent is the skill, which the service can revise
    at any time — not a schema frozen into a released client.
    """
    path = payload.path if payload.path.startswith('/') else '/' + payload.path
    if path.startswith(ads_client.RESERVED_PREFIXES):
        raise HTTPException(
            status_code=403,
            detail=(
                f'{path} is part of binding, which a person does in '
                f'Settings — not something a task may call.'
            ),
        )

    params = dict(payload.params or {})
    store = await _store_for_task(db, payload.task_id, payload.store)
    if store is not None:
        try:
            params['store_key'] = ads_client.resolve_store_key(
                store, payload.marketplace
            )
        except ads_client.AdsServiceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    try:
        result = await ads_client.call(
            db,
            path,
            method=payload.method,
            params=params or None,
            json_body=payload.body,
        )
    except ads_client.AdsServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if isinstance(result, ads_client.AdsFile):
        return {'result': _save_file(result, payload.task_id)}
    return {'result': result}


#: A task id as the scheduler mints it. Anything else is refused before it
#: becomes part of a path.
_TASK_ID = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)


def _save_file(answer: ads_client.AdsFile, task_id: str | None) -> dict:
    """Write a file answer into the task's folder; return where it is.

    The rows go to disk, not into the reply: a reply this large is exactly
    what the service declined to put in the agent's context.
    """
    if not task_id or not _TASK_ID.match(task_id):
        raise HTTPException(
            status_code=400,
            detail=(
                "This answer is a file, and a file is saved into a task's "
                'folder: call it from a task.'
            ),
        )
    folder = VIBE_SELLER_DIR / 'tasks' / task_id / 'ads-data'
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(Path(answer.filename).name).stem or 'ads-data'
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S')
    path = folder / f'{stem}-{stamp}.csv'
    path.write_bytes(answer.content)
    return {
        **answer.meta,
        'file': str(path),
        'bytes': len(answer.content),
        'note': (
            'Too large for a reply, so saved as a CSV file. Open and filter '
            'it there; do not ask for the same data again.'
        ),
    }


async def _store_for_task(db, task_id: str | None, needle: str | None):
    """The store this call may reach, from the task that is making it.

    A task belongs to one store, and its agent may reach that store's
    advertising and no other: naming another store is refused, and naming
    none means its own. Only a task with no store (an all-stores run) may
    name any. A call with no task is refused — the agent's tool always
    sends its task, and nothing else calls this route.
    """
    if not task_id or not _TASK_ID.match(task_id):
        raise HTTPException(
            status_code=400,
            detail='Ads calls are made from a task; this one names none.',
        )
    task = await db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail='Unknown task.')
    if not task.store_id:
        return await _find_store(db, needle) if needle else None
    own = await db.get(Store, task.store_id)
    if needle and needle not in (own.id, own.name):
        raise HTTPException(
            status_code=403,
            detail=(
                f'This task works on store {own.name!r}; it may not reach '
                f'{needle!r}. Run a task on that store, or a task with no '
                f'store, to look at it.'
            ),
        )
    return own


async def _find_store(db, needle: str) -> Store:
    """A store by id or by name, with an error that names the options."""
    stores = (await db.execute(select(Store))).scalars().all()
    for store in stores:
        if store.id == needle:
            return store
    matches = [s for s in stores if s.name == needle]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise HTTPException(
            status_code=404,
            detail=(
                f'No store {needle!r}. Known: '
                f'{", ".join(sorted(s.name for s in stores))}'
            ),
        )
    raise HTTPException(
        status_code=400, detail=f'{needle!r} matches several stores.'
    )
