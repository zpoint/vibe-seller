"""Static checks on the Windows leg harness that need no PowerShell.

Two invariants of the same kind: the file must be able to RUN in the
shell a runner or a developer box actually has, and a helper must not
read state that only a previous step had.

Both were learned the expensive way. CI's six Windows legs died
identically at `win_leg_harness.ps1:254`, `& $script:Cli start`, with
"The expression after '&' in a pipeline element produced an object that
was not valid" (run 43415553813) — the leg bodies had been lifted out of
the workflow steps into this file, and the `$script:Port / Base /
Install / Cli / Exe` they used to share with the step body came across
as references only. Each step dot-sources the harness in a FRESH
PowerShell, so those names were null everywhere. A file that parses and
loads is not a file that runs: that is what this pins.

Comment lines are stripped before scanning. Prose about a variable is
not a use of it — the header discusses `$script:Home` at length, and an
earlier version of this check failed on that paragraph alone.
"""

from pathlib import Path
import re

import pytest

pytestmark = pytest.mark.unit

_HARNESS = Path(__file__).resolve().parents[2] / (
    'tests/releases/win_leg_harness.ps1'
)

_READ = re.compile(r'\$script:([A-Za-z_][A-Za-z0-9_]*)')
_ASSIGN = re.compile(r'\$script:([A-Za-z_][A-Za-z0-9_]*)\s*=')


def _code(text: str) -> str:
    """The file without its comment lines."""
    return '\n'.join(
        line for line in text.splitlines() if not line.lstrip().startswith('#')
    )


def _harness_text() -> str:
    return _HARNESS.read_bytes().decode('utf-8-sig')


def test_harness_exists():
    assert _HARNESS.is_file(), f'missing {_HARNESS}'


def test_every_script_variable_the_harness_reads_it_assigns_too():
    """No helper may depend on the caller's scope.

    Assignments anywhere in the file count — at dot-source time or
    inside a function that sets script state — but a name that is
    never assigned at all exists only in whichever step used to
    contain this code.
    """
    body = _code(_harness_text())
    read = set(_READ.findall(body))
    assigned = set(_ASSIGN.findall(body))
    dangling = sorted(read - assigned)
    assert not dangling, (
        'win_leg_harness.ps1 reads $script:'
        + ', $script:'.join(dangling)
        + ' but never assigns '
        'them, so they are null in every CI step that dot-sources it'
    )


def test_harness_is_utf8_bom_or_plain_ascii():
    """Windows PowerShell 5.1 reads a BOM-less file as ANSI.

    Non-ASCII text — the em-dashes the `FAIL:` messages use — comes out
    mojibaked, which is invisible on pwsh 7 and unreadable in the CI
    log a person is told to read. Either write it as pure ASCII or carry
    the BOM; silently picking the third option is what this stops.
    """
    raw = _HARNESS.read_bytes()
    has_bom = raw.startswith(b'\xef\xbb\xbf')
    is_ascii = True
    try:
        raw.decode('ascii')
    except UnicodeDecodeError:
        is_ascii = False
    assert has_bom or is_ascii, (
        'win_leg_harness.ps1 has non-ASCII bytes and no UTF-8 BOM; '
        'PowerShell 5.1 will not read its messages correctly'
    )
