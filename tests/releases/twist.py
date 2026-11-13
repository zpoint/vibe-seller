#!/usr/bin/env python3
"""Inject ONE named bug into this checkout, so CI can prove a check.

    python tests/releases/twist.py <name>      # inject
    python tests/releases/twist.py --list      # self-test, inject nothing
    python tests/releases/twist.py none        # no-op (the CI default)

A green upgrade test says nothing by itself: it has to be shown capable
of going red. Each twist below is a bug the update feature could plausibly
have — the wrong path chosen, the rollback skipped, the digest never
checked — written into the REAL source by exact-string replacement at the
smallest honest spot, i.e. the one line that carries the decision. Each
twist names the scenario ids it must turn red: the legs of
``.github/workflows/cli-upgrade.yml`` run S1/S2/S5/S5b/S5c/S7, the jobs
of ``bundle-fingerprint.yml`` run S8, the release gate
(``pip-upgrade-gate.yml``) runs S9, and S4/S6 belong to the Windows
installer legs (``windows-installer.yml``), which a Linux/macOS runner
cannot reach.

Two rules keep this useful:

* **It fails loudly.** If the target text is not there exactly once, the
  script exits non-zero and says so. Code moves; a twist that silently
  stopped matching would turn every "prove it can fail" run green, which
  is worse than having no twists at all. ``--list`` runs the same anchor
  check without touching anything, so a PR can self-test cheaply.
* **One file per twist.** Each injection is one hunk in one file, so
  ``git diff --stat`` after a run shows exactly what the job was built
  from — and a red leg points at one decision, not a refactor.

The patched tree is a BUGGY TREE, not a linted one: nothing is checked
beyond "it still compiles", and the ``TWIST(<name>)`` comment on the
injected line is what identifies it in a log or a diff.

Usage from a workflow: run it right after checkout and BEFORE anything is
built, so the bug is inside the wheels and the installer the legs then
upgrade between.
"""

from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
import sys

#: The checkout this script patches (it lives in ``tests/releases``).
ROOT = Path(__file__).resolve().parents[2]

#: Marks an injected line, so a log can name the bug it just wrote.
MARKER = 'TWIST('


@dataclass(frozen=True)
class Twist:
    """One named bug: where it goes in, and what must go red."""

    name: str
    #: Repo-relative source file — the only file this twist touches.
    rel: str
    #: The scenario ids this twist must turn red.
    breaks: str
    #: What the bug is, and what it costs. One line, for the CI log.
    why: str
    #: The exact text to find (must occur exactly once) and its
    #: replacement. Both are line tuples joined with the file's own
    #: newline, so the same anchor matches a CRLF Windows checkout and an
    #: LF one.
    old: tuple[str, ...]
    new: tuple[str, ...]



TWISTS: tuple[Twist, ...] = (
    Twist(
        name='wrong-path',
        rel='app/bundle.py',
        breaks='S4',
        why=(
            'the pip-vs-installer choice always answers pip, so a release '
            'that really changed the bundled runtime gets patched over a '
            'runtime it cannot replace'
        ),
        old=('    return bool(mine) and mine == theirs',),
        new=('    return True  # TWIST(wrong-path): always answer pip',),
    ),
    Twist(
        name='no-rollback',
        rel='app/pip_apply_helper.py',
        breaks='S5, S5b',
        why=(
            'the apply helper puts nothing back when the new build never '
            'boots, so one bad release leaves the machine with no server'
        ),
        old=("    if current['kind'] == 'freeze':",),
        new=(
            '    return  # TWIST(no-rollback): nothing is ever put back',
            "    if current['kind'] == 'freeze':",
        ),
    ),
    Twist(
        name='ignore-idle',
        rel='app/update_idle.py',
        breaks='S2',
        why=(
            'the idle gate never sees a live task agent, so an update '
            "restarts the server in the middle of somebody's running task"
        ),
        # The probe BOTH halves of the gate read (the decision and the
        # re-check after dispatch is paused). Breaking only the first
        # half is caught by the second, so S2 stayed green under the
        # earlier form of this twist and proved nothing.
        old=(
            '    return agents.live_agent_count() if agents is not None '
            'else 0',
        ),
        new=('    return 0  # TWIST(ignore-idle): no agent is ever seen',),
    ),
    Twist(
        name='no-digest-check',
        rel='app/windows_update.py',
        breaks='S6',
        why=(
            'the staged installer is never hashed against the release '
            'asset, so corrupt or tampered bytes are the ones launched'
        ),
        old=('    if expected_sha:', '        actual = _sha256_file(part)'),
        new=(
            '    if False:  # TWIST(no-digest-check): bytes never hashed',
            '        actual = _sha256_file(part)',
        ),
    ),
    Twist(
        name='raw-fingerprint',
        rel='app/bundle.py',
        breaks='S8',
        why=(
            'fingerprint inputs are hashed as raw bytes, so one commit '
            'carries a different fingerprint per checkout (CRLF vs LF) and '
            'no Windows install ever takes the pip path'
        ),
        old=('    return path.suffix.lower() in _HASHED_AS_TEXT',),
        new=('    return False  # TWIST(raw-fingerprint): raw bytes',),
    ),
    Twist(
        name='retry-bad',
        rel='app/auto_update.py',
        breaks='S5c',
        why=(
            'the idle tick stops honouring the refusal, so a release '
            'this machine already applied and had to put back is '
            'applied again every cycle — not one outage for a bad '
            'build but one per check, forever'
        ),
        old=("    if not st['auto_apply']:",),
        new=('    if False:  # TWIST(retry-bad): refusal not honoured',),
    ),
    Twist(
        name='dev-updates',
        rel='app/release_channel.py',
        breaks='S7',
        why=(
            'the dev channel is treated as stable, so a source checkout or '
            'PR build starts fetching published releases'
        ),
        old=(
            '    if parsed.local is not None:',
            "        return 'dev'",
        ),
        new=(
            '    if parsed.local is not None:',
            "        return 'stable'  # TWIST(dev-updates)",
        ),
    ),
)

BY_NAME = {t.name: t for t in TWISTS}


def _out(text: str = '') -> None:
    """Write one line to stdout: this script's whole interface is its log."""
    sys.stdout.write(f'{text}\n')


def _read(rel: str) -> tuple[str, str]:
    """The file's text and its newline, so a CRLF checkout and an LF one
    both find the same anchor and keep their own line endings."""
    raw = (ROOT / rel).read_bytes().decode('utf-8')
    return raw, ('\r\n' if '\r\n' in raw else '\n')


def describe(twist: Twist) -> str:
    return f'{twist.name:<18s} {twist.rel:<26s} breaks {twist.breaks}'


def check(twist: Twist) -> str | None:
    """None when *twist* can still be injected, else why it cannot."""
    path = ROOT / twist.rel
    if not path.is_file():
        return f'{twist.rel} is not under {ROOT} — wrong checkout?'
    text, nl = _read(twist.rel)
    old = nl.join(twist.old)
    new = nl.join(twist.new)
    if new in text:
        return f'{twist.rel}: twist {twist.name} is ALREADY applied'
    hits = text.count(old)
    if hits == 0:
        return (
            f'{twist.rel}: the anchor {twist.old[0]!r} is not there — the '
            'code moved. Point this twist at the new decision line; an '
            'unmatched twist proves nothing.'
        )
    if hits > 1:
        return (
            f'{twist.rel}: the anchor occurs {hits} times — use a longer '
            'anchor so the twist patches exactly one decision'
        )
    return None


def apply(twist: Twist) -> int:
    """Inject *twist*, print the summary, return the exit code."""
    problem = check(twist)
    if problem:
        sys.stderr.write(f'twist {twist.name}: {problem}\n')
        return 1
    _out(f'== twist {twist.name} (breaks {twist.breaks})')
    text, nl = _read(twist.rel)
    patched = text.replace(nl.join(twist.old), nl.join(twist.new), 1)
    try:
        compile(patched, twist.rel, 'exec')
    except SyntaxError as exc:
        sys.stderr.write(
            f'twist {twist.name}: the patched {twist.rel} does not compile '
            f'({exc}); this twist is written wrong\n'
        )
        return 1
    (ROOT / twist.rel).write_bytes(patched.encode('utf-8'))
    _out(f'{MARKER}{twist.name}) in {twist.rel} — breaks {twist.breaks}')
    _out(f'   {twist.why}')
    for line in difflib.unified_diff(
        text.splitlines(),
        patched.splitlines(),
        fromfile=f'a/{twist.rel}',
        tofile=f'b/{twist.rel}',
        lineterm='',
        n=0,
    ):
        _out(
            f'   {line}'
            if line.startswith(('---', '+++', '@@'))
            else f'      {line}'
        )
    return 0


def list_twists() -> int:
    """The self-test: every anchor still exact, nothing injected."""
    stale = 0
    for twist in TWISTS:
        problem = check(twist)
        line = describe(twist)
        if problem:
            stale += 1
            line = f'{line}  [STALE] {problem}'
        _out(line)
    if stale:
        _out(f'{stale} of {len(TWISTS)} twists can no longer be injected')
        return 1
    _out(f'{len(TWISTS)} twists OK (each anchor found exactly once)')
    return 0


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a]
    if '--list' in args or '-l' in args:
        return list_twists()
    if '-h' in args or '--help' in args:
        _out('usage: twist.py <name>|none|--list')
        for twist in TWISTS:
            _out(f'  {describe(twist)}')
            _out(f'      {twist.why}')
        return 0
    name = args[0] if args else 'none'
    if name == 'none':
        _out('twist=none: no bug injected')
        _out(f'   available: {", ".join(BY_NAME)}, none')
        return 0
    twist = BY_NAME.get(name)
    if twist is None:
        sys.stderr.write(
            f'twist: unknown name {name!r}. Known: '
            f'{", ".join(BY_NAME)}, none, --list\n'
        )
        return 2
    return apply(twist)


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
