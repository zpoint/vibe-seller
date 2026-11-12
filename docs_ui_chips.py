"""Render 「UI labels」 and 「设置 → 集成」 menu paths as chips at build time.

The corner brackets are this documentation's convention for naming a UI
element — they quote what the frontend itself shows (「新建任务」,
「授权」, 「设置 → 集成 → API Key」). Written that way they read as plain
text, so a reader scanning a step cannot tell a button from a sentence.

``mark_ui_labels`` wraps them in ``<span class="ui">`` so
``docs/stylesheets/extra.css`` can give them a chip: a hairline box, a
light fill, no wrap. It runs in ``docs_hooks.on_page_markdown`` on the
site build only — the sources keep their brackets, so the GitHub
READMEs and the raw ``.md`` files are unchanged.

What counts as a label, and what is left as plain quoted text:

- at most 24 characters — longer is prose. A label containing ``→`` is a
  menu path and is exempt: 「设置 → 集成 → Vibe Seller 广告 API 服务」 is
  still a breadcrumb at 31 characters, so paths get their own 60-character
  cap;
- a chain of bracketed labels joined by an arrow *outside* the brackets —
  「设置 → 邮箱」→「添加邮箱」 — is one menu path, rendered as a single
  chip (设置 › 邮箱 › 添加邮箱) rather than two chips and a stray arrow;
- no sentence punctuation inside (，。！？；：“”), which is what a quoted
  user prompt or a full sentence looks like;
- not on a heading line (``#``) — heading anchors must not change;
- not inside inline code, a fenced or indented code block, an HTML tag
  or attribute (including a link's target or an image's alt text), or
  front matter — ``docs_cjk.classify_lines`` says which lines are prose,
  and each line's protected spans are masked before the substitution.

A label containing ``→`` becomes a menu path: the segments are trimmed
and joined with a muted ``›``, so 「设置 → 集成」 renders as one chip
reading 设置 › 集成.

English pages use ``"**Label**"`` (quotes around a bolded button name)
for the same purpose; that pattern is converted under the same guards.
``**"Label"**`` is *not*: in these files it is usually bold prose with
quotes as punctuation — ``**" and fill "**`` — which no length rule can
tell from a label.
"""

from __future__ import annotations

import re

from docs_cjk import classify_lines

# A corner-bracketed run: the site's UI-label convention.
_LABEL = re.compile(r'「([^」\n]+)」')

# Quotes around a bold label — the English pages' convention.
_QUOTED_BOLD = re.compile(r'"(?:\*\*|__)([^"\n]+?)(?:\*\*|__)"')

# Punctuation that says "this is a sentence, not a label".
_SENTENCE_CHARS = '，。！？；：“”'

# Longer than this is prose, not a UI element's name.
_MAX_LABEL_CHARS = 24

# A menu path is not prose at any length: 「设置 → 集成 → Vibe Seller
# 广告 API 服务」 is still a breadcrumb, so paths get their own, much
# higher cap.
_MAX_PATH_CHARS = 60

# A chip longer than this is allowed to break across lines (.ui--long).
_WRAP_AFTER_CHARS = 16

# Two or more bracketed labels joined by an arrow *outside* the brackets:
# 「设置 → 邮箱」→「添加邮箱」 is one menu path, not two chips.
_CHAIN = re.compile(r'「[^」\n]+」(?: *→ *「[^」\n]+」)+')

# Spans of a line that must survive untouched, in the order they are
# masked: an image (its alt text is an attribute), a link's target,
# inline code, an HTML tag or autolink.
_PROTECTED = (
    re.compile(r'!\[[^\]]*\]\([^)]*\)'),
    re.compile(r'\]\([^)]*\)'),
    re.compile(r'`+[^`\n]+`+'),
    re.compile(r'<[^>\n]*>'),
)

# A heading line, whose text must keep its anchors.
_HEADING = re.compile(r'^ *(?:#{1,6})\s')

# The muted separator inside a menu-path chip.
_SEP = '<span class="ui-sep">›</span>'

# Placeholder used to park a protected span while the line is rewritten.
_TOKEN = re.compile(r'\x00(\d+)\x00')


def _looks_like_a_sentence(text: str) -> bool:
    """Whether the bracketed text reads as prose rather than a label."""
    return len(text) > _MAX_LABEL_CHARS or any(
        char in _SENTENCE_CHARS for char in text
    )


def _path_chip(text: str) -> str | None:
    """One chip for a menu path, or ``None`` when it is not eligible.

    A path is ``→``-separated; each segment is trimmed of the arrows and
    of a trailing ``›`` the UI's own menu buttons carry (「GLM ›」), and
    the segments are joined with the muted separator. The length cap is
    the path one: a breadcrumb stays a breadcrumb well past the size at
    which a plain label would read as prose.
    """
    parts = [
        part.strip(' \t›→>') for part in text.split('→') if part.strip(' \t›→>')
    ]
    if len(parts) < 2:
        return None
    if any(char in _SENTENCE_CHARS for part in parts for char in part):
        return None
    if len(''.join(parts)) > _MAX_PATH_CHARS:
        return None
    long_class = ' ui--long' if len(''.join(parts)) > _WRAP_AFTER_CHARS else ''
    return f'<span class="ui{long_class}">{_SEP.join(parts)}</span>'


def _chip(text: str) -> str:
    """The chip markup for one label, or the plain text if it is prose."""
    if '→' in text:
        path = _path_chip(text)
        if path is not None:
            return path
    if _looks_like_a_sentence(text):
        return text
    long_class = ' ui--long' if len(text) > _WRAP_AFTER_CHARS else ''
    return f'<span class="ui{long_class}">{text}</span>'


def _chain_chip(match: re.Match[str]) -> str:
    """One chip for 「A」→「B」: the arrows join one path, not two chips."""
    segments = _LABEL.findall(match.group(0))
    return _path_chip(' → '.join(segments)) or match.group(0)


def _mask(line: str) -> tuple[str, list[str]]:
    """Replace protected spans with ``\\x00n\\x00`` tokens."""
    kept: list[str] = []

    def park(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        return f'\x00{len(kept) - 1}\x00'

    masked = line
    for pattern in _PROTECTED:
        masked = pattern.sub(park, masked)
    return masked, kept


def _unmask(line: str, kept: list[str]) -> str:
    """Put the protected spans back."""
    return _TOKEN.sub(lambda m: kept[int(m.group(1))], line)


def _mark_line(line: str) -> str:
    """One prose line: chips for its labels, nothing else changed."""
    if '「' not in line and '"' not in line:
        return line
    masked, kept = _mask(line)
    # Chains first: 「A」→「B」 must be consumed as one path before the
    # single-label pass turns each half into its own chip.
    masked = _CHAIN.sub(_chain_chip, masked)
    masked = _LABEL.sub(lambda m: _chip_or_keep(m.group(0), m.group(1)), masked)
    masked = _QUOTED_BOLD.sub(
        lambda m: _chip_or_keep(m.group(0), m.group(1)), masked
    )
    return _unmask(masked, kept)


def _chip_or_keep(original: str, text: str) -> str:
    """Chip ``text``, or return ``original`` untouched when it is prose.

    The English ``"**Label**"`` form drops its quotes and bold markers
    along with the conversion, so when the text turns out to be prose the
    original must come back exactly as it was.
    """
    chip = _chip(text)
    if chip == text:
        return original
    return chip


def mark_ui_labels(markdown: str) -> str:
    """Rewrite every prose line of ``markdown`` with label chips."""
    out: list[str] = []
    for kind, line in classify_lines(markdown):
        if kind != 'prose' or _HEADING.match(line):
            out.append(line)
            continue
        out.append(_mark_line(line))
    return '\n'.join(out)
