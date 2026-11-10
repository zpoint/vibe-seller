"""One-time knowledge cleanup after an upgrade that changes the harness.

Agents used to record how the platform's own checks behaved — a review
pass that refused the first submit, when an approval froze, which file a
gate read — in ``knowledge/`` and ``stores/<slug>/`` notes. When the
harness changes, those notes become wrong and, read before the skills,
mislead the next task (observed: an agent applied the audit's bid over
the user's because a note described the previous freeze rule).

The base prompt now keeps such notes from being written and tells an
agent to delete one it meets. This module clears the stock an upgrade
leaves behind, once, through the catalog sync that already visits every
tier: a scope (``knowledge`` for L2, ``store:<id>`` for L3) whose
recorded contract is older than :data:`KNOWLEDGE_CONTRACT` gets one
extra instruction in its catalog task and runs even if its catalog is
current. The contract is recorded only when that task COMPLETES — a
failed run (provider out of credits, a crash) leaves the scope behind,
so the next catalog sync runs it again. While a scope is being cleaned,
its writes may only remove lines (:func:`deletion_only_error`).

The recorded values live in ``app_settings`` (server-owned; an agent
cannot write them). Scopes with no record fall back to the install's
floor, which boot writes once (``database.init_db``): the current
contract on a fresh install (nothing to clean), 1 on an install that
existed before it. No floor at all means nothing is owed — a cleanup
only ever runs because an upgrade recorded that one is due.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import posixpath

from app.ai.stop_gates.ad_rules import is_rule_line
from app.models.app_settings import AppSettings

# Bump when a harness change makes notes written before it misleading
#   2 — reviewer/reflection removed; ad checks read TSVs; approval
#       freezes at the execute declaration.
KNOWLEDGE_CONTRACT = 2

FLOOR_KEY = 'knowledge_contract:floor'
L2_SCOPE = 'knowledge'


def store_scope(store_id: str) -> str:
    return f'store:{store_id}'


def cleanup_scope(store_id: str | None) -> str:
    """The scope a catalog task covers: L2 without a store, else L3."""
    return L2_SCOPE if store_id is None else store_scope(store_id)


def _key(scope: str) -> str:
    return f'knowledge_contract:{scope}'


async def _int_setting(db, key: str) -> int | None:
    row = await db.get(AppSettings, key)
    try:
        return int(row.value) if row is not None else None
    except (TypeError, ValueError):
        return None


async def recorded(db, scope: str) -> int:
    """The contract this scope's knowledge was last cleaned to."""
    value = await _int_setting(db, _key(scope))
    if value is not None:
        return value
    floor = await _int_setting(db, FLOOR_KEY)
    return floor if floor is not None else KNOWLEDGE_CONTRACT


async def needs_cleanup(db, scope: str) -> bool:
    return await recorded(db, scope) < KNOWLEDGE_CONTRACT


async def mark_cleaned(db, scope: str) -> None:
    """Record the scope as clean. Caller commits."""
    key = _key(scope)
    row = await db.get(AppSettings, key)
    if row is None:
        db.add(AppSettings(key=key, value=str(KNOWLEDGE_CONTRACT)))
    else:
        row.value = str(KNOWLEDGE_CONTRACT)


# Tiers being cleaned right now: cleanup task id → store slug (None for
# the global tier). While a tier is listed, only its cleanup run may
# write there — the run's outcome check restores any file changed by
# more than deletions, and would otherwise sweep away a concurrent
# writer's legitimate edit. One server process owns the workspace, so
# process memory is the right place.
_active: dict[str, str | None] = {}


def begin_cleanup(task_id: str, store_slug: str | None) -> None:
    _active[task_id] = store_slug


def end_cleanup(task_id: str) -> None:
    _active.pop(task_id, None)


def busy_error(path: str, writer_task_id: str | None) -> str | None:
    """Refuse a write into a tier another run is cleaning right now."""
    for task_id, slug in list(_active.items()):
        if task_id == writer_task_id:
            continue
        if tier_error(path, slug) is None:
            return (
                'The one-time knowledge cleanup is running on this part of '
                'the workspace; try again in a few minutes.'
            )
    return None


def tier_error(path: str, store_slug: str | None) -> str | None:
    """Refuse a cleanup write outside the task's own tier.

    The global run cleans ``knowledge/`` (not the read-only
    ``knowledge/project/``); a store's run cleans ``stores/<slug>/``.
    A store's catalog lists the shared files too, so without this a
    store run could edit notes every store reads.
    """
    # Normalised first: a prefix check on the raw string is escaped by
    # `stores/<slug>/../<other>/notes.md`.
    rel = posixpath.normpath(path)
    if rel.startswith('../') or rel == '..' or posixpath.isabs(rel):
        rel = ''
    if store_slug is None:
        ok = rel.startswith('knowledge/') and not rel.startswith(
            'knowledge/project/'
        )
        tier = 'knowledge/ (not knowledge/project/)'
    else:
        tier = f'stores/{store_slug}/'
        ok = rel.startswith(tier)
    if ok:
        return None
    return (
        f'This one-time cleanup only edits files under {tier}; {path} '
        'belongs to another tier and is cleaned by its own run.'
    )


def _tier_files(root: Path, store_slug: str | None) -> list[Path]:
    if store_slug is None:
        base = root / 'knowledge'
        files = [
            f
            for f in base.rglob('*.md')
            if 'project' not in f.relative_to(base).parts[:1]
        ]
    else:
        files = list((root / 'stores' / store_slug).rglob('*.md'))
    return [f for f in files if f.is_file() and f.name != 'CATALOG.md']


def snapshot_tier(root: Path, store_slug: str | None) -> dict[Path, str]:
    """The tier's notes as they stand before a cleanup run."""
    out: dict[Path, str] = {}
    for f in _tier_files(root, store_slug):
        try:
            out[f] = f.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            continue
    return out


def undo_non_deletions(snapshot: dict[Path, str]) -> list[Path]:
    """Restore every snapshotted file the run changed by more than
    deleting lines; return the restored paths.

    The write endpoint refuses such writes, but an agent with a shell
    can edit the file directly — observed: a run trimmed half-lines out
    of wrapped paragraphs with a Python script, leaving fragments. The
    outcome, not the write path, is what the cleanup is held to.
    """
    restored: list[Path] = []
    for path, old in snapshot.items():
        try:
            new = path.read_text(encoding='utf-8')
        except FileNotFoundError:
            new = ''
        except (OSError, UnicodeDecodeError):
            continue
        if new != old and deletion_only_error(old, new):
            path.write_text(old, encoding='utf-8')
            restored.append(path)
    return restored


def deletion_only_error(old: str, new: str) -> str | None:
    """Refuse a cleanup write that adds or rewords anything.

    Every non-blank line of *new* must appear in *old*, in the same
    order — the write may only remove lines — and a store-rule line
    (``acos_no_lower: 20``) is never among them: it records a standard
    the user set. Returns the reason, or None when the write is a pure
    deletion of notes.
    """
    old_lines = [ln.rstrip() for ln in old.splitlines() if ln.strip()]
    i = 0
    for line in (ln.rstrip() for ln in new.splitlines() if ln.strip()):
        while i < len(old_lines) and old_lines[i] != line:
            i += 1
        if i == len(old_lines):
            shown = line if len(line) <= 80 else line[:77] + '...'
            return (
                'This one-time cleanup may only delete passages; this '
                f'write adds or changes a line: "{shown}". Write the file '
                'back with only the outdated passages removed.'
            )
        i += 1
    kept = Counter(ln.rstrip() for ln in new.splitlines())
    for line in (ln.rstrip() for ln in old.splitlines()):
        if is_rule_line(line):
            if kept[line] <= 0:
                return (
                    f'"{line.strip()}" is a store standard the user set, '
                    'not a note about the platform: keep it.'
                )
            kept[line] -= 1
    return None
