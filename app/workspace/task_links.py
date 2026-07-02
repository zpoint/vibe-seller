"""Per-task workspace linking, cross-platform.

Each task workspace exposes the shared ``knowledge``/``stores``/
``store-data``/``CLAUDE.md`` at a stable path resolving to the single
shared copy (writable-through for the dirs). POSIX does this with
symlinks; Windows uses directory *junctions* + a file copy, because
``os.symlink`` on Windows needs ``SeCreateSymbolicLinkPrivilege`` — a
privilege a normally-launched (non-elevated, Developer-Mode-off) process
does NOT hold, which raised ``WinError 1314`` on task creation.
Junctions and copies need no privilege, so the app runs as a plain user.
"""

import os
from pathlib import Path
import shutil
import stat

_IS_WINDOWS = os.name == 'nt'
if _IS_WINDOWS:
    import _winapi  # noqa: PLC2701 — stdlib junction API, no public equiv

    _create_junction = _winapi.CreateJunction  # CreateJunction(src, dst)
else:
    _create_junction = None

# Shared-root resources linked into every per-task workspace. Kept as a
# module constant so teardown can clear the exact same set without having
# to reason about junctions vs symlinks at each call site.
_SHARED_LINK_NAMES = ('knowledge', 'stores', 'store-data', 'CLAUDE.md')


def _is_junction(path: Path) -> bool:
    """True if *path* is a Windows directory junction (reparse point)."""
    if not _IS_WINDOWS:
        return False
    try:
        attrs = os.lstat(path).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _clear_workspace_link(link_path: Path) -> None:
    """Remove a per-task workspace link/copy *in place*.

    Never follows the link into its shared target: a POSIX symlink is
    unlinked; a Windows junction is dropped with ``os.rmdir`` (calling
    ``shutil.rmtree`` on a junction raises on 3.11 and could delete the
    shared target on other runtimes); a stray real dir/file left over
    from an older layout is removed normally.
    """
    if link_path.is_symlink():  # POSIX symlink (or a broken one)
        link_path.unlink()
    elif _is_junction(link_path):  # Windows junction → drop reparse point
        os.rmdir(link_path)
    elif link_path.exists():
        if link_path.is_dir():
            shutil.rmtree(link_path)
        else:
            link_path.unlink()


def _link_shared_into_task(link_path: Path, target: Path) -> None:
    """Point ``link_path`` at the shared ``target``.

    POSIX uses a symlink. Windows uses a directory *junction* for a
    directory and a *copy* for a file, because ``os.symlink`` on Windows
    needs a privilege stock machines lack (see module docstring).
    Junctions resolve to the same shared dir, so agent write-through is
    preserved; CLAUDE.md is static guidance, so a per-task copy is fine.
    """
    if not _IS_WINDOWS:
        link_path.symlink_to(target)
    elif target.is_dir():
        _create_junction(os.path.abspath(target), str(link_path))
    else:
        shutil.copy2(target, link_path)


def remove_task_workspace(task_dir: Path) -> None:
    """Delete a task workspace dir without touching shared resources.

    Clears the shared-resource links first so the final ``rmtree`` can
    never descend a junction into shared knowledge/stores, then removes
    the task-local files. Safe to call on POSIX (symlinks) too.

    Raises on failure (e.g. a locked file) rather than swallowing it, so
    ``clean=True`` callers surface the error as they did before this
    helper existed; best-effort callers (task deletion) wrap it in
    try/except.
    """
    if not task_dir.exists() and not task_dir.is_symlink():
        return
    for name in _SHARED_LINK_NAMES:
        _clear_workspace_link(task_dir / name)
    shutil.rmtree(task_dir)
