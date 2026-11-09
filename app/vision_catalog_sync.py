"""The network half of the image-model catalog: the ``vision_catalog``
sync job's three steps (catalog from ``main``, live prices, kie.ai
availability). Validation and the served state live in
``app/vision_catalog.py``; this module only fetches and commits there.
Each step fails soft — see :func:`sync_once`.
"""

from __future__ import annotations

import hashlib
import logging

import httpx

from app.env_options import Options
from app.vision_catalog import (
    CatalogSourceError,
    bundled_fingerprint,
    commit,
    current_state,
    flatten,
    match_prices,
    parse_catalog_source,
    price_ref,
    resolve_catalog,
    served,
)

logger = logging.getLogger(__name__)

DEFAULT_CATALOG_URL = (
    'https://raw.githubusercontent.com/zpoint/vibe-seller/main'
    '/app/vision_models.py'
)
PRICING_URL = 'https://api.kie.ai/client/v1/model-pricing/page'
MODELS_URL = 'https://api.kie.ai/api/v1/models'

HTTP_TIMEOUT_S = 20.0
MAX_CATALOG_BYTES = 256 * 1024
# The pricing API caps pageSize at 100; ~640 rows today. The page cap
# bounds a misbehaving ``total``.
_PRICING_PAGE_SIZE = 100
_PRICING_MAX_PAGES = 20

_client_factory = httpx.AsyncClient


# ── Fetch steps ──────────────────────────────────────────────────


def _catalog_url() -> str:
    return Options.VISION_CATALOG_URL.get().strip() or DEFAULT_CATALOG_URL


async def _fetch_catalog(client: httpx.AsyncClient, force: bool) -> dict:
    url = _catalog_url()
    async with client.stream('GET', url, timeout=HTTP_TIMEOUT_S) as resp:
        if resp.status_code != 200:
            return {'status': 'failed', 'error': f'HTTP {resp.status_code}'}
        chunks, size = [], 0
        async for chunk in resp.aiter_bytes():
            size += len(chunk)
            if size > MAX_CATALOG_BYTES:
                return {'status': 'rejected', 'error': 'body too large'}
            chunks.append(chunk)
    raw = b''.join(chunks)
    sha = hashlib.sha256(raw).hexdigest()
    # Same body, fetched under this same release: nothing to do. (After
    # an upgrade the fingerprint differs, so the body is re-adopted.)
    if (
        sha == current_state().catalog_sha
        and current_state().fetched_under == bundled_fingerprint()
        and not force
    ):
        return {'status': 'unchanged'}
    try:
        raw_families, raw_retired = parse_catalog_source(raw.decode('utf-8'))
        fams, _ = resolve_catalog(raw_families, raw_retired)
    except (CatalogSourceError, UnicodeDecodeError) as e:
        return {'status': 'rejected', 'error': str(e)}
    before = served()
    commit(
        raw_families=raw_families,
        raw_retired=raw_retired if isinstance(raw_retired, dict) else {},
        catalog_sha=sha,
        fetched_under=bundled_fingerprint(),
    )
    after = served()
    changed = (before.families, before.retired) != (
        after.families,
        after.retired,
    )
    return {
        'status': 'updated' if changed else 'unchanged',
        'families': len(fams),
    }


async def fetch_pricing_records(client: httpx.AsyncClient) -> list[dict]:
    """Every row of kie.ai's public pricing table (paged)."""
    records: list[dict] = []
    for page in range(1, _PRICING_MAX_PAGES + 1):
        resp = await client.post(
            PRICING_URL,
            json={'pageNum': page, 'pageSize': _PRICING_PAGE_SIZE},
            timeout=HTTP_TIMEOUT_S,
        )
        resp.raise_for_status()
        body = resp.json()
        data = body.get('data') if isinstance(body, dict) else None
        if not isinstance(data, dict) or not isinstance(
            data.get('records'), list
        ):
            raise ValueError(f'unexpected pricing body: {str(body)[:200]}')
        records.extend(data['records'])
        total = data.get('total')
        if not data['records'] or not isinstance(total, int):
            break
        if len(records) >= total:
            break
    return records


async def _fetch_prices(client: httpx.AsyncClient) -> dict:
    records = await fetch_pricing_records(client)
    # Match against the catalog's own prices, not the live ones already
    # served: the drift check must compare a fresh row to the price a
    # maintainer verified, or one bad row would ratchet the anchor.
    catalog = flatten(served().families)
    matched = match_prices(catalog, records)
    if not matched:
        return {'status': 'failed', 'error': 'no price matched'}
    # Replace, don't merge: a tier that no longer matches falls back to
    # its catalog price rather than keeping last week's live one.
    prices = {
        m.id: (matched[m.id], price_ref(m)) for m in catalog if m.id in matched
    }
    changed = prices != current_state().prices
    commit(prices=prices)
    return {
        'status': 'updated' if changed else 'unchanged',
        'priced': len(prices),
        'models': len(catalog),
    }


async def fetch_available_slugs(
    client: httpx.AsyncClient, api_key: str
) -> set[str]:
    resp = await client.get(
        MODELS_URL,
        headers={'Authorization': f'Bearer {api_key}'},
        timeout=HTTP_TIMEOUT_S,
    )
    resp.raise_for_status()
    body = resp.json()
    if not isinstance(body, dict):
        raise ValueError(f'unexpected models body: {str(body)[:200]}')
    data = body.get('data')
    models = data.get('models') if isinstance(data, dict) else None
    if body.get('code') != 200 or not isinstance(models, list) or not models:
        raise ValueError(f'unexpected models body: {str(body)[:200]}')
    # A partial page would hide models that are merely on page two.
    if isinstance(data.get('total'), int) and data['total'] != len(models):
        raise ValueError(
            f'models list incomplete ({len(models)} of {data["total"]})'
        )
    return {
        m['model']
        for m in models
        if isinstance(m, dict) and isinstance(m.get('model'), str)
    }


async def _fetch_available(client: httpx.AsyncClient, api_key: str) -> dict:
    slugs = await fetch_available_slugs(client, api_key)
    ours = {m.slug for m in served().models}
    missing = ours - slugs
    if missing == ours:
        # Every one of our models gone at once is an API change, not a
        # mass delisting. Keep serving what we have.
        return {'status': 'failed', 'error': 'none of our slugs listed'}
    hidden = frozenset(missing)
    changed = hidden != current_state().hidden
    commit(hidden=hidden)
    if missing:
        logger.warning(
            'vision catalog: kie.ai no longer lists %s', sorted(missing)
        )
    return {
        'status': 'updated' if changed else 'unchanged',
        'hidden': len(missing),
    }


async def sync_once(
    api_key: str,
    client: httpx.AsyncClient | None = None,
    *,
    force: bool = False,
) -> dict:
    """Catalog → prices → availability, each failing soft.

    Order matters: prices and availability are computed against the
    catalog the first step just installed. Never raises; each step's
    outcome is reported under its name.
    """
    steps = (
        ('catalog', lambda c: _fetch_catalog(c, force)),
        ('prices', _fetch_prices),
        ('available', lambda c: _fetch_available(c, api_key)),
    )
    out: dict[str, dict] = {}

    async def _run(c: httpx.AsyncClient) -> None:
        for name, step in steps:
            try:
                out[name] = await step(c)
            except Exception as e:  # noqa: BLE001 — a step must fail soft
                logger.warning('vision catalog %s step failed: %s', name, e)
                out[name] = {'status': 'failed', 'error': str(e)[:300]}

    if client is not None:
        await _run(client)
    else:
        async with _client_factory() as own:
            await _run(own)
    return out
