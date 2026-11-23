"""Where the Windows-only tests create files — and the only code in them
that deletes a directory.

Test files live under one :class:`ScratchDir`: a uniquely named,
prefixed directory that is created and removed here and nowhere else.
:func:`assert_scratch_dir` refuses a drive or share root, a relative or
non-canonical path and any name these tests did not choose, so a wrong
target can never take a whole disk with it. A directory a test must
create elsewhere (a conventional install root) goes through
:class:`CreatedDirs`, which takes it back only while empty.
"""

from __future__ import annotations

import ntpath
import os
import shutil
import uuid

PREFIX = 'vibetest-'


def assert_scratch_dir(path: str) -> None:
    """Fail unless ``path`` is a directory these tests made under a root."""
    name = ntpath.basename(path.rstrip('\\'))
    assert name, f'refusing to remove {path!r}: a root, not a directory'
    _drive, rest = ntpath.splitdrive(path)
    assert rest.strip('\\'), (
        f'refusing to remove {path!r}: a drive or share, not a directory'
    )
    assert os.path.isabs(path) or ntpath.isabs(path), (
        f'refusing a relative path: {path!r}'
    )
    parts = path.replace('/', '\\').split('\\')
    assert not {'..', '.'} & set(parts), (
        f'refusing a path with . or .. in it: {path!r}'
    )
    assert name.startswith(PREFIX), (
        f'refusing to remove {path!r}: not made by these tests '
        f'(its name does not start with {PREFIX!r})'
    )


class ScratchDir:
    """A uniquely named directory under ``root``; created and removed here.

    ``mkdir()`` brings it into being and never adopts a directory that
    already exists, so ``remove()`` can only ever delete what this
    object made.
    """

    def __init__(self, root: str):
        self.path = os.path.join(root, PREFIX + uuid.uuid4().hex[:10])
        self._created = False

    def mkdir(self, *parts: str) -> str:
        """The scratch dir with ``parts`` under it, created on first use."""
        assert_scratch_dir(self.path)
        if not self._created:
            os.makedirs(self.path, exist_ok=False)
            self._created = True
        made = os.path.join(self.path, *parts)
        os.makedirs(made, exist_ok=True)
        return made

    def file(self, *parts: str, content: bytes = b'') -> str:
        """Write ``content`` to ``parts`` under the scratch dir."""
        path = os.path.join(self.mkdir(*parts[:-1]), parts[-1])
        with open(path, 'wb') as f:
            f.write(content)
        return path

    def remove(self) -> None:
        """Delete the scratch dir — only the one this object created."""
        if not self._created:
            return
        assert_scratch_dir(self.path)
        shutil.rmtree(self.path, ignore_errors=True)
        self._created = False


class CreatedDirs:
    """Directories a test had to create outside a scratch dir.

    ``ensure()`` creates what is missing and remembers each directory
    it created. ``remove()`` takes back exactly those, innermost first
    and only while they are empty: a directory that holds anything —
    a file the test left, or one that existed before — is left alone.
    """

    def __init__(self) -> None:
        self._made: list[str] = []

    def ensure(self, directory: str) -> str:
        """Create ``directory`` and whatever above it was missing too."""
        missing: list[str] = []
        probe = directory
        while probe and not os.path.isdir(probe):
            missing.append(probe)
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
        for path in reversed(missing):
            os.mkdir(path)
            self._made.append(path)
        return directory

    def remove(self) -> None:
        for path in reversed(self._made):
            try:
                os.rmdir(path)  # never a recursive delete
            except OSError:
                pass  # not empty: not ours to empty
        self._made.clear()


def remove_scratch_dir(path: str) -> None:
    """Remove ``path`` behind the same guard — for a root the tests had
    to create themselves, which no ``ScratchDir`` owns."""
    assert_scratch_dir(path)
    shutil.rmtree(path, ignore_errors=True)
