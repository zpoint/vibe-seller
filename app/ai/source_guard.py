"""Keep task agents out of vibe-seller's own source code.

Observed in CI: a planning agent read ``/proc/<pid>/cmdline``, found the
server's source checkout, opened the e2e test that mirrored its task
("This is the exact test my plan must satisfy"), then spent seven minutes
reading gate modules to work out what they would accept -- and missed
its deadline. Its own reasoning noted the prompt tells it not to dig into
platform source; a prompt is advice, and this is the line in code.

The gates are enforced on what an agent delivers. Reading their source
does not help an agent do the task; it helps it fit a deliverable to a
checker, which is the failure every gate exists to catch. Nothing an
agent is told to use lives here: skills, knowledge and wrappers are
copies under the vibe-seller home, never paths into the package.

Protected: the ``app`` package, and when it runs from a source checkout,
that checkout's ``tests/`` (they describe the gates just as well). Paths
are compared as written, not resolved, so a symlinked skill copy that
happens to point into the package still reads normally.
"""

import os
from pathlib import Path
import re

import app

_APP_DIR = Path(app.__file__).resolve().parent
_CHECKOUT = _APP_DIR.parent
PROTECTED_DIRS = (_APP_DIR,) + (
    (_CHECKOUT / 'tests',) if (_CHECKOUT / 'pyproject.toml').is_file() else ()
)

_HOME = str(Path.home())


def _spellings(d: Path) -> tuple[str, ...]:
    s = str(d)
    return (s, '~' + s[len(_HOME) :]) if s.startswith(_HOME + os.sep) else (s,)


# A protected path in a shell command, ending at a path boundary — so
# ``…/app`` is caught but ``…/apple`` is not.
_PROTECTED_IN_CMD = re.compile(
    '(?:'
    + '|'.join(re.escape(s) for d in PROTECTED_DIRS for s in _spellings(d))
    + r""")(?=$|[/\s'";|&)])"""
)

_DENY = (
    "Blocked: `{path}` is vibe-seller's own source code, not data for this "
    'task. The checks your result must pass are enforced on what you '
    'deliver, and reading their code will not get you there -- do the '
    'task as the request and your loaded skills describe it. Task files, '
    'skills and knowledge are in your working directory.'
)


def _inside(path: str) -> bool:
    p = os.path.normpath(os.path.expanduser(path))
    return any(
        p == str(d) or p.startswith(str(d) + os.sep) for d in PROTECTED_DIRS
    )


def source_access_deny(tool_name: str, tool_input) -> str | None:
    """Deny reason when a tool call reaches into vibe-seller's source."""
    if not isinstance(tool_input, dict):
        return None
    if tool_name == 'Bash':
        cmd = str(tool_input.get('command', ''))
        m = _PROTECTED_IN_CMD.search(cmd)
        return _DENY.format(path=m.group(0)) if m else None
    if tool_name in ('Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
        path = tool_input.get('file_path') or tool_input.get('notebook_path')
    elif tool_name in ('Grep', 'Glob'):
        path = tool_input.get('path')
    else:
        return None
    if (
        path
        and os.path.isabs(os.path.expanduser(str(path)))
        and _inside(str(path))
    ):
        return _DENY.format(path=path)
    return None
