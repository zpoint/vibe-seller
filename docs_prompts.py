"""Render a user prompt as a chat bubble at build time.

The pages quote things the reader types or says — to Vibe Seller or to
their own AI tool: “帮我看看店铺A上周的广告”. The corner brackets 「…」
are this documentation's convention for naming a UI element, so a
prompt written that way is told apart from a label only by guessing
(length, punctuation), and a prompt that guesses wrong renders as a
button chip or plain text — a user prompt styled as code was the exact
complaint that ended this guessing.

The convention now is explicit: a user prompt lives in curly double
quotes ``“…”``, in the Chinese and the English sources alike. ``「…」``
stays for UI labels, error and dialog text; an ASCII ``"…"`` stays
ordinary punctuation.

``mark_prompts`` wraps every prose ``“…”`` pair in
``<span class="say">…</span>`` — the quotes themselves are dropped, the
CSS draws the bubble — under exactly the guards ``docs_ui_chips`` uses
for chips:

- only the lines ``docs_cjk.classify_lines`` calls prose: never front
  matter, fenced or indented code; and never a heading line, whose
  anchors must not change;
- inline code, link targets, image alt text and HTML tags survive
  untouched (masked first with ``docs_ui_chips.mask_line``);
- a prompt wrapped across source lines — the English pages wrap at
  about eighty columns — becomes one bubble: the pair may span the
  newline between two prose lines, but any non-prose line in between
  (blank, heading, code) stops it, so no bubble can leak across blocks.

The READMEs are generated from the sources, so GitHub keeps the plain
curly quotes — which is how a prompt should read in plain text anyway.
"""

from __future__ import annotations

import re

from docs_cjk import classify_lines
from docs_ui_chips import HEADING_LINE, mask_line, unmask_line

# One prompt: a curly-quoted run. The content excludes both quote
# shapes (so lazy matching can never swallow a second prompt), the mask
# token marker (so it cannot cross inline code or a link target) and
# the block barrier below — but not a newline, which is what lets a
# wrapped English prompt join its source lines into one bubble.
_PAIR = re.compile('“([^“”\x00\x02]+?)”')

# Stands in for a whole line the rewrite may not read through.
_BARRIER = '\x02'


def mark_prompts(markdown: str) -> str:
    """Rewrite every prose ``“…”`` pair into a chat-bubble span."""
    originals: list[str] = []
    masked: list[str] = []
    rewrite: list[bool] = []
    kept: list[list[str]] = []

    for kind, line in classify_lines(markdown):
        originals.append(line)
        if kind != 'prose' or HEADING_LINE.match(line):
            masked.append(_BARRIER)
            rewrite.append(False)
            kept.append([])
        else:
            span_masked, parked = mask_line(line)
            masked.append(span_masked)
            rewrite.append(True)
            kept.append(parked)

    marked = _PAIR.sub(
        lambda m: f'<span class="say">{m.group(1)}</span>',
        '\n'.join(masked),
    )

    out: list[str] = []
    for was_prose, part, original, parked in zip(
        rewrite, marked.split('\n'), originals, kept, strict=True
    ):
        out.append(unmask_line(part, parked) if was_prose else original)
    return '\n'.join(out)
