"""Swap a fully built skill dir into place, rolling back on failure.

Used by the optional integration bundles (``app.cli_integrations``):
each builds its skill tree in a staging dir
first, then calls :func:`swap_in` under the skills-tree lock
(``app.workspace.skills_tree``) so no task workspace copy ever sees a
half-built or missing skill.
"""

from __future__ import annotations

import logging
from pathlib import Path
import shutil

logger = logging.getLogger(__name__)


def swap_in(staging: Path, dest: Path, backup: Path) -> None:
    """Replace *dest* with *staging* (two renames, blocking).

    The prior *dest* is renamed to *backup* first, then *staging* to
    *dest*, then the backup is dropped. If the second rename fails, the
    backup is renamed back so the previous install survives; *staging*
    is removed either way. Re-raises the original error.
    """
    backed_up = False
    try:
        if backup.exists():
            shutil.rmtree(backup)
        if dest.exists():
            dest.rename(backup)
            backed_up = True
        staging.rename(dest)
        if backup.exists():
            shutil.rmtree(backup)
        backed_up = False
    except Exception:
        if backed_up and backup.exists() and not dest.exists():
            try:
                backup.rename(dest)
            except Exception:
                logger.exception('skill swap: backup restore failed')
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        raise
