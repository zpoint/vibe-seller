"""Lay WeCom down on an owned e2e server as if an admin had connected it.

CI cannot sign in to a real WeCom, so the "installed" ``wecom-cli`` is
the stand-in ``fake_wecom_cli.py``: the server checks it, and agents run
it, exactly as they would the real one. The umbrella skill is written
the way ``manager.enable`` writes it, so the platform is enabled. Which
permissions are expired is changed with :func:`set_expired` while the
server runs; :func:`renew` is what a new scan does (every permission
works again and the stored sign-in changes).
"""

import json
import os
from pathlib import Path
import shutil
import sys
import time

from app.cli_integrations import paths, skills
from app.cli_integrations.registry import INTEGRATIONS

STANDIN = Path(__file__).with_name('fake_wecom_cli.py')
VERSION = '1.3.4'

#: Each permission the Settings row lists, in its order
#: (``wecom.PERMISSION_PROBES``).
PERMISSIONS = (
    'doc',
    'contact',
    'message',
    'mail',
    'disk',
    'calendar',
    'meeting',
    'todo',
)

# What an umbrella needs to be enabled, for a test that runs no agent.
_MINIMAL_SUBS = {
    'shared': {
        'SKILL.md': b'---\nname: wecomcli-shared\ndescription: Shared.\n---\n'
    },
}


def install(home: Path, *, subs: dict | None = None) -> None:
    """Install the stand-in as WeCom's current version and enable it
    (blocking). *subs*: the sub-skills (default: just enough to be
    enabled; pass the real ones for an agent to read)."""
    if sys.platform == 'win32':
        raise RuntimeError('the stand-in is a shebang script')
    integ = INTEGRATIONS['wecom']
    vdir = paths.version_dir('wecom', VERSION, home)
    vdir.mkdir(parents=True, exist_ok=True)
    exe = vdir / paths.exe_name(integ.binary)
    shutil.copyfile(STANDIN, exe)
    exe.chmod(0o755)
    paths.write_manifest(
        'wecom', {'version': VERSION, 'source': 'e2e stand-in'}, home
    )
    state = paths.state_dir('wecom', home)
    state.mkdir(parents=True, exist_ok=True)
    (state / 'credentials.enc').write_text('stand-in', encoding='utf-8')
    set_expired(home, ())
    skills.install(integ, VERSION, subs or _MINIMAL_SUBS, home)


def set_expired(home: Path, expired) -> None:
    state = paths.state_dir('wecom', home)
    state.mkdir(parents=True, exist_ok=True)
    (state / 'standin.json').write_text(
        json.dumps({'expired': sorted(expired)}), encoding='utf-8'
    )


def renew(home: Path) -> None:
    """A new scan: every permission works and the stored sign-in
    changes (newer than any earlier check, whatever the clock's
    resolution)."""
    set_expired(home, ())
    cred = paths.state_dir('wecom', home) / 'credentials.enc'
    cred.write_text(f'renewed {time.time()}', encoding='utf-8')
    later = time.time() + 5
    os.utime(cred, (later, later))


def calls(home: Path) -> list[list[str]]:
    """Every argv the stand-in was run with, oldest first."""
    log = paths.state_dir('wecom', home) / 'calls.log'
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]
