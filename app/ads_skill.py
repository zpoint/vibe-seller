"""Pulling the ads skill from the bound service.

The skill that tells an agent how to use an ads service ships from that
service, not from this repo. Two reasons, and they pull the same way:
the service grows endpoints on its own schedule and a released client
cannot be upgraded to match, and a deployment with no service bound has
no use for the skill at all.

**A pulled bundle is data. Executable files are refused.**
vibe-seller's skill format allows ``<skill>/gates/*.py``, which runs
inside this server. Accepting one from a remote source would make that
source a code supply-chain dependency of every deployment — so the
unpacker drops anything that is not documentation, and says so rather
than failing silently.

**An unreachable service is not an error.** The cached bundle keeps
working and task creation is never blocked on a version check: a service
hiccup must not stop a store that was working an hour ago.
"""

from __future__ import annotations

import logging
from pathlib import Path
import shutil
import tempfile

from app import ads_client
from app.models.app_settings import AppSettings
from app.workspace.manager import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)

SKILL_NAME = 'amazon-ads-api'
VERSION_KEY = 'ads_skill_version'

#: What may appear in a pulled bundle. Everything else is dropped.
ALLOWED_SUFFIXES = {'.md', '.txt', '.json', '.yaml', '.yml', '.csv'}


def skill_dir() -> Path:
    return VIBE_SELLER_DIR / '.claude' / 'skills' / SKILL_NAME


def is_installed() -> bool:
    return (skill_dir() / 'SKILL.md').is_file()


async def sync_bundle(session, *, force: bool = False) -> dict:
    """Fetch the bundle if the service has a newer one."""
    remote = await ads_client.call(session, '/skill/version')
    version = str((remote or {}).get('version') or '')

    row = await session.get(AppSettings, VERSION_KEY)
    local = row.value if row else ''
    if version and version == local and is_installed() and not force:
        return {'updated': False, 'version': local}

    bundle = await ads_client.call(session, '/skill/bundle')
    files = (bundle or {}).get('files') or {}
    written, dropped = _install(files)

    if row is None:
        session.add(AppSettings(key=VERSION_KEY, value=version))
    else:
        row.value = version
    await session.commit()

    if dropped:
        logger.warning(
            'ads skill bundle carried %d non-documentation file(s); '
            'dropped: %s',
            len(dropped),
            ', '.join(sorted(dropped)),
        )
    return {
        'updated': True,
        'version': version,
        'files': written,
        'dropped': sorted(dropped),
    }


def _install(files: dict[str, str]) -> tuple[int, list[str]]:
    """Write the bundle atomically, keeping only documentation.

    Staged beside the destination so the final move is a rename on the
    same filesystem: a half-written skill directory is one an agent could
    load mid-update.
    """
    destination = skill_dir()
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f'.{SKILL_NAME}-', dir=destination.parent)
    )
    backup = destination.with_name(destination.name + '.old')

    written = 0
    dropped: list[str] = []
    try:
        for relpath, content in files.items():
            safe = _safe_relpath(relpath)
            if (
                safe is None
                or Path(safe).suffix.lower() not in ALLOWED_SUFFIXES
            ):
                dropped.append(relpath)
                continue
            target = staging / safe
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding='utf-8')
            written += 1

        shutil.rmtree(backup, ignore_errors=True)
        if destination.exists():
            destination.rename(backup)
        staging.rename(destination)
        staging = None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
    shutil.rmtree(backup, ignore_errors=True)

    return written, dropped


def _safe_relpath(relpath: str) -> str | None:
    """Reject anything that could write outside the skill directory."""
    candidate = Path(relpath)
    if candidate.is_absolute():
        return None
    parts = candidate.parts
    if any(part in ('..', '') for part in parts):
        return None
    return str(candidate)


async def refresh(session) -> dict:
    """Sync the bundle, reporting failure rather than raising it.

    Called where a failure must not undo the work around it — binding
    succeeded even if the bundle did not arrive, and boot must not stop
    because the service is briefly down. The cached bundle keeps working
    and the next refresh tries again.
    """
    try:
        return await sync_bundle(session)
    except Exception as exc:
        logger.warning('ads skill refresh failed: %s', exc)
        return {'updated': False, 'error': str(exc)}


def remove() -> None:
    """Drop the skill. Used when the service is unbound."""
    shutil.rmtree(skill_dir(), ignore_errors=True)
