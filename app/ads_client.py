"""Talking to an external Amazon Ads service.

vibe-seller holds no Amazon credential and speaks no Amazon API. When a
store is bound to an ads service, everything advertising goes through
that service: it owns the OAuth grant, the historical data and the rules.
This module is the whole client side of that relationship.

**Three things live here and nowhere else**, which is what keeps this a
thin, stable integration rather than a second implementation:

1. The api key, which is injected server-side and **never reaches the
   agent's context.** A ``curl``-based design would put it in bash
   commands, task logs and possibly a task result.
2. The translation from a local store id to the service's ``store_key``.
   The agent names a store the way a person would; it never learns the
   service's identifiers.
3. One generic pass-through. Adding an endpoint on the service must not
   require a vibe-seller release — a released client cannot be upgraded
   on the service's schedule, so the surface it pins has to be the
   narrowest possible one.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from sqlalchemy import select

from app.models.app_settings import AppSettings
from app.models.store import Store
from app.utils.crypto import decrypt_password, encrypt_password

logger = logging.getLogger(__name__)

HOST_KEY = 'ads_service_host'
KEY_KEY = 'ads_service_api_key_enc'

DEFAULT_TIMEOUT = 60.0

#: Paths the client itself owns. The agent's pass-through refuses them:
#: binding is a person's decision made in the UI, not something an agent
#: talks itself into.
RESERVED_PREFIXES = ('/me', '/auth-url', '/assignments', '/ads-binding')


class AdsServiceError(RuntimeError):
    """The ads service is unreachable, unconfigured, or said no."""


class AdsNotConfigured(AdsServiceError):
    """No ads service is bound. Always says how to fix it."""


async def get_config(session) -> dict[str, Any]:
    """Current binding, with the key masked. Safe to return to a UI."""
    host = await session.get(AppSettings, HOST_KEY)
    key = await session.get(AppSettings, KEY_KEY)
    return {
        'host': host.value if host else '',
        'configured': bool(host and host.value and key and key.value),
    }


async def set_config(session, host: str, api_key: str | None) -> None:
    """Bind (or rebind) the service. An empty host unbinds."""
    host = (host or '').strip().rstrip('/')
    await _put(session, HOST_KEY, host)
    if api_key:
        await _put(session, KEY_KEY, encrypt_password(api_key.strip()))
    elif not host:
        await _put(session, KEY_KEY, '')
    await session.commit()


async def _put(session, key: str, value: str) -> None:
    row = await session.get(AppSettings, key)
    if row is None:
        session.add(AppSettings(key=key, value=value))
    else:
        row.value = value


async def _credentials(session) -> tuple[str, str]:
    host = await session.get(AppSettings, HOST_KEY)
    key = await session.get(AppSettings, KEY_KEY)
    if not (host and host.value and key and key.value):
        raise AdsNotConfigured(
            'No ads service is bound. Set the host and API key in '
            'Settings → Integrations → Amazon Ads.'
        )
    return host.value, decrypt_password(key.value)


async def call(
    session,
    path: str,
    *,
    method: str = 'GET',
    params: dict | None = None,
    json_body: dict | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """One request to the bound service, authenticated server-side."""
    host, api_key = await _credentials(session)
    if not path.startswith('/'):
        path = '/' + path

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.request(
                method.upper(),
                f'{host}{path}',
                params=params,
                json=json_body,
                headers={'X-Api-Key': api_key},
            )
        except httpx.RequestError as exc:
            raise AdsServiceError(
                f'Could not reach the ads service at {host}: {exc}'
            )

    if response.status_code >= 400:
        raise AdsServiceError(
            f'{method.upper()} {path} failed ({response.status_code}): '
            f'{response.text[:400]}'
        )
    if not response.content:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


async def sync_stores(session) -> list[dict]:
    """Upload this deployment's stores and record what came back.

    The upload carries an id and a name per store and **nothing about
    marketplaces**: where a store sells is not where it holds an
    advertising account, and only the service can establish the latter —
    it reads them off the profiles the advertiser actually granted.
    """
    stores = (await session.execute(select(Store))).scalars().all()
    payload = {'stores': [{'local_id': s.id, 'name': s.name} for s in stores]}
    body = await call(session, '/me/stores', method='POST', json_body=payload)
    rows = (body or {}).get('stores', [])

    by_local: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get('authorized') and row.get('store_key'):
            by_local.setdefault(row['local_id'], {})[
                row.get('marketplace') or ''
            ] = row['store_key']

    for store in stores:
        keys = by_local.get(store.id, {})
        store.ads_store_keys = json.dumps(keys, sort_keys=True)
        store.ads_authorized = bool(keys)
    await session.commit()
    return rows


async def auth_url(session, store_id: str, region: str = 'EU') -> dict:
    return await call(
        session,
        '/auth-url',
        params={'local_id': store_id, 'region': region},
    )


def store_keys(store: Store) -> dict[str, str]:
    """``{marketplace: store_key}`` for a store, or empty if unbound."""
    try:
        return json.loads(store.ads_store_keys or '{}')
    except (ValueError, TypeError):
        return {}


def resolve_store_key(store: Store, marketplace: str | None) -> str:
    """The key for one marketplace of *store*.

    Raises:
        AdsServiceError: when the store is unbound, or when it has
            several marketplaces and the caller named none. Picking one
            silently is how a report about one marketplace ends up
            labelled as another.
    """
    keys = store_keys(store)
    if not keys:
        raise AdsServiceError(
            f'Store {store.name!r} is not authorized with the ads '
            f'service. Authorize it in Settings → Integrations.'
        )
    if marketplace:
        code = marketplace.strip().upper()
        if code not in keys:
            raise AdsServiceError(
                f'Store {store.name!r} has no authorized marketplace '
                f'{code!r}. Authorized: {", ".join(sorted(keys))}.'
            )
        return keys[code]
    if len(keys) == 1:
        return next(iter(keys.values()))
    raise AdsServiceError(
        f'Store {store.name!r} is authorized on '
        f'{", ".join(sorted(keys))} — name the marketplace.'
    )
