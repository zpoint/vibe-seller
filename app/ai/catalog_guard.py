"""Catalog-first guard: read the catalog before searching around it.

Split out of ``bash_safety`` (which re-exports these names) to keep
that module under the line limit.
"""

import re

from app.ai.shell_patterns import COMMAND_PREFIX

# ── Catalog-first guard ────────────────────────────────────────────
#
# The system prompt instructs the agent to read the store/global
# catalog BEFORE any filesystem search of knowledge/ or stores/.
# Prose-only enforcement is unreliable — glm-4.7 routinely went
# straight to `find`/`ls` against those paths when the user prompt
# included a search verb. This guard turns the prose contract into
# a mechanism: filesystem-search commands targeting knowledge/ or
# stores/ paths are denied until the agent has read a catalog file
# this session. After that, normal `find`/`ls`/`grep` flows work.
#
# The denied set is a small allowlist of search verbs in command
# position, not arbitrary shell — `echo "ls stores"`, `grep pattern
# file.txt`, etc. are all unaffected. False negatives (an obscure
# search form slips through) are fine; false positives (blocking
# innocuous bash) would punish the agent for no design reason.

_SEARCH_INVOCATION = re.compile(
    COMMAND_PREFIX + r'(find|ls|grep|rg|fd|tree)\b([^;|&\n]*)'
)
# Paths that point into either catalog tree. Matches absolute
# (``/home/<user>/.vibe-seller/stores``), home-relative
# (``~/.vibe-seller/knowledge``), and workspace-relative
# (``stores/<slug>/...``, ``knowledge/...``) forms.
_CATALOG_PATH = re.compile(
    r'(?:^|\s|=)'
    r'(?:'
    r'(?:[a-zA-Z]:)?/?(?:[^/\s]*/)*\.vibe-seller/(?:stores|knowledge)'
    r'|'
    r'~/?\.vibe-seller/(?:stores|knowledge)'
    r'|'
    r'(?:\./)?(?:stores|knowledge)(?:/|\b)'
    r')'
)

_CATALOG_FIRST_DENY = (
    'Blocked: direct filesystem search of `knowledge/` or `stores/` '
    'before reading the catalog. The catalog (Read '
    '`stores/<slug>/CATALOG.md` for store tasks or '
    '`knowledge/CATALOG.md` for no-store tasks) is the complete '
    'manifest of available files with a one-line summary of each — '
    'searching by `find`/`ls`/`grep` against those trees usually '
    'duplicates information the catalog already contains. Read the '
    'catalog first; after that, normal search commands work normally.'
)


def check_catalog_first(command: str, catalog_read: bool) -> str | None:
    """Return a deny reason if *command* searches knowledge/stores
    before the agent has read a catalog this session.

    Once any catalog has been read this session (tracked by the
    caller and passed as *catalog_read*), the guard is disabled and
    the agent is free to use `find`/`grep`/etc. against those paths.
    A command that itself prints a catalog is a catalog read, not a
    search around one (see ``bash_reads_catalog``).
    """
    if catalog_read or not command or bash_reads_catalog(command):
        return None
    for match in _SEARCH_INVOCATION.finditer(command):
        args = match.group(2)
        if _CATALOG_PATH.search(args):
            return _CATALOG_FIRST_DENY
    return None


# Path patterns that identify a *catalog* file (any level). Reading
# any of these flips the catalog-first guard off for the rest of
# the session — at that point the agent has seen the manifest and
# may search freely. The pattern is intentionally lax (matches L1
# `knowledge/project/CATALOG.md`, L2 `knowledge/CATALOG.md`, and
# L3 `stores/<slug>/CATALOG.md`).
_CATALOG_FILE = re.compile(
    r'(?:knowledge|stores/[^/]+)(?:/[^/]+)*/CATALOG\.md$'
)


# A shell command that prints a CATALOG.md: a reader in command position
# naming one, or a `find` that selects it by name. In CI an agent asked
# to read the catalog ran `find stores/<slug> -name CATALOG.md -exec cat`
# and was refused as a "search" -- by the guard whose only demand is
# that the catalog be read.
_CATALOG_READER = re.compile(
    COMMAND_PREFIX + r'(?:cat|head|tail|sed|less|more|bat|nl)\b[^;|&\n]*'
    r'CATALOG\.md\b'
)
_CATALOG_FIND = re.compile(
    COMMAND_PREFIX + r"find\b[^;|&\n]*-i?name\s+['\"]?CATALOG\.md\b"
)


def bash_reads_catalog(command: str) -> bool:
    """Return True if *command* prints a CATALOG.md."""
    if not command:
        return False
    return bool(
        _CATALOG_READER.search(command) or _CATALOG_FIND.search(command)
    )


def is_catalog_path(path: str) -> bool:
    """Return True if *path* points at any-level CATALOG.md."""
    if not path:
        return False
    return bool(_CATALOG_FILE.search(path))


def should_mark_catalog_read(tool_name: str, tool_input: dict) -> bool:
    """Return True iff a Read (or shell read) of a catalog was attempted.

    Used by the PreToolUse hook to flip the catalog-first guard off
    once the agent has *tried* to consult the catalog. Existence on
    disk deliberately doesn't matter: a fresh workspace (newly created
    store, never-run catalog-sync) has no ``stores/<slug>/CATALOG.md``
    yet, so the legitimate first move "Read the catalog, find it
    missing, then `ls` to see what's there" was indefinitely denied
    under the old `is_file()`-gated check — which broke every fresh
    task for providers (DeepSeek thinking mode in particular) that
    can't recover from a hook-denied tool_use in the same turn.

    Anti-spoofing concern (agent fabricates a fake catalog file to
    bypass the guard) is mitigated elsewhere: Write/Edit tools are
    routed through the same approval flow, and a fake CATALOG.md
    would still be empty, so the agent gains nothing by reading it
    first.
    """
    if tool_name == 'Bash':
        return bash_reads_catalog(str(tool_input.get('command', '')))
    if tool_name != 'Read':
        return False
    return is_catalog_path(tool_input.get('file_path', ''))


# Same intent as ``check_catalog_first`` for Bash, but applied to the
# Claude Code built-in ``Glob`` and ``Grep`` tools. Without this, an
# agent denied at the Bash layer pivots to ``Glob(pattern='stores/...')``
# or ``Grep(path='knowledge/...')`` and gets the same broad sweep
# through a different tool — exactly what the catalog-first contract
# is supposed to prevent. The check looks at the tool's ``path`` /
# ``pattern`` arguments; if either references a catalog tree the
# guard fires until the agent has read a CATALOG.md.
_PATTERN_TOUCHES_CATALOG = re.compile(
    r'(?:^|/|\*|\\)(stores|knowledge)(?:/|\b|$)'
)


def check_catalog_first_tool_args(
    tool_input: dict, catalog_read: bool
) -> str | None:
    """Return a deny reason if a Glob/Grep tool call sweeps the
    knowledge/ or stores/ trees before the agent has read a catalog.

    Inspects the ``path`` and ``pattern`` fields — either may carry
    the offending directory reference. Once the agent has read any
    CATALOG.md this session, the guard turns off and Glob/Grep work
    normally against those trees.

    Only fires when the input actually looks like a *broad sweep*:
    a wildcard (``*`` / ``**`` / ``?``) somewhere in the pattern, or
    the ``path`` field is set (directory traversal). A wildcard-free
    pattern referring to one specific file (e.g.
    ``Glob(pattern='knowledge/project/CATALOG.md')``) is essentially
    a file-existence check — the L2/L3 catalog-generation agents
    do this legitimately while building the catalog itself, and
    blocking it deadlocks the catalog-sync flow.
    """
    if catalog_read:
        return None
    path = tool_input.get('path', '')
    if isinstance(path, str) and path and _PATTERN_TOUCHES_CATALOG.search(path):
        return _CATALOG_FIRST_DENY
    pattern = tool_input.get('pattern', '')
    if (
        isinstance(pattern, str)
        and pattern
        and _PATTERN_TOUCHES_CATALOG.search(pattern)
        and any(ch in pattern for ch in ('*', '?'))
    ):
        return _CATALOG_FIRST_DENY
    return None
