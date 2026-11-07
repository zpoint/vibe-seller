"""Atomic read-modify-write for JSON object files.

Leaf utility — no app imports, so the workspace and sync layers share
ONE implementation of this instead of growing per-module copies.
Centralising it is the design point of #204: the mutate callback is a
plain function, so no caller can hold the dict across an ``await``
(a pip install, a DB write) and write a stale copy back over
concurrent changes.
"""

from __future__ import annotations

from collections.abc import Callable
import json
import os
from pathlib import Path
import tempfile


def read_json_object(path: Path) -> dict:
    """The JSON object at ``path``, or ``{}`` when it can't be one.

    Missing, corrupt and non-object files all read as empty — the
    tolerance the per-module readers have always had. Note the next
    :func:`update_json_file` then replaces that content; callers that
    need to tell ``empty`` from ``missing`` read the file themselves.
    """
    try:
        blob = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return blob if isinstance(blob, dict) else {}


def update_json_file(path: Path, mutate: Callable[[dict], None]) -> None:
    """Read-modify-write a JSON object file in ONE synchronous step.

    ``mutate`` edits the dict in place — a plain function, so it cannot
    ``await``; this is the hook that makes the lost-update bug class
    (#204) unreachable, and mutations must MERGE their keys rather than
    replace sibling keys a concurrent writer may own.

    The write is atomic (tmp file beside the target + :func:`os.replace`),
    so a concurrent reader never sees a torn file — a torn read parses
    as ``{}`` and the next writer would wipe the content. The tmp name
    is dot-prefixed so directory scanners ignore a crash leftover, and
    it is removed if anything fails. A mutation that changes nothing
    writes nothing (no rewrite of an already-trimmed file).
    """
    blob = read_json_object(path)
    before = json.dumps(blob, sort_keys=True)
    mutate(blob)
    if json.dumps(blob, sort_keys=True) == before:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f'.{path.name}.', suffix='.tmp'
    )
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(blob, f, indent=2)
        os.replace(tmp, str(path))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
