"""Unit tests for app.ai.bash_safety.

Documents which commands the cross-task safety hook blocks vs allows.
The deny patterns exist because in production a pkill -f from one
fanout sub-task killed bash subprocesses in two sibling tasks (real
incident on 2026-05-07).
"""

import pytest

from app.ai.bash_safety import (
    check_catalog_first,
    check_catalog_first_tool_args,
    check_dangerous_kill,
    is_catalog_path,
)

pytestmark = pytest.mark.unit


class TestUnsafeCommandsAreBlocked:
    """Commands that match processes outside the calling task's tree."""

    @pytest.mark.parametrize(
        'command',
        [
            # The exact incident: cleanup of an `until browser-use` poll.
            'pkill -f "until browser-use"',
            'pkill -f "until browser-use" 2>&1 ; pkill -f "browser-use eval"',
            # Single-arg pkill <name> — kills every process named foo.
            'pkill chrome',
            'pkill -9 firefox',
            # Combined flags still reach -f.
            'pkill -fF "pattern"',
            # killall <name>.
            'killall node',
            'killall -9 chrome',
            # Embedded inside a longer pipeline.
            'echo done && pkill -f browser-use',
            # Command substitution / subshell — runs in the same
            # process namespace, so still cross-task hazardous.
            'output=$(pkill -f browser-use)',
            '(pkill chrome) &',
        ],
    )
    def test_unscoped_pkill_or_killall_is_denied(self, command):
        reason = check_dangerous_kill(command)
        assert reason is not None, f'expected deny: {command!r}'
        assert 'Blocked' in reason
        assert 'pkill -P $$' in reason  # alternative is suggested


class TestScopedKillsAreAllowed:
    """Scoped variants don't match cross-task processes."""

    @pytest.mark.parametrize(
        'command',
        [
            # -P scopes to children of a specific PID.
            'pkill -P $$ -f "browser-use"',
            'pkill -P 12345 chrome',
            'pkill --parent $$ "node"',
            # Killing a specific PID is safe — agent already chose.
            'kill 12345',
            'kill -9 $!',
            'kill -TERM $BG_PID',
            # Reading state — not a kill at all.
            'pgrep -f browser-use',
            'pgrep -P $$ chrome',
            # Strings that contain "kill" inside larger words.
            'echo "killer queen"',
            'ls /var/log/skill_cli',
            # `pkill` / `killall` as an argument or inside a quoted
            # string — the agent isn't running them, just naming
            # them. Denying these would block legitimate debugging.
            'grep pkill /tmp/debug.log',
            'echo "pkill -f browser-use" >> /tmp/notes.md',
            'cat /var/log/killall.log',
            'man pkill',
            'which pkill killall',
            # Word boundaries that previously matched (regression
            # check for the command-position tightening).
            'history | grep pkill',
        ],
    )
    def test_scoped_or_unrelated_command_is_allowed(self, command):
        assert check_dangerous_kill(command) is None, (
            f'expected allow: {command!r}'
        )


class TestEdgeCases:
    """Empty input, malformed input, and patterns near separators."""

    def test_empty_string_is_allowed(self):
        assert check_dangerous_kill('') is None

    def test_combined_pkill_then_scoped_pkill_blocks_first(self):
        # First invocation is unscoped → reject the whole command.
        # The agent must rewrite both halves, even though the second
        # one is fine.
        cmd = 'pkill -f "old-loop" ; pkill -P $$ -f "other"'
        assert check_dangerous_kill(cmd) is not None

    def test_scope_flag_must_apply_to_each_pkill(self):
        # `-P $$` here only applies to the second pkill; the first
        # one still kills cross-task.
        cmd = 'pkill -f "x" || pkill -P $$ "y"'
        assert check_dangerous_kill(cmd) is not None


class TestCatalogFirstGuard:
    """Block filesystem search of knowledge/ or stores/ until the
    agent reads any CATALOG.md.

    The pattern is the same as Claude Code's Read-before-Write rule:
    one deny + clear retry hint educates the model in-context, and
    once the catalog has been read the guard turns off for the
    remainder of the session.
    """

    def test_find_on_stores_before_catalog_is_denied(self):
        cmd = 'find /home/runner/.vibe-seller/stores/abc/ -name "*"'
        deny = check_catalog_first(cmd, catalog_read=False)
        assert deny is not None
        assert 'catalog' in deny.lower()

    def test_ls_on_knowledge_before_catalog_is_denied(self):
        cmd = 'ls -la ~/.vibe-seller/knowledge/'
        assert check_catalog_first(cmd, catalog_read=False) is not None

    def test_grep_on_stores_workspace_relative_is_denied(self):
        # Workspace-relative path (no leading /) is the common form
        # an agent will use inside the task cwd.
        cmd = 'grep -r SECRET stores/myslug/'
        assert check_catalog_first(cmd, catalog_read=False) is not None

    def test_after_catalog_read_the_guard_is_off(self):
        cmd = 'find /home/runner/.vibe-seller/stores/abc/ -name "*"'
        assert check_catalog_first(cmd, catalog_read=True) is None

    def test_search_outside_catalog_tree_is_allowed(self):
        # Searching /tmp or arbitrary files has nothing to do with
        # the catalog contract — must not be blocked.
        assert check_catalog_first('ls /tmp/', catalog_read=False) is None
        assert (
            check_catalog_first('grep pattern file.txt', catalog_read=False)
            is None
        )

    def test_non_search_command_against_stores_is_allowed(self):
        # Reading a single file by `cat` isn't a directory search.
        # The guard only fires on find/ls/grep/rg/fd/tree.
        assert (
            check_catalog_first(
                'cat stores/myslug/notes.md', catalog_read=False
            )
            is None
        )

    def test_quoted_search_keyword_in_echo_is_allowed(self):
        # ``ls`` inside a quoted string isn't in command position;
        # false-positive guard.
        assert (
            check_catalog_first('echo "ls stores"', catalog_read=False) is None
        )

    def test_empty_command_is_allowed(self):
        assert check_catalog_first('', catalog_read=False) is None


class TestIsCatalogPath:
    """Identifies any-level CATALOG.md path so the hook can flip
    the catalog-first guard off once the agent reads one."""

    def test_l1_catalog(self):
        assert is_catalog_path(
            '/home/runner/.vibe-seller/knowledge/project/CATALOG.md'
        )

    def test_l2_catalog(self):
        assert is_catalog_path('/home/runner/.vibe-seller/knowledge/CATALOG.md')

    def test_l3_store_catalog(self):
        assert is_catalog_path(
            '/home/runner/.vibe-seller/stores/myslug/CATALOG.md'
        )

    def test_non_catalog_md_is_not_a_catalog(self):
        assert not is_catalog_path(
            '/home/runner/.vibe-seller/stores/myslug/notes.md'
        )

    def test_unrelated_path_is_not_a_catalog(self):
        assert not is_catalog_path('/tmp/CATALOG.md')

    def test_empty_path_is_not_a_catalog(self):
        assert not is_catalog_path('')


class TestCatalogFirstToolArgs:
    """Same catalog-first contract, applied to the Claude Code
    built-in Glob/Grep tools. Without this guard, an agent denied
    at the Bash layer pivots to Glob/Grep on the same trees and
    gets the broad sweep through a different tool.
    """

    def test_glob_pattern_on_stores_is_denied(self):
        inp = {'pattern': 'stores/cat-store-x-1234/**/*'}
        assert check_catalog_first_tool_args(inp, False) is not None

    def test_glob_pattern_on_knowledge_is_denied(self):
        inp = {'pattern': 'knowledge/**/*.md'}
        assert check_catalog_first_tool_args(inp, False) is not None

    def test_grep_path_on_stores_is_denied(self):
        inp = {'pattern': 'SECRET', 'path': 'stores/myslug'}
        assert check_catalog_first_tool_args(inp, False) is not None

    def test_after_catalog_read_glob_is_allowed(self):
        inp = {'pattern': 'stores/x/**/*'}
        assert check_catalog_first_tool_args(inp, True) is None

    def test_glob_outside_catalog_tree_is_allowed(self):
        inp = {'pattern': 'src/**/*.py'}
        assert check_catalog_first_tool_args(inp, False) is None

    def test_glob_with_no_path_or_pattern_is_allowed(self):
        # Defensive — if the tool input is missing the relevant
        # fields entirely, we don't fire (lets unrelated tool calls
        # with the same callback path through).
        assert check_catalog_first_tool_args({}, False) is None
