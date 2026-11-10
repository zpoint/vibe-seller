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

from app.models.app_settings import AppSettings

# Bump when a harness change makes notes written before it misleading
# (same role as plan_states.PLAN_CONTRACT for frozen schedule plans).
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


def deletion_only_error(old: str, new: str) -> str | None:
    """Refuse a cleanup write that adds or rewords anything.

    Every non-blank line of *new* must appear in *old*, in the same
    order — the write may only remove lines. Returns the reason, or
    None when the write is a pure deletion.
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
    return None
