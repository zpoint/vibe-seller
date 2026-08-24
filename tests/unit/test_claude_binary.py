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
    """How the prompt reaches claude depends on what we are spawning.

    Inline is right for a native binary (32767-char CreateProcess limit,
    no shell). A batch shim routes through ``cmd.exe``, whose 8191-char
    cap a 10-20KB store-context prompt blows straight past — that case
    has to go by file or the agent dies before it starts.
    """

    PROMPT = 'store context ' * 1000  # ~14KB, a realistic size

    def test_native_exe_keeps_the_prompt_inline(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cbu, 'IS_WINDOWS', True)
        cmd = [r'C:\vibe\node_modules\@anthropic-ai\claude-code\claude.exe']
        cbu.append_system_prompt(cmd, self.PROMPT, 'task1234', tmp_path)
        assert '--append-system-prompt' in cmd
        assert '--append-system-prompt-file' not in cmd
        assert self.PROMPT in cmd

    def test_batch_shim_passes_the_prompt_by_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cbu, 'IS_WINDOWS', True)
        cmd = [r'C:\Users\me\AppData\Roaming\npm\claude.cmd']
        cbu.append_system_prompt(cmd, self.PROMPT, 'task1234', tmp_path)
        assert '--append-system-prompt-file' in cmd
        assert self.PROMPT not in cmd
        sp_file = tmp_path / '.system-prompt.md'
        assert sp_file.read_text(encoding='utf-8') == self.PROMPT
        # In the task dir, which is per-task and wiped on retry — never a
        # predictable name in a shared, world-readable temp dir.
        assert str(sp_file) in cmd

    def test_bat_shim_too(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cbu, 'IS_WINDOWS', True)
        cmd = [r'C:\tools\claude.BAT']
        cbu.append_system_prompt(cmd, self.PROMPT, 'task1234', tmp_path)
        assert '--append-system-prompt-file' in cmd

    def test_posix_is_never_routed_through_a_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cbu, 'IS_WINDOWS', False)
        # A POSIX path can legitimately end in .cmd; only Windows spawns
        # batch files through a shell.
        cmd = ['/usr/local/bin/claude.cmd']
        cbu.append_system_prompt(cmd, self.PROMPT, 'task1234', tmp_path)
        assert '--append-system-prompt-file' not in cmd
        assert self.PROMPT in cmd

    def test_unwritable_task_dir_falls_back_to_inline(
        self, monkeypatch, tmp_path
    ):
        """Degraded, but loud — better than no prompt at all."""
        monkeypatch.setattr(cbu, 'IS_WINDOWS', True)
        cmd = [r'C:\npm\claude.cmd']
        missing = tmp_path / 'no' / 'such' / 'dir'
        cbu.append_system_prompt(cmd, self.PROMPT, 'task1234', missing)
        assert '--append-system-prompt-file' not in cmd
        assert self.PROMPT in cmd
