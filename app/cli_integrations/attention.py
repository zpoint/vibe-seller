"""Which signed-in platforms stopped working, for the page and the bar.

A platform counts only once it has been signed in: *enabled* is that
proof (``manager.enable`` refuses a platform whose sign-in does not
work), so a platform someone only downloaded, or started signing in to
and never finished, never shows up here.

Two ways an enabled platform stops working:

* its sign-in itself expired or was revoked (``auth_status`` not
  usable) — any platform;
* a capability it grants on its own expired — WeCom grants each one
  (docs, contacts, messages…) for 7 days, and ``wecom-cli auth show``
  still says "authorized" afterwards. Only a real call tells, so
  ``permissions`` makes one read-only call per capability.

Permission checks go online, so the bar reuses the last one for
``RECHECK_S`` — or until the stored sign-in changes (signing in again
renews the grants); the Settings card always checks afresh. A capability
alerts only if it has worked here before (``granted``): one the admin
never granted is not "expired" to them — the card still lists it.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
import time

from app.cli_integrations import manager, paths
from app.cli_integrations.base import CliIntegration
from app.cli_integrations.registry import INTEGRATIONS

logger = logging.getLogger(__name__)

#: How long the bar trusts the last permission check.
RECHECK_S = 30 * 60

_locks: dict[str, asyncio.Lock] = {}


def _cache_path(key: str, home: Path | None) -> Path:
    return paths.tool_dir(key, home) / 'permissions.json'


def _read_cache(key: str, home: Path | None) -> dict:
    try:
        data = json.loads(_cache_path(key, home).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(key: str, data: dict, home: Path | None) -> None:
    path = _cache_path(key, home)
    tmp = path.with_name('.permissions.json.tmp')
    tmp.write_text(json.dumps(data), encoding='utf-8')
    tmp.replace(path)


async def permissions(
    integ: CliIntegration, home: Path | None = None
) -> list[dict] | None:
    """Each capability's state, checked now (None: nothing to check —
    not a per-permission platform, not installed, or not signed in)."""
    h = home or paths.vibe_home()
    lock = _locks.setdefault(integ.key, asyncio.Lock())
    async with lock:
        found = await manager.check_permissions(integ, h)
        if found is None:
            return None
        items = [
            {'key': p.key, 'state': p.state, 'renew_url': p.renew_url}
            for p in found
        ]
        old = await asyncio.to_thread(_read_cache, integ.key, h)
        granted = set(old.get('granted') or ()) | {
            p['key'] for p in items if p['state'] == 'ok'
        }
        await asyncio.to_thread(
            _write_cache,
            integ.key,
            {
                'checked_at': time.time(),
                'checked': datetime.now(UTC).isoformat(),
                'items': items,
                'granted': sorted(granted),
            },
            h,
        )
        return items


def _signed_in_at(key: str, home: Path) -> float:
    """When the stored sign-in last changed: the newest file directly in
    the state dir (the CLI keeps its credentials there; its caches live
    in sub-folders and change on every call)."""
    try:
        return max(
            (
                f.stat().st_mtime
                for f in paths.state_dir(key, home).iterdir()
                if f.is_file()
            ),
            default=0.0,
        )
    except OSError:
        return 0.0


async def _expired_permissions(integ: CliIntegration, h: Path) -> list[str]:
    """Capabilities that worked here before and are expired now."""
    cache = await asyncio.to_thread(_read_cache, integ.key, h)
    checked_at = float(cache.get('checked_at') or 0)
    signed_in_at = await asyncio.to_thread(_signed_in_at, integ.key, h)
    # Signed in again since the last check (a new scan renews the
    # grants): that check no longer says anything.
    if time.time() - checked_at > RECHECK_S or signed_in_at > checked_at:
        if await permissions(integ, h) is None:
            return []
        cache = await asyncio.to_thread(_read_cache, integ.key, h)
    granted = set(cache.get('granted') or ())
    return [
        p['key']
        for p in cache.get('items') or ()
        if p.get('state') == 'expired' and p.get('key') in granted
    ]


async def _one(integ: CliIntegration, h: Path) -> dict | None:
    if not await asyncio.to_thread(
        paths.is_enabled, integ.key, integ.binary, h
    ):
        return None
    auth = await manager.auth_status(integ, h)
    if not auth.usable:
        # A sign-in that cannot be checked here (``error``) is not
        # "expired": nobody should be sent to sign in again for it.
        if auth.reason == 'error':
            return None
        return {'key': integ.key, 'title': integ.title, 'expired': []}
    if integ.per_permission:
        expired = await _expired_permissions(integ, h)
        if expired:
            return {'key': integ.key, 'title': integ.title, 'expired': expired}
    return None


async def attention(home: Path | None = None) -> list[dict]:
    """Every enabled platform that needs signing in again: ``expired``
    lists the capabilities that ran out (empty: the whole sign-in)."""
    h = home or paths.vibe_home()
    found = await asyncio.gather(
        *(_one(i, h) for i in INTEGRATIONS.values()), return_exceptions=True
    )
    out = []
    for integ, item in zip(INTEGRATIONS.values(), found, strict=True):
        if isinstance(item, BaseException):
            logger.warning('%s attention check failed: %s', integ.key, item)
        elif item is not None:
            out.append(item)
    return out
