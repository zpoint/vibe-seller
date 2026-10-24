"""Task agents may not read vibe-seller's own source.

Observed in CI: a planning agent found the server's source checkout via
``/proc/<pid>/cmdline``, opened the e2e test that mirrored its task, then
spent seven minutes in the gate modules working out what they accept --
and missed its deadline. The prompt already told it not to; this is the
line in code.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.ai import source_guard as sg
from app.ai.claude_backend import AgentSession
from app.ai.claude_backend_utils import AUTO_APPROVE_CALLBACK

pytestmark = pytest.mark.unit

APP = sg.PROTECTED_DIRS[0]
GATE = APP / 'ai' / 'stop_gates' / 'report_reviewer.py'


class TestSourceAccessDeny:
    @pytest.mark.parametrize(
        ('tool', 'tool_input'),
        [
            ('Read', {'file_path': str(GATE)}),
            ('Grep', {'pattern': 'Status', 'path': str(APP)}),
            ('Glob', {'pattern': '*.py', 'path': str(APP / 'ai')}),
            ('Bash', {'command': f'sed -n 1,120p {GATE}'}),
            ('Bash', {'command': f'cd {APP} && grep -rn gate .'}),
            ('Bash', {'command': f"grep -rl 'Ad Console' {APP}"}),
        ],
    )
    def test_reaching_into_the_package_is_refused(self, tool, tool_input):
        deny = sg.source_access_deny(tool, tool_input)
        assert deny and 'source code' in deny

    def test_checkout_tests_are_protected_when_present(self):
        tests_dir = APP.parent / 'tests'
        if tests_dir not in sg.PROTECTED_DIRS:
            pytest.skip('not running from a source checkout')
        cmd = f'cat {tests_dir}/e2e/fake_ads_console.py'
        assert sg.source_access_deny('Bash', {'command': cmd})

    @pytest.mark.parametrize(
        ('tool', 'tool_input'),
        [
            ('Read', {'file_path': '/tmp/task/REVIEW_x_iter1.md'}),
            ('Read', {'file_path': 'stores/acme/notes.md'}),  # relative
            ('Bash', {'command': 'ls ~/.vibe-seller/tasks'}),
            ('Bash', {'command': f'ls {APP}le'}),  # a sibling, not app/
            ('Grep', {'pattern': 'x'}),  # no path: cwd
            ('WebFetch', {'url': str(APP)}),
            ('Bash', 'not-a-dict'),
        ],
    )
    def test_everything_else_passes(self, tool, tool_input):
        assert sg.source_access_deny(tool, tool_input) is None

    def test_a_symlink_into_the_package_reads_normally(self, tmp_path):
        """Compared as written, not resolved: a task-dir skill copy that
        links into the package must keep working."""
        link = tmp_path / 'skill.md'
        link.symlink_to(Path(sg.__file__))
        assert sg.source_access_deny('Read', {'file_path': str(link)}) is None


def test_hook_refuses_it():
    s = AgentSession(task_id='test-task', prompt='t', mode='auto')
    sent = []
    s._send_hook_response = AsyncMock(
        side_effect=lambda rid, out: sent.append(out)
    )
    request = {
        'callback_id': AUTO_APPROVE_CALLBACK,
        'input': {'tool_name': 'Read', 'tool_input': {'file_path': str(GATE)}},
    }
    asyncio.run(s._handle_hook_callback('req', request))
    out = sent[0]['hookSpecificOutput']
    assert out['permissionDecision'] == 'deny'
    assert 'source code' in out['permissionDecisionReason']
