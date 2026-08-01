"""No agent instruction may tell an agent to set VIBE_TASK_ID.

The per-store wrapper owns the browser environment: it derives the
browser-use session (`<slug>-<first 8 hex>`) AND the CDP mux client id
from VIBE_TASK_ID, which the runtime exports before the agent starts.

Three shipped reference docs used to instruct agents to regenerate it
inline with every wrapper call. An agent following that landed each
command in a DIFFERENT, empty session — one live run made 204
browser-use calls and paid 34 re-navigations and 122 sleeps because no
page state carried over. It also leaked from the reviewer docs into the
main agent, which reads the same references.

A reviewer subagent does not need its own id: session and client id both
derive from the same variable, so inheriting it puts the subagent on the
same daemon as the parent, where calls serialize and the page persists.
"""

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

# Prose that would put the variable under agent control.
_ASSIGN_RE = re.compile(r'(?:export\s+)?VIBE_TASK_ID\s*=')


def _agent_facing_docs():
    """Everything shipped into an agent's context."""
    for base in (REPO / 'app' / 'skills_v2', REPO / 'app' / 'knowledge'):
        if base.is_dir():
            yield from base.rglob('*.md')


@pytest.mark.unit
class TestBrowserEnvOwnership:
    def test_no_shipped_skill_tells_an_agent_to_set_it(self):
        offenders = [
            str(p.relative_to(REPO))
            for p in _agent_facing_docs()
            if _ASSIGN_RE.search(p.read_text(encoding='utf-8'))
        ]
        assert not offenders, (
            'these agent-facing docs assign VIBE_TASK_ID; the wrapper owns '
            f'it and the runtime sets it: {offenders}'
        )

    def test_the_browser_prompt_says_not_to(self):
        src = (REPO / 'app' / 'task_runner_context.py').read_text(
            encoding='utf-8'
        )
        # Both the store and all-stores context blocks carry the rule.
        assert src.count('Do NOT set or regenerate `VIBE_TASK_ID`') == 2

    def test_the_wrapper_still_derives_session_and_client_from_it(self):
        """The rule only holds because the wrapper does the work."""
        src = (REPO / 'app' / 'browser' / 'wrapper.py').read_text(
            encoding='utf-8'
        )
        assert 'SESSION="{slug}-${{VIBE_TASK_ID:0:8}}"' in src
        assert 'CLIENT_ID="${{VIBE_TASK_ID:-' in src
