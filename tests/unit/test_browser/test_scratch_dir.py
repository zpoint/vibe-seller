"""The deletion guard of the Windows-only tests.

Those tests run on Windows alone, so this file is where CI checks the
guard on every OS.
"""

import os

import pytest

from tests.unit.test_browser import scratch_dir as sd

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    'path',
    [
        pytest.param('D:\\', id='drive'),
        pytest.param('D:', id='drive-no-slash'),
        pytest.param('\\\\server\\share\\', id='share'),
        pytest.param('/', id='posix-root'),
        pytest.param('', id='empty'),
        pytest.param('vibetest-x', id='relative'),
        pytest.param('C:vibetest-x', id='drive-relative'),
        pytest.param('C:\\Users\\someone', id='foreign-name'),
        pytest.param('D:\\vibetest-x\\..\\..\\vibetest-y', id='dot-dot'),
    ],
)
def test_roots_and_foreign_paths_are_refused(path):
    """A drive, a share, a relative or non-canonical path, a name these
    tests did not choose: refused, so nothing is ever removed.

    This calls the guard itself, never a function that deletes: if the
    guard regressed, the test must fail, not remove the path.
    """
    with pytest.raises(AssertionError, match='refusing'):
        sd.assert_scratch_dir(path)


def test_remove_deletes_only_its_own_directory(tmp_path):
    """Whatever else sits under the root stays, prefixed or not; removing
    twice or before anything was created does nothing."""
    keep = tmp_path / 'keep.txt'
    keep.write_text('mine')
    theirs = tmp_path / (sd.PREFIX + 'theirs')
    theirs.mkdir()
    area = sd.ScratchDir(str(tmp_path))

    area.remove()  # nothing created yet
    exe = area.file('紫鸟测试', 'ziniao', 'ziniao.exe', content=b'MZ')
    assert os.path.isfile(exe)
    area.remove()
    area.remove()  # already gone

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        'keep.txt',
        theirs.name,
    ]
    assert keep.read_text() == 'mine'

    # A root a test had to create itself goes through the same guard.
    made_root = os.path.join(str(tmp_path), sd.PREFIX + 'root-abc')
    os.makedirs(made_root)
    sd.remove_scratch_dir(made_root)
    assert not os.path.exists(made_root)


def test_a_directory_that_already_existed_is_never_adopted(tmp_path):
    """A name collision fails loudly and never lets ``remove()`` delete a
    directory this object did not create."""
    area = sd.ScratchDir(str(tmp_path))
    theirs = tmp_path / os.path.basename(area.path)
    theirs.mkdir()
    (theirs / 'note.txt').write_text('theirs')

    with pytest.raises(FileExistsError):
        area.mkdir('x')
    area.remove()

    assert (theirs / 'note.txt').read_text() == 'theirs'


def test_created_dirs_are_taken_back_only_while_empty(tmp_path):
    """Only what ``ensure()`` created, innermost first, and only when
    nothing is left inside; a directory that existed before is not
    touched at all."""
    made = sd.CreatedDirs()
    theirs = tmp_path / 'theirs'
    theirs.mkdir()
    (theirs / 'note.txt').write_text('keep')
    made.ensure(str(theirs))  # already there: nothing to take back
    target = made.ensure(str(tmp_path / 'Program Files' / 'ziniao'))
    leftover = os.path.join(target, 'ziniao.exe')
    with open(leftover, 'wb') as f:
        f.write(b'MZ')

    made.remove()

    assert os.path.isfile(leftover)  # not empty: left alone
    assert (theirs / 'note.txt').read_text() == 'keep'

    fresh = sd.CreatedDirs()
    fresh.ensure(str(tmp_path / 'a' / 'b'))

    fresh.remove()

    assert not (tmp_path / 'a').exists()  # empty and ours: gone
