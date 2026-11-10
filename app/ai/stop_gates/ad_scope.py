"""Which marketplaces an ad task owes.

``AUDIT_TARGETS.json`` is written by the SERVER into the task dir: the
(platform, country) combos the store is configured for, which an
``audit`` declared over the whole store owes (``ad_declaration.
owed_combos``). The agent cannot shrink it.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config import VIBE_SELLER_DIR

TARGETS_FILENAME = 'AUDIT_TARGETS.json'


def targets_path(task_id: str):
    """Path to a task's AUDIT_TARGETS.json (task-workspace root)."""
    return VIBE_SELLER_DIR / 'tasks' / task_id / TARGETS_FILENAME


def write_declared_targets(
    task_dir,
    platform_countries: dict[str, list[str]],
    slug: str | None = None,
) -> None:
    """Write the SERVER's combo obligation for a task (best-effort).

    Which combos exist is the server's fact, not the agent's: an agent
    that chose its own combo set could shrink it (observed live: a
    store configured for 5 combos audited one, and four marketplaces
    went unaudited). ``Store.platform_countries`` — user-configured in
    Settings, and a monotonic union of what past tasks observed, so it
    never shrinks — is written into the workspace at task start, where
    the coverage gate checks ``CAMPAIGNS.tsv`` against it and the agent
    can read what it owes.

    A combo with no live campaigns is a legitimate finding (one row with
    an empty ``campaign_id`` and a note). What the agent cannot do is
    omit the combo silently.

    ``slug`` is recorded so the audit finds this store's own files
    without globbing ``stores/*`` and borrowing another store's.

    Best-effort: an unwritable workspace leaves the gate with no store
    combos to demand, rather than blocking the task.
    """
    combos = [
        {'platform': str(p).strip().lower(), 'country': str(c).strip().upper()}
        for p, cs in (platform_countries or {}).items()
        if isinstance(cs, list)
        for c in cs
        if str(p).strip() and str(c).strip()
    ]
    if not combos:
        return  # ensure_targets_file covers it once an ad task declares
    payload: dict = {'combos': combos}
    if slug:
        payload['slug'] = slug
    try:
        (task_dir / TARGETS_FILENAME).write_text(
            json.dumps(payload, indent=2) + '\n',
            encoding='utf-8',
        )
    except OSError:  # pragma: no cover — best-effort
        pass


def ensure_targets_file(task_dir) -> None:
    """Give a declared ad task an explicit empty menu if it has none.

    The ad skills say "declare, then read ``AUDIT_TARGETS.json``", but a
    store with no configured marketplace never got the file, and its
    absence read as "the server hasn't finished yet": in CI audits on
    such a store re-read the spec dozens of times hunting for it,
    rewrote the store's ``metadata.json`` to invent a menu, and ran out
    of time. Written at DECLARATION, not at task start, so only work
    that reads a menu ever sees one — a store task that merely looks
    something up is not handed an ad artefact to wonder about. An empty
    menu owes exactly what an absent one did (see
    :func:`load_declared_targets`); the skill docs give its rule.
    """
    path = Path(task_dir) / TARGETS_FILENAME
    if path.exists():
        return
    try:
        path.write_text(json.dumps({'combos': []}) + '\n', encoding='utf-8')
    except OSError:  # pragma: no cover — best-effort
        pass


def load_declared_targets(task_id: str | None) -> list[dict]:
    """``[{platform, country}, …]`` the SERVER says this task must audit.

    Empty when the file is absent (a task from before this contract, or a
    store with no recorded platforms) — callers then fall back to the
    agent-declared combo set, i.e. the previous behaviour. Absence is
    never itself a gap: only a MISSING combo that the server did declare
    is one.
    """
    if not task_id:
        return []
    try:
        raw = targets_path(task_id).read_text(encoding='utf-8')
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(data, dict):
        return []
    out: list[dict] = []
    for c in data.get('combos') or []:
        if not isinstance(c, dict):
            continue
        platform = str(c.get('platform') or '').strip()
        country = str(c.get('country') or '').strip()
        if platform and country:
            out.append({'platform': platform, 'country': country})
    return out
