"""Detect malformed markdown tables in a task result.

The frontend renders task results via ``react-markdown`` + ``remark-gfm``.
GFM tables that look like tables but have inconsistent column counts
(header vs separator vs body) are silently downgraded to ``<p>`` —
the user sees a wall of pipe-separated text instead of a table.

mistune's GFM table parser has the same accept/reject behavior as
remark-gfm, so we use it as the oracle: if mistune doesn't produce a
``<table>``, neither will the frontend. We extract every block that
*looks* like a table (lines starting with ``|`` separated from the
surrounding text), render each through mistune, and flag any that
fails to parse as a table.
"""

from __future__ import annotations

import re

import mistune

from app.ai.stop_gates import GateDeny

GATE_NAME = 'markdown_format'

# A "pipe block" is two or more consecutive non-empty lines whose
# stripped form starts with ``|``. The header + separator + body of a
# GFM table all share that shape. We only flag the block if it also
# contains a separator-row pattern, so plain prose with ``|`` chars
# (e.g. shell pipelines in a paragraph) doesn't trigger.
_PIPE_LINE_RE = re.compile(r'^\s*\|')
_SEPARATOR_LINE_RE = re.compile(r'^\s*\|[\s\-:|]+\|\s*$')

_md = mistune.create_markdown(plugins=['table'])


def _find_pipe_blocks(text: str) -> list[tuple[int, str]]:
    """Return [(start_line_1based, block_text), ...] for each
    consecutive run of pipe-prefixed lines that contains a separator
    row. Lines outside fenced code blocks only.
    """
    out: list[tuple[int, str]] = []
    lines = text.split('\n')
    in_fence = False
    i = 0
    while i < len(lines):
        stripped = lines[i].lstrip()
        # Track fenced code blocks so a ``| --- |`` inside ```...```
        # doesn't get misread as a table.
        if stripped.startswith('```') or stripped.startswith('~~~'):
            in_fence = not in_fence
            i += 1
            continue
        if not in_fence and _PIPE_LINE_RE.match(lines[i]):
            start = i
            j = i
            while j < len(lines) and _PIPE_LINE_RE.match(lines[j]):
                j += 1
            block_lines = lines[start:j]
            if any(_SEPARATOR_LINE_RE.match(ln) for ln in block_lines):
                out.append((start + 1, '\n'.join(block_lines)))
            i = j
            continue
        i += 1
    return out


def _block_renders_as_table(block: str) -> bool:
    """True if mistune produces a ``<table>`` element for this block.
    Mirrors remark-gfm's accept/reject behavior (verified locally).
    """
    html = _md(block)
    return '<table' in html


def check(result_text: str) -> GateDeny | None:
    """Return a ``GateDeny`` if any intended-table block in
    *result_text* is malformed; otherwise ``None``. A pass-through
    for results with no tables at all.
    """
    if not result_text or not isinstance(result_text, str):
        return None
    blocks = _find_pipe_blocks(result_text)
    if not blocks:
        return None
    bad: list[str] = []
    for line_no, block in blocks:
        if not _block_renders_as_table(block):
            first = block.split('\n', 1)[0].strip()
            preview = first[:80] + ('…' if len(first) > 80 else '')
            bad.append(f'line {line_no}: `{preview}`')
    if not bad:
        return None
    reason = (
        'Your result contains markdown that looks like tables but '
        'fails GFM parsing (header / separator / body column counts '
        'must all match, and every row must start and end with `|`). '
        'The frontend will silently render these as a wall of '
        'pipe-separated text instead of a table.\n\n'
        'Blocks that failed parsing:\n  - '
        + '\n  - '.join(bad)
        + '\n\nFix the column counts and call set_task_result again.'
    )
    return GateDeny(gate=GATE_NAME, reason=reason)
