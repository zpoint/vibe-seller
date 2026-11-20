"""Retire the earlier Google Workspace switch (one-time, at boot).

Before, Settings → Integrations had a Google Workspace on/off switch
that needed a ``gws`` the person installed and signed in to themselves
(their personal ``~/.config/gws``), and built ``.claude/skills/gws``
from it. Google Workspace is now a managed platform (``gws.py``) with
its own copy and its own sign-in; it never reads or changes a personal
install. So on the first boot after the upgrade:

- the old umbrella (a ``.claude/skills/gws`` WITHOUT the managed
  umbrella's ``.cli-version``) is removed — it documented a ``gws``
  the agent no longer reaches (the guard answers that name);
- the old ``google_workspace_enabled`` setting is dropped;
- if either said the switch was on, the row shows a notice
  (``gws_moved``) until Google Workspace is connected again.

The personal ``gws`` and its sign-in are left exactly as they are.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import shutil

from sqlalchemy.ext.asyncio import AsyncSession

from app.cli_integrations import paths
from app.models.app_settings import AppSettings
from app.workspace import skills_tree

logger = logging.getLogger(__name__)

KEY = 'gws'
LEGACY_SETTING = 'google_workspace_enabled'
NOTICE = 'gws_moved'


def _remove_legacy_umbrella(home: Path) -> bool:
    """Remove the old switch's umbrella (blocking); True if there was one."""
    umbrella = paths.skill_dir(KEY, home)
    if not (umbrella / 'SKILL.md').is_file():
        return False
    if (umbrella / paths.SKILLS_VERSION_FILE).is_file():
        return False  # the managed umbrella
    shutil.rmtree(umbrella)
    return True


async def migrate(db: AsyncSession, home: Path | None = None) -> bool:
    """Run the one-time retirement; True when the old switch was on."""
    h = home or paths.vibe_home()
    async with skills_tree.hold():
        removed = await asyncio.to_thread(_remove_legacy_umbrella, h)
    row = await db.get(AppSettings, LEGACY_SETTING)
    was_on = removed or bool(row and row.value == 'true')
    if row is not None:
        await db.delete(row)
        await db.commit()
    if was_on:
        await asyncio.to_thread(paths.write_notice, KEY, NOTICE, h)
        logger.info(
            'Google Workspace: retired the earlier switch; it is connected '
            'from Settings → Integrations now'
        )
    return was_on
