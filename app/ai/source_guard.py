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
that checkout's ``tests/`` (they describe the gates just as well). Also the
app's own data dir: observed, a planner spent its first six minutes in
``vibe_seller.db`` and the mailbox databases instead of planning — and the
frozen ad approvals live there too. Mail is read through the email MCP
tools; nothing a task needs is under it. Paths are compared as written, not
resolved, so a symlinked skill copy that happens to point into the package
still reads normally.
"""

import os
from pathlib import Path
import re

import app
from app.config import DATA_DIR, VIBE_SELLER_DIR

_APP_DIR = Path(app.__file__).resolve().parent
_CHECKOUT = _APP_DIR.parent
SOURCE_DIRS = (_APP_DIR,) + (
    (_CHECKOUT / 'tests',) if (_CHECKOUT / 'pyproject.toml').is_file() else ()
)
PROTECTED_DIRS = SOURCE_DIRS + (DATA_DIR,)

_HOME = str(Path.home())

# Running from a source checkout, the checkout ROOT is protected too, not
# only its ``app``/``tests``: observed in CI, an agent read the checkout
# path from its environment, ran ``cd <checkout> && sed … app/ai/…`` with
# a RELATIVE path the package match never sees, and spent ten minutes in
# the gate modules. Its ``.venv`` stays usable (an agent may run the app's
# interpreter by absolute path), and if the vibe-seller home itself lives
# inside the checkout the root is left alone rather than blocking the
# task's own files.
_ROOT = (
    _CHECKOUT
    if (_CHECKOUT / 'pyproject.toml').is_file()
    and not str(VIBE_SELLER_DIR.resolve()).startswith(str(_CHECKOUT) + os.sep)
    else None
)
_VENV = _CHECKOUT / '.venv'


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

_ROOT_IN_CMD = (
    re.compile(
        '(?:'
        + '|'.join(re.escape(s) for s in _spellings(_ROOT))
        + r""")(?!/\.venv(?=$|[/\s'";|&)]))(?=$|[/\s'";|&)])"""
    )
    if _ROOT
    else None
)

_DATA_DENY = (
    "Blocked: `{path}` is vibe-seller's own data, not data for this task. "
    'Mail is read with the email tools, store files and knowledge are in '
    'your working directory, and other tasks are not yours to read.'
)

_DENY = (
    "Blocked: `{path}` is vibe-seller's own source code, not data for this "
    'task. The checks your result must pass are enforced on what you '
    'deliver, and reading their code will not get you there -- do the '
    'task as the request and your loaded skills describe it. Task files, '
    'skills and knowledge are in your working directory.'
)


def _under(path: str, dirs: tuple[Path, ...]) -> bool:
    p = os.path.normpath(os.path.expanduser(path))
    return any(p == str(d) or p.startswith(str(d) + os.sep) for d in dirs)


def _deny(path: str) -> str:
    data = _under(path, (DATA_DIR,))
    return (_DATA_DENY if data else _DENY).format(path=path)


def source_access_deny(tool_name: str, tool_input) -> str | None:
    """Deny reason when a tool call reaches into vibe-seller's source."""
    if not isinstance(tool_input, dict):
        return None
    if tool_name == 'Bash':
        cmd = str(tool_input.get('command', ''))
        m = _PROTECTED_IN_CMD.search(cmd) or (
            _ROOT_IN_CMD.search(cmd) if _ROOT_IN_CMD else None
        )
        return _deny(m.group(0)) if m else None
    if tool_name in ('Read', 'Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
        path = tool_input.get('file_path') or tool_input.get('notebook_path')
    elif tool_name in ('Grep', 'Glob'):
        path = tool_input.get('path')
    else:
        return None
    if not path or not os.path.isabs(os.path.expanduser(str(path))):
        return None
    p = str(path)
    if _under(p, PROTECTED_DIRS) or (
        _ROOT and _under(p, (_ROOT,)) and not _under(p, (_VENV,))
    ):
        return _deny(p)
    return None
