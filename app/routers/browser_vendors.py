"""Generic browser-vendor HTTP surface (registry-driven, vendor-neutral).

One route family for every registered vendor — the routes read the
:mod:`app.browser.vendors` registry, so a vendor added through the plugin
context appears here (and in the generic frontend) with zero edits to
this module:

- ``GET /api/browser-vendors`` — the UI-facing metadata (no secrets).
- ``GET|POST /api/browser-vendors/{key}/credentials`` — the stored
  machine login of a vendor that DECLARES ``credential_fields``: masked
  status (secret fields are never returned) and a save that refuses
  while the vendor's stores are bound (the one-account-per-machine
  rule, R8 — applies to ``CARDINALITY_SINGLE`` vendors).
- ``GET /api/browser-vendors/{key}/stores`` — the profiles the vendor's
  logged-in client can see, ``StoreRef`` serialized generically (asdict)
  so new fields flow through untouched, with the structured statuses the
  UI renders (``unsupported_platform`` / ``client_not_running`` /
  ``login_required``).

Secrets never leave: the POST body's secret fields are Fernet-encrypted
at rest, the GET returns only non-secret fields, and the response-side
guard masks anything classified. Per-vendor compat routes (the old
pre-registry per-vendor route families) delegate to the same helpers.
"""

from dataclasses import asdict
import json
import logging

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.browser import vendor_creds
from app.browser.inject.errors import (
    ClientNotRunning,
    InjectError,
    LoginRequired,
)
from app.browser.vendors import (
    CARDINALITY_SINGLE,
    BrowserVendorSpec,
    vendor_spec,
    vendor_specs_metadata,
)
from app.browser.ziniao_utils import get_platform
from app.database import get_db
from app.models.store import Store
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api/browser-vendors', tags=['browser-vendors'])


def _require_spec(key: str) -> BrowserVendorSpec:
    spec = vendor_spec(key)
    if spec is None:
        raise HTTPException(status_code=404, detail='Unknown vendor')
    return spec


async def vendor_store_count(db: AsyncSession, key: str) -> int:
    """How many of this vendor's stores are bound."""
    return (
        await db.execute(
            select(func.count(Store.id)).where(Store.browser_backend == key)
        )
    ).scalar() or 0


async def list_store_refs(spec: BrowserVendorSpec, client, platform: str):
    """The generic enumerate flow, mapping client errors to the structured
    statuses the UI renders.

    ``platform`` is passed in (not read here) so the per-vendor compat
    routes keep their own patchable ``get_platform`` seam. Raises
    ``HTTPException``; returns ``list[StoreRef]``.

    Every :class:`~app.browser.inject.errors.InjectError` the client
    raises becomes ``{"status": <code.lower()>, "message": ...}`` — the
    shared taxonomy is the whole status space, so a vendor's new error
    (a paid gate, an account mismatch, an unsupported kernel) reaches
    the UI as a structured status instead of a 500. ``client_not_running``
    and ``login_required`` keep their exact historical shapes.
    """
    if platform not in spec.platforms:
        raise HTTPException(
            status_code=400,
            detail=json.dumps({
                'status': 'unsupported_platform',
                'platform': platform,
            }),
        )
    try:
        await client.ensure_running()
    except ClientNotRunning as e:
        raise HTTPException(
            status_code=502,
            detail=json.dumps({
                'status': 'client_not_running',
                'message': str(e),
            }),
        ) from e
    try:
        await client.ensure_logged_in()
    except LoginRequired as e:
        raise HTTPException(
            status_code=502,
            detail=json.dumps({'status': 'login_required'}),
        ) from e
    try:
        return await client.list_stores()
    except InjectError as e:
        raise HTTPException(
            status_code=502,
            detail=json.dumps({
                'status': e.code.lower(),
                'message': str(e),
            }),
        ) from e


@router.get('')
async def list_vendors(_user: User = Depends(get_current_user)):
    """Every registered vendor's UI metadata (registration order)."""
    return vendor_specs_metadata()


@router.get('/{key}/credentials')
async def get_vendor_credentials(
    key: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Whether the vendor's machine login is configured + the non-secret
    fields (never the secrets) so the form can show/prefill them."""
    spec = _require_spec(key)
    if not spec.credential_fields:
        raise HTTPException(
            status_code=400,
            detail='This vendor has no machine-level credentials',
        )
    return await vendor_creds.credentials_status(db, spec)


@router.post('/{key}/credentials')
async def save_vendor_credentials(
    key: str,
    data: dict[str, str] = Body(...),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Store the vendor's machine login (secrets encrypted).

    A ``CARDINALITY_SINGLE`` vendor (one account per machine, R8)
    refuses the save while any of its stores is bound: a bound store
    belongs to the current account, so changing the account would
    orphan it. The user deletes those stores first, then adds — the
    whole account-conflict story, spec-driven instead of per-vendor
    code. Vendors that store nothing (empty ``credential_fields`` —
    the user signs in in the client) have no credentials route at all.
    """
    spec = _require_spec(key)
    if not spec.credential_fields:
        raise HTTPException(
            status_code=400,
            detail='This vendor has no machine-level credentials',
        )
    required = [f.key for f in spec.credential_fields if f.required]
    missing = [key for key in required if not (data.get(key) or '').strip()]
    if missing:
        # Name every required field (the message the first migrated
        # vendor's users saw), not just the missing ones.
        if len(required) == 1:
            names = required[0]
        else:
            names = ' '.join([', '.join(required[:-1]), f'and {required[-1]}'])
        raise HTTPException(
            status_code=400,
            detail=f'{names} are all required',
        )
    if (
        spec.account_cardinality == CARDINALITY_SINGLE
        and await vendor_store_count(db, spec.key) > 0
    ):
        raise HTTPException(
            status_code=400,
            detail=json.dumps({'status': 'one_account'}),
        )
    await vendor_creds.save_credentials(db, spec, data)
    return {'ok': True}


@router.get('/{key}/stores')
async def list_vendor_stores(
    key: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """The profiles the vendor's logged-in client can see (dropdown
    source), serialized generically so new StoreRef fields pass through."""
    spec = _require_spec(key)
    if not spec.lists_profiles or spec.client_factory is None:
        raise HTTPException(
            status_code=400,
            detail='This vendor does not list profiles',
        )
    cfg: dict = {}
    await vendor_creds.inject_vendor_config(cfg, db, spec)
    client = spec.client_factory(cfg)
    refs = await list_store_refs(spec, client, get_platform())
    return [asdict(ref) for ref in refs]
