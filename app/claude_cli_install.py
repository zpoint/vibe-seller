"""Make sure the project-local Claude Code CLI can actually run.

``@anthropic-ai/claude-code`` ships a stub at ``bin/claude.exe``; its
``postinstall`` (``install.cjs``) puts the platform's native binary in
its place. Newer npm refuses unapproved install scripts by default
("not yet covered by allowScripts") and still exits 0, so an install
looked done while every agent call would fail with "claude native
binary not installed". ``finish`` runs the package's own install step
explicitly when the CLI does not answer, and reports a CLI that still
does not answer instead of passing it.

Used by ``vibe-seller upgrade`` (``app/cli.py``); ``install.sh`` does
the same for a fresh install.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

PACKAGE = ('@anthropic-ai', 'claude-code')


def binary(home: Path) -> Path:
    """The npm shim every agent task resolves first."""
    name = 'claude.cmd' if os.name == 'nt' else 'claude'
    return home / 'node_modules' / '.bin' / name


def runs(cli: Path) -> bool:
    """Whether the installed CLI answers ``--version``."""
    try:
        done = subprocess.run(
            [str(cli), '--version'],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def finish(home: Path) -> int:
    """0 when the CLI under *home* runs (after its install step if
    npm skipped it), else 1 with what to do printed."""
    cli = binary(home)
    if runs(cli):
        return 0
    package = home.joinpath('node_modules', *PACKAGE)
    node = shutil.which('node')
    if node and (package / 'install.cjs').is_file():
        print(
            "==> npm skipped Claude Code's install step; running it: "
            f'node install.cjs (in {package})'
        )
        subprocess.run([node, 'install.cjs'], cwd=str(package))
        if runs(cli):
            return 0
    print(
        f'Claude Code CLI is installed at {cli} but does not run. '
        f'Run `node install.cjs` in {package}, or reinstall with npm '
        'install scripts allowed.',
        file=sys.stderr,
    )
    return 1
