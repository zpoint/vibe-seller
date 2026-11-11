"""Has this session read a catalog yet?

Tracked for PreToolUse plugin gates, whose uniform signature carries
``catalog_read`` (see ``app.plugins``). Nothing in core refuses a call on
it: the base prompt asks the agent to read the catalog first.
"""

import re

from app.ai.shell_patterns import COMMAND_PREFIX

# Any-level catalog file: L1 `knowledge/project/CATALOG.md`, L2
# `knowledge/CATALOG.md`, L3 `stores/<slug>/CATALOG.md`.
_CATALOG_FILE = re.compile(
    r'(?:knowledge|stores/[^/]+)(?:/[^/]+)*/CATALOG\.md$'
)


# A shell command that prints a CATALOG.md: a reader in command position
# naming one, or a `find` that selects it by name.
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

    An attempt counts: a fresh store has no catalog yet.
    """
    if tool_name == 'Bash':
        return bash_reads_catalog(str(tool_input.get('command', '')))
    if tool_name != 'Read':
        return False
    return is_catalog_path(tool_input.get('file_path', ''))
