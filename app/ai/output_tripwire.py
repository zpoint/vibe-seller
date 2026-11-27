"""The server-side tripwire on tool OUTPUT — paths only.

The pre-tool guard (:mod:`app.ai.vendor_guard`) denies the obvious
spellings; this is the second net. The server already reads every tool
result for the timeline, so it also checks each one for what must never
be there: a vendor's declared private path, or a browser-profile
database path, in any of its spellings. A hit STOPS the task — no
redaction games, no partial credit — because output that reached the
model is already out of our control.

MATCH SCOPE (user decision, 2026-11-27 — binding): PATHS ONLY. No key
or token formats, no content signatures (no hex shapes, no SQLite
header sniff, no ``moz_*`` table names): email bodies, CLI output and
page text contain arbitrary strings, and a false trip kills a
legitimate task. The accepted RESIDUAL, stated plainly: output that
carries a secret WITHOUT any path (e.g. a bare ``grep -rh`` over a
vendor tree) is not caught here — the pre-tool deny on the command
text is the layer for that, and the guard denies the vendor paths the
grep would name.

Per D-C3-1, profile-database names match only as path segments — a
cookie banner, an Amazon page dump saying "cookies" and a
``cookies-report.csv`` deliverable must not trip.

Incident 3 added the vibe-seller SERVER-STATE paths (the vendors tree,
``profiles.json``, the data dir minus the task's own ``email_dbs``):
a result printing one means the agent walked where the pre-tool guard
refuses, and the output is the detection. The guard's own DENY
messages never trip it — the backend skips results whose tool call it
refused (a reason names a path; it never carries file contents).

Limits, same as the guard: the agent runs as the same OS user, so a
determined agent can encode a leak past any string matcher. The
tripwire makes the OBSERVED leaks loud, not every conceivable one.
"""

from __future__ import annotations

import re

from app.ai.agent_lateral_guard import norm_home_now, normalize_path_text
from app.ai.vendor_guard import (
    _PROFILE_DB_SEGMENT_RE,
    dir_name_hits,
    vendor_matcher,
)

TRIP_REASON = (
    'A tool result contained {kind}: {detail}. This task is stopped — '
    'browser-client private data is never part of a task. If you '
    'believe this is a mistake, report it to the user.'
)


# Incident 3's backstop: a result that PRINTS a vibe-seller server-state
# path (the vendors tree, profiles.json, the data dir) means the agent
# walked somewhere the pre-tool guard refuses — the output is the
# detection. The task's own mailbox databases under ``data/email_dbs``
# are exempt: ``vibe_seller_email_info`` hands exactly those paths to
# the agent, so they are normal output, never a leak. ``bin`` is not
# matched: wrapper paths appear in ordinary browser tooling output.
def _home_leak_re(home: str) -> re.Pattern:
    """Built from the ONE normalised home; text is normalised too."""
    return re.compile(
        re.escape(home)
        + r'/(?:vendors|profiles\.json|data(?!/email_dbs))(?=$|[/\s\'";|&)])'
    )


def _scan_paths(text: str) -> str | None:
    """A declared vendor private path or profile-DB path in *text*."""
    hits = dir_name_hits(text)
    if hits:
        return f'{hits[0][1]!r} ({hits[0][0]} private path)'
    lowered = text.lower()
    for _key, regex in vendor_matcher().templates:
        mm = regex.search(lowered)
        if mm:
            return f'{mm.group(0)!r}'
    m = _PROFILE_DB_SEGMENT_RE.search(text)
    if m:
        return f'{m.group(0)!r} (browser profile database)'
    m = _home_leak_re(norm_home_now()).search(normalize_path_text(text))
    if m:
        return f'{m.group(0)!r} (vibe-seller server state)'
    return None


def scan_tool_result(text: str) -> str | None:
    """The trip reason for a tool result, else ``None``.

    The reason names WHAT matched and the path — there are no key
    values to hide, because key shapes are not matched at all.
    """
    if not text:
        return None
    path = _scan_paths(text)
    if path:
        return TRIP_REASON.format(kind='a private path', detail=path)
    return None
