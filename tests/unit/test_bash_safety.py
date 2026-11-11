"""Unit tests for app.ai.bash_safety.

Documents which commands the cross-task safety hook blocks vs allows.
The deny patterns exist because in production a pkill -f from one
fanout sub-task killed bash subprocesses in two sibling tasks (a real
incident).
"""

import pytest

from app.ai.bash_safety import (
    check_dangerous_kill,
    check_skill_file_write,
    first_bash_deny,
)
from app.ai.catalog_read import is_catalog_path, should_mark_catalog_read
from app.ai.image_guards import (
    check_generated_image_write,
    check_local_image_edit,
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


class TestIsCatalogPath:
    """Identifies any-level CATALOG.md path so the hook can flip
    the catalog-read state once the agent reads one."""

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

    def test_a_write_tool_does_not_flip(self):
        assert not should_mark_catalog_read(
            'Edit', {'file_path': 'stores/x/CATALOG.md'}
        )

    @pytest.mark.parametrize(
        'command',
        [
            'cat stores/x/CATALOG.md',
            'head -50 knowledge/CATALOG.md',
            # The exact form a CI agent was refused for.
            'find stores/x -name "CATALOG.md" -print -exec cat {} \\;',
        ],
    )
    def test_a_shell_read_of_the_catalog_flips(self, command):
        assert should_mark_catalog_read('Bash', {'command': command})

    @pytest.mark.parametrize(
        'command',
        [
            'grep -rn SECRET stores/ knowledge/',
            'grep -rn CATALOG.md knowledge/',  # names it, does not read it
            'ls stores/x/',
        ],
    )
    def test_a_search_that_does_not_read_it_does_not_flip(self, command):
        assert not should_mark_catalog_read('Bash', {'command': command})

    def test_empty_input_does_not_flip(self):
        assert not should_mark_catalog_read('Read', {})
        assert not should_mark_catalog_read('Read', {'file_path': ''})


class TestSkillFileWriteGuard:
    """Built-in Write/Edit against .claude/skills/** must be denied and
    steered to vibe_seller_save_skill — the per-task .claude copy makes
    those writes silently non-persistent."""

    @pytest.mark.parametrize('tool', ['Write', 'Edit', 'MultiEdit'])
    def test_absolute_skill_path_denied(self, tool):
        deny = check_skill_file_write(
            tool,
            {
                'file_path': (
                    '/home/runner/.vibe-seller/tasks/abc/.claude/skills/'
                    'csv-revenue-total/SKILL.md'
                )
            },
        )
        assert deny is not None
        assert 'vibe_seller_save_skill' in deny

    def test_relative_skill_path_denied(self):
        deny = check_skill_file_write(
            'Edit', {'file_path': '.claude/skills/my-skill/SKILL.md'}
        )
        assert deny is not None

    def test_bundled_skill_file_denied(self):
        """Not just SKILL.md — any file under the skill dir (references,
        scripts) must go through save_skill's `files` map."""
        deny = check_skill_file_write(
            'Write',
            {'file_path': '.claude/skills/my-skill/references/notes.md'},
        )
        assert deny is not None

    def test_non_skill_path_allowed(self):
        assert (
            check_skill_file_write(
                'Edit', {'file_path': 'stores/acme/CATALOG.md'}
            )
            is None
        )

    def test_other_dot_claude_path_allowed(self):
        """Only skills/ is guarded — e.g. a settings file is not."""
        assert (
            check_skill_file_write(
                'Write', {'file_path': '.claude/settings.json'}
            )
            is None
        )

    def test_read_tool_not_guarded(self):
        assert (
            check_skill_file_write(
                'Read', {'file_path': '.claude/skills/my-skill/SKILL.md'}
            )
            is None
        )


class TestGeneratedImagesProvenance:
    """``generated_images/`` has one writer: the vision router. Every
    file there carries a recorded prompt + model — the claim the user's
    inline card renders those bytes under — so no local command may
    write, move, or delete what is inside it.

    The incident: asked to "keep the content, only remove the
    background", an agent generated with the tool, could not SEE the
    result (text-only model), audited it with a numpy pixel script, and
    rebuilt the deliverable with rembg + Pillow over the tool's own PNG
    — leaving the user's inline card claiming a model that had not
    produced those bytes.

    Local image processing of anything ELSE is ordinary work: resizing
    a user's photo to a platform's minimum, webp→jpg, cropping a
    screenshot. The guard denies only acts landing on the tool's
    directory.
    """

    @pytest.mark.parametrize(
        'command',
        [
            # Editing files outside the directory is not the guard's
            # business — the whole set the incident over-broadly denied.
            '~/.vibe-seller/.venv/bin/uv pip install rembg 2>&1 | tail -5',
            'rembg i in.jpg out.png',
            # Resize a user's photo to Amazon's minimum.
            'magick photo.webp -resize 1000x1000 photo.jpg',
            'convert in.jpg -fuzz 5% -transparent white out.png',
            'mogrify -background white -flatten shot.png',
            'ffmpeg -i clip.mp4 -frames:v 1 frame.png',
            # Pillow / OpenCV writing anywhere outside the directory.
            (
                'python -c "from PIL import Image; '
                "c = Image.new('RGB', (2000, 2000), 'white'); "
                "c.paste(p, (10, 10)); c.save('out.png')\""
            ),
            (
                "python << 'PYEOF'\nfrom PIL import Image\n"
                "im = Image.open('a.png')\nim.save('b.png')\nPYEOF"
            ),
            'python -c "import cv2; cv2.imwrite(\'out.png\', arr)"',
            # Copying a generated file OUT to work on a copy — a read.
            'cat generated_images/main.png > work/main.png',
        ],
    )
    def test_local_processing_of_other_files_allowed(self, command):
        assert check_local_image_edit(command) is None

    @pytest.mark.parametrize(
        'command',
        [
            'cp /tmp/fixed.png generated_images/main.png',
            'mv /tmp/a.png generated_images/amazon_main.png',
            'rm generated_images/amazon_main_bg_removed.png',
            'cat /tmp/a.png > generated_images/main.png',
            'tee generated_images/main.png < /tmp/a.png',
            (
                'python -c "import pathlib; '
                "pathlib.Path('generated_images/m.png').write_bytes(b)\""
            ),
            # A converter may not take over the tool's path either.
            'magick work/x.png generated_images/main.png',
            'convert work/x.png generated_images/main.png',
            'ffmpeg -i clip.mp4 generated_images/frame.png',
            (
                'python -c "from PIL import Image; '
                "im = Image.open('in.png'); "
                "im.save('generated_images/main.png')\""
            ),
        ],
    )
    def test_writes_into_generated_images_denied(self, command):
        """generated_images/ has one writer: the vision router."""
        deny = check_local_image_edit(command)
        assert deny is not None
        assert 'generated_images/' in deny
        assert 'vibe_seller_generate_image' in deny

    @pytest.mark.parametrize(
        'command',
        [
            # Inspecting an image is how you audit it — never blocked.
            'file generated_images/main.png && stat -f "%z bytes" '
            'generated_images/main.png',
            'ls -l generated_images/',
            (
                'python -c "from PIL import Image; '
                "print(Image.open('generated_images/m.png').size)\""
            ),
            (
                'python -c "from PIL import Image; import numpy as np; '
                "a = np.array(Image.open('m.png')); print(a[0, 0])\""
            ),
            # A skill script is invoked by name; its own PIL use (e.g.
            # amazon-listing/scripts/ocr_1688.py reading images for OCR)
            # is never inspected here.
            'python scripts/ocr_1688.py ~/refs --json',
            # openpyxl workbooks also call .save() — no imaging library
            # in the snippet, so no deny.
            'python scripts/ads_bulk.py --out report.xlsx',
            'python -c "wb.save(\'out.xlsx\')"',
            # 'convert' as an ordinary word.
            'echo "convert the listing to FBA" >> notes.md',
            'mkdir -p generated_images',
        ],
    )
    def test_reading_and_unrelated_saves_allowed(self, command):
        assert check_local_image_edit(command) is None

    def test_empty_command_allowed(self):
        assert check_local_image_edit('') is None

    @pytest.mark.parametrize('tool', ['Write', 'Edit', 'MultiEdit'])
    def test_file_tool_into_generated_images_denied(self, tool):
        """The Write-tool hop around the Bash guard."""
        deny = check_generated_image_write(
            tool,
            {
                'file_path': (
                    '/home/runner/.vibe-seller/tasks/abc/generated_images/'
                    'main.png'
                )
            },
        )
        assert deny is not None
        assert 'vibe_seller_generate_image' in deny

    def test_relative_generated_images_path_denied(self):
        deny = check_generated_image_write(
            'Write', {'file_path': 'generated_images/main.png'}
        )
        assert deny is not None

    def test_other_workspace_paths_allowed(self):
        assert (
            check_generated_image_write(
                'Write', {'file_path': 'stores/acme/notes.md'}
            )
            is None
        )

    def test_read_tool_not_guarded(self):
        assert (
            check_generated_image_write(
                'Read', {'file_path': 'generated_images/main.png'}
            )
            is None
        )


class TestLocalImageEditIsInTheGuardChain:
    """The guard must be reachable through first_bash_deny — a guard
    that exists but is not registered denies nothing."""

    def test_generated_images_takeover_denied_through_chain(self):
        deny = first_bash_deny('cp /tmp/fixed.png generated_images/main.png')
        assert deny is not None
        label, reason = deny
        assert label == 'Local-image edit'
        assert 'vibe_seller_generate_image' in reason

    def test_local_processing_outside_the_directory_passes(self):
        """The other half of the narrowing: a guard that denies
        nothing unrelated must still be reachable for what it does."""
        assert first_bash_deny('uv pip install rembg') is None
        assert first_bash_deny('magick a.webp b.jpg') is None
