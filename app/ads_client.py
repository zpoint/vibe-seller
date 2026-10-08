"""Talking to an external Amazon Ads service.

vibe-seller holds no Amazon credential and speaks no Amazon API. When a
store is bound to an ads service, everything advertising goes through
that service: it owns the OAuth grant, the historical data and the rules.
This module is the whole client side of that relationship.

**Three things live here and nowhere else**, which is what keeps this a
thin, stable integration rather than a second implementation:

1. The installation's key, which is minted by the service the first
   time ads are used (nobody types one in), injected server-side, and
   **never reaches the agent's context.** A ``curl``-based design would
   put it in bash commands, task logs and possibly a task result.
2. The translation from a local store id to the service's ``store_key``.
   The agent names a store the way a person would; it never learns the
   service's identifiers.
3. One generic pass-through. Adding an endpoint on the service must not
   require a vibe-seller release — a released client cannot be upgraded
   on the service's schedule, so the surface it pins has to be the
   narrowest possible one.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
import re
from typing import Any

import httpx
from sqlalchemy import select

from app import telemetry
from app.models.app_settings import AppSettings
from app.models.store import Store
from app.utils.crypto import decrypt_password, encrypt_password

logger = logging.getLogger(__name__)

#: The ads service every deployment binds to. A constant, not a setting:
#: there is one service, and a host field is one more thing a person can
#: type wrong into a form whose only effect is to send their store list
#: somewhere else. The environment can point it at a stand-in, which is
#: how the end-to-end tests run a real agent against a fake service.
SERVICE_URL = (
    os.environ.get('VIBE_ADS_SERVICE_URL') or 'https://listwizard.cloud/ads-api'
)

KEY_KEY = 'ads_service_api_key_enc'

DEFAULT_TIMEOUT = 60.0

#: Paths the client itself owns. The agent's pass-through refuses them:
#: binding is a person's decision made in the UI, not something an agent
#: talks itself into.
RESERVED_PREFIXES = (
    '/me',
    '/auth-url',
    '/assignments',
    '/ads-binding',
    '/installations',
)

#: Serialises the first registration, so two requests racing on a fresh
#: install do not each mint a key and keep the second.
_REGISTERING = asyncio.Lock()


@dataclass(frozen=True)
class AdsFile:
    """An answer the service sent as a file because it was too large.

    The service answers a list whole and switches to a file above a size
    an agent can read in its context. ``meta`` is the framing the JSON
    would have carried — ``as_of``, ``window``, the row count.
    """

    filename: str
    content: bytes
    meta: dict


class AdsServiceError(RuntimeError):
    """The ads service is unreachable, unconfigured, or said no."""


async def get_config(session) -> dict[str, Any]:
    """Whether this installation has registered. The key is never returned."""
    key = await session.get(AppSettings, KEY_KEY)
    return {'configured': bool(key and key.value)}


async def _put(session, key: str, value: str) -> None:
    row = await session.get(AppSettings, key)
    if row is None:
        session.add(AppSettings(key=key, value=value))
    else:
        row.value = value


async def _credentials(session) -> tuple[str, str]:
    key = await session.get(AppSettings, KEY_KEY)
    if key and key.value:
        return SERVICE_URL, decrypt_password(key.value)
    return SERVICE_URL, await _register(session)


async def _register(session) -> str:
    """Mint this installation's key with the service, once, and keep it.

    The key only says which installation is calling. What lets the
    service act on a store is the store owner's Amazon consent, so there
    is nothing for a person to hand out or type in — and nothing is sent
    until somebody actually opens the ads settings.
    """
    async with _REGISTERING:
        key = await session.get(AppSettings, KEY_KEY)
        if key and key.value:
            return decrypt_password(key.value)
        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
            try:
                response = await client.post(
                    f'{SERVICE_URL}/installations',
                    # The id shown under the version: what a person quotes
                    # to support, and what the service files us under.
                    json={'installation_id': telemetry.stable_install_id()},
                )
            except httpx.RequestError as exc:
                raise AdsServiceError(
                    f'Could not reach the ads service at {SERVICE_URL}: {exc}'
                )
        if response.status_code >= 400:
            raise AdsServiceError(
                f'Registering with the ads service failed '
                f'({response.status_code}): {response.text[:400]}'
            )
        api_key = response.json()['api_key']
        await _put(session, KEY_KEY, encrypt_password(api_key))
        await session.commit()
        return api_key


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
    disposition = response.headers.get('content-disposition', '')
    if 'attachment' in disposition:
        match = re.search(r'filename="?([^";]+)"?', disposition)
        try:
            meta = json.loads(response.headers.get('x-meta') or '{}')
        except ValueError:
            meta = {}
        return AdsFile(
            filename=(match.group(1) if match else 'ads-data.csv'),
            content=response.content,
            meta=meta,
        )
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
    rows = _ours(stores, (body or {}).get('stores', []))
    await _apply(session, stores, rows)
    return rows


async def unbind(session, store_id: str, *, purge: bool = False) -> dict:
    """Drop one store's Amazon authorization with the service.

    ``purge`` also deletes its history — unless another installation still
    manages the same advertiser, whose history it also is; the service
    keeps those and says so in ``kept_shared``.
    """
    body = await call(
        session,
        f'/me/stores/{store_id}/unbind',
        method='POST',
        json_body={'purge': purge},
    )
    stores = (await session.execute(select(Store))).scalars().all()
    body = dict(body or {})
    body['stores'] = _ours(stores, body.get('stores', []))
    await _apply(session, stores, body['stores'])
    return body


def _ours(stores, rows: list[dict]) -> list[dict]:
    """The service's rows for stores this deployment still has, by their
    local names.

    The service keeps a row for every store id it was ever sent and never
    learns that a store was deleted here, or that the database it came
    from was replaced — so its list can name stores that no longer exist,
    and the same store twice under an old id and a new one. Only the
    local store list says what exists.
    """
    names = {s.id: s.name for s in stores}
    return [
        {**row, 'name': names[row['local_id']]}
        for row in rows
        if row.get('local_id') in names
    ]


async def _apply(session, stores, rows: list[dict]) -> None:
    """Record the service's view of which stores are authorized where."""
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
        record_marketplaces(store, [m for m in keys if m])
    await session.commit()


def record_marketplaces(store: Store, marketplaces: list[str]) -> bool:
    """Add Amazon and the marketplaces the store advertises on to its
    platform and country lists. Returns whether anything changed.

    An authorized Ads profile is Amazon's own word that the store is on
    Amazon in that marketplace — firmer than the lists, which a person or
    an AI guessed. So they are added. Nothing is ever removed: advertising
    nowhere is not the same as selling nowhere, and an unbound or expired
    authorization says nothing about where the store sells.
    """
    if not marketplaces:
        return False
    platforms = _json_list(store.platforms)
    countries = _json_list(store.countries)
    try:
        by_platform = json.loads(store.platform_countries or '{}')
    except (ValueError, TypeError):
        by_platform = {}
    if not isinstance(by_platform, dict):
        by_platform = {}
    amazon = list(by_platform.get('amazon') or [])

    before = (list(platforms), list(countries), list(amazon))
    if 'amazon' not in platforms:
        platforms.append('amazon')
    for code in sorted({m.upper() for m in marketplaces}):
        if code not in countries:
            countries.append(code)
        if code not in amazon:
            amazon.append(code)
    if (platforms, countries, amazon) == before:
        return False

    by_platform['amazon'] = amazon
    store.platforms = json.dumps(platforms)
    store.countries = json.dumps(countries)
    store.platform_countries = json.dumps(by_platform)
    return True


def _json_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or '[]')
    except (ValueError, TypeError):
        return []
    return [str(v) for v in value] if isinstance(value, list) else []


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
