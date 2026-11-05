"""Drift guard: ``references/tools.md`` documents the served toolset.

The packaged skill is the external agent's manual. If the server
serves a tool the manual does not describe, the agent either never
calls it or calls it wrong; if the manual documents an argument the
server rejects, the agent's call is refused with "unknown argument".
Neither is discoverable from the other side, so this test compares
them:

* every tool in ``tool_definitions()`` has a section in tools.md;
* every ARGUMENT of every tool is named in that tool's section;
* tools.md has no section for a tool that no longer exists.

The legacy argument aliases (``action`` on ``review_plan``,
``content`` on ``send_message``) used to be exempt here while the
server still advertised them. They are gone from the served schemas
now, so the exemption is gone with them and the guard is strict: an
argument the manual names but the server does not serve is a drift
too (see ``test_no_argument_is_documented_that_the_server_does_not_serve``).
"""

from pathlib import Path
import re

import pytest

from app.agent_mcp.registry import tool_definitions

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_MD = REPO_ROOT / 'agent/skills/vibe-seller/references/tools.md'


def _sections(text: str) -> dict[str, str]:
    """{tool name: the text under its '## name' heading}."""
    out: dict[str, str] = {}
    matches = list(re.finditer(r'^## (\w+)\s*$', text, re.MULTILINE))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out[m.group(1)] = text[m.start() : end]
    return out


def _mentions(section: str, name: str) -> bool:
    """Is *name* named in this section?"""
    return re.search(rf'`{re.escape(name)}\b', section) is not None


def _table_arguments(section: str) -> set[str]:
    """The argument names in this section's argument TABLE.

    The table is ``| `name` | type | notes |``; the backticked names
    elsewhere in the section are prose (return shapes, examples), so
    only the table is read here.
    """
    return {
        match.group(1)
        for match in (
            re.match(r'^\|\s*`([A-Za-z0-9_]+)`\s*\|', line)
            for line in section.splitlines()
        )
        if match
    }


@pytest.fixture(scope='module')
def sections() -> dict[str, str]:
    return _sections(TOOLS_MD.read_text(encoding='utf-8'))


def test_every_tool_is_documented(sections):
    missing = sorted(
        definition['name']
        for definition in tool_definitions()
        if definition['name'] not in sections
    )
    assert not missing, f'tools.md does not document: {missing}'


def test_no_orphan_sections(sections):
    served = {definition['name'] for definition in tool_definitions()}
    # 'Status vocabulary' is prose, not a tool.
    orphans = sorted(
        name
        for name in sections
        if name != 'Status vocabulary' and name not in served
    )
    assert not orphans, f'tools.md documents tools that do not exist: {orphans}'


def test_no_tool_is_documented_twice():
    """A tool documented twice teaches two shapes.

    ``_sections`` keeps the LAST section under a name, so a duplicated
    heading hides the first copy from every other check in this file --
    and an agent reading the manual would see whichever copy came
    first.
    """
    names = re.findall(
        r'^## (\w+)\s*$',
        TOOLS_MD.read_text(encoding='utf-8'),
        re.MULTILINE,
    )
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert not duplicates, f'tools.md documents these tools twice: {duplicates}'


def test_every_argument_is_documented(sections):
    undocumented: list[str] = []
    for definition in tool_definitions():
        name = definition['name']
        section = sections.get(name)
        if section is None:
            continue  # reported by test_every_tool_is_documented
        properties = definition['inputSchema'].get('properties', {})
        for argument in properties:
            if not _mentions(section, argument):
                undocumented.append(f'{name}.{argument}')
    assert not undocumented, (
        f'arguments the manual does not document: {sorted(undocumented)}'
    )


def test_required_arguments_are_documented(sections):
    """Required arguments are the ones a call cannot do without."""
    undocumented: list[str] = []
    for definition in tool_definitions():
        name = definition['name']
        section = sections.get(name)
        if section is None:
            continue
        for argument in definition['inputSchema'].get('required', []):
            if not _mentions(section, argument):
                undocumented.append(f'{name}.{argument}')
    assert not undocumented, (
        f'required arguments the manual does not document: '
        f'{sorted(undocumented)}'
    )


def test_no_argument_is_documented_that_the_server_does_not_serve(sections):
    """The reverse direction: the manual must not name an argument the
    server would reject with "unknown argument".

    ``test_every_argument_is_documented`` catches a served argument the
    manual forgot; this catches the opposite — a manual that promises
    an argument the server never had. An agent following the manual
    would send it and get refused, so the drift is only visible from
    one side unless both directions are checked.
    """
    undocumented: list[str] = []
    for definition in tool_definitions():
        name = definition['name']
        section = sections.get(name)
        if section is None:
            continue  # reported by test_every_tool_is_documented
        served = set(definition['inputSchema'].get('properties', {}))
        for argument in _table_arguments(section):
            if argument not in served:
                undocumented.append(f'{name}.{argument}')
    assert not undocumented, (
        f'arguments the manual documents but the server does not serve: '
        f'{sorted(undocumented)}'
    )


def test_schedule_lifecycle_wording_is_present():
    """The manual must say the thing that makes an agent lie-safe:
    a schedule that cannot fire is not "scheduled"."""
    # Markdown wraps the prose, so match on whitespace-normalised
    # text — a phrase split across a line break is still a phrase.
    text = re.sub(r'\s+', ' ', TOOLS_MD.read_text(encoding='utf-8'))
    assert 'fires' in text
    assert 'never fires' in text
    assert 'ready' in text
    assert 'stale' in text
