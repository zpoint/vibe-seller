"""Every MCP tool a skill doc or system prompt names must actually exist.

The docs tell an agent to call ``vibe_seller_<name>``. When that name is
not registered by the in-task MCP server, the call is a dead end — the
agent burns a turn on a tool it cannot have, and learns that this repo's
docs are not trustworthy. Same class of defect as a prompt threatening a
check the server does not perform: prose that describes machinery which
is not there.

The registered set is DERIVED, never listed here:

* advertised — ``app.mcp_tool_schemas.TOOLS``, the list the MCP process
  returns from ``tools/list`` (via ``mcp_server._visible_tools()``, which
  only ever *subtracts* capability-gated tools from it);
* dispatched — the ``name == 'vibe_seller_…'`` branches in
  ``mcp_server.handle_tool_call``, i.e. the tools the server can actually
  execute.

So a tool added on another branch needs no edit in this file — and a doc
that names a tool nobody ships fails here instead of in front of an agent.
"""

from pathlib import Path
import re

import pytest

from app.mcp_tool_schemas import TOOLS

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
# Skill docs (the current release line only — ``app/skills`` is the frozen
# legacy tree for older clients) and the system prompts the server injects.
_DOC_GLOBS = ('app/skills_v2/**/*.md', 'app/prompts/*.md')
_MCP_SERVER = _ROOT / 'app/mcp_server.py'

# A tool name as the docs write it. ``vibe_seller_`` is the whole
# namespace: every tool the stdio server exposes starts with it, so a bare
# ``create_task`` in prose is not a claim about an MCP tool.
_TOOL_RE = re.compile(r'vibe_seller_[A-Za-z0-9_]+')
# The dispatch chain inside the MCP process: the branch naming a tool it
# can actually execute.
_DISPATCH_RE = re.compile(r"""name == '(vibe_seller_[A-Za-z0-9_]+)'""")


def _doc_files() -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in _DOC_GLOBS:
        for path in _ROOT.glob(pattern):
            if path.is_file():
                seen[path] = None
    return sorted(seen)


def _advertised() -> set[str]:
    return {t['name'] for t in TOOLS}


def _dispatched() -> set[str]:
    return set(_DISPATCH_RE.findall(_MCP_SERVER.read_text(encoding='utf-8')))


def _mentions(path: Path) -> set[str]:
    return set(_TOOL_RE.findall(path.read_text(encoding='utf-8')))


def test_the_scan_finds_something():
    """A glob that stopped matching would make every check below vacuous."""
    docs = _doc_files()
    assert len(docs) > 20, f'only {len(docs)} docs scanned — check _DOC_GLOBS'
    assert sum(len(_mentions(p)) for p in docs) > 0
    assert len(_advertised()) > 10, 'the tool list came up near-empty'


def test_advertised_and_dispatched_are_the_same_tools():
    """One tool list, two halves: nothing advertised that would error,
    nothing executable that no agent can discover."""
    advertised, dispatched = _advertised(), _dispatched()
    assert advertised - dispatched == set(), (
        f'{sorted(advertised - dispatched)} are in tools/list but '
        'handle_tool_call has no branch for them'
    )
    assert dispatched - advertised == set(), (
        f'{sorted(dispatched - advertised)} are executable but missing '
        'from app/mcp_tool_schemas.TOOLS'
    )


def test_every_registered_tool_name_is_prefixed():
    """The scan keys on the prefix; a tool outside it would be invisible
    to this file's checks."""
    odd = [n for n in _advertised() if not n.startswith('vibe_seller_')]
    assert odd == [], f'tool names outside the scanned namespace: {odd}'


def test_no_doc_names_a_tool_the_server_does_not_have():
    """The headline check, reported as one list so a fix sees all of it."""
    registered = _advertised() & _dispatched()
    broken = {
        str(p.relative_to(_ROOT)): sorted(_mentions(p) - registered)
        for p in _doc_files()
        if _mentions(p) - registered
    }
    assert broken == {}, (
        'docs name MCP tools the in-task server does not register '
        f'(remove the claim, or ship the tool): {broken}'
    )
