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
from app.ai.tool_call_guards import tool_call_deny
from app.config import DATA_DIR

pytestmark = pytest.mark.unit

APP = sg.PROTECTED_DIRS[0]
GATE = APP / 'ai' / 'stop_gates' / 'ad_changes.py'


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

    @pytest.mark.parametrize(
        ('tool', 'tool_input'),
        [
            (
                'Bash',
                {'command': f'sqlite3 {DATA_DIR}/vibe_seller.db .tables'},
            ),
            (
                'Bash',
                {'command': f'cd /tmp && sqlite3 -json {DATA_DIR / "x.db"}'},
            ),
            ('Bash', {'command': f'ls -la {DATA_DIR}/ 2>/dev/null'}),
            ('Read', {'file_path': str(DATA_DIR / 'email_dbs' / 'a.db')}),
            (
                'Write',
                {'file_path': str(DATA_DIR / 'approved_changes' / 'x')},
            ),
        ],
    )
    def test_the_apps_own_data_is_refused(self, tool, tool_input):
        """Observed: a planner spent six minutes in the app DB and the
        mailbox DBs instead of planning."""
        deny = sg.source_access_deny(tool, tool_input)
        assert deny and "vibe-seller's own data" in deny

    def test_checkout_tests_are_protected_when_present(self):
        tests_dir = APP.parent / 'tests'
        if tests_dir not in sg.PROTECTED_DIRS:
            pytest.skip('not running from a source checkout')
        cmd = f'cat {tests_dir}/e2e/fake_ads_console.py'
        assert sg.source_access_deny('Bash', {'command': cmd})

    def test_the_checkout_root_is_protected_when_present(self):
        """Observed in CI: an agent read ``GITHUB_WORKSPACE`` from its
        environment, ran ``cd <checkout> && sed -n … app/ai/stop_gates/…``
        — a RELATIVE path the package-dir match never saw — and spent ten
        minutes in the gate modules until the task timed out."""
        root = APP.parent
        if root / 'tests' not in sg.PROTECTED_DIRS:
            pytest.skip('not running from a source checkout')
        for cmd in (
            f'cd {root} && sed -n 1,200p app/ai/stop_gates/ad_changes.py',
            f'cd {root} 2>/dev/null && grep -rn acos_no_lower -l',
            f'grep -rn acos_no_lower {root}',
        ):
            assert sg.source_access_deny('Bash', {'command': cmd}), cmd
        assert sg.source_access_deny(
            'Read', {'file_path': str(root / 'README.md')}
        )

    def test_the_package_match_ends_at_a_path_boundary(self):
        """``…/app`` is protected, ``…/apple`` is not a match for it (from
        a source checkout the root rule covers the latter separately)."""
        assert sg._PROTECTED_IN_CMD.search(f'ls {APP}le') is None
        assert sg._PROTECTED_IN_CMD.search(f'ls {APP}/x')

    def test_the_checkout_venv_stays_usable(self):
        """Agents may run the app's interpreter by its absolute path."""
        venv = APP.parent / '.venv' / 'bin' / 'python'
        cmd = f'{venv} -I - <<PY\nprint(1)\nPY'
        assert sg.source_access_deny('Bash', {'command': cmd}) is None

    @pytest.mark.parametrize(
        ('tool', 'tool_input'),
        [
            ('Read', {'file_path': '/tmp/task/report_x.md'}),
            ('Read', {'file_path': 'stores/acme/notes.md'}),  # relative
            ('Bash', {'command': 'ls ~/.vibe-seller/tasks'}),
            # A sibling of the CHECKOUT, not inside it.
            ('Bash', {'command': f'ls {APP.parent}-other/x'}),
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


class TestTheTasksOwnMailboxes:
    """A task reads its own mailboxes' databases (the db_path values
    ``vibe_seller_email_info`` gives it); every other file in the data
    dir, other mailboxes' included, stays refused."""

    OWN = DATA_DIR / 'email_dbs' / 'email_own.db'
    OWN_FILES = (str(OWN), str(OWN.parent / 'email_own_attachments'))
    OTHER = DATA_DIR / 'email_dbs' / 'email_other.db'

    @pytest.mark.parametrize(
        ('tool', 'tool_input'),
        [
            (
                'Bash',
                {'command': f'sqlite3 "{OWN}" "SELECT subject FROM emails"'},
            ),
            ('Bash', {'command': f"sqlite3 -json '{OWN}' .schema"}),
            (
                'Bash',
                {'command': 'sqlite3 ~' + str(OWN)[len(str(Path.home())) :]},
            ),
            ('Read', {'file_path': str(OWN)}),
            (
                'Read',
                {
                    'file_path': str(
                        OWN.parent / 'email_own_attachments' / 'a.pdf'
                    )
                },
            ),
        ],
    )
    def test_its_own_mailbox_reads(self, tool, tool_input):
        assert sg.source_access_deny(tool, tool_input, self.OWN_FILES) is None

    @pytest.mark.parametrize(
        ('tool', 'tool_input'),
        [
            ('Bash', {'command': f'sqlite3 "{OTHER}" .tables'}),
            ('Read', {'file_path': str(OTHER)}),
            ('Bash', {'command': f'sqlite3 {OWN} .tables; ls {DATA_DIR}'}),
            ('Bash', {'command': f'sqlite3 {OWN}/../email_other.db .tables'}),
            ('Read', {'file_path': f'{OWN}/../email_other.db'}),
        ],
    )
    def test_everything_else_in_the_data_dir_stays_refused(
        self, tool, tool_input
    ):
        deny = sg.source_access_deny(tool, tool_input, self.OWN_FILES)
        assert deny and "vibe-seller's own data" in deny

    def test_the_hooks_check_takes_them(self):
        s = AgentSession(task_id='test-task', prompt='t', mode='auto')
        call = {
            'tool_name': 'Read',
            'tool_input': {'file_path': str(self.OWN)},
        }
        assert tool_call_deny(s, call)  # not given: refused
        assert tool_call_deny(s, call, self.OWN_FILES) is None
