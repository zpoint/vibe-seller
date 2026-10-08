"""``resolve_claude_binary`` must return a path THIS OS can spawn.

On Windows npm lays down three shims next to each other — ``claude``
(a ``#!/bin/sh`` script), ``claude.cmd`` and ``claude.ps1`` — and
``os.access(path, os.X_OK)`` answers True for all of them, because on
Windows that call cannot distinguish an executable from any other
readable file. The resolver therefore handed CreateProcess the POSIX
shell script and every task died with ``WinError 2``.

The package's real entry point is a native binary
(``"bin": {"claude": "bin/claude.exe"}``) on every platform, so that is
what Windows must resolve to. Doing so also keeps ``cmd.exe`` out of the
spawn, which is what caps a command line at 8191 chars.
"""

import pytest

from app.ai import claude_backend_utils as cbu
from app.workspace.ignored_paths import RUNTIME_DIRS

pytestmark = pytest.mark.unit


def _npm_layout(root, *, exe=True, cmd=True, posix_shim=True):
    """Recreate an npm install of @anthropic-ai/claude-code."""
    pkg_bin = root / 'node_modules' / '@anthropic-ai' / 'claude-code' / 'bin'
    dot_bin = root / 'node_modules' / '.bin'
    pkg_bin.mkdir(parents=True, exist_ok=True)
    dot_bin.mkdir(parents=True, exist_ok=True)
    if exe:
        (pkg_bin / 'claude.exe').write_bytes(b'MZ fake native binary')
    if cmd:
        (dot_bin / 'claude.cmd').write_text('@echo off\r\n')
    if posix_shim:
        shim = dot_bin / 'claude'
        shim.write_text('#!/bin/sh\nexec node "$basedir/cli.js" "$@"\n')
        shim.chmod(0o755)
    return pkg_bin, dot_bin


@pytest.fixture
def windows(monkeypatch, tmp_path):
    monkeypatch.setattr(cbu, 'IS_WINDOWS', True)
    monkeypatch.setattr(cbu, 'VIBE_SELLER_DIR', tmp_path)
    monkeypatch.setattr(cbu.shutil, 'which', lambda _name: None)
    return tmp_path


@pytest.fixture
def posix(monkeypatch, tmp_path):
    monkeypatch.setattr(cbu, 'IS_WINDOWS', False)
    monkeypatch.setattr(cbu, 'VIBE_SELLER_DIR', tmp_path)
    monkeypatch.setattr(cbu.shutil, 'which', lambda _name: None)
    return tmp_path


class TestWindows:
    def test_prefers_the_native_exe_over_every_shim(self, windows):
        pkg_bin, _ = _npm_layout(windows)
        assert cbu.resolve_claude_binary() == str(pkg_bin / 'claude.exe')

    def test_never_returns_the_extensionless_posix_shim(self, windows):
        # The WinError 2 bug class: a bare-name script is not spawnable.
        _npm_layout(windows, exe=False, cmd=False, posix_shim=True)
        resolved = cbu.resolve_claude_binary()
        assert not resolved.endswith('.bin/claude')
        assert not resolved.endswith('\\.bin\\claude')
        assert resolved == 'claude'  # PATH fallback, not the shim

    def test_falls_back_to_the_cmd_shim_when_the_exe_is_absent(self, windows):
        _, dot_bin = _npm_layout(windows, exe=False)
        assert cbu.resolve_claude_binary() == str(dot_bin / 'claude.cmd')

    def test_uses_path_when_nothing_is_installed_locally(
        self, windows, monkeypatch
    ):
        bundled = r'C:\Program Files\VibeSeller\claude\claude.exe'
        monkeypatch.setattr(cbu.shutil, 'which', lambda _name: bundled)
        # This is the packaged-installer deployment: it bundles its own
        # claude.exe and puts that dir on PATH.
        assert cbu.resolve_claude_binary() == bundled

    def test_bare_name_is_the_last_resort(self, windows):
        assert cbu.resolve_claude_binary() == 'claude'


class TestPosix:
    def test_project_local_shim_wins(self, posix):
        _, dot_bin = _npm_layout(posix)
        assert cbu.resolve_claude_binary() == str(dot_bin / 'claude')

    def test_non_executable_file_is_skipped(self, posix):
        _, dot_bin = _npm_layout(posix)
        (dot_bin / 'claude').chmod(0o644)
        assert cbu.resolve_claude_binary() == 'claude'

    def test_the_windows_exe_is_not_picked_up_on_posix(self, posix):
        # Only the .bin shim is a POSIX candidate; the package binary is
        # reached through it, so precedence here must not change.
        _npm_layout(posix, posix_shim=False)
        assert cbu.resolve_claude_binary() == 'claude'


class TestSystemPromptDelivery:
    """The system prompt never rides the command line.

    It carries the store context and, for a planned run, the whole plan,
    so its size is not ours to bound — and Windows caps the whole
    command line (32767 via CreateProcess, 8191 via a cmd.exe shim).
    Inline, a schedule whose plan grew past ~7K chars died at spawn with
    ``WinError 206`` and zero messages. By file, the command line is the
    same size whatever the prompt holds, on every OS and binary.
    """

    # Past the 32767 CreateProcess cap on its own: inline, no binary
    # could spawn with it.
    PROMPT = 'Execute the following plan:\n\n' + 'step ' * 8000

    @pytest.mark.parametrize('is_windows', [True, False])
    @pytest.mark.parametrize(
        'binary',
        [
            r'C:\vibe\claude\claude.exe',
            r'C:\Users\me\AppData\Roaming\npm\claude.cmd',
            '/usr/local/bin/claude',
        ],
    )
    def test_prompt_goes_by_file_whatever_the_binary(
        self, monkeypatch, tmp_path, is_windows, binary
    ):
        monkeypatch.setattr(cbu, 'IS_WINDOWS', is_windows)
        cmd = [binary, '-p']
        cbu.append_system_prompt(cmd, self.PROMPT, 'task1234', tmp_path)
        sp_file = cbu.system_prompt_file('task1234', tmp_path)

        assert '--append-system-prompt' not in cmd
        assert cmd[-2:] == ['--append-system-prompt-file', str(sp_file)]
        assert sp_file.read_text(encoding='utf-8') == self.PROMPT
        # The invariant itself: command-line size does not depend on the
        # prompt's.
        assert sum(len(arg) + 3 for arg in cmd) < 1000

    def test_empty_prompt_adds_no_flag(self, tmp_path):
        cmd = ['claude']
        cbu.append_system_prompt(cmd, '  \n', 'task1234', tmp_path)
        assert cmd == ['claude']
        assert not (tmp_path / '.system-prompt.md').exists()

    def test_task_prompt_lives_in_the_task_dir(self, tmp_path):
        # Per-task, gitignored, wiped on retry — never a predictable
        # name in a shared, world-readable temp dir.
        assert cbu.system_prompt_file('task1234', tmp_path) == (
            tmp_path / '.system-prompt.md'
        )

    def test_dirless_session_stays_out_of_the_tracked_workspace(self):
        """The workspace assistant has no task dir; its prompt must not
        land in the git-tracked workspace root."""
        path = cbu.system_prompt_file('user-1', None)
        rel = path.relative_to(cbu.VIBE_SELLER_DIR)
        assert rel.parts[0] in RUNTIME_DIRS

    def test_unwritable_location_fails_loudly(self, tmp_path):
        """No inline fallback — that is the unbounded path this removes."""
        blocker = tmp_path / 'file'
        blocker.write_text('')
        cmd = ['claude']
        with pytest.raises(OSError):
            cbu.append_system_prompt(cmd, self.PROMPT, 'task1234', blocker)
        assert self.PROMPT not in cmd
