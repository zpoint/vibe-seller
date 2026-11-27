"""Enabled workplace platforms that need signing in again.

A platform counts only once it was signed in: *enabled* is that proof
(``manager.enable`` refuses a platform whose sign-in does not work), so
a platform someone merely downloaded never alerts.

WeCom's grant lasts 7 days, while ``wecom-cli auth show`` keeps saying
"authorized" after it expires. ``check_expired`` therefore uses real,
read-only calls. The top bar caches that answer for ``RECHECK_S`` (or
until the stored sign-in changes); the Settings card checks afresh.
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

RECHECK_S = 30 * 60
_locks: dict[str, asyncio.Lock] = {}


def _cache_path(key: str, home: Path | None) -> Path:
    return paths.tool_dir(key, home) / 'expiry.json'


def _read_cache(key: str, home: Path | None) -> dict:
    try:
        data = json.loads(_cache_path(key, home).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(key: str, expired: bool, home: Path | None) -> None:
    path = _cache_path(key, home)
    tmp = path.with_name('.expiry.json.tmp')
    tmp.write_text(
        json.dumps({
            'checked_at': time.time(),
            'checked': datetime.now(UTC).isoformat(),
            'expired': expired,
        }),
        encoding='utf-8',
    )
    tmp.replace(path)


async def expired(
    integ: CliIntegration, home: Path | None = None
) -> bool | None:
    """Check now whether this platform's authorization expired."""
    h = home or paths.vibe_home()
    lock = _locks.setdefault(integ.key, asyncio.Lock())
    async with lock:
        found = await manager.check_expired(integ, h)
        if found is not None:
            await asyncio.to_thread(_write_cache, integ.key, found, h)
        return found


def _signed_in_at(key: str, home: Path) -> float:
    """Newest file directly in the CLI's state dir (its credentials)."""
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


async def _one(integ: CliIntegration, h: Path) -> dict | None:
    if not await asyncio.to_thread(
        paths.is_enabled, integ.key, integ.binary, h
    ):
        return None
    item = {'key': integ.key, 'title': integ.title}
    auth = await manager.auth_status(integ, h)
    if not auth.usable:
        # An uncheckable sign-in is not evidence that it expired.
        return None if auth.reason == 'error' else item
    if not integ.checks_expiry:
        return None

    cache = await asyncio.to_thread(_read_cache, integ.key, h)
    checked_at = float(cache.get('checked_at') or 0)
    signed_in_at = await asyncio.to_thread(_signed_in_at, integ.key, h)
    if time.time() - checked_at > RECHECK_S or signed_in_at > checked_at:
        found = await expired(integ, h)
        return item if found else None
    return item if cache.get('expired') is True else None


async def attention(home: Path | None = None) -> list[dict]:
    """Enabled platforms whose authorization needs renewing."""
    h = home or paths.vibe_home()
    found = await asyncio.gather(
        *(_one(i, h) for i in INTEGRATIONS.values()), return_exceptions=True
    )
    out = []
    for integ, item in zip(INTEGRATIONS.values(), found, strict=True):
        if isinstance(item, BaseException):
            logger.warning('%s expiry check failed: %s', integ.key, item)
        elif item is not None:
            out.append(item)
    return out
