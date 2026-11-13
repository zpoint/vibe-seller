"""Installing this project's own uv tool — the argv, and the receipt.

`vibe-seller upgrade` is one of two shapes: "move to whatever the index
calls newest" (what a person typing the command means), or "install THIS
version" (what the in-app update means, because an update check already
chose it — `app/pip_update.py`, review 4's sibling). The second shape is
the awkward one: uv records the requirement it was given into
``uv-receipt.toml``, and a ``==`` sitting there freezes every later
``uv tool upgrade`` at that version, which is precisely why the plain
path deliberately installs unpinned. So an exact install has to undo its
own pin afterwards, and that repair is the whole reason this module
exists — it is a detail of uv's bookkeeping, not of the upgrade's
meaning, and it does not belong inline in the CLI.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)

#: uv's own record of what a tool env was installed from.
RECEIPT_NAME = 'uv-receipt.toml'


def has_uv() -> bool:
    """Is uv on PATH — i.e. is this a `uv tool install` layout at all?"""
    return bool(shutil.which('uv'))


def reinstall_argv(spec: str) -> list[str]:
    """The uv command that installs *spec* over the current tool env."""
    return ['uv', 'tool', 'install', '--force', spec]


def pip_install_argv(spec: str) -> list[str]:
    # No --no-deps: a target that adds or bumps a dependency must get it.
    return [sys.executable, '-m', 'pip', 'install', spec]


def receipt_path() -> Path:
    """``<tool env>/uv-receipt.toml`` — next to the running interpreter.

    The same location `app/auto_update.py` uses to recognise a uv tool
    install, so the two agree by construction.
    """
    return Path(sys.prefix) / RECEIPT_NAME


def unpin_receipt() -> bool:
    """Drop the ``specifier = "=="`` this install recorded. Best-effort.

    Returns True when the receipt changed. A receipt that cannot be read
    or written is not an upgrade failure — the software on disk is
    already the version that was asked for, and the pin only affects
    what a LATER `uv tool upgrade` will consider. That is worth a
    warning, not a rolled-back update.

    Line-wise, because uv writes one requirement table per line and the
    only thing to remove is `specifier` on the vibe-seller entry:

        [tool]
        requirements = [{ name = "vibe-seller", specifier = "==99.0.2" }]
        entrypoints = [{ name = "vibe-seller", install-path = … }]

    A dependency's pin is the resolver's business and is left alone.
    """
    path = receipt_path()
    try:
        text = path.read_text(encoding='utf-8')
    except OSError as exc:
        logger.warning('could not read the uv receipt %s: %s', path, exc)
        return False
    out: list[str] = []
    changed = False
    for line in text.splitlines(keepends=True):
        if 'specifier =' in line and 'name = "vibe-seller"' in line:
            new = re.sub(r',\s*specifier = "[^"]*"', '', line)
            changed = changed or new != line
            out.append(new)
        else:
            out.append(line)
    if not changed:
        if 'specifier =' in text:
            logger.warning(
                'the uv receipt %s still pins vibe-seller somewhere this '
                'parser did not recognise — a later `vibe-seller upgrade` '
                'may stay at the installed version',
                path,
            )
        return False
    try:
        path.write_text(''.join(out), encoding='utf-8')
    except OSError as exc:
        logger.warning('could not rewrite the uv receipt %s: %s', path, exc)
        return False
    return True


def install_exactly(version: str) -> int:
    """Install EXACTLY *version*, and leave no pin behind.

    The in-app update calls this through `vibe-seller upgrade --version`
    (see `app/pip_update.py` building the argv): the update check has
    already decided which release to move to — it read the channel
    rules, fingerprinted the runtime bundle, warmed this release's
    wheels into the cache and pre-installed the browser this release's
    Playwright pins. Asking the index again at apply time answers a
    different question, and can answer it differently: a release
    published in the minutes between the check and the apply, or any
    newer wheel in a find-links directory the receipt remembers. The
    Linux end-to-end measured exactly that (a fabricated broken 99.0.3
    installed in place of the approved 99.0.2, and an apply record
    describing an update that had never happened).
    """
    spec = f'vibe-seller=={version}'
    cmd = reinstall_argv(spec) if has_uv() else pip_install_argv(spec)
    print(f'==> {" ".join(cmd)}')
    rc = subprocess.run(cmd).returncode  # noqa: S603
    if rc != 0:
        return rc
    unpin_receipt()
    return 0
