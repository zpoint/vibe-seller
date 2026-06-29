"""Deterministic catalog-stub contract.

The L2/L3 catalog sync agent summarizes every knowledge/store file as a
row in ``CATALOG.md``. The sync prompt tells it to write the literal
``Empty/stub`` summary for empty/near-empty files and a real one-line
topic summary otherwise (see ``app/prompts/__init__.py`` CATALOG_DESC_L2/
L3). On a weak model that *empty-vs-substantive* judgment is unreliable:
it intermittently stubs a file that has a clear topic, which hides where
the content lives and forces a catalog reader to grep instead of
navigating straight to the file (the observed catalog-first e2e flake —
the find-secret agent read CATALOG.md, found the secret file stubbed as
``Empty/stub``, and wandered/grepped to locate it).

This module moves that judgment out of the prompt and into code:
``is_near_empty`` is computed deterministically, and a ``CATALOG.md``
write that stubs a *substantive* file is rejected so the sync agent must
summarize it. The contract becomes impossible to violate from the write
surface, rather than relying on the model getting the call right.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

# Must match the summary string the sync prompt instructs the agent to
# write for empty files (app/prompts/__init__.py CATALOG_DESC_L2/L3).
STUB_SUMMARY = 'Empty/stub — knowledge accumulates here'

# A file with fewer than this many body words (after stripping YAML
# frontmatter, markdown headings, and blank lines) has nothing to
# summarize — a legitimate stub. At/above it the file has a topic and
# MUST be summarized, not stubbed. Deliberately low so genuinely tiny
# placeholders (a bare heading, a one-word marker) still stub cleanly
# and only files with real prose are protected.
_MIN_BODY_WORDS = 5


def is_near_empty(content: str) -> bool:
    """True when a file has no describable body.

    Deterministic replacement for the sync model's "empty or near-empty"
    judgment: drop YAML frontmatter, markdown heading lines, and blank
    lines, then count the remaining body words.
    """
    s = content
    if s.startswith('---'):
        end = s.find('\n---', 3)
        if end != -1:
            s = s[end + 4 :]
    words: list[str] = []
    for line in s.splitlines():
        t = line.strip()
        if not t or t.startswith('#'):
            continue
        words.extend(t.split())
    return len(words) < _MIN_BODY_WORDS


def _data_rows(catalog_text: str):
    """Yield ``(file_path, summary)`` for each data row of the table.

    Skips the markdown header row and the ``|---|`` separator.
    """
    for line in catalog_text.splitlines():
        t = line.strip()
        if not t.startswith('|'):
            continue
        cells = [c.strip() for c in t.strip('|').split('|')]
        if len(cells) < 2:
            continue
        # Separator row (only dashes/colons/spaces) — not data.
        if not ''.join(cells).strip('-: '):
            continue
        if cells[0].lower() == 'file':  # header row
            continue
        path, summary = cells[0], cells[-1]
        if '/' in path:  # a real workspace path, not a label
            yield path, summary


def find_wrongly_stubbed(
    catalog_text: str, read_text: Callable[[str], str | None]
) -> list[str]:
    """Paths the catalog stubs although the file is substantive.

    ``read_text(rel_path)`` returns the referenced file's text, or None if
    it can't be read (missing/binary) — those are skipped, since a stub
    can't be disproven without the content.
    """
    bad: list[str] = []
    for path, summary in _data_rows(catalog_text):
        if STUB_SUMMARY not in summary:
            continue
        try:
            content = read_text(path)
        except Exception:
            content = None
        if content is not None and not is_near_empty(content):
            bad.append(path)
    return bad


def reject_wrong_stubs(
    catalog_text: str, resolve_file: Callable[[str], Path]
) -> None:
    """Raise ValueError if the catalog stubs a substantive file.

    ``resolve_file(rel_path)`` returns the workspace Path for a row's
    file (may raise OSError/ValueError if missing/unsafe — treated as
    unreadable). Called from the CATALOG.md write path so the contract
    is enforced server-side rather than trusted to the sync agent.
    """

    def _read(rel: str) -> str | None:
        try:
            return resolve_file(rel).read_text(encoding='utf-8')
        except (OSError, ValueError):
            return None

    bad = find_wrongly_stubbed(catalog_text, _read)
    if bad:
        listed = ', '.join(bad[:10])
        raise ValueError(
            f'CATALOG marks {len(bad)} file(s) as "{STUB_SUMMARY}" but '
            f'they have real content: {listed}. Read each and write a '
            'one-line topic summary (≤80 chars) instead of the stub, '
            'then write CATALOG.md again.'
        )
