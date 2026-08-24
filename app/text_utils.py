"""Surrogate-safe text helpers for anything we persist.

Agent-supplied free text occasionally carries lone surrogate code
points (U+D800-U+DFFF). Claude's ``stream-json`` can emit an unpaired
surrogate for certain CJK/emoji sequences, and JSON escapes them
losslessly (``"\\udcad"`` is legal JSON), so ``json.loads`` — in the
stream reader and in FastAPI's request parsing alike — hands us a
``str`` that Python cannot encode:

    UnicodeEncodeError: 'utf-8' codec can't encode character '\\udcad'
    in position 42: surrogates not allowed

Every persistence boundary encodes to UTF-8, so every one of them is a
crash site: a ``db.commit()`` binding a TEXT column, a
``Path.write_text(..., encoding='utf-8')`` for a workspace file. The
agent sees an opaque tool error and usually retries with the same text.

The guard therefore lives AT the encode, not at its callers:

- ``SafeText`` — a column type. Any commit through any code path
  is safe, so a new writer of ``Task.result`` cannot reintroduce the
  crash.
- ``sanitize_text`` — called inside the workspace/skills managers,
  which own the file writes.

Sanitizing at routers instead leaves every sibling endpoint exposed
(``vibe_seller_set_task_error``, ``vibe_seller_save_skill``,
``vibe_seller_set_schedule_state``), which is how this bug was first
found. A lone surrogate is not valid Unicode and carries no
information, so replacing it with U+FFFD loses nothing.
"""

from typing import Annotated

from pydantic import BeforeValidator
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

_SURROGATE_LOW = 0xD800
_SURROGATE_HIGH = 0xDFFF


def sanitize_text(text: str) -> str:
    """Replace lone surrogates with U+FFFD so the text can be encoded.

    Returns the input unchanged (same object) when it holds no
    surrogates, which is the overwhelmingly common case.
    """
    if not any(_SURROGATE_LOW <= ord(ch) <= _SURROGATE_HIGH for ch in text):
        return text
    return ''.join(
        '�' if _SURROGATE_LOW <= ord(ch) <= _SURROGATE_HIGH else ch
        for ch in text
    )


class SafeText(TypeDecorator):
    """``Text`` that cannot carry a lone surrogate into the DB driver.

    Same DDL as ``Text`` (no migration needed); the only difference is
    that bound values are sanitized on the way in. Use it for every
    column that stores agent- or user-authored prose.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if isinstance(value, str):
            return sanitize_text(value)
        return value


def _coerce_safe(value):
    """Sanitize a str; leave anything else for pydantic to reject."""
    if isinstance(value, str):
        return sanitize_text(value)
    return value


# Use for every request field that carries agent- or user-authored text.
#
# Persistence is not the only consumer: the result gates hand the text to
# a language detector (a Rust extension) that encodes it to UTF-8, so a
# surrogate crashed the submission BEFORE any write. Cleaning at the
# request boundary means nothing downstream — gate, file write, DB bind,
# SMTP — can be handed a str Python cannot encode.
SANITIZE_SURROGATES = BeforeValidator(_coerce_safe)
SafeStr = Annotated[str, SANITIZE_SURROGATES]

# Compose it around an ALREADY-constrained type, never inside one:
# ``Annotated[SafeStr, StringConstraints(...)]`` silently discards
# the constraint, whereas
# ``Annotated[Annotated[str, StringConstraints(...)],
# SANITIZE_SURROGATES]`` keeps both.
