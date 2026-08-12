"""Shared text helpers.

``sanitize_text`` lives here (rather than in a router) because both the
task-submission path and the workspace file-write path persist
agent-supplied free text, and both need the same surrogate guard without
one router importing the other's dependency stack.
"""


def sanitize_text(text: str) -> str:
    """Make a str safe to persist (SQLite TEXT binds as UTF-8).

    Agent-supplied free text occasionally carries lone surrogate code
    points (D800-DFFF) — claude's stream-json can emit an unpaired
    surrogate for certain CJK/emoji sequences. They are invalid Unicode,
    so an utf-8 encode — a ``db.commit()`` of ``task.submitted_result``,
    or ``Path.write_text(..., encoding='utf-8')`` for a workspace file —
    dies with ``'utf-8' codec can't encode character '\\udcad' ...
    surrogates not allowed``, surfacing to the agent as an opaque tool
    error. Replace them (U+FFFD) so the write always succeeds; an invalid
    code point carries no information worth keeping.
    """
    if not any(0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        return text
    return ''.join(
        '\ufffd' if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in text
    )
