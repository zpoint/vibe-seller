"""Unit tests for app.ai.bash_safety.

Documents which commands the cross-task safety hook blocks vs allows.
The deny patterns exist because in production a pkill -f from one
fanout sub-task killed bash subprocesses in two sibling tasks (real
incident on 2026-05-07).
"""

import pytest

from app.ai.bash_safety import check_dangerous_kill

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
