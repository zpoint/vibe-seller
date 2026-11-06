"""Filesystem walks that survive concurrent writers.

The skills/knowledge trees are mutated from worker threads while UI
views walk them. A path that vanishes mid-walk is skipped — treated as
"not there" — instead of failing the whole listing: a walk is a
snapshot, and blocking every workspace view behind the tree lock would
be worse than dropping an entry that no longer exists.

(Writes are serialised against each other and against the
task-workspace copy by :mod:`app.workspace.skills_tree`; these helpers
are what the lock-free readers use.)
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any


def tolerant_rglob(root: Path, pattern: str = '*') -> Iterator[Path]:
    """``root.rglob(pattern)``, ending early if a dir vanishes."""
    try:
        yield from root.rglob(pattern)
    except OSError:
        return


def safe_stat(path: Path) -> Any | None:
    """``path.stat()``, or ``None`` when it vanished before we looked."""
    try:
        return path.stat()
    except OSError:
        return None
