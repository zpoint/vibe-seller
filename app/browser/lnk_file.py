"""Read the target of a Windows shortcut (``.lnk``) file — pure Python.

``WScript.Shell`` is bound to the system ANSI code page and refuses a
shortcut whose path or target holds characters that page cannot write —
an install folder with a Chinese name, on an English Windows. The file
itself keeps the target as UTF-16, as an ANSI copy and as a path
relative to the link, so reading it needs no code page, COM or
subprocess.

Layout ([MS-SHLLINK])::

    ShellLinkHeader (0x4C)   starts with its own size, then the CLSID
    LinkTargetIDList         only if HasLinkTargetIDList
    LinkInfo                 only if HasLinkInfo
    StringData               NAME, RELATIVE_PATH, …
    ExtraData

In ``LinkInfo`` the header size alone says which copies exist: 0x1C is
the ANSI copy only, 0x24 adds the offsets of the UTF-16 copy (no flag
marks it). The path is ``LocalBasePath`` + ``CommonPathSuffix``. A
``StringData`` entry is a character count followed by that many
characters, with no terminator.

The target is taken from the UTF-16 copy, else the ANSI one, else
``RELATIVE_PATH`` resolved against the folder holding the link.
"""

from __future__ import annotations

import ntpath
import os
import struct

_HEADER_SIZE = 0x4C
_SHELLLINK_CLSID = bytes.fromhex('0114020000000000c000000000000046')

# LinkFlags
_HAS_TARGET_IDLIST = 0x1
_HAS_LINK_INFO = 0x2
_HAS_NAME = 0x4
_HAS_RELATIVE_PATH = 0x8
_HAS_WORKING_DIR = 0x10
_HAS_ARGUMENTS = 0x20
_HAS_ICON_LOCATION = 0x40
_IS_UNICODE = 0x80
_STRINGS_IN_ORDER = (
    (_HAS_NAME, False),
    (_HAS_RELATIVE_PATH, True),
    (_HAS_WORKING_DIR, False),
    (_HAS_ARGUMENTS, False),
    (_HAS_ICON_LOCATION, False),
)

# LinkInfoFlags
_LOCAL_BASE_PATH = 0x1
_LINK_INFO_FIXED = 0x1C  # header size without the UTF-16 offsets
_LINK_INFO_UNICODE_FIXED = 0x24


def _ansi(data: bytes) -> str:
    """Decode an ANSI string: the system code page on Windows
    (``mbcs``), which is the one Windows wrote it with."""
    try:
        return data.decode('mbcs', errors='replace')
    except LookupError:  # not Windows (WSL): ASCII paths still read
        return data.decode('utf-8', errors='replace')


def _utf16(data: bytes) -> str:
    return data.decode('utf-16-le', errors='replace')


def _link_info(data: bytes, offset: int) -> tuple[str, str] | None:
    """``(utf-16 path, ansi path)`` from the LinkInfo at ``offset``.

    Either may be ''. ``None`` when there is no usable LinkInfo or it
    has no local base path (a network-only link).
    """
    if offset + _LINK_INFO_FIXED > len(data):
        return None
    (size, header_size, flags, _vol, local, _net, suffix) = struct.unpack_from(
        '<7I', data, offset
    )
    if size < _LINK_INFO_FIXED or header_size < _LINK_INFO_FIXED:
        return None
    end = min(offset + size, len(data))

    def in_data(item_offset: int) -> bool:
        # Paths live after the fixed header; anything else is corrupt.
        return header_size <= item_offset < size

    def c_string(item_offset: int) -> bytes:
        if not in_data(item_offset):
            return b''
        start = offset + item_offset
        stop = data.find(b'\0', start, end)
        return data[start : stop if stop >= 0 else end]

    def utf16_string(item_offset: int) -> str:
        # Terminated by a NUL CHARACTER: every ASCII char holds a zero
        # byte, so a byte-wise search would stop after one character.
        if not in_data(item_offset):
            return ''
        start = stop = offset + item_offset
        while stop + 1 < end and data[stop : stop + 2] != b'\0\0':
            stop += 2
        return data[start:stop].decode('utf-16-le', errors='replace')

    if not flags & _LOCAL_BASE_PATH or not in_data(local):
        return None
    ansi = _ansi(c_string(local) + c_string(suffix))
    wide = ''
    # The UTF-16 copy exists whenever the header has room for its
    # offsets; no flag marks it.
    has_wide_offsets = offset + _LINK_INFO_UNICODE_FIXED <= len(data)
    if header_size >= _LINK_INFO_UNICODE_FIXED and has_wide_offsets:
        (local_u, suffix_u) = struct.unpack_from('<2I', data, offset + 0x1C)
        if in_data(local_u):
            wide = utf16_string(local_u) + utf16_string(suffix_u)
    return wide, ansi


def _strings(data: bytes, offset: int, flags: int) -> dict[int, str]:
    """StringData as ``{flag: text}``; a truncated file yields less."""
    out: dict[int, str] = {}
    wide = bool(flags & _IS_UNICODE)
    for flag, wanted in _STRINGS_IN_ORDER:
        if not flags & flag:
            continue
        if offset + 2 > len(data):
            break
        (count,) = struct.unpack_from('<H', data, offset)
        size = count * (2 if wide else 1)
        raw = data[offset + 2 : offset + 2 + size]
        if len(raw) < size:
            break
        if wanted:
            out[flag] = _utf16(raw) if wide else _ansi(raw)
        offset += 2 + size
    return out


def read_target(data: bytes, link_dir: str = '') -> str | None:
    """The absolute target path a ``.lnk`` points at, or ``None``.

    ``link_dir`` is the folder holding the link (needed only for a
    link that stores just a relative path). Never raises on a
    truncated or malformed file: anything unreadable is ``None``, the
    same as a link without a filesystem target (MSI "advertised" and
    Store-app shortcuts), which the caller simply skips.
    """
    if len(data) < _HEADER_SIZE:
        return None
    (header_size,) = struct.unpack_from('<I', data, 0)
    if header_size != _HEADER_SIZE or data[4:20] != _SHELLLINK_CLSID:
        return None
    (flags,) = struct.unpack_from('<I', data, 0x14)
    offset = _HEADER_SIZE
    if flags & _HAS_TARGET_IDLIST:
        if offset + 2 > len(data):
            return None
        (idlist_size,) = struct.unpack_from('<H', data, offset)
        offset += 2 + idlist_size
    info = None
    if flags & _HAS_LINK_INFO:
        info = _link_info(data, offset)
        # Always step over it — a link without a path carries a 4-byte
        # empty LinkInfo — or the strings would be read from inside it.
        if offset + 4 <= len(data):
            offset += max(struct.unpack_from('<I', data, offset)[0], 4)
    relative = ''
    if flags & _HAS_RELATIVE_PATH:
        relative = _strings(data, offset, flags).get(_HAS_RELATIVE_PATH, '')
    if link_dir and relative:
        relative = ntpath.normpath(ntpath.join(link_dir, relative))
    if not info:
        return relative or None
    wide, ansi = info
    # '?' in the ANSI copy means Windows could not write the characters
    # in this code page — the UTF-16 or relative form is the real path.
    if wide:
        return wide
    if '?' in ansi and relative:
        return relative
    return ansi or relative or None


# ---- the shortcut folders ----------------------------------------------

_MAX_BYTES = 1 << 20  # a shortcut is a few hundred bytes


def shortcut_targets(roots: list[str], to_local) -> list[tuple[str, str]]:
    """``(target path, .lnk path)`` for every shortcut under ``roots``.

    Both paths are Windows-style; ``to_local`` maps a Windows path to
    where this process reads it (identity on Windows, ``/mnt/<drive>``
    under WSL). The trees are walked without following links — Start
    Menu and Desktop hold junctions — and an unreadable file is
    skipped, never raised.
    """
    out: list[tuple[str, str]] = []
    for root in roots:
        root = root.rstrip('\\')
        local_root = to_local(root)
        for dirpath, dirs, files in os.walk(local_root):
            dirs.sort()  # deterministic; junction loops are not followed
            for name in sorted(files):
                if not name.lower().endswith('.lnk'):
                    continue
                try:
                    with open(os.path.join(dirpath, name), 'rb') as f:
                        data = f.read(_MAX_BYTES)
                except OSError:
                    continue
                relative = os.path.relpath(dirpath, local_root)
                folder = root
                if relative != os.curdir:
                    folder = ntpath.join(root, relative.replace(os.sep, '\\'))
                target = read_target(data, folder)
                if target:
                    out.append((target, folder + '\\' + name))
    return out
