"""Parse GitHub release bodies into merged, per-section changelog items.

An in-app update can span several versions (0.1.0 → 0.3.2), and the
release bodies carry notes per version. The UI shows them **merged by
section** — one 亮点 list and one 升级须知 list, each item tagged with
the version it came from, newest version first — rather than one block
per version.

The body format (written by the release workflow; see a real release,
e.g. ``gh api repos/zpoint/vibe-seller/releases/tags/v0.3.1``)::

    # title
    intro paragraph

    ## Highlights
    - 中文条目（可能折行续写）
      - 子条目
    - 中文条目 2
    ---
    - English bullet
    ...

    ## Notes
    - note bullet
    ...

``## Notes`` ends at a ``---`` line, a ``**Full changelog`` line, the
next ``## `` heading, or the end of the body.

A body that doesn't match (no ``## Highlights`` section, or it carries
no bullets) becomes ONE highlight item with the whole body — never
silently dropped, so a release always shows up somewhere.

Item texts stay MARKDOWN: the bullets are authored in markdown, so
``**bold**``, `` `code` `` and links pass through untouched for
``react-markdown`` to render.
"""

from __future__ import annotations

import re

_HIGHLIGHTS_HEAD = '## Highlights'
_NOTES_HEAD = '## Notes'
# A top-level bullet starts at column 0; an indented ``- `` is a
# sub-bullet of the item before it.
_SUB_BULLET = re.compile(r'^[ \t]+[-*][ \t]+')
_TOP_BULLET = re.compile(r'^[-*][ \t]+')
_SECTION_HEAD = re.compile(r'^##[ \t]')


def _find_heading(lines: list[str], title: str) -> int | None:
    for i, line in enumerate(lines):
        if line.strip() == title:
            return i
    return None


def _bullets(lines: list[str]) -> list[str]:
    """The markdown bullet items in *lines*.

    Continuation lines join the current item (single newlines inside a
    markdown paragraph), indented sub-bullets are kept as nested list
    lines under their parent.
    """
    items: list[list[str]] = []
    for line in lines:
        if not line.strip():
            continue
        if not line.startswith((' ', '\t')) and _TOP_BULLET.match(line):
            items.append([line.strip()])
            continue
        if not items:
            # Prose before the first bullet (an intro line) — skip it;
            # only bullets are list items.
            continue
        if _SUB_BULLET.match(line):
            # Re-indent to exactly two spaces: the content column of a
            # ``- `` item, so remark nests it under the parent.
            items[-1].append('  ' + line.strip())
            continue
        items[-1].append(line.strip())
    return ['\n'.join(item).rstrip() for item in items]


def _cut_section(lines: list[str], start: int) -> list[str]:
    """Lines after heading *start*, up to the next ``## `` heading."""
    out = []
    for line in lines[start + 1 :]:
        if _SECTION_HEAD.match(line):
            break
        out.append(line)
    return out


def parse_release_body(body: str) -> dict | None:
    """Split a release body into its changelog sections.

    Returns ``{'highlights': [{'zh', 'en'}], 'notes': [str]}`` or None
    when the body is not in the release-notes format (caller falls back
    to showing the whole body as one highlight item).

    ``## Highlights`` bullets come in two halves separated by a ``---``
    line: Chinese first, then English. They are paired 1:1 into single
    highlight items (the format guarantees one entry per feature in
    each language); when the halves disagree in length the missing side
    stays empty and the UI falls back to the other language.
    """
    lines = body.replace('\r\n', '\n').split('\n')
    hi = _find_heading(lines, _HIGHLIGHTS_HEAD)
    if hi is None:
        return None
    notes_at = _find_heading(lines, _NOTES_HEAD)
    hi_block = (
        lines[hi + 1 : notes_at] if notes_at is not None else (lines[hi + 1 :])
    )
    seps = [i for i, line in enumerate(hi_block) if line.strip() == '---']
    zh = _bullets(hi_block[: seps[0]] if seps else hi_block)
    en = _bullets(hi_block[seps[0] + 1 :]) if seps else []
    if not zh and not en:
        return None
    highlights = [
        {'zh': zh[i] if i < len(zh) else '', 'en': en[i] if i < len(en) else ''}
        for i in range(max(len(zh), len(en)))
    ]

    notes: list[str] = []
    if notes_at is not None:
        block = []
        for line in lines[notes_at + 1 :]:
            if (
                line.strip() == '---'
                or line.startswith('**Full')
                or _SECTION_HEAD.match(line)
            ):
                break
            block.append(line)
        notes = _bullets(block)

    return {'highlights': highlights, 'notes': notes}


def merged_notes(releases: list[dict]) -> dict:
    """Merge release bodies into ``{'highlights', 'notes'}`` item lists.

    *releases* is newest-first, each ``{'version', 'body'}``. Every
    item carries its ``version``; the section order across versions
    keeps the newest version first.
    """
    highlights: list[dict] = []
    notes: list[dict] = []
    for rel in releases:
        version = rel.get('version') or ''
        body = (rel.get('body') or '').strip()
        parsed = parse_release_body(body) if body else None
        if parsed is None:
            if body:
                # Format mismatch: the whole body becomes one highlight
                # item so the release is still visible.
                highlights.append({'version': version, 'zh': body, 'en': body})
            continue
        for item in parsed['highlights']:
            highlights.append({'version': version, **item})
        for text in parsed['notes']:
            notes.append({'version': version, 'text': text})
    return {'highlights': highlights, 'notes': notes}
