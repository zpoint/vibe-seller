"""Join hard-wrapped Chinese lines so the site stops rendering stray spaces.

Chinese prose in ``docs/*.md`` is wrapped by hand, mid-sentence, because
that is what makes the source readable in a diff. Markdown turns each of
those newlines into a space, and a space inside Chinese text is wrong —
the sources read fine but the site shows 「发到企业微信 群」, 「一格一格点
网页」, 「给这个 店铺创建」.

``join_cjk_lines`` fixes it at build time, in
``docs_hooks.on_page_markdown``, instead of rewrapping every source file
(which would undo itself the next time someone wraps a paragraph, and
would churn every diff in the repo). The sources keep their line breaks;
the English build and the GitHub READMEs — generated from the sources,
not from the build output — are unaffected.

Two lines are merged, with no space, when the first *renders* a CJK
character or Chinese punctuation at its end and the second renders one at
its start. Inline markup is looked through, not past: a line ending in
``、`` before a ``**bold**`` opener, or one ending in a
``[安装](install.md)`` link, is a wrapped sentence and gets joined. A
Latin or digit on either side keeps its space: 「API Key 卡片」 is written
that way on purpose.

Nothing else is touched: fenced and indented code, tables, HTML, front
matter and any line that starts a new block (heading, list marker,
admonition, ``|`` row, ``<`` tag, ``[^footnote``) are left alone. Lines
that merely *continue* a list item, a blockquote, a ``!!!`` admonition
or a ``===`` tab are still joined — their indent or ``> `` marker is
stripped and the text is appended to the line above, which keeps that
marker.
"""

from __future__ import annotations

import re

# Characters that count as "CJK": the ideographs, the CJK punctuation and
# bracket block, the fullwidth forms (，：！？（）), kana, and the handful
# of general punctuation marks this corpus uses as Chinese punctuation
# (— … and curly quotes).
_CJK_CLASS = (
    r'‘-”–—…'
    r'　-〿぀-ヿ㐀-䶿'
    r'一-鿿豈-﫿＀-￯'
)
_CJK = f'[{_CJK_CLASS}]'

# Line that opens or closes a fenced code block, at any indent — a fence
# inside a list item is indented (docs/chat-bots.en.md has several), and
# treating its body as wrapped prose would corrupt code.
_FENCE = re.compile(r'^\s*(`{3,}|~{3,})')

# A blockquote continuation marker at the start of a line.
_QUOTE_MARK = re.compile(r'^>+\s*')

# Prefixes that begin a block of their own. A line starting with one is
# never pulled into the line above it, and a line that is one never
# reaches down into the next. ``>`` is deliberately absent: a quoted line
# continues the quote it is in, so 「> 中文」 over 「> 中文」 is a wrapped
# sentence, not a new block — the marker is stripped on the join.
_BLOCK_PREFIXES = (
    '#',
    '|',
    '<',
    '!!!',
    '???',
    '===',
    '```',
    '~~~',
    '[!',
    '[^',
)

# A line that opens a container whose body is indented: list items,
# quotes, admonitions, collapsibles, content tabs, footnote definitions.
# Text indented under one of these is prose, not an indented code block.
_CONTAINER = re.compile(
    r'^ *(?:[-*+] +|\d+[.)] +|!{3}\?{0,1}|\?{3}|={3}|>|\[\^[^\]]+\]:)'
)

# A hard line break in Markdown: two or more trailing spaces, or a
# backslash. The author asked for the break, so the lines stay apart.
_FORCED_BREAK = re.compile(r'(?: {2,}|\\)$')

# Inline markup at the edge of a line, looked past when deciding what the
# reader actually sees there: a link target plus its closing bracket,
# emphasis markers, a code-span tick, a stray bracket.
_TAIL_MARKUP = re.compile(r'(?:\]\([^)]*\)|\*\*|__|[*_`~\[\]])+$')
_HEAD_MARKUP = re.compile(r'^(?:!?\[|\*\*|__|[*_`~])+')


def _is_cjk(char: str) -> bool:
    """Whether ``char`` is a CJK ideograph or Chinese punctuation."""
    return bool(char) and bool(re.match(f'(?u)^{_CJK}$', char))


def _block_body(line: str) -> str:
    """The line's text with its indent and quote markers removed."""
    body = line.lstrip()
    return _QUOTE_MARK.sub('', body)


def _starts_block(line: str) -> bool:
    """Whether this line opens a block rather than continuing one."""
    return _block_body(line).startswith(_BLOCK_PREFIXES)


def _tail_char(body: str) -> str:
    """The last character a reader sees on this line."""
    stripped = _TAIL_MARKUP.sub('', body.rstrip())
    return stripped[-1] if stripped else ''


def _head_char(body: str) -> str:
    """The first character a reader sees on this line."""
    stripped = _HEAD_MARKUP.sub('', body.lstrip())
    return stripped[0] if stripped else ''


def _markers_collide(prev_body: str, cur_body: str) -> bool:
    """Whether joining would butt two runs of markup against each other.

    ``**a**`` over ``**b**`` becomes ``**a****b**``, which Markdown reads
    as different emphasis; the same goes for ``__`` and for a code span
    closing against one opening. Those lines stay apart — a stray space
    is the lesser wrong next to changed formatting.
    """
    if prev_body[-2:] + cur_body[:2] in {'****', '____'}:
        return True
    return prev_body.endswith('`') and cur_body.startswith('`')


def _joinable(prev: str, cur: str) -> bool:
    """Whether ``cur`` continues ``prev`` mid-sentence in Chinese."""
    if _FORCED_BREAK.search(prev) or _starts_block(prev) or _starts_block(cur):
        return False
    prev_body, cur_body = prev.rstrip(), _block_body(cur)
    if not prev_body or not cur_body:
        return False
    if _markers_collide(prev_body, cur_body):
        return False
    return _is_cjk(_tail_char(prev_body)) and _is_cjk(_head_char(cur_body))


def _merge(prev: str, cur: str) -> str:
    """Append ``cur``'s text to ``prev``, keeping ``prev``'s own prefix."""
    return f'{prev.rstrip()}{_block_body(cur)}'


def _front_matter_end(lines: list[str]) -> int:
    """Index just past a YAML front-matter block, or 0 when absent."""
    if not lines or lines[0].strip() != '---':
        return 0
    for index in range(1, len(lines)):
        if lines[index].strip() in {'---', '...'}:
            return index + 1
    return 0


def classify_lines(markdown: str):
    """Yield ``(kind, line)`` for every line — ``'prose'`` or ``'skip'``.

    Front matter, fenced code and indented code are ``'skip'``: they are
    never merged with a neighbour and never rewritten. Everything else is
    ``'prose'``. Shared with ``docs_ui_chips``, which must not turn a
    「…」 inside a code sample into markup.
    """
    lines = markdown.split('\n')
    end = _front_matter_end(lines)
    yield from (('skip', line) for line in lines[:end])

    fence = ''  # the open ``` / ~~~ marker, '' at top level
    container = -1  # indent of the list/quote/admonition in play
    in_code = False
    prev_blank = True

    for line in lines[end:]:
        stripped = line.strip()
        indent = len(line) - len(line.lstrip(' '))

        if fence:
            yield 'skip', line
            if stripped.startswith(fence):
                fence = ''
            prev_blank = False
            continue

        if not stripped:
            yield 'skip', line
            prev_blank = True
            continue

        if _CONTAINER.match(line):
            container = indent
        elif indent <= container and indent < 4:
            container = -1

        # Indented code: four spaces at a point where nothing is holding
        # the text open. List, quote, admonition and tab bodies are
        # indented too, and those are prose — they still get fixed.
        if indent >= 4:
            if prev_blank and container < 0:
                in_code = True
            if in_code:
                yield 'skip', line
                prev_blank = False
                continue
        else:
            in_code = False

        opened = _FENCE.match(line)
        if opened:
            fence = opened.group(1)[0] * 3
            yield 'skip', line
            prev_blank = False
            continue

        yield 'prose', line
        prev_blank = False


def join_cjk_lines(markdown: str) -> str:
    """Rejoin Chinese lines that were wrapped mid-sentence."""
    out: list[str] = []
    prev_prose = False
    for kind, line in classify_lines(markdown):
        # A merge only ever happens between two prose lines that the
        # walker saw back to back — never across a blank line, a fence or
        # a run of indented code.
        if kind == 'prose' and prev_prose and _joinable(out[-1], line):
            out[-1] = _merge(out[-1], line)
        else:
            out.append(line)
        prev_prose = kind == 'prose'
    return '\n'.join(out)
