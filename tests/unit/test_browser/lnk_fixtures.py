"""Build Windows shortcut (``.lnk``) files byte by byte, per [MS-SHLLINK].

Three details of the real layout are easy to get wrong (checked against
Windows-created links): the header opens with its own size (0x4C)
before the CLSID; the target is ``LocalBasePath`` + ``CommonPathSuffix``;
``StringData`` strings are a character count with no terminator.

``link_info`` takes the ANSI path as raw bytes so a test can choose the
code page — including the ``?`` Windows writes for characters the
system code page cannot represent.
"""

import struct

CLSID = bytes.fromhex('0114020000000000c000000000000046')
HAS_IDLIST = 0x1
HAS_LINK_INFO = 0x2
HAS_NAME = 0x4
HAS_RELATIVE_PATH = 0x8
IS_UNICODE = 0x80


def header(*flags: int) -> bytes:
    return (
        struct.pack('<I', 0x4C)  # HeaderSize
        + CLSID
        + struct.pack('<I', sum(flags))  # LinkFlags
        + struct.pack('<I', 0x20)  # FileAttributes: directory
        + b'\0' * 24  # creation / access / write time
        + struct.pack('<III', 0x1000, 0, 1)  # size, icon index, show
        + b'\0' * 12  # hotkey + reserved
    )


def id_list(*items: bytes) -> bytes:
    body = b''.join(items) + b'\0\0'  # TerminalID
    return struct.pack('<H', len(body)) + body


def link_info(
    path: bytes,
    *,
    wide: str | None = None,
    suffix: bytes = b'',
    wide_suffix: str = '',
    local: bool = True,
) -> bytes:
    """A LinkInfo structure.

    ``path``: the ANSI ``LocalBasePath`` (bytes). ``wide``: the UTF-16
    copy — when given, the header grows to 0x24, as Windows writes it
    for a path its ANSI copy cannot hold. ``local=False``: a
    network-only link with no local base path.
    """
    volume = struct.pack('<III', 0x10, 3, 0x12345678) + struct.pack('<I', 0x10)
    fixed = 0x24 if wide is not None else 0x1C
    # Every offset counts from the START of the LinkInfo — header included.
    local_offset = fixed + len(volume)
    suffix_offset = local_offset + len(path) + 1
    body = volume + path + b'\0' + suffix + b'\0'
    wide_local = wide_suffix_offset = 0
    if wide is not None:
        wide_local = fixed + len(body)
        body += wide.encode('utf-16-le') + b'\0\0'
        wide_suffix_offset = fixed + len(body)
        body += wide_suffix.encode('utf-16-le') + b'\0\0'
    # No flag marks the UTF-16 copy — Windows sets none (the header
    # size alone tells); the reader must not wait for one.
    flags = 0x1 if local else 0x2
    head = struct.pack(
        '<7I',
        fixed + len(body),
        fixed,
        flags,
        fixed,
        local_offset,
        0,
        suffix_offset,
    )
    if wide is not None:
        head += struct.pack('<2I', wide_local, wide_suffix_offset)
    return head + body


def string(text: str, wide: bool = True) -> bytes:
    """One StringData entry: CountCharacters + the characters."""
    return struct.pack('<H', len(text)) + text.encode(
        'utf-16-le' if wide else 'latin-1'
    )


def lnk(
    info: bytes | None = None,
    *,
    relative: str | None = None,
    name: str | None = None,
    wide: bool = True,
    idlist: bytes | None = None,
    extra: bytes = b'',
) -> bytes:
    """A whole shortcut file from its pieces."""
    flags = []
    if idlist is not None:
        flags.append(HAS_IDLIST)
    if info is not None:
        flags.append(HAS_LINK_INFO)
    if name is not None:
        flags.append(HAS_NAME)
    if relative is not None:
        flags.append(HAS_RELATIVE_PATH)
    if wide:
        flags.append(IS_UNICODE)
    out = header(*flags)
    if idlist is not None:
        out += idlist
    if info is not None:
        out += info
    if name is not None:
        out += string(name, wide)
    if relative is not None:
        out += string(relative, wide)
    return out + extra


# The first 512 bytes of a real shortcut Windows (en-US) wrote for a link
# to D:\紫鸟测试\ziniao\ziniao.exe: header, IDList and the start of
# LinkInfo — header 0x24, flags 0x1 (no flag), ANSI copy "D:\????\…", the
# path in UTF-16 at its offset. Transcribed from a hexdump of the file;
# windows_ci_chinese_lnk() completes it from its declared offsets.
WINDOWS_CI_LINK_PREFIX = bytes.fromhex(
    '4c0000000114020000000000c00000000000004683000000200000005735352baa7add01b820362baa7add01b820362baa7add01000001000000000001000000'
    '000000000000000000000000350114001f50e04fd020ea3a6910a2d808002b30309d19002f443a5c000000000000000000000000000000000000005200350000'
    '000000765d7d8e10002b7d1f9e4b6dd58b00003a0009000400efbe765d7d8e765d818e2e00000006750000000002000000000000000000000000000000a97d10'
    '002b7d1f9e4b6dd58b000018005400310000000000765d7d8e10007a696e69616f00003e0009000400efbe765d7d8e765d818e2e000000077500000000010000'
    '00000000000000000000000000a97d10007a0069006e00690061006f00000016006000320000000100765d7d8e20007a696e69616f2e65786500004600090004'
    '00efbe765d7d8e765d7d8e2e0000000875000000000100000000000000000000000000000048920f007a0069006e00690061006f002e0065007800650000001a'
    '000000980000002400000001000000240000004600000000000000940000006000000096000000220000000300000065c5d53a1000000054656d706f72617279'
    '2053746f7261676500443a5c3f3f3f3f5c7a696e69616f5c7a696e69616f2e6578650044003a005c002b7d1f9e4b6dd58b5c007a0069006e00690061006f005c'
)

# Where that file's LinkInfo starts, and what it points at.
_CI_LINK_INFO_AT = 0x183
_CI_TARGET = 'D:\\紫鸟测试\\ziniao\\ziniao.exe'


def windows_ci_chinese_lnk() -> bytes:
    """That shortcut, whole enough to read.

    Its real bytes end inside LinkInfo. The rest is completed from the
    offsets and size LinkInfo's header declares — the remainder of the
    UTF-16 path, its terminator, an empty suffix — after checking that
    the bytes we have are the start of exactly that path.
    """
    prefix = WINDOWS_CI_LINK_PREFIX
    header = struct.unpack_from('<9I', prefix, _CI_LINK_INFO_AT)
    size, _header_size, _flags, _vol, _local, _net, _suf, local_u, suf_u = (
        header
    )
    info_at = _CI_LINK_INFO_AT
    path_at, suffix_at, end = info_at + local_u, info_at + suf_u, info_at + size
    path = _CI_TARGET.encode('utf-16-le') + b'\0\0'
    assert path.startswith(prefix[path_at:]), 'real bytes disagree'
    assert path_at + len(path) <= suffix_at < end
    return (
        prefix
        + path[len(prefix) - path_at :]
        + b'\0' * (suffix_at - path_at - len(path))
        + b'\0' * (end - suffix_at)
    )
