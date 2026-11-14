"""The project-local Claude Code CLI must actually run after an install.

Newer npm skips ``@anthropic-ai/claude-code``'s ``postinstall`` unless
install scripts are approved, leaving the ``bin/claude.exe`` stub that
answers every call with "claude native binary not installed" — and npm
still exits 0. These tests stand the package up on disk with a stub
that only answers once its ``install.cjs`` has run, and a ``node`` that
runs it, so the real subprocess calls are exercised end to end.
"""

import os
from pathlib import Path
import stat

import pytest

from app import claude_cli_install

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(os.name == 'nt', reason='POSIX shell stubs'),
]


def _exe(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('#!/bin/sh\n' + body, encoding='utf-8')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _package(home: Path, *, install_works: bool = True) -> Path:
    """A claude-code package whose CLI answers only after install.cjs."""
    package = home.joinpath('node_modules', *claude_cli_install.PACKAGE)
    package.mkdir(parents=True)
    marker = package / 'native-installed'
    _exe(
        home / 'node_modules' / '.bin' / 'claude',
        f'[ -f "{marker}" ] || {{ echo "claude native binary not installed" >&2;'
        ' exit 1; }\necho "9.9.9 (Claude Code)"\n',
    )
    (package / 'install.cjs').write_text('// placed by the test\n')
    if install_works:
        (package / 'install-effect').write_text(str(marker))
    return package


@pytest.fixture
def fake_node(tmp_path, monkeypatch):
    """A `node` that "runs install.cjs": it creates the marker the stub
    waits for, when the package says the install step works."""
    node = _exe(
        tmp_path / 'bin' / 'node',
        '[ "$1" = install.cjs ] || exit 2\n'
        '[ -f install-effect ] && touch "$(cat install-effect)"\nexit 0\n',
    )
    monkeypatch.setattr(
        claude_cli_install.shutil,
        'which',
        lambda name: str(node) if name == 'node' else None,
    )
    return node


def test_a_cli_that_runs_is_left_alone(tmp_path, fake_node):
    package = _package(tmp_path)
    (package / 'native-installed').touch()
    assert claude_cli_install.finish(tmp_path) == 0


def test_the_skipped_install_step_is_run_and_the_cli_then_runs(
    tmp_path, fake_node, capsys
):
    """The case newer npm produces: stub in place, postinstall skipped."""
    _package(tmp_path)
    assert not claude_cli_install.runs(claude_cli_install.binary(tmp_path))
    assert claude_cli_install.finish(tmp_path) == 0
    assert claude_cli_install.runs(claude_cli_install.binary(tmp_path))
    assert 'install.cjs' in capsys.readouterr().out


def test_a_cli_that_still_does_not_run_fails_loudly(
    tmp_path, fake_node, capsys
):
    _package(tmp_path, install_works=False)
    assert claude_cli_install.finish(tmp_path) == 1
    assert 'does not run' in capsys.readouterr().err


def test_without_node_the_failure_is_reported_not_passed(
    tmp_path, monkeypatch, capsys
):
    _package(tmp_path)
    monkeypatch.setattr(claude_cli_install.shutil, 'which', lambda _n: None)
    assert claude_cli_install.finish(tmp_path) == 1
    assert 'does not run' in capsys.readouterr().err


def test_a_missing_cli_does_not_run(tmp_path):
    assert not claude_cli_install.runs(tmp_path / 'nope' / 'claude')
