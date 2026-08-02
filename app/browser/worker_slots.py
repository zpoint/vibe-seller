"""Parallel browser worker slots — the `--worker N` wrapper contract.

An agent's subagents run **in the same process** as their parent (see
Claude Code's ``src/utils/agentContext.ts``: Agent-tool subagents
are isolated by ``AsyncLocalStorage``, not by process, precisely because
several can run concurrently). Nothing in Claude Code writes a per-agent
id into a child command's environment — ``CLAUDE_CODE_AGENT_ID`` is only
ever *read*, and only for separate-process swarm teammates. So every Bash
call in an agent tree, parent or subagent, inherits one identical
environment, including one ``VIBE_TASK_ID``.

The per-store wrapper derives BOTH identifiers from that variable::

    SESSION = '{slug}-${VIBE_TASK_ID:0:8}'  # which browser-use daemon
    CLIENT_ID = '${VIBE_TASK_ID}'  # which CDP mux client

Because they move together, a **sequential** subagent inheriting the id is
already correct: same daemon, calls serialise through one connection, and
the page the parent opened is still there. A **concurrent** subagent is
not. Same ``BU_NAME`` means the same daemon, and ``browser_harness``
serves each socket connection in its own asyncio task with no lock
anywhere in the package — two ``handle()`` coroutines interleave and both
drive one tab. The failure mode is silent wrong data, not a crash.

The fix cannot be "let the agent pick an id": an arbitrary id per call is
the churn that per-task sessions exist to prevent, and the browser
environment is infra's to own, not the agent's. Instead the wrapper hands
out a **fixed, validated set of slots**. The caller says only *which
worker it is*; the wrapper derives everything::

    browser-use --worker 2      -> BU_NAME   = {slug}-{task[:8]}-w2
                                   BU_CDP_WS = .../client-{task}-w2

Distinct ``BU_NAME`` → distinct daemon → distinct tab. Distinct client id
→ the CDP mux gives it its own targets (``_filter_targets`` hides other
clients', cross-client ``attachToTarget`` is refused), which is the same
mechanism that already lets two *tasks* share one store browser in
separate tabs. The mapping is bijective on purpose: a slot can never
produce a daemon and a mux client that disagree.

Slot *assignment* stays with the parent agent — it is the only party that
knows how many subagents it is about to launch. A parent that hands the
same slot to two subagents no longer corrupts data silently: they land on
one daemon and one mux client id, and
``cdp_mux_routing._handle_client_ws`` logs the collision at ERROR and
closes the older connection instead of overwriting it in place.
"""

from __future__ import annotations

import textwrap

from app.env_options import Options

# Slot names are `-w<N>`: short, unambiguous next to the `-aux` override,
# and (unlike a bare number) still a legal `BU_NAME` fragment under
# browser_harness's `[A-Za-z0-9_-]{1,64}` check.
WORKER_PREFIX = 'w'


def indent_block(block: str, spaces: int) -> str:
    """Re-indent a generated bash block for a wrapper template.

    Wrapper templates are ``textwrap.dedent``-ed *after* interpolation,
    so an inserted block must carry the same indentation as the lines
    around it — otherwise dedent finds a shorter common prefix and
    flattens the whole script. The template already supplies the first
    line's indentation (``{block}`` sits at column ``spaces``), so only
    continuation lines are padded, and the trailing newline is dropped
    because the template's own newline follows.
    """
    body = block.rstrip('\n')
    if not body:
        return ''
    return ('\n' + ' ' * spaces).join(body.split('\n'))


def max_worker_slots() -> int:
    """How many parallel slots this wrapper generation allows (0 = off)."""
    return max(0, Options.BROWSER_WORKER_SLOTS.get_int())


def worker_session_regex_fragment(slots: int) -> str:
    """Regex for the ``-w<N>`` tail of a session name, e.g. ``-w[1-3]``.

    Returns ``''`` when slots are disabled, so the caller's allowlist
    collapses back to the pre-v5 shape and ``--worker`` can never widen
    what a session name is permitted to be. The allowlist is the last
    line of defence behind the flag's own range check — a slot number
    that got past one still has to get past the other.
    """
    if slots <= 0:
        return ''
    if slots == 1:
        return f'-{WORKER_PREFIX}1'
    if slots < 10:
        return f'-{WORKER_PREFIX}[1-{slots}]'
    alts = '|'.join(str(i) for i in range(1, slots + 1))
    return f'-{WORKER_PREFIX}({alts})'


def worker_flag_arm(slots: int) -> str:
    """The ``--worker`` case-arm for a generated wrapper's arg loop.

    Validation is deliberately server-side (baked into the script we
    write) rather than prose in a skill: the agent supplies a slot
    *number* and nothing else, so "invent an arbitrary session id" stays
    unrepresentable. Rejects non-digits, leading zeros (``08`` would be
    read as invalid octal by ``[ -lt ]``), and out-of-range slots.
    """
    if slots <= 0:
        return textwrap.dedent("""\
            --worker|--worker=*)
              echo "ERROR: parallel worker slots are disabled on this server (VIBE_BROWSER_WORKER_SLOTS=0)" >&2
              exit 1
              ;;
        """)
    return textwrap.dedent(f"""\
        --worker|--worker=*)
          case "$1" in
            --worker) shift; _REQ_WORKER="${{1:-}}"; shift ;;
            *)        _REQ_WORKER="${{1#--worker=}}"; shift ;;
          esac
          case "$_REQ_WORKER" in
            ''|0*|*[!0-9]*)
              echo "ERROR: --worker takes a slot NUMBER 1..{slots} (got '$_REQ_WORKER')" >&2
              exit 1
              ;;
          esac
          if [ "$_REQ_WORKER" -gt {slots} ]; then
            echo "ERROR: --worker must be 1..{slots} (got '$_REQ_WORKER')" >&2
            exit 1
          fi
          _WORKER="$_REQ_WORKER"
          ;;
    """)


def worker_resolve_block(slots: int) -> str:
    """Post-arg-loop block that folds the slot into SESSION/CLIENT_ID.

    ``--worker`` and ``--session`` are mutually exclusive: ``--session``
    names a session outright, ``--worker`` asks the wrapper to derive
    one. Allowing both would let ``--session {slug}-aux --worker 2``
    graft a slot onto the aux browser, which is a different browser.
    """
    if slots <= 0:
        return ''
    return textwrap.dedent("""\
        if [ -n "$_WORKER" ]; then
          if [ "$_SESSION_GIVEN" = "1" ]; then
            echo "ERROR: --worker and --session are mutually exclusive" >&2
            exit 1
          fi
          SESSION="${SESSION}-w${_WORKER}"
          WORKER_SUFFIX="-w${_WORKER}"
        fi
    """)


def worker_init_block(slots: int) -> str:
    """Declare the slot variables the arg loop and env step reference."""
    init = '_WORKER=""\n_SESSION_GIVEN=0\nWORKER_SUFFIX=""\n'
    return init if slots > 0 else '_SESSION_GIVEN=0\nWORKER_SUFFIX=""\n'
