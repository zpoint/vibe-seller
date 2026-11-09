"""Remove Claude Code auto-memory left behind by earlier releases.

Agents now run with auto-memory off (``profiles.disable_auto_memory``),
so nothing reads these directories any more. They are deleted so that a
later re-enable, or a person opening a session in the runtime home,
does not resurrect stale notes.

Claude Code keys a session's memory by its cwd:
``<config>/projects/<sanitized cwd>/memory/``. Ours are the runtime home
(the workspace assistant) and ``<home>/tasks/<id>`` (one per task). Only
those ``memory/`` folders are removed; transcripts beside them stay.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
import shutil

from app.ai.profiles import ProfileManager
from app.config import VIBE_SELLER_DIR

logger = logging.getLogger(__name__)


def sanitize_path(path: str) -> str:
    """Claude Code's project-directory name for a cwd.

    Mirrors ``sanitizePath`` in the CLI. Names past 200 characters get a
    hash suffix there; ours are far shorter, and the prefix match below
    still holds for a truncated one.
    """
    return re.sub(r'[^a-zA-Z0-9]', '-', path)


def _home_slugs(vibe_home: Path) -> set[str]:
    paths = {str(vibe_home)}
    try:
        paths.add(str(vibe_home.resolve()))
    except OSError:
        pass
    return {sanitize_path(p) for p in paths}


def _ours(name: str, slugs: set[str]) -> bool:
    return any(name == s or name.startswith(s + '-tasks-') for s in slugs)


def config_dirs(profile_envs: list[dict]) -> set[Path]:
    """Every Claude config dir an agent may have run under."""
    dirs = {
        Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude')
    }
    for env in profile_envs:
        if env.get('CLAUDE_CONFIG_DIR'):
            dirs.add(Path(env['CLAUDE_CONFIG_DIR']).expanduser())
    return dirs


def purge_agent_auto_memory(
    vibe_home: Path, claude_config_dirs: set[Path]
) -> list[Path]:
    """Delete our sessions' ``memory/`` folders; return what was removed."""
    slugs = _home_slugs(vibe_home)
    removed = []
    for config in claude_config_dirs:
        projects = config / 'projects'
        if not projects.is_dir():
            continue
        for project in projects.iterdir():
            memory = project / 'memory'
            if _ours(project.name, slugs) and memory.is_dir():
                shutil.rmtree(memory, ignore_errors=True)
                removed.append(memory)
    if removed:
        logger.info('Removed %d agent auto-memory folders', len(removed))
    return removed


def purge_on_boot() -> None:
    """Boot hook: never lets a cleanup failure stop the server."""
    try:
        profiles = ProfileManager.load().get('profiles', {})
        envs = [p.get('env', {}) for p in profiles.values()]
        purge_agent_auto_memory(VIBE_SELLER_DIR, config_dirs(envs))
    except Exception:  # noqa: BLE001 -- best-effort housekeeping
        logger.warning('Auto-memory cleanup failed', exc_info=True)
