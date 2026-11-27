"""Install an enabled stand-in WeCom on an owned e2e server.

``set_expired`` switches its one authorization state while the server
runs; ``renew`` also changes the stored sign-in, as a fresh scan does.
"""

import json
import os
from pathlib import Path
import sys
import tempfile
import time

from app.cli_integrations import paths, skills
from app.cli_integrations.registry import INTEGRATIONS

STANDIN = Path(__file__).with_name('fake_wecom_cli.py')
VERSION = '1.3.4'
_MINIMAL_SUBS = {
    'shared': {
        'SKILL.md': b'---\nname: wecomcli-shared\ndescription: Shared.\n---\n'
    },
}


def install_executable(source: Path, destination: Path) -> None:
    """Atomically replace *destination* with an executable *source*.

    A previous test's server may still be executing the old file. Opening
    that inode for writing raises ETXTBSY; replacing its directory entry
    does not. The temporary file lives in the same directory, so
    ``os.replace`` is atomic, and it is closed before the swap.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(
        dir=destination.parent, prefix=f'.{destination.name}.'
    )
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(source.read_bytes())
            out.flush()
            os.fsync(out.fileno())
        temporary.chmod(0o755)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def install(home: Path, *, subs: dict | None = None) -> None:
    if sys.platform == 'win32':
        raise RuntimeError('the stand-in is a shebang script')
    integ = INTEGRATIONS['wecom']
    vdir = paths.version_dir('wecom', VERSION, home)
    vdir.mkdir(parents=True, exist_ok=True)
    exe = vdir / paths.exe_name(integ.binary)
    install_executable(STANDIN, exe)
    paths.write_manifest(
        'wecom', {'version': VERSION, 'source': 'e2e stand-in'}, home
    )
    state = paths.state_dir('wecom', home)
    state.mkdir(parents=True, exist_ok=True)
    (state / 'credentials.enc').write_text('stand-in', encoding='utf-8')
    set_expired(home, False)
    skills.install(integ, VERSION, subs or _MINIMAL_SUBS, home)


def set_expired(home: Path, expired: bool) -> None:
    state = paths.state_dir('wecom', home)
    state.mkdir(parents=True, exist_ok=True)
    (state / 'standin.json').write_text(
        json.dumps({'expired': expired}), encoding='utf-8'
    )


def renew(home: Path) -> None:
    set_expired(home, False)
    cred = paths.state_dir('wecom', home) / 'credentials.enc'
    cred.write_text(f'renewed {time.time()}', encoding='utf-8')
    later = time.time() + 5
    os.utime(cred, (later, later))


def calls(home: Path) -> list[list[str]]:
    log = paths.state_dir('wecom', home) / 'calls.log'
    if not log.is_file():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]
