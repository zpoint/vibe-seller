"""The ``model_pricing`` sync job: pull models.dev, keep our slice.

Fetch → validate (:func:`app.ai.model_pricing.slice_catalog`) → cache →
serve. Fails soft: any error keeps the catalog currently served (the
cache, else the bundled snapshot). Scheduled by app/sync/jobs.py.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging

import httpx

from app.ai import model_pricing
from app.env_options import Options

logger = logging.getLogger(__name__)

DEFAULT_URL = 'https://models.dev/api.json'
HTTP_TIMEOUT_S = 60.0
#: models.dev is ~6 MB today; the cap only bounds a runaway body.
MAX_BODY_BYTES = 48 * 1024 * 1024

_client_factory = httpx.AsyncClient


def _source_url() -> str:
    return Options.MODEL_PRICING_URL.get().strip() or DEFAULT_URL


async def _fetch_with(client: httpx.AsyncClient, url: str) -> dict:
    async with client.stream('GET', url, timeout=HTTP_TIMEOUT_S) as resp:
        if resp.status_code != 200:
            return {'status': 'failed', 'error': f'HTTP {resp.status_code}'}
        raw = bytearray()
        async for chunk in resp.aiter_bytes():
            raw.extend(chunk)
            if len(raw) > MAX_BODY_BYTES:
                return {
                    'status': 'rejected',
                    'error': f'body over {MAX_BODY_BYTES} bytes',
                }
    try:
        body = json.loads(bytes(raw))
        sliced = model_pricing.slice_catalog(body)
        catalog = model_pricing.normalize_catalog(sliced)
    except (ValueError, model_pricing.PricingSourceError) as e:
        return {'status': 'rejected', 'error': str(e)[:300]}
    changed = catalog != model_pricing.current()
    if changed:
        await asyncio.to_thread(model_pricing.write_cache, sliced)
        model_pricing.apply(catalog)
    digest = hashlib.sha256(
        json.dumps(sliced, sort_keys=True).encode()
    ).hexdigest()
    return {
        'status': 'updated' if changed else 'unchanged',
        'sha256': digest,
        'providers': len(catalog),
        'models': sum(len(m) for m in catalog.values()),
    }


async def fetch_once(client: httpx.AsyncClient | None = None) -> dict:
    """One sync cycle. Never raises; reports ``{'status': ...}``."""
    url = _source_url()
    try:
        if client is not None:
            result = await _fetch_with(client, url)
        else:
            async with _client_factory() as own:
                result = await _fetch_with(own, url)
    except Exception as e:  # noqa: BLE001 — a sync must fail soft
        logger.warning('Model pricing sync failed: %s', e)
        return {'status': 'failed', 'error': str(e)[:300]}
    if result['status'] in ('failed', 'rejected'):
        logger.warning(
            'Model pricing sync %s: %s', result['status'], result['error']
        )
    return result
