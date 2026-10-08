"""Whether a script is one a bundled skill ships, unmodified.

Guards that read a script's source to judge what it does (the report
-script guard) exist for code an agent wrote under turn pressure. A
skill's own script — say the PDF renderer, which names the report and
writes a file next to it — is reviewed code the skill tells the agent
to run; refusing it sends the agent probing the guard instead. It is
trusted only while it is byte-for-byte the shipped copy, so editing it
in the task folder does not carry the trust along.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from app.config import SKILLS_SUBDIR
from app.plugins import registered_skill_sources


def _sources() -> list[Path]:
    sources = [Path(str(importlib.resources.files('app') / SKILLS_SUBDIR))]
    return sources + list(registered_skill_sources())


def is_shipped_script(path: Path) -> bool:
    """``<…>/skills/<skill>/scripts/<file>``, identical to the shipped one."""
    scripts = path.parent
    if scripts.name != 'scripts' or scripts.parent.parent.name != 'skills':
        return False
    skill = scripts.parent.name
    try:
        local = path.read_bytes()
    except OSError:
        return False
    for source in _sources():
        shipped = source / skill / 'scripts' / path.name
        try:
            if shipped.is_file() and shipped.read_bytes() == local:
                return True
        except OSError:
            continue
    return False


def source_unless_shipped(path: Path) -> str:
    """A script's source to inspect; empty for a skill's own, unedited."""
    if is_shipped_script(path):
        return ''
    return path.read_text(encoding='utf-8', errors='ignore')
