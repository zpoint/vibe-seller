"""The .lnk reader: what it must resolve, and that it never raises.

Fixtures come from ``lnk_fixtures`` (layout checked against
Windows-created links).
"""

import os
import random
import struct

import pytest

from app.browser import lnk_file
from tests.unit.test_browser import lnk_fixtures as fx

pytestmark = pytest.mark.unit

_ZINIAO = 'D:\\紫鸟\\ziniao\\ziniao.exe'
_ZINIAO_DIR = 'D:\\紫鸟\\ziniao'


def test_utf16_path_survives_a_codepage_that_cannot_write_it():
    """The ANSI copy of the target is ``?``-mangled; the UTF-16 copy
    holds the real path."""
    data = fx.lnk(
        fx.link_info(_ZINIAO.encode('ascii', 'replace'), wide=_ZINIAO)
    )
    assert lnk_file.read_target(data) == _ZINIAO


def test_real_windows_file_utf16_path_without_a_flag():
    """A real file: Windows wrote the UTF-16 path and set no flag. The
    header size alone says the copy is there."""
    data = fx.windows_ci_chinese_lnk()
    (size,) = struct.unpack_from('<I', data, fx._CI_LINK_INFO_AT)
    assert len(data) == fx._CI_LINK_INFO_AT + size  # LinkInfo is whole
    assert lnk_file.read_target(data) == 'D:\\紫鸟测试\\ziniao\\ziniao.exe'


def test_no_flag_is_needed_to_read_the_utf16_copy():
    """Crafted both ways — with and without 0x80 in LinkInfoFlags."""
    for extra in (0, 0x80):
        info = bytearray(fx.link_info(b'D:\\??\\a.exe', wide='D:\\紫鸟\\a.exe'))
        (flags,) = struct.unpack_from('<I', info, 8)
        struct.pack_into('<I', info, 8, flags | extra)
        assert lnk_file.read_target(fx.lnk(bytes(info))) == 'D:\\紫鸟\\a.exe'


def test_ansi_only_path():
    """A Windows 8.1 link to an ASCII path carries the ANSI copy only."""
    data = fx.lnk(fx.link_info(b'C:\\Users\\u\\App\\app.exe'))
    assert lnk_file.read_target(data) == 'C:\\Users\\u\\App\\app.exe'


@pytest.mark.parametrize(
    'info',
    [
        fx.link_info(b'C:\\Users\\', suffix=b'Administrator'),
        fx.link_info(b'C:\\Users\\', wide='C:\\Users\\', wide_suffix='用户'),
    ],
    ids=['ansi', 'utf-16'],
)
def test_path_is_local_base_path_plus_common_suffix(info):
    """A Windows 7 folder link stores ``C:\\Users\\`` and ``Administrator``
    as two strings."""
    assert lnk_file.read_target(fx.lnk(info)).startswith('C:\\Users\\')


@pytest.mark.parametrize(
    'relative, name, link_dir, expected',
    [
        ('ziniao.exe', None, _ZINIAO_DIR, _ZINIAO),
        ('app.exe', 'a description', 'E:\\Apps', 'E:\\Apps\\app.exe'),
        (
            '..\\..\\ziniao\\ziniao.exe',
            None,
            'E:\\Apps\\Ziniao.lnk\\..\\x',
            'E:\\ziniao\\ziniao.exe',
        ),
    ],
)
def test_relative_path_resolves_against_the_link_folder(
    relative, name, link_dir, expected
):
    data = fx.lnk(relative=relative, name=name)
    assert lnk_file.read_target(data, link_dir) == expected


def test_a_mangled_ansi_copy_defers_to_the_relative_path():
    """No UTF-16 copy and ``?`` in the ANSI one: the relative path is
    the only trustworthy form."""
    data = fx.lnk(
        fx.link_info(b'D:\\??\\ziniao\\ziniao.exe'), relative='ziniao.exe'
    )
    assert lnk_file.read_target(data, _ZINIAO_DIR) == _ZINIAO


def test_a_clean_ansi_path_wins_over_the_relative_one():
    data = fx.lnk(fx.link_info(b'E:\\real\\app.exe'), relative='app.exe')
    assert lnk_file.read_target(data, 'E:\\moved\\..') == 'E:\\real\\app.exe'


@pytest.mark.parametrize(
    'data',
    [
        pytest.param(fx.lnk(fx.link_info(b'', local=False)), id='network'),
        pytest.param(fx.lnk(fx.id_list(b'\x00\x00')), id='advertised'),
        pytest.param(fx.lnk(struct.pack('<I', 4)), id='empty-link-info'),
    ],
)
def test_links_without_a_filesystem_target_are_none(data):
    """Network-only, MSI \"advertised\"/Store links and a 4-byte empty
    LinkInfo (a Vista IDList-only link) have no path to follow."""
    assert lnk_file.read_target(data, 'C:\\Links') is None


def test_an_empty_link_info_does_not_hide_the_relative_path():
    """The strings follow the 4-byte empty LinkInfo: read from inside
    it instead, and the path turns into garbage."""
    data = fx.lnk(struct.pack('<I', 4), relative='ziniao.exe')
    assert lnk_file.read_target(data, _ZINIAO_DIR) == _ZINIAO


@pytest.mark.parametrize(
    'data',
    [
        pytest.param(b'', id='empty'),
        pytest.param(b'not a shortcut at all' * 8, id='text'),
        pytest.param(
            fx.header(fx.HAS_LINK_INFO)[:4]
            + bytes(16)
            + fx.header(fx.HAS_LINK_INFO)[20:],
            id='other-clsid',
        ),
        pytest.param(
            b'\x4d\x00\x00\x00' + fx.lnk(fx.link_info(b'C:\\x.exe'))[4:],
            id='header-size',
        ),
        pytest.param(
            _ZINIAO_DIR.encode('utf-16-le') + b'\0' * 40,
            id='utf16-text',
        ),
    ],
)
def test_files_that_are_not_shortcuts_are_none(data):
    assert lnk_file.read_target(data, 'C:\\Links') is None


def test_corrupt_offsets_are_not_read_as_paths():
    """An offset into the fixed header is ignored, never followed; a
    structure size past the end of the file is clamped to it."""
    good = 'C:\\x\\y.exe'
    info = bytearray(fx.link_info(b'C:\\x\\y.exe', wide=good))
    # Where LinkInfo keeps its offsets.
    local_offset, suffix_offset, wide_offset, wide_suffix_offset = (
        0x10,
        0x18,
        0x1C,
        0x20,
    )

    into_header = bytearray(info)  # the main path is "in the header"
    struct.pack_into('<I', into_header, local_offset, 4)
    assert lnk_file.read_target(fx.lnk(bytes(into_header))) is None

    wide_into_header = bytearray(info)  # … so the ANSI copy is used
    struct.pack_into('<I', wide_into_header, wide_offset, 4)
    assert lnk_file.read_target(fx.lnk(bytes(wide_into_header))) == good

    suffix_into_header = bytearray(info)  # an empty suffix, not garbage
    struct.pack_into('<I', suffix_into_header, suffix_offset, 4)
    struct.pack_into('<I', suffix_into_header, wide_suffix_offset, 4)
    assert lnk_file.read_target(fx.lnk(bytes(suffix_into_header))) == good

    past_eof = bytearray(info)
    struct.pack_into('<I', past_eof, 0, 10_000)  # LinkInfoSize
    assert lnk_file.read_target(fx.lnk(bytes(past_eof))) == good


def test_never_raises_however_the_file_is_truncated():
    full = fx.lnk(
        fx.link_info(b'D:\\??\\ziniao.exe', wide=_ZINIAO),
        idlist=fx.id_list(b'\x20\x00\x00\x00' + bytes(30)),
        name='紫鸟浏览器',
        relative='..\\ziniao\\ziniao.exe',
    )
    assert lnk_file.read_target(full) == _ZINIAO
    for cut in range(len(full)):
        lnk_file.read_target(full[:cut], _ZINIAO_DIR)  # must not raise


def test_never_raises_on_random_bytes():
    rng = random.Random(20261123)
    for _ in range(300):
        blob = fx.CLSID[:4] + bytes(rng.randrange(256) for _ in range(400))
        blob = struct.pack('<I', 0x4C) + fx.CLSID + blob[20:]
        lnk_file.read_target(blob, 'C:\\Links')


# ---- the shortcut folders ------------------------------------------------


def _tree(tmp_path):
    """A Desktop and a nested Start-Menu folder under ``tmp_path``."""
    desktop = tmp_path / 'desktop'
    vendor = tmp_path / 'programs' / 'vendor'
    vendor.mkdir(parents=True)
    desktop.mkdir()
    return desktop, vendor


def _write(path, data):
    path.write_bytes(data)


def test_shortcut_targets_walks_every_root_and_reports_windows_paths(
    tmp_path,
):
    desktop, vendor = _tree(tmp_path)
    _write(desktop / '紫鸟浏览器.lnk', fx.lnk(fx.link_info(b'?', wide=_ZINIAO)))
    _write(vendor / 'ziniao.lnk', fx.lnk(fx.link_info(b'E:\\Apps\\ziniao.exe')))
    roots = {
        'C:\\Desktop': str(desktop),
        'C:\\Programs': str(tmp_path / 'programs'),
    }

    found = lnk_file.shortcut_targets(list(roots), roots.__getitem__)

    assert sorted(found) == [
        (_ZINIAO, 'C:\\Desktop\\紫鸟浏览器.lnk'),
        ('E:\\Apps\\ziniao.exe', 'C:\\Programs\\vendor\\ziniao.lnk'),
    ]


def test_shortcut_targets_skips_what_has_no_target(tmp_path):
    desktop, vendor = _tree(tmp_path)
    _write(desktop / 'notes.txt', b'this is not a shortcut')
    _write(desktop / 'UPPER.LNK', fx.lnk(fx.link_info(b'C:\\x.exe')))
    _write(desktop / 'dead.lnk', fx.lnk(fx.id_list(b'\0\0')))
    _write(desktop / 'broken.lnk', b'garbage' * 10)
    _write(vendor / 'stale.lnk', fx.lnk(fx.link_info(b'')))
    roots = {
        'C:\\Desktop': str(desktop),
        'C:\\Programs': str(tmp_path / 'programs'),
    }

    found = lnk_file.shortcut_targets(list(roots), roots.__getitem__)

    assert found == [('C:\\x.exe', 'C:\\Desktop\\UPPER.LNK')]


def test_shortcut_targets_resolves_relative_links_in_place(tmp_path):
    desktop, _vendor = _tree(tmp_path)
    # A link lying next to its target, as an install folder may hold.
    _write(desktop / 'ziniao.lnk', fx.lnk(relative='ziniao.exe'))

    found = lnk_file.shortcut_targets(
        ['C:\\Desktop'], {'C:\\Desktop': str(desktop)}.__getitem__
    )

    assert found == [('C:\\Desktop\\ziniao.exe', 'C:\\Desktop\\ziniao.lnk')]


def test_shortcut_targets_tolerates_a_missing_root(tmp_path):
    missing = str(tmp_path / 'not-there')
    assert (
        lnk_file.shortcut_targets(
            ['C:\\None'], {'C:\\None': missing}.__getitem__
        )
        == []
    )


def test_shortcut_targets_does_not_follow_links_between_folders(
    tmp_path,
):
    """Start Menu and Desktop hold junctions; a link back into the tree
    must not be walked twice (or forever)."""
    desktop, _vendor = _tree(tmp_path)
    _write(desktop / 'ziniao.lnk', fx.lnk(fx.link_info(b'C:\\x.exe')))
    os.symlink(str(tmp_path), str(desktop / 'loop'), target_is_directory=True)
    roots = {'C:\\Desktop': str(desktop)}

    found = lnk_file.shortcut_targets(['C:\\Desktop'], roots.__getitem__)

    assert found == [('C:\\x.exe', 'C:\\Desktop\\ziniao.lnk')]


def test_shortcut_targets_skips_an_unreadable_file(tmp_path, monkeypatch):
    """A shortcut that cannot be read is skipped, never raised.

    The failure is injected where the contract is: opening the file.
    (Making it unreadable with ``chmod(0)`` works on POSIX only — on
    Windows the owner still reads it, and the test proves nothing.)
    """
    desktop, _vendor = _tree(tmp_path)
    secret = desktop / 'secret.lnk'
    _write(secret, fx.lnk(fx.link_info(b'C:\\x.exe')))
    real_open = open

    def denied(path, *args, **kwargs):
        if os.fspath(path) == str(secret):
            raise PermissionError(13, 'Permission denied', str(secret))
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr('builtins.open', denied)

    found = lnk_file.shortcut_targets(
        ['C:\\Desktop'], {'C:\\Desktop': str(desktop)}.__getitem__
    )

    assert found == []
