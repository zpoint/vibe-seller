"""Unit tests for app.ai.bash_safety.

Documents which commands the cross-task safety hook blocks vs allows.
The deny patterns exist because in production a pkill -f from one
fanout sub-task killed bash subprocesses in two sibling tasks (a real
incident).
"""

import pytest

from app.ai.bash_safety import (
    check_catalog_first,
    check_catalog_first_tool_args,
    check_dangerous_kill,
    check_report_overwrite,
    check_report_script_write,
    is_catalog_path,
    should_mark_catalog_read,
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

    def test_glob_exact_catalog_path_is_allowed(self):
        # Regression for the catalog-sync deadlock: the L2/L3
        # catalog-generation agents legitimately do
        # ``Glob(pattern='knowledge/project/CATALOG.md')`` to check
        # the L1 catalog exists before reading it. An exact path
        # with no wildcards isn't a broad sweep — must pass.
        assert (
            check_catalog_first_tool_args(
                {'pattern': 'knowledge/project/CATALOG.md'}, False
            )
            is None
        )
        assert (
            check_catalog_first_tool_args(
                {'pattern': 'stores/myslug/CATALOG.md'}, False
            )
            is None
        )

    def test_glob_wildcard_on_stores_still_denied(self):
        # Wildcard form is still a sweep — keep blocking.
        assert (
            check_catalog_first_tool_args(
                {'pattern': 'stores/myslug/*.md'}, False
            )
            is not None
        )
        assert (
            check_catalog_first_tool_args(
                {'pattern': 'stores/?slug/CATALOG.md'}, False
            )
            is not None
        )


class TestShouldMarkCatalogRead:
    """The PreToolUse hook flips ``_catalog_read = True`` once the
    agent has tried to consult a catalog. The predicate must accept
    *any* Read of a catalog-shaped path — even if the file doesn't
    exist on disk yet (fresh store, no L3 catalog generated). The
    older ``is_file()``-gated version trapped fresh-store tasks in
    a deny loop the moment the agent reached for a CATALOG.md that
    hadn't been generated, which then 400'd DeepSeek thinking mode
    on the next turn.
    """

    def test_read_existing_l3_catalog_flips_guard(self, tmp_path):
        store_dir = tmp_path / 'stores' / 'real-store'
        store_dir.mkdir(parents=True)
        catalog = store_dir / 'CATALOG.md'
        catalog.write_text('# real catalog\n')
        assert should_mark_catalog_read('Read', {'file_path': str(catalog)})

    def test_read_missing_l3_catalog_still_flips_guard(self, tmp_path):
        # The whole point: file may not exist yet on a fresh install.
        missing = tmp_path / 'stores' / 'fresh-store' / 'CATALOG.md'
        assert not missing.exists()
        assert should_mark_catalog_read('Read', {'file_path': str(missing)})

    def test_read_missing_l2_catalog_still_flips_guard(self, tmp_path):
        missing = tmp_path / 'knowledge' / 'CATALOG.md'
        assert not missing.exists()
        assert should_mark_catalog_read('Read', {'file_path': str(missing)})

    def test_read_non_catalog_path_does_not_flip(self):
        assert not should_mark_catalog_read(
            'Read', {'file_path': '/home/user/.vibe-seller/stores/x/notes.md'}
        )

    def test_non_read_tool_does_not_flip(self):
        # Even pointing at a CATALOG.md, only Read counts as
        # "tried to consult the catalog".
        assert not should_mark_catalog_read(
            'Bash', {'command': 'cat stores/x/CATALOG.md'}
        )
        assert not should_mark_catalog_read(
            'Edit', {'file_path': 'stores/x/CATALOG.md'}
        )

    def test_empty_input_does_not_flip(self):
        assert not should_mark_catalog_read('Read', {})
        assert not should_mark_catalog_read('Read', {'file_path': ''})


class TestReportScriptGuard:
    """The AD_AUDIT report is hand-authored via Edit. Twice an agent
    under turn pressure script-regenerated the whole report from
    TSVs, wiping sessions of hand analysis — the prose prohibition
    didn't hold, so the contract is enforced here."""

    def test_python_script_that_writes_report_blocked(self, tmp_path):
        script = tmp_path / 'build_report.py'
        script.write_text(
            'OUTPUT = "AD_AUDIT_2026-06-10.md"\nopen(OUTPUT, "w").write(body)\n'
        )
        deny = check_report_script_write('python3 build_report.py', tmp_path)
        assert deny is not None and 'Edit' in deny

    def test_renamed_script_still_blocked(self, tmp_path):
        # Guard reads script content, so renaming doesn't evade it.
        script = tmp_path / 'helper.py'
        script.write_text("p = 'AD_AUDIT_x.md'\nPath(p).write_text(data)\n")
        assert check_report_script_write('python3 helper.py', tmp_path)

    def test_readonly_analysis_script_allowed(self, tmp_path):
        script = tmp_path / 'analyze.py'
        script.write_text(
            "text = open('AD_AUDIT_2026-06-10.md').read()\nprint(len(text))\n"
        )
        assert check_report_script_write('python3 analyze.py', tmp_path) is None

    def test_inline_python_write_blocked(self):
        cmd = "python3 -c \"open('AD_AUDIT_2026-06-10.md', 'w').write(x)\""
        assert check_report_script_write(cmd) is not None

    def test_redirect_onto_report_blocked(self):
        assert check_report_script_write(
            'cat /tmp/built.md > AD_AUDIT_2026-06-10.md'
        )
        assert check_report_script_write(
            'echo "| row |" >> ./AD_AUDIT_2026-06-10.md'
        )

    def test_redirect_from_report_allowed(self):
        # Reading the report into a pipe/file is fine.
        assert (
            check_report_script_write(
                'grep 对账 AD_AUDIT_2026-06-10.md > /tmp/recon.txt'
            )
            is None
        )

    def test_cp_onto_report_blocked_but_previous_restore_allowed(self):
        assert check_report_script_write(
            'cp /tmp/generated.md AD_AUDIT_2026-06-10.md'
        )
        assert (
            check_report_script_write(
                'cp AD_AUDIT_PREVIOUS.md AD_AUDIT_2026-06-10.md'
            )
            is None
        )

    def test_sed_in_place_fix_allowed(self):
        # Targeted in-place batch fixes are tolerated by design.
        assert (
            check_report_script_write(
                "sed -i '' 's/维持或提高/提高出价/' AD_AUDIT_2026-06-10.md"
            )
            is None
        )

    def test_unrelated_commands_allowed(self, tmp_path):
        assert check_report_script_write('ls -la', tmp_path) is None
        assert check_report_script_write('', None) is None
        assert check_report_script_write('python3 missing.py', tmp_path) is None


class TestReportOverwriteGuard:
    """Write may not replace an existing AD_AUDIT report — the
    Write-tool hop around the report-script guard (script builds a
    'corrected' file in /tmp, agent Write-dumps it over the report)
    replaced 334KB of revisions with a stale 298KB base live."""

    def test_write_over_existing_report_denied(self, tmp_path):
        f = tmp_path / 'AD_AUDIT_2026-06-10.md'
        f.write_text('# report')
        deny = check_report_overwrite('Write', {'file_path': str(f)}, tmp_path)
        assert deny is not None and 'Edit' in deny

    def test_write_creates_new_report_allowed(self, tmp_path):
        # Scaffold creation: no file yet → Write is the right tool.
        assert (
            check_report_overwrite(
                'Write',
                {'file_path': str(tmp_path / 'AD_AUDIT_2026-06-12.md')},
                tmp_path,
            )
            is None
        )

    def test_relative_path_resolved_against_task_dir(self, tmp_path):
        (tmp_path / 'AD_AUDIT_2026-06-10.md').write_text('x')
        assert check_report_overwrite(
            'Write', {'file_path': 'AD_AUDIT_2026-06-10.md'}, tmp_path
        )

    def test_other_tools_and_files_ignored(self, tmp_path):
        (tmp_path / 'AD_AUDIT_2026-06-10.md').write_text('x')
        assert (
            check_report_overwrite(
                'Edit',
                {'file_path': str(tmp_path / 'AD_AUDIT_2026-06-10.md')},
                tmp_path,
            )
            is None
        )
        assert (
            check_report_overwrite(
                'Write', {'file_path': str(tmp_path / 'notes.md')}, tmp_path
            )
            is None
        )


class TestReportForkGuard:
    """One canonical report per task dir: with AD_AUDIT_A.md present,
    creating AD_AUDIT_B.md is the fork workaround (observed live:
    overwrite denied → agent created AD_AUDIT_<today>.md seeded from
    a stale temp and burned a session fixing the zombie)."""

    def test_fork_to_new_dated_report_denied(self, tmp_path):
        (tmp_path / 'AD_AUDIT_2026-06-10.md').write_text('# r')
        deny = check_report_overwrite(
            'Write',
            {'file_path': str(tmp_path / 'AD_AUDIT_2026-06-12.md')},
            tmp_path,
        )
        assert deny is not None and 'AD_AUDIT_2026-06-10.md' in deny

    def test_first_report_scaffold_still_allowed(self, tmp_path):
        assert (
            check_report_overwrite(
                'Write',
                {'file_path': str(tmp_path / 'AD_AUDIT_2026-06-12.md')},
                tmp_path,
            )
            is None
        )

    def test_fork_via_relative_path_denied(self, tmp_path):
        (tmp_path / 'AD_AUDIT_2026-06-10.md').write_text('# r')
        deny = check_report_overwrite(
            'Write', {'file_path': 'AD_AUDIT_2026-06-12.md'}, tmp_path
        )
        assert deny is not None
