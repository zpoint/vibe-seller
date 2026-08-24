"""Resolution of the REAL ``browser-use`` binary a wrapper forwards to.

Both generated wrappers (per-store ``wrapper.py`` and store-less
``web_wrapper.py``) exec ``REAL_BU``. That value must be an ABSOLUTE
path, and this module is the only thing allowed to produce it.

Why absolute is a correctness requirement, not a style preference: the
agent's ``PATH`` puts the wrapper dir FIRST (so bare ``browser-use``
reaches the wrapper — see ``apply_agent_venv_path``). A wrapper that
forwards to the bare name therefore re-resolves to ITSELF. On Windows,
where the console shim is ``browser-use.exe`` with no extensionless
sibling, that is exactly what happened: the wrapper recursed until the
guard rejected the inherited ``BU_NAME``.

So resolution has two outcomes, never three: an absolute path, or
``BrowserUseNotFound``. There is deliberately no bare-name fallback —
writing a wrapper around a name is the bug. Refusing to write one at
all is safe, because ``bin/_guard/browser-use`` sits on the agent PATH
below the wrapper dir and fails loudly instead of letting bare
``browser-use`` reach the real binary (which would attach to the user's
own Chrome). See docs/browser.md § Wrapper safety.
"""

import logging
from pathlib import Path
import shutil
import sys

from app.config import BROWSER_USE_BIN_DIR
from app.platform import IS_WINDOWS

logger = logging.getLogger(__name__)

# Windows installs console scripts as ``browser-use.exe``; there is no
# extensionless sibling to find, and a cmd/bat-launched server may not
# carry the venv bin on PATH either, so ``shutil.which`` misses it too.
_WINDOWS_SHIM_EXTS = ('.exe', '.cmd', '.bat')


class BrowserUseNotFound(RuntimeError):
    """No real ``browser-use`` to point a wrapper at.

    ``browser-use`` is a hard dependency of the app, so this means a
    broken install (or an interpreter whose venv lost its scripts), not
    a configuration a user is expected to hit.
    """


def resolve_browser_use(daemon_bin: Path | None = None) -> str:
    """Return the absolute path of the real ``browser-use`` binary.

    Prefers the binary that lives in the same ``bin``/``Scripts`` dir as
    the daemon's own interpreter. In ``uv tool install vibe-seller`` mode
    that is the ONLY place it exists — the tool venv's bin is not on
    ``PATH``, so a ``shutil.which`` alone would miss it.

    Do NOT ``.resolve()`` the interpreter dir: uv venvs symlink the
    interpreter to a base Python (pyenv / homebrew / conda) whose bin
    often carries its OWN, older ``browser-use``; following the symlink
    would silently drive the wrong CLI instead of the pinned 0.13+.

    Raises:
        BrowserUseNotFound: if no absolute path can be established.
    """
    if daemon_bin is None:
        daemon_bin = Path(sys.executable).parent

    candidates = [daemon_bin / 'browser-use']
    if IS_WINDOWS:
        candidates += [
            daemon_bin / f'browser-use{ext}' for ext in _WINDOWS_SHIM_EXTS
        ]
    found = next((c for c in candidates if c.is_file()), None)
    if found is None:
        which = shutil.which('browser-use')
        found = Path(which) if which else None

    if found is None:
        raise BrowserUseNotFound(
            'No browser-use binary found next to the daemon interpreter '
            f'({daemon_bin}) or on PATH. A wrapper is deliberately NOT '
            'written without one: it could only forward to the bare name '
            '"browser-use", which re-resolves through the agent PATH to '
            'the wrapper itself. Reinstall the app dependencies.'
        )

    # A candidate inside the wrapper tree IS the recursion this module
    # exists to prevent — reject it however it was found.
    if _is_within(found, BROWSER_USE_BIN_DIR):
        raise BrowserUseNotFound(
            f'Resolved browser-use ({found}) lives inside the wrapper '
            f'tree ({BROWSER_USE_BIN_DIR}); forwarding there would make '
            'the wrapper exec itself. Check PATH ordering in the server '
            'process.'
        )
    # Absolute, but NOT resolved: a relative daemon_bin, or a relative
    # PATH entry behind shutil.which, would otherwise put a cwd-dependent
    # path in a wrapper that runs from anywhere. ``absolute()`` is purely
    # lexical, so it cannot follow a venv's interpreter symlink into a
    # base Python whose bin holds an older browser-use.
    return str(found.absolute())


def _is_within(path: Path, parent: Path) -> bool:
    """True when ``path`` is ``parent`` or sits underneath it.

    Compares fully RESOLVED paths — unlike the value we return, which
    must stay unresolved. A lexical check is dodgeable: a PATH entry
    outside the wrapper tree can be a symlink whose target is a
    generated wrapper inside it, and the new wrapper would then exec the
    old one. Resolving is safe here because the answer is only ever used
    to reject.
    """
    try:
        path.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True
